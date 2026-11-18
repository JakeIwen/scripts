"""What role a process has, for grouping and the signal policy: here, uncertainty protects.

The display parse (`process_invocation`) shows less when it is unsure. These classifiers
decide what may be signalled, so they lean the other way. Infra and compute roots are found
by substring of the whole command text (or the kernel's name), with no option parsing at
all, so no spelling of the options can hide one; a false positive only makes a row
read-only. Inside a session a mention alone does not count: a process there roots a group
only when it also runs one of the programs these roles run under (`runs_role_program`), so a
session's own `grep model-gateway.js` stays its member. Every Codex
process roots a group of its own wherever it runs (`resource_groups`), so its role decides
only how that group is shown and whether it may be ended as a session: a session only when
`codex_role` reads the whole line and finds one, otherwise read-only infrastructure, with
nothing in it signalled when the role is unknown (`CodexRole.UNCERTAIN`).
"""
from enum import Enum
import os

from process_table import KERNEL_NAME_LIMIT, ProcessRow

SHELLS = frozenset(('zsh', 'bash', 'sh', 'dash', 'fish', 'ksh', 'tcsh', 'csh'))
CLAUDE = 'claude'
CODEX = 'codex'


class CodexRole(str, Enum):
    # No operand (interactive), or `exec`/`e`/`resume`, on a line read in full: may be ended.
    SESSION = 'session'
    APP_SERVER = 'app-server'
    # Another known service or helper that runs on: `exec-server`, `responses-api-proxy`, ...
    SERVICE = 'service'
    # Any other known subcommand (`login`, `mcp`, `review`, ...).
    COMMAND = 'command'
    # Everything else: an option the table lacks, a free-text value with words after it, or an
    # operand no table names (a subcommand newer than the table and an interactive prompt look
    # the same in `ps`). Nothing in such a group is signalled.
    UNCERTAIN = 'uncertain'


# Codex's global options (`codex --help`, codex-cli 0.170.0, 2026-11-18). A session is
# recognised only when every option before the first operand is in one of these tables.
# Values Codex's parser checks (an enum, or a plain profile name), so each is exactly one word.
CODEX_WORD_VALUE_OPTIONS = frozenset(('-a', '--ask-for-approval', '-s', '--sandbox', '-p',
                                      '--profile'))
# Values taken as free text: `ps` cannot show where one ends, since it may be empty (`-c ''`
# leaves no word) or hold spaces (`-C '/tmp/a b'`), and `-i` takes several. Any word after
# the option's name, its own value included, could be the operand, so the line is read only
# when the option is the last word (`--config=k=v`, or a bare `-c` whose value was empty).
CODEX_TEXT_VALUE_OPTIONS = frozenset(('-c', '--config', '--enable', '--disable', '-m',
                                      '--model', '--local-provider', '--remote',
                                      '--remote-auth-token-env', '-C', '--cd', '--add-dir',
                                      '-i', '--image'))
CODEX_FLAGS = frozenset(('--oss', '--search', '--no-alt-screen', '--no-daemon', '--worktree',
                         '--strict-config', '--approve-for-me',
                         '--dangerously-bypass-approvals-and-sandbox',
                         '--dangerously-bypass-hook-trust', '-h', '--help', '-V', '--version'))
# Subcommands by the role each gives (`codex --help`, plus the internal ones it hides).
SESSION_SUBCOMMANDS = frozenset(('exec', 'e', 'resume'))
OPERAND_ROLES = {
    'app-server': CodexRole.APP_SERVER,
    **dict.fromkeys(('exec-server', 'remote-control', 'responses-api-proxy', 'stdio-to-uds',
                     'mcp-server'), CodexRole.SERVICE),
    **dict.fromkeys(('agents', 'review', 'login', 'logout', 'mcp', 'plugin', 'app', 'completion',
                     'update', 'doctor', 'sandbox', 'debug', 'apply', 'a', 'queue', 'archive',
                     'delete', 'migrate-rollouts', 'migrate-nfs-db', 'unarchive', 'fork', 'cloud',
                     'features', 'help'), CodexRole.COMMAND),
    **dict.fromkeys(SESSION_SUBCOMMANDS, CodexRole.SESSION),
}

