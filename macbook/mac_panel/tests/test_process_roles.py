"""Safety roles: a parse that is unsure protects, whatever the display parse shows."""
import unittest

from panel_contracts import ApiError
from process_roles import CodexRole, codex_role, infra_label, is_compute_root, is_shell
from process_signals import ProcessSignaller
from resource_groups import Kind, build_grouping
from resources_fixtures import PANEL_PID, STARTED, FakeReader, make_table
from signal_policy import (KIND_REASONS, ROLE_UNKNOWN, SHELL, Scope, SignalPolicy,
                           tree_blocked)

BASE = (
    (1, 0, '/sbin/launchd', {'uid': 0}),
    (100, 1, '/Applications/iTerm.app/Contents/MacOS/iTerm2'),
    (101, 100, '-zsh', {'tty': 'ttys001'}),
    (PANEL_PID, 1, '/venv/bin/python server.py'),
)


def row(command, **options):
    return make_table((50, 1, command, options)).rows[50]


class Harness:
    def __init__(self, *specs):
        self.table = make_table(*BASE, *specs)
        self.grouping = build_grouping(self.table)
        self.policy = SignalPolicy(uid=501, panel_pid=PANEL_PID)
        self.context = self.policy.context(self.table, self.grouping)
        self.calls = []
        self.signaller = ProcessSignaller(FakeReader(self.table), self.policy,
                                          lambda pid, sig: self.calls.append(pid))

    def reason(self, pid, scope=Scope.PROCESS):
        return self.policy.blocked(self.table.rows[pid], self.grouping.kind(pid), scope, self.context)

    def tree_blocked(self, gid):
        return tree_blocked(self.policy, self.grouping.groups[gid], self.context)

    def post(self, pid, scope='tree'):
        return self.signaller.mutate({'action': 'terminate', 'pid': pid, 'started': STARTED,
                                      'scope': scope, 'confirmed': True})


class CodexRoleTests(unittest.TestCase):
    def test_the_subcommand_decides_and_a_prompt_never_overrides_it(self):
        cases = {
            'codex exec review the app-server implementation': CodexRole.SESSION,
            'codex --no-alt-screen --ask-for-approval never': CodexRole.SESSION,
            'codex resume --last': CodexRole.SESSION,
            'codex': CodexRole.SESSION,
            'codex -c a=1 app-server --remote-control': CodexRole.APP_SERVER,
            'codex exec-server --listen 1': CodexRole.SERVICE,
            'codex remote-control start': CodexRole.SERVICE,
            'codex -a never exec-server': CodexRole.SERVICE,
            # Not an interactive, exec or resume run: never offered as a session.
            'codex review': CodexRole.COMMAND,
            'codex fork --last': CodexRole.COMMAND,
            'codex login': CodexRole.COMMAND,
            # A value with a space, or an option value that swallowed the subcommand.
            'codex -c x=1 y exec-server': CodexRole.UNCERTAIN,
            'codex -c a -m exec-server': CodexRole.UNCERTAIN,
            'codex -m gpt fix the login page': CodexRole.UNCERTAIN,
            'codex fix the exec-server tests': CodexRole.UNCERTAIN,
            '/x/codex app-server daemon pid-update-loop': CodexRole.APP_SERVER,
            'codex --config=a=1 app-server': CodexRole.APP_SERVER,
            # A value with a space shifts every later word: `b` is not a known subcommand.
            'codex -c model=a b app-server --listen x': CodexRole.UNCERTAIN,
            # A value option before a known subcommand: its value could have hidden words.
            'codex -m gpt exec about app-server': CodexRole.UNCERTAIN,
            # An option the table does not know may take a value.
            'codex --future-flag app-server': CodexRole.UNCERTAIN,
            # An interactive prompt that mentions it.
            'codex fix the app-server tests': CodexRole.UNCERTAIN,
            'codex fix the gateway tests': CodexRole.SESSION,
        }
        for command, role in cases.items():
            self.assertEqual(codex_role(row(command)), role, command)

    def test_a_prompt_mentioning_app_server_leaves_a_nested_session_a_boundary(self):
        # claude → codex exec "review the app-server implementation" → worker
        world = Harness((200, 101, 'claude --permission-mode bypassPermissions'),
                        (210, 200, 'codex exec review the app-server implementation'),
                        (211, 210, 'node worker.js'))
        self.assertEqual(world.grouping.kind(210), Kind.CODEX)
        self.assertEqual(world.grouping.membership[211], 'codex:210')
        self.assertEqual(world.grouping.groups['claude:200'].nested, ['codex:210'])
        self.assertIn('nested sessions', world.tree_blocked('claude:200'))
        with self.assertRaises(ApiError) as caught:
            world.post(200)
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(world.calls, [])
        # The nested session itself is certain, so it can be ended on its own.
        self.assertIsNone(world.tree_blocked('codex:210'))

    def test_a_codex_whose_role_is_unknown_is_a_boundary_and_refused_itself(self):
        world = Harness((200, 101, 'claude'),
                        (210, 200, '/x/codex -c model=a b app-server --listen x'),
                        (211, 210, 'node helper.js'))
        self.assertEqual(world.grouping.groups['claude:200'].nested, ['infra:210'])
        self.assertEqual(world.grouping.groups['infra:210'].label, 'Codex (role unread)')
        self.assertIn('nested sessions', world.tree_blocked('claude:200'))
        self.assertEqual(world.tree_blocked('infra:210'), ROLE_UNKNOWN)
        self.assertEqual(world.reason(210), ROLE_UNKNOWN)
        self.assertEqual(world.reason(211), ROLE_UNKNOWN)
        for pid, scope in ((210, 'tree'), (210, 'process'), (211, 'process')):
            with self.assertRaises(ApiError):
                world.post(pid, scope)
        self.assertEqual(world.calls, [])

    def test_codex_is_known_by_its_kernel_name_under_another_argv0(self):
        world = Harness((200, 101, 'claude'),
                        (210, 200, 'agent exec run', {'ucomm': 'codex'}))
        self.assertEqual(world.grouping.kind(210), Kind.CODEX)
        self.assertIn('nested sessions', world.tree_blocked('claude:200'))


