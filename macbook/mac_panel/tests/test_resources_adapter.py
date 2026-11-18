"""Resources reads: probes, memory estimate, health, shared snapshot, session names, history."""
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
from llm_sessions import LSOF
from panel_contracts import ApiError, Level, Section
from panel_routes import PanelRoutes
from panel_test_support import FakeRunner, auth, request, running_server
from process_table import started_epoch
from resources_adapter import SNAPSHOT_TTL, ResourcesAdapter, health
from resources_fixtures import (PANEL_PID, STARTED, ArgvRunner, FakeMetrics, FakeReader,
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
    (101, 1, '-zsh'),
    (200, 101, 'claude -p secret prompt about payroll --password hunter2', {'cpu': 50.0, 'rss_kb': 4096}),
    (201, 200, 'node /x/tool.js --api-key sk-live-123', {'cpu': 25.0}),
    (300, 101, 'codex --no-alt-screen fix the tax bug'),
    (400, 1, 'node /x/model-gateway.js serve', {'cpu': 1.0}),
    (500, 1, 'python3 -m van_compute.worker', {'rss_kb': 2048}),
    (600, 1, 'Mail', {'cpu': 3.0}),
    (601, 600, 'mail-helper', {'cpu': 2.0}),
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
        self.assertEqual(self.level({Probe.DISK: {'total': 100, 'free': 1}}, ['pressure'])['level'], 'warn')


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
        for secret in ('payroll', 'hunter2', 'sk-live', 'tax bug', 'from the key', '--'):
            self.assertNotIn(secret, text)
        groups = {group['id']: group for group in payload['groups']}
        claude = groups['claude:200']
        self.assertEqual((claude['name'], claude['status'], claude['cwd'], claude['count']),
                         ('Budget review', 'busy', '/Users/me/dev/pay', 2))
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

    def test_a_registry_entry_for_a_reused_pid_is_ignored(self):
        self.write_registry(200, name='Old session', procStart='Mon Nov  2 01:00:00 2026')
        self.runner.answers[(LSOF, '-a', '-p', '200,300', '-Fpfn')] = CommandResult(
            1, 'p200\nfcwd\nn/Users/me/dev/other\np300\nfcwd\nn/Users/me/dev/taxes\n')
        groups = {group['id']: group for group in self.adapter().read()['groups']}
        self.assertEqual((groups['claude:200']['name'], groups['claude:200']['detail']),
                         ('claude · other', 'headless'))

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
        self.assertEqual(groups['codex:300']['name'], 'Export data')
        # Cached per (pid, started): the next snapshot needs no lsof.
        calls = len(self.runner.calls)
        self.clock.now += SNAPSHOT_TTL
        adapter.read()
        self.assertEqual(len(self.runner.calls), calls)

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
