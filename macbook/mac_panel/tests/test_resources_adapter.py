"""Resources reads: probes, memory estimate, health, shared snapshot, session names, history."""
import dataclasses
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from cli_runner import CommandResult
from llm_sessions import LSOF, NameSource
from panel_contracts import ApiError, Level, Section
from panel_routes import PanelRoutes
from panel_test_support import FakeRunner, auth, request, running_server
from process_table import started_epoch
import resources_adapter
from resources_adapter import SNAPSHOT_TTL, ResourcesAdapter, health
from resources_fixtures import (PANEL_PID, PANEL_ROW, STARTED, ArgvRunner, FakeMetrics, FakeReader,
                                make_table)
from sampler import CADENCES, samples
from signal_policy import SignalPolicy
from system_metrics import (DISK_PATH, PMSET, SYSCTL, VM_STAT, Pressure, Probe, SystemFacts,
                            SystemMetrics, Thermal, memory_split)

PAGE = 16384
VM_STAT_TEXT = f'''Mach Virtual Memory Statistics: (page size of {PAGE} bytes)
Pages free:                                     7813.
Pages active:                                 277590.
Pages speculative:                               415.
Pages wired down:                             307195.
Pages purgeable:                                   2.
"Translation faults":                   117231836521.
File-backed pages:                            203342.
Anonymous pages:                              349040.
Pages occupied by compressor:                 650218.
'''
TOTAL = 1572864 * PAGE
SYSCTL_TEXT = {'hw.memsize': f'{TOTAL}\n', 'hw.ncpu': '14\n', 'vm.loadavg': '{ 3.77 12.15 14.98 }\n',
               'vm.swapusage': 'total = 5120.00M  used = 3553.38M  free = 1566.62M  (encrypted)\n',
               'kern.memorystatus_vm_pressure_level': '1\n',
               'kern.boottime': '{ sec = 1000, usec = 5 } Sat Oct 17 08:12:55 2026\n'}
THERM = 'Note: No thermal warning level has been recorded\nNote: No performance warning level has been recorded\n'
BATT = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t51%; discharging; 3:29 remaining present: true\n"
WORLD = (
    (1, 0, '/sbin/launchd', {'uid': 0}),
    PANEL_ROW,
    (101, 1, '-zsh'),
    (200, 101, 'claude -p secret prompt about payroll --password hunter2', {'cpu': 50.0, 'rss_kb': 4096}),
    (201, 200, 'node /x/tool.js --api-key sk-live-123', {'cpu': 25.0}),
    (300, 101, 'codex --no-alt-screen fix the tax bug'),
    (400, 1, 'node /x/model-gateway.js serve', {'cpu': 1.0}),
    (500, 1, 'python3 -m van_compute.worker', {'rss_kb': 2048}),
    (600, 1, 'Mail', {'cpu': 3.0}),
    (601, 600, 'mail-helper', {'cpu': 2.0}),
    # Argument values the display names must not pick up.
    (700, 1, 'bash --rcfile /Users/me/private-rc.sh'),
    (701, 1, 'node --redirect-warnings /Users/me/warn.js app.js'),
    (702, 1, 'perl -esleep 9'),
    (703, 1, 'python3 /Users/me/tax notes/run.py'),
)


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


def probe_answers(overrides=None):
    answers = {(SYSCTL, '-n', key): text for key, text in SYSCTL_TEXT.items()}
    answers.update({(VM_STAT,): VM_STAT_TEXT, (PMSET, '-g', 'therm'): THERM,
                    (PMSET, '-g', 'batt'): BATT})
    answers.update(overrides or {})
    return answers


def disk(total=1000, free=500):
    return lambda path: mock.Mock(total=total, free=free) if path == DISK_PATH else None


