"""Grouping by position and the signal policy's block reasons."""
import unittest

from functools import partial

from resource_groups import Kind
from resource_groups import build_grouping as build_any_grouping
from resources_fixtures import PANEL_PID, make_table
from signal_policy import (INCOMPLETE, KIND_REASONS, OTHER_USER, PANEL, PANEL_ANCESTOR,
                           PANEL_DESCENDANT, PANEL_UNKNOWN, PROTECTED_REASON, SHELL, SYSTEM_PID,
                           TREE_ONLY_SESSIONS, Scope, SignalPolicy, hosts_reason, tree_blocked)

JOBS_ROOT = '/Users/me/Library/Caches/van-compute/jobs'
build_grouping = partial(build_any_grouping, jobs_root=JOBS_ROOT)

WORLD = (
    (1, 0, '/sbin/launchd', {'uid': 0}),
    (100, 1, '/Applications/iTerm.app/Contents/MacOS/iTerm2'),
    (101, 100, '-zsh'),
    (200, 101, 'claude --permission-mode bypassPermissions'),
    (201, 200, '/bin/zsh -c source /Users/me/.claude/shell-snapshots/snap.sh'),
    (202, 200, 'npm exec kapture-mcp@latest bridge'),
    (203, 202, 'node /Users/me/.npm/_npx/1/node_modules/.bin/kapture-mcp bridge'),
    (210, 201, 'claude -p inner prompt text'),
    (211, 210, 'caffeinate -i -t 300'),
    (300, 101, 'codex --no-alt-screen --ask-for-approval never'),
    (301, 1, '/Users/me/.codex/bin/codex -c a=1 app-server --remote-control'),
    (302, 301, 'node /Users/me/kapture-mcp/dist/cli.js bridge'),
    (400, 1, '/Users/me/.nvm/bin/node /Users/me/.claude/plugins/model-gateway.js serve-shim'),
    (401, 400, '/Users/me/.claude/model-gateway/bin/claude-code-proxy serve'),
    (410, 1, 'node /Users/me/.local/node_modules/@playwright/mcp/cli.js --config x'),
    (500, 1, '/opt/Python.app/Contents/MacOS/Python -P -m van_compute.worker --serve'),
    (501, 500, '/usr/bin/ssh -N -M -S /c/control.sock.1 pi@vanpi.lan'),
    (502, 500, '/bin/sh /Users/me/Library/Caches/van-compute/jobs/job-42/run.sh'),
    (503, 502, 'python3 work.py'),
    (600, 101, 'grep -r van_compute.worker model-gateway.js claude-code-proxy'),
    (601, 101, 'python3 /tmp/other.py -m van_compute.worker'),
    (602, 101, 'node /tmp/not-cli.js @playwright/mcp/cli.js'),
    (603, 101, 'vim notes'),
)


def world():
    return make_table(*WORLD)


