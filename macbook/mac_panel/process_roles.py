"""What role a process has, for grouping and the signal policy: here, uncertainty protects.

The display parse (`process_invocation`) shows less when it is unsure. These classifiers
decide what may be signalled, so they lean the other way. A process is an infra or compute
root when its program or any whole argument names one, whatever options surround it; a
false positive only makes a row read-only. Every Codex process roots a group of its own
wherever it runs (`resource_groups`), so its role decides only how that group is shown and
whether it may be ended as a session: a session when `codex_role` is sure, otherwise
read-only infrastructure, with nothing in it signalled when the role is unknown
(`CodexRole.UNCERTAIN`).
"""
from enum import Enum
import os
import re

from process_table import ProcessRow

SHELLS = frozenset(('zsh', 'bash', 'sh', 'dash', 'fish', 'ksh', 'tcsh', 'csh'))
CLAUDE = 'claude'
CODEX = 'codex'
APP_SERVER = 'app-server'


class CodexRole(str, Enum):
    APP_SERVER = 'app-server'
    SESSION = 'session'
    # The subcommand cannot be read from the flattened text: read-only, never a session.
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
# `codex --help` subcommands other than app-server (2026-11-17).
CODEX_SUBCOMMANDS = frozenset((
    'agents', 'exec', 'e', 'review', 'login', 'logout', 'mcp', 'plugin', 'remote-control', 'app',
    'completion', 'update', 'doctor', 'sandbox', 'debug', 'apply', 'a', 'resume', 'queue',
    'archive', 'delete', 'migrate-rollouts', 'migrate-nfs-db', 'unarchive', 'fork', 'cloud',
    'exec-server', 'features', 'help'))

# The infra label of a Codex group that is not a session.
CODEX_LABELS = {CodexRole.APP_SERVER: 'Codex app server', CodexRole.UNCERTAIN: 'Codex (role unread)'}
# Base names of a program or of any argument that make a process shared infrastructure.
INFRA_NAMES = {'claude-code-proxy': 'model gateway proxy', 'model-gateway.js': 'model gateway',
               'usage-server.mjs': 'usage server', 'llm_switchboard': 'LLM switchboard',
               'llm_switchboard.py': 'LLM switchboard'}
# The Playwright MCP server is a generic `cli.js`, so its package path identifies it.
PLAYWRIGHT_PACKAGE = '@playwright/mcp/'
PLAYWRIGHT_SCRIPT = 'cli.js'
PLAYWRIGHT_LABEL = 'Playwright MCP'
INFRA_MODULES = {'llm_switchboard': 'LLM switchboard'}
COMPUTE_MODULE = 'van_compute.worker'
# `-m module`, or a short-option cluster ending in m (`-Bm module`).
MODULE_OPTION = re.compile(r'-[A-Za-z]*m')


def program_names(row: ProcessRow) -> frozenset:
    """Every name the program goes by: argv[0]'s base name (login dash removed), the kernel's name
    and the display program, so a renamed argv[0] cannot hide what was run."""
    base = os.path.basename(row.executable.strip().rstrip('/'))
    return frozenset(name for name in (base, base.lstrip('-'), row.kernel_name, row.exe) if name)


def is_shell(row: ProcessRow) -> bool:
    return not program_names(row).isdisjoint(SHELLS)


def is_codex(row: ProcessRow) -> bool:
    return CODEX in program_names(row)


def first_operand_role(token, valued, mentioned_later) -> CodexRole:
    if token == APP_SERVER:
        return CodexRole.APP_SERVER
    # A known subcommand reached through flags alone is certain; a prompt or a value shifted
    # by a space is not, when `app-server` appears further on.
    if mentioned_later and (valued or token not in CODEX_SUBCOMMANDS):
        return CodexRole.UNCERTAIN
    return CodexRole.SESSION


def codex_role(row: ProcessRow) -> CodexRole:
    """A Codex process's role from its subcommand (the first operand after global options)."""
    tokens, index, valued = row.arguments, 0, False
    while index < len(tokens):
        token = tokens[index]
        if token.split('=', 1)[0] in CODEX_VALUE_OPTIONS:
            valued = True
            index += 1 if '=' in token else 2
        elif token in CODEX_FLAGS:
            index += 1
        elif token.startswith('-'):
            # An unknown option may take a value, so the subcommand's position is unknown.
            return CodexRole.UNCERTAIN if APP_SERVER in tokens else CodexRole.SESSION
        else:
            return first_operand_role(token, valued, APP_SERVER in tokens[index + 1:])
    # No operand: `app-server` can only have been read as an option's value.
    return CodexRole.UNCERTAIN if APP_SERVER in tokens else CodexRole.SESSION


def modules(tokens):
    """Every module named after `-m` (or `-Bm`, or `-mname`) anywhere in the arguments."""
    for index, token in enumerate(tokens):
        if MODULE_OPTION.fullmatch(token) and index + 1 < len(tokens):
            yield tokens[index + 1]
        elif token.startswith('-m') and not token.startswith('--') and len(token) > 2:
            yield token[2:]


def argument_names(token):
    """Base names an argument could carry: the whole token and an `--option=value` value."""
    return {os.path.basename(token), os.path.basename(token.split('=')[-1])}


def infra_label(row: ProcessRow) -> str | None:
    """The infra table's label when the program or any whole argument names shared infrastructure."""
    for name in program_names(row):
        if name in INFRA_NAMES:
            return INFRA_NAMES[name]
    for token in row.arguments:
        for name in argument_names(token):
            if name in INFRA_NAMES:
                return INFRA_NAMES[name]
            if name == PLAYWRIGHT_SCRIPT and PLAYWRIGHT_PACKAGE in token:
                return PLAYWRIGHT_LABEL
    return next((INFRA_MODULES[name] for name in modules(row.arguments) if name in INFRA_MODULES), None)


def is_compute_root(row: ProcessRow) -> bool:
    return COMPUTE_MODULE in modules(row.arguments)