class SystemMetricsTests(unittest.TestCase):
    def collect(self, runner, **kwargs):
        return SystemMetrics(runner, wall_clock=lambda: 4600, disk_usage=disk(**kwargs)).collect()

    def test_every_probe_parses(self):
        facts = self.collect(ArgvRunner(probe_answers()))
        self.assertEqual(facts.unavailable, [])
        self.assertEqual(facts.get(Probe.NCPU), 14)
        self.assertEqual(facts.get(Probe.LOAD), [3.77, 12.15, 14.98])
        self.assertEqual(facts.get(Probe.SWAP), {'total': 5120 * 2 ** 20, 'used': round(3553.38 * 2 ** 20)})
        self.assertIs(facts.get(Probe.PRESSURE), Pressure.NORMAL)
        self.assertEqual(facts.get(Probe.UPTIME), 3600)
        self.assertIs(facts.get(Probe.THERMAL), Thermal.NOMINAL)
        self.assertEqual(facts.get(Probe.POWER), {'source': 'battery', 'percent': 51, 'charging': False})
        self.assertEqual(facts.get(Probe.DISK), {'total': 1000, 'free': 500})
        memory = facts.get(Probe.MEMORY)
        self.assertEqual(memory['app'], (349040 - 2) * PAGE)
        self.assertEqual(memory['used'], (349038 + 307195 + 650218) * PAGE)
        self.assertEqual(memory['cached'], (203342 + 2) * PAGE)
        self.assertEqual(memory['free'], (7813 + 415) * PAGE)
        self.assertEqual(sum(memory[key] for key in ('used', 'cached', 'free', 'other')), TOTAL)

    def test_one_failed_probe_leaves_the_others(self):
        runner = ArgvRunner(probe_answers({
            (PMSET, '-g', 'therm'): ApiError(504, 'timed out'),
            (SYSCTL, '-n', 'vm.swapusage'): CommandResult(1, '', 'unknown oid'),
            (SYSCTL, '-n', 'kern.memorystatus_vm_pressure_level'): '3\n'}))
        facts = self.collect(runner)
        self.assertEqual(sorted(facts.unavailable), ['pressure', 'swap', 'thermal'])
        self.assertEqual(facts.get(Probe.NCPU), 14)
        self.assertIsNotNone(facts.get(Probe.MEMORY))
        self.assertEqual(health(facts, make_table())['level'], Level.UNKNOWN)

    def test_negative_accounting_makes_memory_unavailable(self):
        vm = {'anonymous': 1, 'purgeable': 5, 'wired': 0, 'compressor': 0, 'file': 0, 'free': 0,
              'speculative': 0}
        self.assertIsNone(memory_split(vm, 100))
        self.assertIsNone(memory_split({**vm, 'anonymous': 50, 'free': 80}, 100))
        text = VM_STAT_TEXT.replace('Anonymous pages:                              349040.',
                                    'Anonymous pages:                                   1.')
        facts = self.collect(ArgvRunner(probe_answers({(VM_STAT,): text})))
        self.assertIn('memory', facts.unavailable)
        self.assertIsNone(facts.get(Probe.MEMORY))

    def test_malformed_numbers_make_only_their_probe_unavailable(self):
        runner = ArgvRunner(probe_answers({
            (SYSCTL, '-n', 'vm.loadavg'): '{ 1..2 3.0 4.0 }\n',
            (SYSCTL, '-n', 'vm.swapusage'): 'total = 1..2M  used = 3.0M  free = 1.0M\n',
            (SYSCTL, '-n', 'hw.ncpu'): '1..2\n'}))
        facts = self.collect(runner)
        self.assertEqual(sorted(facts.unavailable), ['load', 'ncpu', 'swap'])
        self.assertIsNotNone(facts.get(Probe.MEMORY))
        self.assertIs(facts.get(Probe.PRESSURE), Pressure.NORMAL)

    def test_a_spent_budget_marks_the_rest_unavailable(self):
        clock = Clock()

        def slow(*_args, **_kwargs):
            clock.now += 100
            return CommandResult(0, '14\n')
        metrics = SystemMetrics(mock.Mock(run=slow), clock=clock, disk_usage=disk())
        facts = metrics.collect()
        self.assertEqual(facts.unavailable[:2], ['ncpu', 'load'])
        self.assertEqual(facts.get(Probe.MEMORY_TOTAL), 14)