CODEX_LABELS = {CodexRole.APP_SERVER: 'Codex app server', CodexRole.SERVICE: 'Codex service',
                CodexRole.COMMAND: 'Codex command', CodexRole.UNCERTAIN: 'Codex (role unread)'}
# Text that makes a process shared infrastructure wherever it appears in its command.
INFRA_MARKERS = {'llm_switchboard': 'LLM switchboard', 'model-gateway.js': 'model gateway',
                 'claude-code-proxy': 'model gateway proxy', 'usage-server.mjs': 'usage server',
                 '@playwright/mcp': 'Playwright MCP'}
COMPUTE_MARKER = 'van_compute.worker'
# The programs the infra and compute entries above really run as: their interpreters and the
# proxy binary. A process inside a session that names a marker and runs one of these is the
# worker or gateway itself; one running anything else (`grep`, an editor, a shell) mentions it.
ROLE_INTERPRETER_PREFIXES = ('python', 'Python')
ROLE_PROGRAMS = frozenset(('node', 'claude-code-proxy'))
ROLE_PROGRAM_NAMES = ROLE_PROGRAMS | frozenset(name[:KERNEL_NAME_LIMIT] for name in ROLE_PROGRAMS)


def program_names(row: ProcessRow) -> frozenset:
    """Every name the program goes by: argv[0]'s base name (login dash removed), the kernel's name
    and the display program, so a renamed argv[0] cannot hide what was run."""
    base = os.path.basename(row.executable.strip().rstrip('/'))
    return frozenset(name for name in (base, base.lstrip('-'), row.kernel_name, row.exe) if name)


def is_shell(row: ProcessRow) -> bool:
    return not program_names(row).isdisjoint(SHELLS)


def is_codex(row: ProcessRow) -> bool:
    return CODEX in program_names(row)


def codex_operand(row: ProcessRow) -> tuple[bool, str | None]:
    """(read, operand): whether every word before the first operand is understood, and that
    operand (None for none). Options are those of the tables above only."""
    tokens, index = row.arguments, 0
    while index < len(tokens):
        token = tokens[index]
        name, attached = token.split('=', 1)[0], '=' in token
        if name in CODEX_TEXT_VALUE_OPTIONS:
            return index == len(tokens) - 1, None
        if name in CODEX_WORD_VALUE_OPTIONS:
            index += 1 if attached else 2
        elif token in CODEX_FLAGS:
            index += 1
        elif token.startswith('-'):
            return False, None
        else:
            return True, token
    return True, None


def codex_role(row: ProcessRow) -> CodexRole:
    """A Codex process's role: a session only on positive evidence, never by default."""
    read, operand = codex_operand(row)
    if not read:
        return CodexRole.UNCERTAIN
    if operand is None:
        return CodexRole.SESSION
    return OPERAND_ROLES.get(operand, CodexRole.UNCERTAIN)


def names_marker(row: ProcessRow, marker) -> bool:
    """The command text contains the marker, or the kernel's (truncated) name is it."""
    return marker in row.argv_text or row.kernel_name == marker[:KERNEL_NAME_LIMIT]


def infra_label(row: ProcessRow) -> str | None:
    """The infra table's label when the command text names shared infrastructure anywhere."""
    return next((label for marker, label in INFRA_MARKERS.items() if names_marker(row, marker)), None)


def is_compute_root(row: ProcessRow) -> bool:
    return names_marker(row, COMPUTE_MARKER)


def runs_role_program(row: ProcessRow) -> bool:
    """argv[0]'s base name or the kernel's name is an interpreter or binary a role runs as."""
    return any(name.startswith(ROLE_INTERPRETER_PREFIXES) or name in ROLE_PROGRAM_NAMES
               for name in program_names(row))
