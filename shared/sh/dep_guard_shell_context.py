"""Where the shell's lexical command list cannot describe code or configuration in effect."""

import re

from dep_guard_options import scan
from destructive_guard_shell import BOUNDARIES

_CONFIG_NAMES = {'.npmrc', 'npmrc', 'pip.conf', 'uv.toml', 'bunfig.toml', '.bunfig.toml',
                 'pyproject.toml', 'poetry.toml', 'pnpm-workspace.yaml', 'package.json'}
_CONFIG_WRITERS = {'tee', 'cp', 'mv', 'install', 'sed', 'perl', 'dd', 'touch', 'ln', 'rm', 'patch',
                   'rsync', 'git', 'chmod', 'chown', 'truncate'}
_MUTATE = {'set', 'unset', 'delete', 'edit', 'fix', 'rm', 'remove'}
_CONFIG_PROGS = {'npm', 'pnpm', 'yarn', 'pip', 'uv', 'poetry', 'bun'}
_ROUTE_ENV = re.compile(r'(?i)(?:npm_|pip_|uv_|pipx_|poetry_|pnpm_|corepack_|yarn_|bun_|xdg_|'
                        r'home(?:=|$)|virtual_env(?:=|$)|prefix(?:=|$)|destdir(?:=|$)|path(?:=|$))')


def routing_assignment(word):
    return bool(_ROUTE_ENV.match(word))


def _config_path(word):
    name = word.rsplit('/', 1)[-1]
    return name in _CONFIG_NAMES or name.startswith('.yarnrc') or name in {'yarnrc', '.env'} or name.startswith('.env.')


def _maybe_config(word):
    return _config_path(word) or any(c in word.rsplit('/', 1)[-1] for c in '$`*?[]{}')


def writes_config(argv, redirects):
    if any(op in {'>', '>>', '>|', '&>', '&>>', '<>'} and _maybe_config(t.text) for _, op, t in redirects):
        return True
    if not argv:
        return False
    tool = argv[0].rsplit('/', 1)[-1]
    if tool in _CONFIG_WRITERS and any(_maybe_config(w) for w in argv[1:]):
        return True
    args = argv[1:]
    if tool.startswith('python') and 'pip' in args:
        args = args[args.index('pip')+1:]
        tool = 'pip'
    if re.fullmatch(r'pip\d+(?:\.\d+)?', tool):
        tool = 'pip'
    if tool not in _CONFIG_PROGS or not _MUTATE.intersection(args):
        return False
    verb = scan(args, tool, first=True)
    return (verb.operands == ['config'] and bool(_MUTATE.intersection(args[verb.consumed:])) or
            tool == 'npm' and verb.operands in (['set'], ['unset']) or
            verb.uncertain and 'config' in args and bool(_MUTATE.intersection(args)))


def _is_start(tokens, i):
    if i == 0:
        return True
    prev = tokens[i-1]
    return (prev.operator and prev.text in BOUNDARIES or
            not prev.operator and prev.text in {'{', 'if', 'then', 'else', 'elif', 'do', '!'})


def executable_tokens(tokens, source, functions):
    """Declarations are not executions. Preserve the following list separator."""
    def raw(j):
        return source[tokens[j].start:tokens[j].end] if j < len(tokens) else ''

    out, i = [], 0
    while i < len(tokens):
        t = tokens[i]
        explicit = not t.operator and raw(i) == 'function'
        j = i + int(explicit)
        parens = raw(j+1) == '(' and raw(j+2) == ')'
        if not _is_start(tokens, i) or (not explicit and (not parens or t.operator)):
            out.append(t)
            i += 1
            continue
        if j == len(tokens):
            break
        functions.add(tokens[j].text)
        j += 3 if parens else 1
        while raw(j) == '\n':
            j += 1
        opening = raw(j)
        if opening not in {'{', '('}:
            break  # Other compound bodies need shell grammar. Do not look inside.
        closing = '}' if opening == '{' else ')'
        count = 1
        j += 1
        while j < len(tokens) and count:
            text = raw(j)
            if text in {opening, closing} and (opening == '(' or _is_start(tokens, j)):
                count += 1 if text == opening else -1
            j += 1
        i = j
    return out