class HealthTests(unittest.TestCase):
    def level(self, values, unavailable=()):
        return health(SystemFacts(values, list(unavailable)), make_table())

    def test_levels(self):
        ok = {Probe.PRESSURE: Pressure.NORMAL, Probe.DISK: {'total': 100, 'free': 50}}
        self.assertEqual(self.level(ok)['level'], 'ok')
        self.assertEqual(self.level({**ok, Probe.PRESSURE: Pressure.CRITICAL})['level'], 'error')
        self.assertEqual(self.level({**ok, Probe.PRESSURE: Pressure.WARN})['level'], 'warn')
        self.assertEqual(self.level({**ok, Probe.THERMAL: Thermal.WARNING})['level'], 'warn')
        low = self.level({**ok, Probe.DISK: {'total': 100, 'free': 9}})
        self.assertEqual(low['level'], 'warn')
        self.assertIn('9% free', low['reasons'][0])
        self.assertEqual(self.level({}, ['pressure'])['level'], 'unknown')

    def test_rows_lost_between_ps_passes_are_not_a_health_reason(self):
        # A table that passed the usable-table check has only the few rows that start or
        # exit between passes missing; reporting them would be a permanent Overview item.
        table = dataclasses.replace(make_table(), malformed=2)
        ok = {Probe.PRESSURE: Pressure.NORMAL, Probe.DISK: {'total': 100, 'free': 50}}
        self.assertEqual(health(SystemFacts(ok, []), table)['reasons'], [])

    def test_unknown_pressure_stays_visible_under_a_stronger_warning(self):
        # Precedence is error > warn > unknown > ok, and the unknown probe is still reported.
        for unavailable in (['pressure'], []):
            result = self.level({Probe.DISK: {'total': 100, 'free': 1}}, unavailable)
            self.assertEqual(result['level'], 'warn')
            self.assertIn('Memory pressure is unavailable', result['reasons'])
            self.assertEqual(result['unavailable'], ['pressure'])


class AdapterFixture(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)
        self.reader = FakeReader(make_table(*WORLD))
        self.metrics = FakeMetrics({Probe.NCPU: 10, Probe.PRESSURE: Pressure.NORMAL, Probe.LOAD: [1.0, 2.0, 3.0],
                                    Probe.MEMORY_TOTAL: TOTAL}, ['thermal'])
        self.clock = Clock()
        self.lsof = CommandResult(0, 'p300\nfcwd\nn/Users/me/dev/taxes\n')
        self.runner = ArgvRunner({(LSOF, '-a', '-p', '300', '-Fpfn'): self.lsof})
        self.write_registry(200, name='Budget review', status='busy', cwd='/Users/me/dev/pay',
                            kind='interactive', procStart=STARTED)

    def adapter(self):
        return ResourcesAdapter(runner=self.runner, reader=self.reader, metrics=self.metrics,
                                clock=self.clock, wall_clock=lambda: 1234.0, home=self.home,
                                policy=SignalPolicy(uid=501, panel_pid=PANEL_PID))

    def write_registry(self, pid, **fields):
        directory = self.home / '.claude/sessions'
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f'{pid}.json').write_text(json.dumps({'pid': pid, **fields}))
        (directory / f'{pid}.abc123.key').write_text('{"pid": %d, "name": "from the key"}' % pid)


