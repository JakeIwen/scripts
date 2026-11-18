"""POST /api/resources behaviour: guards, ordering, per-target re-checks and result counts.

Signals and priority calls go to recording fakes; the one real process signalled is a
`/bin/sleep` this file starts itself.
"""
from dataclasses import replace
import signal
import subprocess
import unittest

from cli_runner import CliRunner
from panel_contracts import ApiError
from process_signals import NICE_FLOOR, ProcessSignaller
from process_table import ProcessReader
from resources_adapter import ResourcesAdapter
from resources_fixtures import LATER, PANEL_PID, STARTED, FakeReader, make_table
from signal_policy import SHELL, SignalPolicy

WORLD = (
    (1, 0, '/sbin/launchd', {'uid': 0}),
    (100, 1, '/Applications/iTerm.app/Contents/MacOS/iTerm2'),
    (101, 100, '-zsh'),
    (200, 101, 'claude --permission-mode bypassPermissions'),
    (201, 200, '/bin/zsh -c source snapshot.sh'),
    (202, 200, 'node /x/tool.js'),
    (203, 202, 'sleep 600'),
    (300, 101, 'vim notes'),
    (400, 1, 'node /x/model-gateway.js serve'),
    (500, 1, 'python3 -m van_compute.worker'),
    (501, 500, '/bin/sh /Users/me/Library/Caches/van-compute/jobs/j1/run.sh'),
)


class Recorder:
    def __init__(self, reader=None, nice=None):
        self.calls, self.reader, self.nice = [], reader, nice or {}
        self.failures = {}

    def kill(self, pid, sig):
        if pid in self.failures:
            raise self.failures[pid]
        self.calls.append((pid, sig))

    def getpriority(self, _which, pid):
        return self.nice.get(pid, 0)

    def setpriority(self, _which, pid, value):
        self.calls.append((pid, 'nice', value))
        self.nice[pid] = value


def body(pid, action='terminate', scope='process', started=None, **extra):
    return {'action': action, 'pid': pid, 'started': STARTED if started is None else started, 'scope': scope,
            'confirmed': True, **extra}


