"""POST /api/resources behaviour: guards, ordering, per-target re-checks and result counts.

Signals and priority calls go to recording fakes; the one real process signalled is a
`/bin/sleep` this file starts itself.
"""
from dataclasses import replace
import signal
import subprocess
import unittest
from unittest import mock

from cli_runner import CliRunner
from panel_contracts import ApiError
from process_signals import NICE_FLOOR, TREE_TOO_LARGE, ProcessSignaller
from process_table import ProcessReader
from resources_adapter import ResourcesAdapter
from resources_fixtures import LATER, PANEL_PID, PANEL_ROW, STARTED, FakeReader, make_table
from signal_policy import SHELL, SignalPolicy
from signal_recheck import (MUTATION_BUDGET, STOPPED_NESTED, STOPPED_ROOT, STOPPED_TIME,
                            STOPPED_UNREADABLE)

COUNTS = ('applied', 'gone', 'changed', 'skipped', 'refused', 'unchanged')


def counts(stopped=None, **values):
    return {**{key: values.get(key, 0) for key in COUNTS}, 'stopped': stopped}


def row_with(row, command, **overrides):
    """The same process (pid and start) after an exec into another program."""
    return replace(make_table((row.pid, row.ppid, command)).rows[row.pid], **overrides)

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
    PANEL_ROW,
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

    def test_without_the_panels_own_row_nothing_is_signalled(self):
        # The panel's row missed the three-pass join: its parent (here 300's sibling launcher)
        # could be anything, so every process is refused, not only the panel's lineage.
        del self.reader.rows[PANEL_PID]
        self.reader.rows.update(make_table((8001, 1, 'launcher')).rows)
        for request in (body(300), body(8001), body(200, scope='tree'), body(203, 'pause')):
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
        self.assertEqual(result, counts(applied=4))
        self.assertEqual([pid for pid, _ in self.recorder.calls], [203, 201, 202, 200])
        self.assertTrue(all(sig == signal.SIGTERM for _, sig in self.recorder.calls))
        # One full re-read per target, after the request's own read.
        self.assertEqual(self.reader.table_calls, 5)

    def test_root_first_order_would_leave_children_running(self):
        # The same reparenting fixture under pause (root first) shows why terminate must not
        # use it: once the root's children are reparented, none of the subtree is the
        # session's any more, the grandchild included, so only the root is signalled.
        def kill(pid, sig):
            self.recorder.calls.append((pid, sig))
            self.reader.reparent_children(pid)
        self.signaller.kill = kill
        result = self.signaller.mutate(body(200, 'pause', 'tree'))
        self.assertEqual([pid for pid, _ in self.recorder.calls], [200])
        self.assertEqual(result, counts(applied=1, changed=3))

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
            rows[201] = row_with(rows[201], '/usr/bin/tmux')
        self.reader.after_table = change_world
        result = self.signaller.mutate(body(200, scope='tree'))
        self.assertEqual(result, counts(applied=1, changed=3))
        self.assertEqual(self.recorder.calls, [(200, signal.SIGTERM)])

    def test_a_member_reparented_out_of_the_tree_is_not_signalled(self):
        self.reader.after_table = lambda reader: reader.reparent_children(202)
        result = self.signaller.mutate(body(200, scope='tree'))
        self.assertEqual(result['changed'], 1)
        self.assertNotIn(203, [pid for pid, _ in self.recorder.calls])

    def test_a_grandchild_whose_parent_left_the_session_is_not_signalled(self):
        # 202 is reparented out of the session; its child 203 still has 202 as its parent,
        # which was in the planned set, but its ancestry no longer reaches the root.
        self.reader.after_table = lambda reader: reader.rows.update(
            {202: replace(reader.rows[202], ppid=1)})
        result = self.signaller.mutate(body(200, scope='tree'))
        self.assertEqual(result, counts(applied=2, changed=2))
        self.assertEqual(sorted(pid for pid, _ in self.recorder.calls), [200, 201])

    def test_a_gap_in_a_members_ancestry_refuses_it(self):
        self.reader.after_table = lambda reader: reader.rows.pop(202)
        result = self.signaller.mutate(body(200, scope='tree'))
        self.assertEqual(result, counts(applied=2, gone=1, changed=1))
        self.assertNotIn(203, [pid for pid, _ in self.recorder.calls])

    def test_a_member_that_execs_into_a_session_stops_the_whole_action(self):
        self.reader.after_table = lambda reader: reader.rows.update(
            {202: row_with(reader.rows[202], 'claude -p nested')})
        result = self.signaller.mutate(body(200, scope='tree'))
        self.assertEqual(result, counts(changed=4, stopped=STOPPED_NESTED))
        self.assertEqual(self.recorder.calls, [])

    def test_a_session_appearing_midway_stops_the_rest(self):
        def kill(pid, sig):
            self.recorder.calls.append((pid, sig))
            if pid == 203:
                self.reader.rows.update(make_table((209, 201, 'codex --no-alt-screen')).rows)
        self.signaller.kill = kill
        result = self.signaller.mutate(body(200, scope='tree'))
        self.assertEqual(self.recorder.calls, [(203, signal.SIGTERM)])
        self.assertEqual(result, counts(applied=1, changed=3, stopped=STOPPED_NESTED))

    def test_a_root_that_execs_into_an_app_server_stops_the_whole_action(self):
        self.reader.after_table = lambda reader: reader.rows.update(
            {200: row_with(reader.rows[200], '/x/codex app-server --listen x')})
        result = self.signaller.mutate(body(200, scope='tree'))
        self.assertEqual(result, counts(changed=4, stopped=STOPPED_ROOT))
        self.assertEqual(self.recorder.calls, [])

    def test_a_process_that_execs_into_something_protected_is_refused(self):
        self.reader.after_table = lambda reader: reader.rows.update(
            {300: row_with(reader.rows[300], 'innocent', kernel_name='tmux')})
        self.assertEqual(self.signaller.mutate(body(300)), counts(changed=1))
        self.assertEqual(self.recorder.calls, [])

    def test_a_process_that_starts_hosting_a_terminal_is_refused(self):
        self.reader.after_table = lambda reader: reader.rows.update(
            make_table((310, 300, '-zsh', {'tty': 'ttys020'})).rows)
        self.assertEqual(self.signaller.mutate(body(300)), counts(changed=1))

    def test_an_unreadable_re_read_or_the_time_bound_stops_the_action(self):
        def fail(reader):
            reader.fail_table = True
        self.reader.after_table = fail
        result = self.signaller.mutate(body(200, scope='tree'))
        self.assertEqual(result, counts(changed=4, stopped=STOPPED_UNREADABLE))
        self.reader.fail_table = False
        now = [0.0]
        self.signaller.clock = lambda: now[0]

        def kill(pid, sig):
            self.recorder.calls.append((pid, sig))
            now[0] += MUTATION_BUDGET
        self.signaller.kill = kill
        result = self.signaller.mutate(body(200, scope='tree'))
        self.assertEqual(result, counts(applied=1, skipped=3, stopped=STOPPED_TIME))

    def test_a_tree_above_the_size_cap_is_refused(self):
        with mock.patch('process_signals.TREE_TARGET_LIMIT', 3):
            self.assertStatus(403, body(200, scope='tree'))
        self.assertIn('processes in this session', TREE_TOO_LARGE)

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
        self.assertEqual(result, counts(applied=2, gone=1, refused=1))
        del self.reader.rows[203]
        self.recorder.failures = {}
        self.assertEqual(self.signaller.mutate(body(202))['applied'], 1)
        self.reader.after_table = lambda reader: reader.rows.pop(300)
        self.assertEqual(self.signaller.mutate(body(300))['gone'], 1)