class AdapterReadTests(AdapterFixture):
    def test_payload_shape_names_and_no_arguments(self):
        opened = []
        real_open = os.open
        with mock.patch('llm_sessions.os.open', side_effect=lambda p, *a: opened.append(str(p)) or real_open(p, *a)):
            payload = self.adapter().read()
        self.assertFalse(any(path.endswith('.key') for path in opened), opened)
        self.assertEqual(set(payload), {'sampled_at', 'health', 'system', 'totals', 'groups', 'apps',
                                        'processes'})
        text = json.dumps(payload)
        for secret in ('payroll', 'hunter2', 'sk-live', 'tax bug', 'from the key', '--', 'rcfile',
                       'private', 'notes', 'sleep 9', 'esleep', 'warn.js'):
            self.assertNotIn(secret, text)
        groups = {group['id']: group for group in payload['groups']}
        claude = groups['claude:200']
        self.assertEqual((claude['name'], claude['status'], claude['cwd'], claude['count']),
                         ('Budget review', 'busy', '/Users/me/dev/pay', 2))
        self.assertEqual(claude['name_source'], 'registry')
        self.assertEqual(groups['codex:300']['name_source'], 'fallback')
        self.assertIsNone(groups['infra:400']['name_source'])
        names = {p['pid']: p['name'] for p in payload['processes']}
        self.assertEqual([names[pid] for pid in (700, 701, 702, 703)], ['bash', 'node', 'perl', 'python3'])
        self.assertEqual(claude['cpu'], 75.0)
        self.assertIsNone(claude['tree_blocked'])
        self.assertEqual([p['name'] for p in claude['processes']], ['claude', 'node tool.js'])
        self.assertEqual(groups['codex:300']['name'], 'codex · taxes')
        self.assertEqual(groups['infra:400']['name'], 'model gateway')
        self.assertIsNotNone(groups['infra:400']['tree_blocked'])
        self.assertEqual(groups['compute:500']['name'], 'worker')
        self.assertEqual(payload['totals']['claude'], {'cpu': 75.0, 'rss': 5120 * 1024, 'count': 2})
        self.assertEqual(set(payload['totals']), {'claude', 'codex', 'infra', 'compute'})
        self.assertEqual(payload['apps'][0], {'name': 'Mail', 'root_pid': 600, 'started': STARTED,
                                              'cpu': 5.0, 'rss': 2048 * 1024, 'count': 2})
        self.assertEqual(payload['system']['cpu_percent'], 8.1)
        self.assertEqual(payload['health']['unavailable'], ['thermal'])
        self.assertEqual(payload['system']['memory']['total'], TOTAL)
        self.assertIsNone(payload['system']['memory']['used'])
        process = next(p for p in payload['processes'] if p['pid'] == 101)
        self.assertEqual((process['name'], process['group'], process['user']),
                         ('zsh', None, process['user']))
        self.assertIsNotNone(process['blocked'])

    def test_every_process_is_listed_busiest_first_so_an_idle_one_can_be_found_by_name(self):
        busy = [(1000 + n, 1, f'/usr/bin/busy{n}', {'cpu': 50.0 + n, 'rss_kb': 100000 + n}) for n in range(100)]
        idle = (2001, 1, '/bin/sleep 901', {'cpu': 0.0, 'rss_kb': 100})
        self.reader = FakeReader(make_table(*WORLD, *busy, idle))
        payload = self.adapter().read()
        self.assertEqual(len(payload['processes']), len(WORLD) + 101)
        self.assertEqual(payload['system']['process_count'], len(payload['processes']))
        self.assertEqual(payload['processes'][0]['pid'], 1099)
        self.assertIn(2001, {row['pid'] for row in payload['processes']})

    def test_process_list_is_capped_but_keeps_paused_rows_resumable(self):
        busy = [(1000 + n, 1, f'/usr/bin/busy{n}', {'cpu': 50.0 + n}) for n in range(10)]
        paused = (2000, 1, '/bin/sleep 900', {'state': 'T', 'cpu': 0.0, 'rss_kb': 100})
        idle = (2001, 1, '/bin/sleep 901', {'cpu': 0.0, 'rss_kb': 100})
        self.reader = FakeReader(make_table(*busy, paused, idle))
        with mock.patch('resources_adapter.PROCESS_LIST_CAP', 5):
            listed = {row['pid']: row for row in self.adapter().read()['processes']}
        self.assertEqual(len(listed), 6)
        self.assertTrue(listed[2000]['stopped'])
        self.assertNotIn(2001, listed)

    def test_a_registry_entry_for_a_reused_pid_is_ignored(self):
        self.runner.answers[(LSOF, '-a', '-p', '200,300', '-Fpfn')] = CommandResult(
            1, 'p200\nfcwd\nn/Users/me/dev/other\np300\nfcwd\nn/Users/me/dev/taxes\n')
        # Another start, or no well-formed procStart at all, is not this process's record.
        for proc_start in ('Mon Nov  2 01:00:00 2026', None, True, 17, '', 'garbage'):
            fields = {} if proc_start is None else {'procStart': proc_start}
            self.write_registry(200, name='Old session', **fields)
            self.clock.now += SNAPSHOT_TTL
            groups = {group['id']: group for group in self.adapter().read()['groups']}
            self.assertEqual((groups['claude:200']['name'], groups['claude:200']['detail'],
                              groups['claude:200']['name_source']),
                             ('claude · other', 'headless', 'fallback'), proc_start)

    def test_registry_start_matches_across_day_padding(self):
        padded = 'Mon Nov  2 01:00:00 2026'
        self.reader = FakeReader(make_table(*[(pid, ppid, command, {**(extra[0] if extra else {}), 'started': padded})
                                              for pid, ppid, command, *extra in WORLD]))
        self.write_registry(200, name='Padded', status='idle', procStart=padded)
        groups = {group['id']: group for group in self.adapter().read()['groups']}
        self.assertEqual(groups['claude:200']['name'], 'Padded')

    def test_malformed_registry_fields_are_dropped_not_fatal(self):
        self.write_registry(200, name=['x'], status=['busy'], kind={'a': 1}, cwd=7, procStart=STARTED)
        payload = self.adapter().read()
        claude = {group['id']: group for group in payload['groups']}['claude:200']
        self.assertEqual((claude['name'], claude['status'], claude['detail'], claude['cwd']),
                         ('claude', None, 'interactive', None))
        self.assertEqual(payload['totals']['claude']['count'], 2)

    def test_only_registry_files_of_current_claude_processes_are_opened(self):
        for pid in range(5000, 5020):
            self.write_registry(pid, name=f'stale {pid}', procStart=STARTED)
        opened = []
        real_open = os.open
        with mock.patch('llm_sessions.os.open', side_effect=lambda p, *a: opened.append(str(p)) or real_open(p, *a)), \
                mock.patch('llm_sessions.os.listdir', side_effect=AssertionError('no listing')):
            self.adapter().read()
        self.assertEqual([os.path.basename(path) for path in opened], ['200.json'])

    def test_a_parent_missing_from_the_table_does_not_fail_the_section(self):
        # 601's parent exited between reads: Mail's helper still shows, under itself.
        rows = dict(self.reader.rows)
        del rows[600]
        self.reader.rows = rows
        payload = self.adapter().read()
        helper = next(app for app in payload['apps'] if app['root_pid'] == 601)
        self.assertEqual(helper['name'], 'mail-helper')
        process = next(p for p in payload['processes'] if p['pid'] == 601)
        self.assertIsNotNone(process['blocked'])

    def test_codex_title_from_the_rollout_matching_its_start(self):
        start = started_epoch(STARTED) + 1
        day = self.home / '.codex/sessions' / time.strftime('%Y/%m/%d', time.localtime(start))
        day.mkdir(parents=True)
        uuid = '01a1e7e8-07f9-7e72-9440-6fb9a04728ea'
        stamp = lambda at: time.strftime('%Y-%m-%dT%H-%M-%S', time.localtime(at))
        (day / f'rollout-{stamp(start)}-{uuid}.jsonl').touch()
        # A minute later is outside the start window: another session's rollout.
        other = '00000000-0000-0000-0000-000000000000'
        (day / f'rollout-{stamp(start + 60)}-{other}.jsonl').touch()
        (self.home / '.codex/session_index.jsonl').write_text(
            json.dumps({'id': uuid, 'thread_name': 'Old title'}) + '\n'
            + json.dumps({'id': uuid, 'thread_name': 'Export data'}) + '\n' + 'not json\n')
        adapter = self.adapter()
        groups = {group['id']: group for group in adapter.read()['groups']}
        self.assertEqual((groups['codex:300']['name'], groups['codex:300']['name_source']),
                         ('Export data', 'rollout-time'))
        # A guess is never cached: a second rollout in the window makes the next one unproven.
        (day / f'rollout-{stamp(start + 2)}-{other}.jsonl').touch()
        self.clock.now += SNAPSHOT_TTL
        groups = {group['id']: group for group in adapter.read()['groups']}
        self.assertEqual((groups['codex:300']['name'], groups['codex:300']['name_source']),
                         ('codex · taxes', 'fallback'))

    def codex_world(self, uuids_at):
        start = started_epoch(STARTED)
        day = self.home / '.codex/sessions' / time.strftime('%Y/%m/%d', time.localtime(start))
        day.mkdir(parents=True, exist_ok=True)
        lines = []
        for uuid, offset in uuids_at.items():
            stamp = time.strftime('%Y-%m-%dT%H-%M-%S', time.localtime(start + offset))
            (day / f'rollout-{stamp}-{uuid}.jsonl').touch()
            lines.append(json.dumps({'id': uuid, 'thread_name': f'title {uuid[:4]}'}))
        (self.home / '.codex/session_index.jsonl').write_text('\n'.join(lines) + '\n')

    def test_two_codex_processes_starting_together_are_not_guessed(self):
        # One resumes an old thread, one starts a new one: the new rollout fits both.
        self.reader.rows.update(make_table((301, 101, 'codex resume')).rows)
        self.runner.answers[(LSOF, '-a', '-p', '300,301', '-Fpfn')] = CommandResult(0, '')
        self.codex_world({'11111111-0000-0000-0000-000000000000': 1})
        groups = {group['id']: group for group in self.adapter().read()['groups']}
        for gid in ('codex:300', 'codex:301'):
            self.assertEqual((groups[gid]['name'], groups[gid]['name_source']), ('codex', 'fallback'))

    def test_an_open_rollout_is_reserved_before_any_guess(self):
        proven, other = '22222222-0000-0000-0000-000000000000', '33333333-0000-0000-0000-000000000000'
        self.reader.rows.update(make_table((301, 1, 'codex', {'started': 'Tue Nov 17 23:19:53 2026'})).rows)
        self.codex_world({proven: 1, other: 3601})
        self.runner.answers[(LSOF, '-a', '-p', '300,301', '-Fpfn')] = CommandResult(
            0, f'p301\nf20w\nn/x/rollout-2026-11-17T00-00-00-{proven}.jsonl\n')
        groups = {group['id']: group for group in self.adapter().read()['groups']}
        self.assertEqual((groups['codex:301']['name'], groups['codex:301']['name_source']),
                         ('title 2222', 'rollout-open'))
        # 300's window holds only the rollout 301 has open, so it gets no name.
        self.assertEqual(groups['codex:300']['name_source'], 'fallback')

    def test_stalled_naming_returns_fallback_names_within_its_bound(self):
        release = threading.Event()
        self.addCleanup(release.set)
        adapter = self.adapter()
        adapter.sessions.describe = lambda *args: release.wait(10) and {}
        with mock.patch('resources_adapter.ENRICHMENT_BUDGET', 0.05), \
                mock.patch('resources_adapter.ENRICHMENT_GRACE', 0.05):
            began = time.monotonic()
            payload = adapter.read()
            self.assertLess(time.monotonic() - began, 1)
            groups = {group['id']: group for group in payload['groups']}
            self.assertEqual((groups['claude:200']['name'], groups['claude:200']['name_source']),
                             ('claude', 'fallback'))
            # The stalled call is not stacked on: the next snapshot does not wait at all.
            self.clock.now += SNAPSHOT_TTL
            began = time.monotonic()
            adapter.read()
            self.assertLess(time.monotonic() - began, 0.05)

    def test_rollout_folders_are_read_by_date_and_bounded(self):
        start = started_epoch(STARTED)
        stale = self.home / '.codex/sessions/2020/01/01'
        stale.mkdir(parents=True)
        (stale / 'rollout-2020-01-01T00-00-00-44444444-0000-0000-0000-000000000000.jsonl').touch()
        self.codex_world({f'{n:08d}-0000-0000-0000-000000000000': 3600 + n for n in range(30)})
        scanned = []
        real_scandir = os.scandir
        with mock.patch('llm_sessions.os.scandir', side_effect=lambda p: scanned.append(str(p)) or real_scandir(p)), \
                mock.patch('llm_sessions.ROLLOUT_SCAN', 10):
            self.adapter().read()
        days = {time.strftime('%Y/%m/%d', time.localtime(start + offset)) for offset in (0, 5)}
        self.assertEqual({path.split('.codex/sessions/')[1] for path in scanned}, days)

    def test_naming_that_times_out_falls_back_without_failing(self):
        self.runner.answers[(LSOF, '-a', '-p', '300', '-Fpfn')] = ApiError(504, 'timed out')
        payload = self.adapter().read()
        groups = {group['id']: group for group in payload['groups']}
        self.assertEqual(groups['codex:300']['name'], 'codex')
        self.assertEqual(payload['totals']['claude']['count'], 2)

    def test_failed_process_table_is_502(self):
        self.reader.fail_table = True
        with self.assertRaises(ApiError) as caught:
            self.adapter().read()
        self.assertEqual(caught.exception.status, 502)


