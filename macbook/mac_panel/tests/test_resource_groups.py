"""Grouping by position and the signal policy's block reasons."""
import unittest

from resource_groups import Kind, build_grouping
from resources_fixtures import PANEL_PID, make_table
from signal_policy import (KIND_REASONS, OTHER_USER, PANEL, PANEL_ANCESTOR, PANEL_DESCENDANT,
                           PROTECTED_REASON, SHELL, SYSTEM_PID, TREE_ONLY_SESSIONS, Scope,
                           SignalPolicy, tree_blocked)

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
        self.lineage = self.policy.lineage(self.table)

    def reason(self, pid, scope=Scope.PROCESS):
        return self.policy.blocked(self.table.rows[pid], self.grouping.kind(pid), scope, self.lineage)

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
        self.assertEqual(policy.blocked(self.table.rows[1], Kind.APP, Scope.PROCESS, self.lineage), SYSTEM_PID)

    def test_shells_are_allowed_as_session_tree_members(self):
        self.assertIsNone(self.reason(201, Scope.TREE))
        self.assertEqual(self.reason(603, Scope.TREE), TREE_ONLY_SESSIONS)
        self.assertEqual(self.reason(101, Scope.PROCESS), SHELL)

    def test_tree_blocked_for_nested_sessions_and_panel_ancestors(self):
        outer, inner = self.grouping.groups['claude:200'], self.grouping.groups['claude:210']
        reason = tree_blocked(self.policy, outer, self.lineage, {'claude:210': 'inner work'})
        self.assertIn('inner work', reason)
        self.assertIn('end those first', reason)
        self.assertIsNone(tree_blocked(self.policy, inner, self.lineage))
        self.assertEqual(tree_blocked(self.policy, self.grouping.groups['infra:400'], self.lineage),
                         TREE_ONLY_SESSIONS)
        # A session that (in a dev instance) hosts the panel cannot be ended as a tree.
        table = make_table((50, 1, 'claude'), (51, 50, '/bin/zsh -c x'), (PANEL_PID, 51, 'python server.py'))
        grouping = build_grouping(table)
        lineage = self.policy.lineage(table)
        self.assertEqual(tree_blocked(self.policy, grouping.groups['claude:50'], lineage), PANEL_ANCESTOR)


if __name__ == '__main__':
    unittest.main()