class InfraAndComputeTests(unittest.TestCase):
    def test_options_with_operands_do_not_hide_a_gateway_or_a_worker(self):
        world = Harness((400, 1, 'node --require preload.js /app/model-gateway.js serve'),
                        (500, 1, 'python3 -X dev -m van_compute.worker'))
        self.assertEqual(world.grouping.kind(400), Kind.INFRA)
        self.assertEqual(world.grouping.groups['infra:400'].label, 'model gateway')
        self.assertEqual(world.grouping.kind(500), Kind.COMPUTE)
        self.assertEqual(world.reason(400), KIND_REASONS[Kind.INFRA])
        self.assertEqual(world.reason(500), KIND_REASONS[Kind.COMPUTE])
        for pid in (400, 500):
            with self.assertRaises(ApiError) as caught:
                world.post(pid, 'process')
            self.assertEqual(caught.exception.status, 403)
        self.assertEqual(world.calls, [])

    def test_any_whole_argument_or_module_matches(self):
        for command, label in (
                ('node --inspect=0 /x/usage-server.mjs', 'usage server'),
                ('node --loader x.mjs /a/node_modules/@playwright/mcp/cli.js --port 1', 'Playwright MCP'),
                ('/opt/bin/claude-code-proxy serve', 'model gateway proxy'),
                ('python3 -X utf8 -m llm_switchboard', 'LLM switchboard'),
                ('python3 /srv/llm_switchboard.py', 'LLM switchboard'),
                ('node --script=/x/model-gateway.js', 'model gateway')):
            self.assertEqual(infra_label(row(command)), label, command)
        self.assertIsNone(infra_label(row('node /x/cli.js')))
        for command in ('python3 -Bm van_compute.worker', 'python3 -W ignore -m van_compute.worker',
                        'python3 -mvan_compute.worker'):
            self.assertTrue(is_compute_root(row(command)), command)

    def test_a_sessions_own_command_that_mentions_the_gateway_stays_its_member(self):
        world = Harness((200, 101, 'claude'), (201, 200, 'grep -r model-gateway.js .'))
        self.assertEqual(world.grouping.membership[201], 'claude:200')
        self.assertIsNone(world.reason(201))


class RoundThreeTests(unittest.TestCase):
    """The round-3 review's reproductions, verbatim as `ps` shows them (quotes are lost)."""

    def assertRefused(self, world, *requests):
        for pid, scope in requests:
            with self.assertRaises(ApiError, msg=(pid, scope)) as caught:
                world.post(pid, scope)
            self.assertEqual(caught.exception.status, 403)
        self.assertEqual(world.calls, [])

    def test_finding1_a_codex_inside_a_claude_session_is_never_an_ordinary_member(self):
        for command in (
                # codex "app-server tests are failing"
                'codex app-server tests are failing',
                # codex -C "/tmp/project app-server" exec review
                'codex -C /tmp/project app-server exec review'):
            world = Harness((200, 101, 'claude --permission-mode bypassPermissions'),
                            (210, 200, command), (211, 210, 'node worker.js'))
            self.assertNotEqual(world.grouping.membership.get(210), 'claude:200', command)
            self.assertNotEqual(world.grouping.membership.get(211), 'claude:200', command)
            gid = world.grouping.membership[210]
            self.assertIn(gid, world.grouping.groups['claude:200'].nested, command)
            self.assertIsNotNone(world.tree_blocked('claude:200'), command)
            self.assertIsNotNone(world.tree_blocked(gid), command)
            self.assertRefused(world, (200, 'tree'), (210, 'tree'), (210, 'process'))

    def test_finding1_any_group_root_beneath_a_session_blocks_its_tree_and_is_named(self):
        world = Harness((200, 101, 'claude'), (201, 200, '/bin/zsh -c x'),
                        (210, 201, '/x/codex app-server --listen x'))
        self.assertEqual(world.grouping.kind(210), Kind.INFRA)
        self.assertEqual(world.grouping.groups['claude:200'].nested, ['infra:210'])
        reason = tree_blocked(world.policy, world.grouping.groups['claude:200'], world.context,
                              {'infra:210': 'Codex app server'})
        self.assertIn('Codex app server', reason)
        self.assertRefused(world, (200, 'tree'))

    def test_finding4_codex_service_subcommands_are_read_only_infra(self):
        for command in ('codex exec-server --listen 1', 'codex remote-control start',
                        '/x/codex -c a=1 exec-server'):
            world = Harness((300, 101, command), (301, 300, 'node child.js'))
            self.assertEqual(world.grouping.kind(300), Kind.INFRA, command)
            self.assertEqual(world.grouping.kind(301), Kind.INFRA, command)
            self.assertEqual(world.reason(300), KIND_REASONS[Kind.INFRA], command)
            self.assertRefused(world, (300, 'tree'), (300, 'process'), (301, 'process'))


class ShellTests(unittest.TestCase):
    def test_a_shell_is_known_by_argv0_or_kernel_name(self):
        self.assertTrue(is_shell(row('worker --serve', ucomm='bash')))
        self.assertTrue(is_shell(row('-zsh')))
        self.assertFalse(is_shell(row('vim notes')))
        world = Harness((300, 101, 'worker --serve', {'ucomm': 'bash'}))
        self.assertEqual(world.reason(300), SHELL)


if __name__ == '__main__':
    unittest.main()