def shape(value):
    """Keys all the way down; a list is described by its first element."""
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [shape(value[0])] if value and isinstance(value[0], (dict, list)) else []
    return None


class FixtureContractTests(AdapterFixture):
    def test_saved_live_payload_matches_the_wire_shape(self):
        # tests/resources_payload.json is a real read with private names replaced; the page's
        # tests may load it, so it must keep the exact shape read() returns.
        saved = json.loads((Path(__file__).parent / 'resources_payload.json').read_text())
        payload = self.adapter().read()
        self.assertEqual(shape(saved), {**shape(payload), 'totals': shape(saved['totals'])})
        self.assertEqual(set(saved['totals']), set(payload['totals']))
        self.assertEqual(shape(saved['totals']['claude']), shape(payload['totals']['claude']))


class SnapshotTests(AdapterFixture):
    def test_concurrent_reads_share_one_collection(self):
        release, entered = threading.Event(), threading.Event()
        table = self.reader.table

        def slow_table(timeout=5):
            entered.set()
            release.wait(5)
            return table(timeout)
        self.reader.table = slow_table
        adapter = self.adapter()
        results = []
        threads = [threading.Thread(target=lambda: results.append(adapter.read())) for _ in range(2)]
        threads[0].start()
        entered.wait(5)
        threads[1].start()
        time.sleep(0.05)
        release.set()
        for thread in threads:
            thread.join(5)
        self.assertEqual(len(results), 2)
        self.assertIs(results[0], results[1])
        self.assertEqual((self.reader.table_calls, self.metrics.calls), (1, 1))

    def test_snapshot_is_reused_for_five_seconds(self):
        adapter = self.adapter()
        first = adapter.read()
        self.clock.now += SNAPSHOT_TTL - 0.1
        self.assertIs(adapter.read(), first)
        self.clock.now += 0.1
        self.assertIsNot(adapter.read(), first)
        self.assertEqual(self.reader.table_calls, 2)

    def test_a_mutation_takes_a_fresh_table_and_invalidates_the_snapshot(self):
        adapter = self.adapter()
        first = adapter.read()
        with self.assertRaises(ApiError):
            adapter.mutate({'action': 'terminate', 'pid': 101, 'started': STARTED,
                            'scope': 'process', 'confirmed': True})
        self.assertEqual(self.reader.table_calls, 2)
        self.assertIsNot(adapter.read(), first)