class GroupingTests(unittest.TestCase):
    def setUp(self):
        self.table = world()
        self.grouping = build_grouping(self.table)

    def members(self, gid):
        return [(m.row.pid, m.depth) for m in self.grouping.groups[gid].members]

    def test_session_descendants_and_a_nested_session(self):
        self.assertEqual(self.members('claude:200'), [(200, 0), (201, 1), (202, 1), (203, 2)])
        self.assertEqual(self.members('claude:210'), [(210, 0), (211, 1)])
        inner = self.grouping.groups['claude:210']
        self.assertEqual(inner.parent, 'claude:200')
        self.assertEqual(self.grouping.groups['claude:200'].nested, ['claude:210'])
        self.assertEqual(self.members('codex:300'), [(300, 0)])

    def test_infra_and_compute_match_by_position(self):
        self.assertEqual(self.grouping.kind(301), Kind.INFRA)
        self.assertEqual(self.members('infra:301'), [(301, 0), (302, 1)])
        self.assertEqual(self.members('infra:400'), [(400, 0), (401, 1)])
        self.assertEqual(self.grouping.groups['infra:400'].label, 'model gateway')
        self.assertEqual(self.grouping.groups['infra:410'].label, 'Playwright MCP')
        for pid in (600, 601, 602, 603, 100, 101):
            self.assertEqual(self.grouping.kind(pid), Kind.APP, pid)

    def test_compute_worker_row_and_job_rows(self):
        self.assertEqual(self.members('compute:500'), [(500, 0), (501, 1)])
        self.assertEqual(self.grouping.groups['compute:500'].label, 'worker')
        self.assertEqual(self.members('compute:502'), [(502, 0), (503, 1)])
        self.assertEqual(self.grouping.groups['compute:502'].label, 'job-42')
        self.assertEqual(self.grouping.groups['compute:502'].kind, Kind.COMPUTE)

    def test_a_job_without_a_job_path_is_named_by_its_program(self):
        table = make_table((500, 1, 'python3 -m van_compute.worker'), (502, 500, '/usr/bin/sandbox-exec -p x tool'))
        self.assertEqual(build_grouping(table).groups['compute:502'].label, 'job · sandbox-exec')

    def test_a_job_name_comes_only_from_an_argument_under_the_jobs_root(self):
        table = make_table(
            (500, 1, 'python3 -m van_compute.worker'),
            (502, 500, 'python3 -c print("Caches/van-compute/jobs/secret-name/x")'),
            (503, 500, '/bin/sh --cwd=/Users/me/Library/Caches/van-compute/jobs/also-secret/x'),
            (504, 500, f'/bin/sh {JOBS_ROOT}/job-7/run.sh'))
        labels = {gid: group.label for gid, group in build_grouping(table).groups.items()}
        self.assertEqual(labels['compute:502'], 'job · python3')
        self.assertEqual(labels['compute:503'], 'job · sh')
        self.assertEqual(labels['compute:504'], 'job-7')

    def test_a_zombie_roots_no_group(self):
        table = make_table((1, 0, '/sbin/launchd', {'uid': 0}),
                           (47, 1, '/x/codex app-server daemon'),
                           (48, 47, '<defunct>', {'state': 'Z', 'ucomm': 'codex'}),
                           (49, 1, '<defunct>', {'state': 'Z', 'ucomm': 'claude'}))
        grouping = build_grouping(table)
        self.assertEqual(set(grouping.groups), {'infra:47'})
        self.assertEqual(grouping.membership[48], 'infra:47')
        self.assertEqual((grouping.kind(49), table.rows[49].name), (Kind.APP, 'claude'))

    def test_a_codex_option_value_with_a_space_cannot_turn_an_app_server_into_a_session(self):
        # ps shows `-c 'model=a b'` as three words, so "b" sits where the subcommand would be.
        table = make_table((1, 0, '/sbin/launchd', {'uid': 0}),
                           (301, 1, '/Users/me/.codex/bin/codex -c model=a b app-server --listen x'))
        grouping = build_grouping(table)
        self.assertEqual(grouping.kind(301), Kind.INFRA)
        self.assertEqual(grouping.groups['infra:301'].label, 'Codex app server')

    def test_a_session_inside_a_compute_job_or_infra_is_its_own_group(self):
        table = make_table((400, 1, 'node /x/model-gateway.js'), (405, 400, 'claude -p x'), (406, 405, 'sleep 1'))
        grouping = build_grouping(table)
        self.assertEqual(grouping.membership[406], 'claude:405')
        self.assertEqual(grouping.membership[400], 'infra:400')


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.table = make_table(
            *WORLD,
            (700, 1, '/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal'),
            (701, 1, '/System/Library/CoreServices/Dock.app/Contents/MacOS/Dock'),
            (702, 1, '/usr/bin/login -pf me'),
            (703, 1, 'other-user-tool', {'uid': 502}),
            (8000, 1, '/bin/zsh -l'),
            (8001, 8000, 'launcher'),
            (PANEL_PID, 8001, '/venv/bin/python server.py'),
            (9001, PANEL_PID, '/bin/ps -axww'))
        self.grouping = build_grouping(self.table)
        self.policy = SignalPolicy(uid=501, panel_pid=PANEL_PID)
        self.context = self.policy.context(self.table, self.grouping)

    def reason(self, pid, scope=Scope.PROCESS, table=None):
        table = table or self.table
        grouping = build_grouping(table)
        return self.policy.blocked(table.rows[pid], grouping.kind(pid), scope,
                                   self.policy.context(table, grouping))

    def test_each_block_reason(self):
        self.assertEqual(self.reason(703), OTHER_USER)
        self.assertEqual(self.reason(1), OTHER_USER)
        self.assertEqual(self.reason(PANEL_PID), PANEL)
        self.assertEqual(self.reason(8001), PANEL_ANCESTOR)
        self.assertEqual(self.reason(9001), PANEL_DESCENDANT)
        for pid in (100, 700, 701, 702):
            self.assertEqual(self.reason(pid), PROTECTED_REASON, pid)
        self.assertEqual(self.reason(201), SHELL)
        self.assertEqual(self.reason(301), KIND_REASONS[Kind.INFRA])
        self.assertEqual(self.reason(401), KIND_REASONS[Kind.INFRA])
        self.assertEqual(self.reason(502), KIND_REASONS[Kind.COMPUTE])
        self.assertIsNone(self.reason(603))
        self.assertIsNone(self.reason(203))

    def test_pid_one_is_blocked_even_for_its_owner(self):
        policy = SignalPolicy(uid=0, panel_pid=PANEL_PID)
        self.assertEqual(policy.blocked(self.table.rows[1], Kind.APP, Scope.PROCESS, self.context), SYSTEM_PID)

    def test_shells_are_allowed_as_session_tree_members(self):
        self.assertIsNone(self.reason(201, Scope.TREE))
        self.assertEqual(self.reason(603, Scope.TREE), TREE_ONLY_SESSIONS)
        self.assertEqual(self.reason(101, Scope.PROCESS), SHELL)

    def test_tree_blocked_for_nested_sessions_and_panel_ancestors(self):
        outer, inner = self.grouping.groups['claude:200'], self.grouping.groups['claude:210']
        reason = tree_blocked(self.policy, outer, self.context, {'claude:210': 'inner work'})
        self.assertIn('inner work', reason)
        self.assertIn('end those first', reason)
        self.assertIsNone(tree_blocked(self.policy, inner, self.context))
        self.assertEqual(tree_blocked(self.policy, self.grouping.groups['infra:400'], self.context),
                         TREE_ONLY_SESSIONS)
        # A session that (in a dev instance) hosts the panel cannot be ended as a tree.
        table = make_table((1, 0, '/sbin/launchd', {'uid': 0}), (50, 1, 'claude'),
                           (51, 50, '/bin/zsh -c x'), (PANEL_PID, 51, 'python server.py'))
        grouping = build_grouping(table)
        context = self.policy.context(table, grouping)
        self.assertEqual(tree_blocked(self.policy, grouping.groups['claude:50'], context), PANEL_ANCESTOR)

    def test_terminal_hosts_are_protected_by_kernel_name_and_decorated_titles(self):
        table = make_table(
            (1, 0, '/sbin/launchd', {'uid': 0}),
            (60, 1, 'tmux: server', {'argv0': 'tmux: server', 'ucomm': 'tmux'}),
            (61, 1, 'SCREEN -S work', {'ucomm': 'screen'}),
            (62, 1, '/Applications/Ghostty.app/Contents/MacOS/ghostty'),
            # A host started under another argv[0] is still known by its kernel name.
            (63, 1, 'innocent', {'ucomm': 'iTerm2'}),
            (64, 1, '/Applications/WezTerm.app/Contents/MacOS/wezterm-gui start'))
        for pid in (60, 61, 62, 63, 64):
            self.assertEqual(self.reason(pid, table=table), PROTECTED_REASON, pid)

    def test_a_process_hosting_terminals_or_llm_roots_is_blocked_by_structure(self):
        table = make_table(
            (1, 0, '/sbin/launchd', {'uid': 0}),
            # An IDE helper parenting a Codex app server (infra) and a terminal with a session.
            (70, 1, '/Applications/Code.app/Contents/MacOS/Code Helper (Plugin) --type=x',
             {'argv0': '/Applications/Code.app/Contents/MacOS/Code Helper (Plugin)'}),
            (71, 70, '/Users/me/.vscode/bin/codex app-server --analytics'),
            (72, 70, '/bin/zsh -il', {'tty': 'ttys009'}),
            (73, 72, 'claude', {'tty': 'ttys009'}),
            (74, 73, 'caffeinate -i -t 300', {'tty': 'ttys009'}),
            # An unknown multiplexer with two panes.
            (80, 1, '/opt/bin/newmux --daemon'),
            (81, 80, '-zsh', {'tty': 'ttys010'}),
            (82, 80, '-zsh', {'tty': 'ttys011'}),
            # A foreground job and its children on its own terminal host nothing.
            (90, 1, 'make all', {'tty': 'ttys012'}),
            (91, 90, 'cc -c x.c', {'tty': 'ttys012'}),
            (95, 1, '/usr/bin/plain-daemon'))
        self.assertEqual(self.reason(70, table=table), hosts_reason(2))
        self.assertEqual(self.reason(80, table=table), hosts_reason(2))
        # A keep-awake helper below a session is that session's own, not a host above it.
        self.assertIsNone(self.reason(74, table=table))
        for pid in (90, 91, 95):
            self.assertIsNone(self.reason(pid, table=table), pid)

    def test_a_gap_in_the_ancestry_blocks_rather_than_guesses(self):
        table = make_table((1, 0, '/sbin/launchd', {'uid': 0}),
                           (500, 1, 'python3 -m van_compute.worker'),
                           # 501 (the job's shell) is missing: 502 could be a compute job.
                           (502, 501, 'python3 work.py'))
        self.assertEqual(self.reason(502, table=table), INCOMPLETE)
        # The panel's own row with a gap above it: its ancestors are unknown, so nothing goes.
        table = make_table((1, 0, '/sbin/launchd', {'uid': 0}), (8001, 7999, 'launcher'),
                           (PANEL_PID, 8001, 'python server.py'), (603, 1, 'vim notes'))
        self.assertEqual(self.reason(603, table=table), PANEL_UNKNOWN)


if __name__ == '__main__':
    unittest.main()