class RealProcessTest(unittest.TestCase):
    def state(self, pid):
        result = CliRunner().run(('/bin/ps', '-o', 'state=,nice=', '-p', str(pid)))
        return result.stdout.split()

    def own_child(self):
        child = subprocess.Popen(['/bin/sleep', '600'])

        def cleanup():
            # One cleanup, kill before wait: a stopped child never exits on its own.
            if child.poll() is None:
                child.kill()
            child.wait(5)
        self.addCleanup(cleanup)
        return child

    def test_cleanup_reaps_a_stopped_child(self):
        child = self.own_child()
        child.send_signal(signal.SIGSTOP)
        # What happens when an assertion fails while the child is paused.
        self.doCleanups()
        self.assertEqual(child.returncode, -signal.SIGKILL)

    def test_own_sleep_is_paused_resumed_deprioritised_and_terminated(self):
        child = self.own_child()
        # The panel's seat is taken by a second sleep of ours, a sibling: the test runner is
        # the real parent of both, and the target is neither the stand-in's ancestor nor its
        # descendant, as with an ordinary process beside the real panel.
        panel = self.own_child()
        reader = ProcessReader(CliRunner())
        row = reader.table().rows[child.pid]
        adapter = ResourcesAdapter(reader=reader, policy=SignalPolicy(panel_pid=panel.pid))
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
