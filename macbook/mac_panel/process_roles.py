"""What role a process has, for grouping and the signal policy: here, uncertainty protects.

The display parse (`process_invocation`) shows less when it is unsure. These classifiers
decide what may be signalled, so they lean the other way. Infra and compute roots are found
by substring of the whole command text (or the kernel's name), with no option parsing at
all, so no spelling of the options can hide one; a false positive only makes a row
read-only, and inside a session such a process is still that session's member. Every Codex process roots a group of its own
wherever it runs (`resource_groups`), so its role decides only how that group is shown and
whether it may be ended as a session: a session when `codex_role` is sure, otherwise
read-only infrastructure, with nothing in it signalled when the role is unknown
(`CodexRole.UNCERTAIN`).
"""
from enum import Enum
import os

from process_table import KERNEL_NAME_LIMIT, ProcessRow

SHELLS = frozenset(('zsh', 'bash', 'sh', 'dash', 'fish', 'ksh', 'tcsh', 'csh'))
CLAUDE = 'claude'
CODEX = 'codex'


class CodexRole(str, Enum):
    # Interactive (with or without a prompt), `exec` or `resume`: may be ended as a session.
    SESSION = 'session'
    APP_SERVER = 'app-server'
    # A running service or its supervisor: `exec-server`, `remote-control`.
    SERVICE = 'service'
    # Any other subcommand (`login`, `mcp`, `review`, ...): not confidently a session.
    COMMAND = 'command'
    # The subcommand cannot be read from the flattened text.
    UNCERTAIN = 'uncertain'


# Codex global options that take a value (`-c k=v` or `--config=k=v`). A value can contain
# spaces, which `ps` cannot show as one argument, so any of these before the subcommand
# makes later positions uncertain.
CODEX_VALUE_OPTIONS = frozenset(('-c', '--config', '-m', '--model', '-p', '--profile', '-C',
                                 '--cd', '-s', '--sandbox', '-a', '--ask-for-approval', '-i',
                                 '--image', '--enable', '--disable', '--local-provider',
                                 '--add-dir', '--remote', '--remote-auth-token-env'))
CODEX_FLAGS = frozenset(('--oss', '--search', '--no-alt-screen', '--no-daemon', '--worktree',
                         '--strict-config', '--approve-for-me', '--full-auto',
                         '--dangerously-bypass-approvals-and-sandbox',
                         '--dangerously-bypass-hook-trust', '-h', '--help', '-V', '--version'))
# `codex --help` subcommands (2026-11-17), by the role each gives.
SESSION_SUBCOMMANDS = frozenset(('exec', 'e', 'resume'))
SERVICE_SUBCOMMANDS = {'app-server': CodexRole.APP_SERVER, 'exec-server': CodexRole.SERVICE,
                       'remote-control': CodexRole.SERVICE}
OTHER_SUBCOMMANDS = frozenset((
    'agents', 'review', 'login', 'logout', 'mcp', 'plugin', 'app', 'completion', 'update',
    'doctor', 'sandbox', 'debug', 'apply', 'a', 'queue', 'archive', 'delete', 'migrate-rollouts',
    'migrate-nfs-db', 'unarchive', 'fork', 'cloud', 'features', 'help'))
NON_SESSION_SUBCOMMANDS = frozenset(SERVICE_SUBCOMMANDS) | OTHER_SUBCOMMANDS

CODEX_LABELS = {CodexRole.APP_SERVER: 'Codex app server', CodexRole.SERVICE: 'Codex service',
                CodexRole.COMMAND: 'Codex command', CodexRole.UNCERTAIN: 'Codex (role unread)'}
# Text that makes a process shared infrastructure wherever it appears in its command.
INFRA_MARKERS = {'llm_switchboard': 'LLM switchboard', 'model-gateway.js': 'model gateway',
                 'claude-code-proxy': 'model gateway proxy', 'usage-server.mjs': 'usage server',
                 '@playwright/mcp': 'Playwright MCP'}
COMPUTE_MARKER = 'van_compute.worker'


def program_names(row: ProcessRow) -> frozenset:
    """Every name the program goes by: argv[0]'s base name (login dash removed), the kernel's name
    and the display program, so a renamed argv[0] cannot hide what was run."""
    base = os.path.basename(row.executable.strip().rstrip('/'))
    return frozenset(name for name in (base, base.lstrip('-'), row.kernel_name, row.exe) if name)


def is_shell(row: ProcessRow) -> bool:
    return not program_names(row).isdisjoint(SHELLS)


def is_codex(row: ProcessRow) -> bool:
    return CODEX in program_names(row)


def operand_role(token) -> CodexRole:
    """The role a first operand gives: a subcommand's, or a session for a prompt."""
    if token in SERVICE_SUBCOMMANDS:
        return SERVICE_SUBCOMMANDS[token]
    return CodexRole.COMMAND if token in OTHER_SUBCOMMANDS else CodexRole.SESSION


def codex_role(row: ProcessRow) -> CodexRole:
    """A Codex process's role from its subcommand (the first operand after global options).

    It is a session only when that is certain. After a value option or an unknown option,
    a value with a space shifts every later word, so any non-session subcommand named later
    makes the role uncertain; a prompt that names a service subcommand does too.
    """
    tokens, index, shifted = row.arguments, 0, False
    while index < len(tokens):
        token = tokens[index]
        if token.split('=', 1)[0] in CODEX_VALUE_OPTIONS:
            shifted = True
            index += 1 if '=' in token else 2
        elif token in CODEX_FLAGS:
            index += 1
        elif token.startswith('-'):
            # An unknown option may take a value, so no later position is certain.
            later = frozenset(tokens[index:])
            return CodexRole.SESSION if later.isdisjoint(NON_SESSION_SUBCOMMANDS) else CodexRole.UNCERTAIN
        else:
            break
    if index >= len(tokens):
        # No operand: a subcommand can only have been read as an option's value.
        return (CodexRole.UNCERTAIN if shifted and not NON_SESSION_SUBCOMMANDS.isdisjoint(tokens)
                else CodexRole.SESSION)
    operand, later = tokens[index], frozenset(tokens[index + 1:])
    role = operand_role(operand)
    if role is not CodexRole.SESSION:
        return role
    if shifted and not later.isdisjoint(NON_SESSION_SUBCOMMANDS):
        return CodexRole.UNCERTAIN
    if operand not in SESSION_SUBCOMMANDS and not later.isdisjoint(SERVICE_SUBCOMMANDS):
        return CodexRole.UNCERTAIN
    return CodexRole.SESSION


def names_marker(row: ProcessRow, marker) -> bool:
    """The command text contains the marker, or the kernel's (truncated) name is it."""
    return marker in row.argv_text or row.kernel_name == marker[:KERNEL_NAME_LIMIT]


def infra_label(row: ProcessRow) -> str | None:
    """The infra table's label when the command text names shared infrastructure anywhere."""
    return next((label for marker, label in INFRA_MARKERS.items() if names_marker(row, marker)), None)


def is_compute_root(row: ProcessRow) -> bool:
    return names_marker(row, COMPUTE_MARKER)