class SamplerTests(AdapterFixture):
    def test_resources_series(self):
        self.assertEqual(CADENCES[Section.RESOURCES], 60)
        rows = {row['key']: row for row in samples(Section.RESOURCES, self.adapter().read(), 7)}
        expected = {'system.cpu', 'system.memory_used', 'system.swap_used', 'level',
                    *(f'{kind}.{series}' for kind in ('claude', 'codex', 'infra', 'compute')
                      for series in ('cpu', 'rss'))}
        self.assertEqual(set(rows), expected)
        self.assertEqual(rows['system.cpu']['value'], 8.1)
        self.assertEqual(rows['claude.rss']['value'], 5120 * 1024)
        # Unavailable values are recorded as unknown, never as zero.
        self.assertEqual((rows['system.memory_used']['value'], rows['system.memory_used']['level']),
                         (None, Level.UNKNOWN))
        self.assertEqual(rows['level']['level'], Level.OK)

    def test_unknown_section_still_raises(self):
        with self.assertRaises(ValueError):
            samples(Section.NTFY, {}, 1)


class RouteTests(unittest.TestCase):
    def test_post_goes_through_the_request_guard(self):
        runner = FakeRunner()
        routes = PanelRoutes(runner=runner)
        self.addCleanup(routes.close)
        self.assertIs(routes.adapters[Section.RESOURCES], routes.resources)
        body = json.dumps({'action': 'terminate', 'pid': 123, 'started': STARTED, 'scope': 'process'})
        with running_server(routes) as server:
            headers = auth(server, True)
            code, _, _ = request(server, '/api/resources', 'POST', body, {**headers, 'X-Panel-Token': None})
            self.assertEqual(code, 403)
            code, _, _ = request(server, '/api/resources', 'POST', body, {**headers, 'Origin': 'http://evil'})
            self.assertEqual(code, 403)
            code, _, raw = request(server, '/api/resources', 'POST', body, headers)
            self.assertEqual(code, 400)
            self.assertIn('confirmation', raw)
            code, _, _ = request(server, '/api/resources', headers={'Host': headers['Host']})
            self.assertEqual(code, 403)
        self.assertEqual(runner.calls, [])


if __name__ == '__main__':
    unittest.main()