class SignalTests(unittest.TestCase):
    def setUp(self):
        self.reader = FakeReader(make_table(*WORLD))
        self.recorder = Recorder(self.reader)
        self.signaller = ProcessSignaller(self.reader, SignalPolicy(uid=501, panel_pid=PANEL_PID),
                                          self.recorder.kill, self.recorder.getpriority,
                                          self.recorder.setpriority)

    def assertStatus(self, status, request):
        with self.assertRaises(ApiError) as caught:
            self.signaller.mutate(request)
        self.assertEqual(caught.exception.status, status, caught.exception)
        self.assertEqual(self.recorder.calls, [])

    def test_every_action_needs_literal_confirmation(self):
        for action in ('terminate', 'kill', 'pause', 'resume', 'lower-priority'):
            request = body(300, action)
            del request['confirmed']
            self.assertStatus(400, request)
            self.assertStatus(400, {**body(300, action), 'confirmed': 'true'})
            self.assertStatus(400, {**body(300, action), 'confirmed': 1})
        self.assertEqual(self.reader.table_calls, 0)

    def test_malformed_requests_are_400(self):
        for request in (body(300, 'explode'), body(300, scope='everything'), body(True),
                        body(0), body(300, started=''), {**body(300), 'extra': 1}, []):
            self.assertStatus(400, request)

    def test_a_changed_or_gone_process_is_409(self):
        self.assertStatus(409, body(300, started=LATER))
        self.assertStatus(409, body(999))

    def test_blocked_roots_are_403(self):
        for request in (body(101), body(100), body(400), body(501), body(201), body(1),
                        body(202, scope='tree'), body(300, scope='tree'), body(400, scope='tree')):
            self.assertStatus(403, request)

    def test_nested_session_blocks_the_outer_tree(self):
        self.reader.rows.update(make_table((210, 201, 'claude -p inner'), (211, 210, 'sleep 5')).rows)
        self.assertStatus(403, body(200, scope='tree'))
        self.assertEqual(self.signaller.mutate(body(210, scope='tree'))['applied'], 2)

    def test_terminate_goes_deepest_first_and_survives_reparenting(self):
        def kill(pid, sig):
            self.recorder.calls.append((pid, sig))
            # The kernel reparents survivors to launchd the moment their parent exits.
            self.reader.reparent_children(pid)
            del self.reader.rows[pid]
        self.signaller.kill = kill
        result = self.signaller.mutate(body(200, scope='tree'))
        self.assertEqual(result, {'applied': 4, 'gone': 0, 'changed': 0, 'skipped': 0,
                                  'refused': 0, 'unchanged': 0})
        self.assertEqual([pid for pid, _ in self.recorder.calls], [203, 201, 202, 200])
        self.assertTrue(all(sig == signal.SIGTERM for _, sig in self.recorder.calls))

    def test_root_first_order_would_leave_children_running(self):
        # The same fixture under pause (root first) shows why terminate must not use it.
        def kill(pid, sig):
            self.recorder.calls.append((pid, sig))
            self.reader.reparent_children(pid)
        self.signaller.kill = kill
        result = self.signaller.mutate(body(200, 'pause', 'tree'))
        self.assertEqual([pid for pid, _ in self.recorder.calls], [200, 203])
        self.assertEqual(result['changed'], 2)

    def test_pause_goes_root_first_and_resume_deepest_first(self):
        self.signaller.mutate(body(200, 'pause', 'tree'))
        self.assertEqual(self.recorder.calls, [(200, signal.SIGSTOP), (201, signal.SIGSTOP),
                                               (202, signal.SIGSTOP), (203, signal.SIGSTOP)])
        self.recorder.calls.clear()
        self.signaller.mutate(body(200, 'resume', 'tree'))
        self.assertEqual([pid for pid, _ in self.recorder.calls], [203, 201, 202, 200])
        self.recorder.calls.clear()
        self.signaller.mutate(body(300, 'kill'))
        self.assertEqual(self.recorder.calls, [(300, signal.SIGKILL)])

    def test_each_target_is_rechecked_just_before_its_signal(self):
        def change_world(reader):
            rows = reader.rows
            rows[203] = replace(rows[203], started=LATER)
            rows[202] = replace(rows[202], uid=502)
            rows[201] = replace(rows[201], argv_text='/usr/bin/tmux', executable='/usr/bin/tmux')
        self.reader.after_table = change_world
        result = self.signaller.mutate(body(200, scope='tree'))
        self.assertEqual(result['changed'], 3)
        self.assertEqual(result['applied'], 1)
        self.assertEqual(self.recorder.calls, [(200, signal.SIGTERM)])
        self.assertEqual(self.reader.row_calls, [203, 201, 202, 200])

    def test_a_member_reparented_out_of_the_tree_is_not_signalled(self):
        self.reader.after_table = lambda reader: reader.reparent_children(202)
        result = self.signaller.mutate(body(200, scope='tree'))
        self.assertEqual(result['changed'], 1)
        self.assertNotIn(203, [pid for pid, _ in self.recorder.calls])

    def test_blocked_members_are_skipped_and_counted(self):
        self.reader.rows.update(make_table((205, 200, '/usr/bin/tmux new')).rows)
        result = self.signaller.mutate(body(200, scope='tree'))
        self.assertEqual(result['skipped'], 1)
        self.assertNotIn(205, [pid for pid, _ in self.recorder.calls])
        self.assertIn((201, signal.SIGTERM), self.recorder.calls)

    def test_shell_member_needs_its_session_tree(self):
        with self.assertRaises(ApiError) as caught:
            self.signaller.mutate(body(201))
        self.assertEqual((caught.exception.status, str(caught.exception)), (403, SHELL))

    def test_lower_priority_never_raises_priority(self):
        self.recorder.nice.update({200: 0, 201: 5, 202: 15, 203: NICE_FLOOR})
        result = self.signaller.mutate(body(200, 'lower-priority', 'tree'))
        self.assertEqual(result['applied'], 2)
        self.assertEqual(result['unchanged'], 2)
        self.assertEqual(self.recorder.calls, [(200, 'nice', 10), (201, 'nice', 10)])

    def test_gone_and_refused_are_counted_without_raising(self):
        self.recorder.failures = {203: ProcessLookupError(), 202: PermissionError()}
        result = self.signaller.mutate(body(200, scope='tree'))
        self.assertEqual(result, {'applied': 2, 'gone': 1, 'changed': 0, 'skipped': 0,
                                  'refused': 1, 'unchanged': 0})
        del self.reader.rows[203]
        self.recorder.failures = {}
        self.assertEqual(self.signaller.mutate(body(202))['applied'], 1)
        self.reader.after_table = lambda reader: reader.rows.pop(300)
        self.assertEqual(self.signaller.mutate(body(300))['gone'], 1)


class RealProcessTest(unittest.TestCase):
    def state(self, pid):
        result = CliRunner().run(('/bin/ps', '-o', 'state=,nice=', '-p', str(pid)))
        return result.stdout.split()

    def test_own_sleep_is_paused_resumed_deprioritised_and_terminated(self):
        child = subprocess.Popen(['/bin/sleep', '600'])
        self.addCleanup(lambda: child.poll() is None and child.kill())
        self.addCleanup(child.wait)
        reader = ProcessReader(CliRunner())
        row = reader.row(child.pid)
        # The test runner stands in for no panel: a pid that cannot exist, so the sleep is
        # not the panel's descendant.
        adapter = ResourcesAdapter(reader=reader, policy=SignalPolicy(panel_pid=10 ** 7))
        request = {'pid': child.pid, 'started': row.started, 'scope': 'process', 'confirmed': True}
        self.assertEqual(adapter.mutate({**request, 'action': 'pause'})['applied'], 1)
        self.assertTrue(self.state(child.pid)[0].startswith('T'))
        self.assertEqual(adapter.mutate({**request, 'action': 'resume'})['applied'], 1)
        self.assertFalse(self.state(child.pid)[0].startswith('T'))
        self.assertEqual(adapter.mutate({**request, 'action': 'lower-priority'})['applied'], 1)
        self.assertEqual(self.state(child.pid)[1], '10')
        self.assertEqual(adapter.mutate({**request, 'action': 'terminate'})['applied'], 1)
        self.assertEqual(child.wait(5), -signal.SIGTERM)


if __name__ == '__main__':
    unittest.main()
