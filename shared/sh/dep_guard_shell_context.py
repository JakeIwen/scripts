"""Where the shell's lexical command list cannot describe code or configuration in effect."""

import re

from dep_guard_config_paths import possible_config_path
from dep_guard_options import scan
from destructive_guard_shell import BOUNDARIES

_CONFIG_WRITERS = {'tee', 'cp', 'mv', 'install', 'sed', 'perl', 'dd', 'touch', 'ln', 'rm', 'patch',
                   'rsync', 'chmod', 'chown', 'truncate'}
_GIT_READS = {'status', 'diff', 'log', 'show', 'grep', 'ls-files', 'rev-parse', 'blame'}
_GIT_GLOBAL_VALUES = {'-C', '-c', '--git-dir', '--work-tree', '--config-env', '--namespace', '--super-prefix'}
_GIT_GLOBAL_FLAGS = {'-p', '-P', '--paginate', '--no-pager', '--bare', '--no-replace-objects',
                     '--literal-pathspecs', '--glob-pathspecs', '--noglob-pathspecs', '--icase-pathspecs',
                     '--no-optional-locks', '--no-advice', '--no-lazy-fetch'}
_MUTATE = {'set', 'unset', 'delete', 'edit', 'fix', 'rm', 'remove'}
_CONFIG_PROGS = {'npm', 'pnpm', 'yarn', 'pip', 'uv', 'poetry', 'bun'}
_ROUTE_ENV = re.compile(r'(?i)(?:npm_|pip_|uv_|pipx_|poetry_|pnpm_|corepack_|yarn_|bun_|xdg_|'
                        r'home(?:=|$)|virtual_env(?:=|$)|prefix(?:=|$)|destdir(?:=|$)|path(?:=|$))')


def routing_assignment(word):
    return bool(_ROUTE_ENV.match(word))


def _git_may_write(args, destination):
    """Read-only commands name config files as *inputs*. Unknown verbs (including aliases) may write them."""
    i = 0
    while i < len(args) and args[i].startswith('-'):
        opt, eq, _ = args[i].partition('=')
        if opt in _GIT_GLOBAL_FLAGS or (args[i][:2] in ('-C', '-c') and len(args[i]) > 2):
            i += 1
        elif opt in _GIT_GLOBAL_VALUES:
            i += 1 if eq else 2
        elif opt == '--exec-path' and eq:
            i += 1
        else:
            return True
    if i == len(args):
        return False
    if i > len(args):
        return True
    verb, tail = args[i], args[i+1:]
    read_subcommand = (verb == 'stash' and tail[:1] in (('list',), ('show',)) or
                       verb == 'worktree' and tail[:1] == ('list',))
    if verb in _GIT_READS or read_subcommand:
        for j, word in enumerate(tail):
            if word == '--':
                break  # The remaining words are pathspecs, not output options.
            if word.startswith('--output=') or word == '--output':
                path = word.partition('=')[2] if '=' in word else next(iter(tail[j+1:j+2]), '')
                # Global options such as -C may change where Git writes a relative path.
                destination(path if not i or path.startswith('/') else None)
                if not path or possible_config_path(path):
                    return True
        return False
    if verb == 'branch':
        if not tail:
            return False
        before_end = tail[:tail.index('--')] if '--' in tail else tail
        options = {word.partition('=')[0] for word in before_end if word.startswith('-')}
        # An option requiring a value can consume the apparent --list token.
        # Only bless that spelling when its argument is attached or unambiguous.
        for j, word in enumerate(before_end):
            if word in {'--sort', '--format', '--points-at'} and (
                    j+1 >= len(before_end) or before_end[j+1].startswith('-')):
                return True
        list_options = {'--list', '-a', '-r', '-v', '-vv', '--all', '--remotes', '--verbose', '--sort', '--format',
                        '--contains', '--no-contains', '--merged', '--no-merged', '--points-at', '--ignore-case',
                        '--color', '--no-color', '--column', '--no-column', '--omit-empty'}
        return not ('--list' in options and options <= list_options)
    if verb == 'remote':
        return tuple(tail) not in {(), ('-v',), ('--verbose',)}
    return True


def _file_outputs(tool, args):
    """Literal outputs for simple forms only; other writers may derive their targets."""
    if tool == 'dd':
        yield from (arg[3:] for arg in args if arg.startswith('of='))
        return
    # These switches take no operand and don't add alternate output names.
    switches = {'tee': 'ai', 'cp': 'afipPRrv', 'mv': 'finv', 'ln': 'fns', 'touch': 'acm', 'rm': 'dfirRv'}
    if tool not in switches:
        yield None  # e.g. sed/perl/patch can name destinations inside their program or input.
        return
    paths = []
    ended = False
    for arg in args:
        if not ended and arg == '--':
            ended = True
        elif not ended and arg.startswith('-') and arg != '-':
            if not all(c in switches[tool] for c in arg[1:]):
                yield None
                return
        else:
            paths.append(arg)
    if tool in {'cp', 'mv', 'ln'}:
        if len(paths) < 2:
            yield None  # ln, for instance, can derive the link name from its input.
        elif tool == 'mv':
            yield from paths  # Moving also removes the source.
        else:
            yield paths[-1]
    else:
        yield from paths


def writes_config(argv, redirects, destination):
    for _, op, token in redirects:
        if op in {'>', '>>', '>|', '&>', '&>>', '<>'} or op == '>&' and not re.fullmatch(r'\d+|-', token.text):
            destination(token.text)
            if possible_config_path(token.text):
                return True
    if not argv:
        return False
    tool = argv[0].rsplit('/', 1)[-1]
    args = argv[1:]
    if tool == 'git':
        return _git_may_write(args, destination)
    if tool in {'source', '.'} or tool == 'builtin' and args and args[0] in {'source', '.'}:
        return True
    if tool in _CONFIG_WRITERS:
        for output in _file_outputs(tool, args):
            destination(output)
        return any(possible_config_path(w) or (w.startswith('of=') and possible_config_path(w[3:])) for w in args)
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
