"""Where the shell's lexical command list cannot describe code or configuration in effect."""

import re

from dep_guard_forms import PYTHON
from destructive_guard_shell import BOUNDARIES

_GIT_READS = {'status', 'diff', 'log', 'show', 'grep', 'ls-files', 'rev-parse', 'blame'}
_GIT_GLOBAL_VALUES = {'-C', '--git-dir', '--work-tree', '--namespace', '--super-prefix'}
_GIT_GLOBAL_FLAGS = {'-P', '--no-pager', '--bare', '--no-replace-objects',
                     '--literal-pathspecs', '--glob-pathspecs', '--noglob-pathspecs', '--icase-pathspecs',
                     '--no-optional-locks', '--no-advice', '--no-lazy-fetch'}
_ROUTE_ENV = re.compile(r'(?i)(?:npm_|pip_|uv_|pipx_|poetry_|pnpm_|corepack_|yarn_|bun_|xdg_|'
                        r'home(?:=|$)|virtual_env(?:=|$)|prefix(?:=|$)|destdir(?:=|$)|path(?:=|$))')


def routing_assignment(word):
    return bool(_ROUTE_ENV.match(word))


def _git_may_write(args):
    """Read-only commands name config files as *inputs*. Unknown verbs (including aliases) may write them."""
    i = 0
    while i < len(args) and args[i].startswith('-'):
        opt, eq, _ = args[i].partition('=')
        if args[i].startswith('-c'):
            config = args[i][2:] or next(iter(args[i+1:i+2]), '')
            if not re.fullmatch(r'color\.ui=(?:true|false|auto|always|never)', config):
                return True
            i += 2 if args[i] == '-c' else 1
        elif opt in _GIT_GLOBAL_FLAGS or (args[i].startswith('-C') and len(args[i]) > 2):
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
                if not _output_sink(path):
                    return True
            elif word.startswith('-') and not re.fullmatch(
                    r'-[sbpU0-9]+|--(?:stat|short|branch|oneline|cached|staged|name-only|name-status|'
                    r'porcelain(?:=v[12])?|show-toplevel|abbrev-ref|verify|no-ext-diff|no-textconv)', word):
                return True  # Other options can write files or invoke external programs.
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


def _output_sink(path):
    # Compare literal names only: another path could be a link to a config
    # (or a config could link to it). A numeric filename is not an fd.
    return path in {'/dev/null', '/dev/stdout', '/dev/stderr'} or bool(re.fullmatch(r'/dev/fd/[0-9]+', path))


def redirects_may_write(redirects):
    for _, op, word in redirects:
        if op in {'<', '<&', '<<', '<<-', '<<<'}:
            continue
        if op == '>&' and re.fullmatch(r'[0-9]+-?|-', word.text):
            continue  # Duplicate, move or close a descriptor; do not open a file.
        if not _output_sink(word.text):
            return True
    return False


def _tee_only_sinks(args):
    # BSD and GNU can stop option parsing at different points. Only discard
    # switches before the first output; every remaining word must be a sink.
    i = 0
    while i < len(args) and args[i] in ('-a', '-i'):
        i += 1
    if args[i:i+1] == ('--',):
        i += 1
    return all(_output_sink(w) for w in args[i:])


def _options_known(args, switches):
    for arg in args:
        if arg.startswith('-') and arg not in ('-', '--') and not re.fullmatch(switches, arg):
            return False
    return True


# This is an allowlist of *predecessors*, not a list of package managers.
# Every other command could rewrite routing or the files used to prove a bin.
# Keep predicates narrow for commands with both read and write/execute forms.
_ROUTE_NEUTRAL = {
    **dict.fromkeys(('cd', 'pushd', 'ls', 'pwd', 'echo', 'cat', 'head', 'tail', 'wc', 'test', '[',
                     'true', 'which', 'type', 'sleep'), lambda a: True),
    'printf': lambda a: not a or not a[0].startswith('-v') and all('%n' not in w for w in a),
    'tee': _tee_only_sinks,
    'find': lambda a: all(not w.startswith('-') or w in {
        '-name', '-iname', '-path', '-ipath', '-type', '-maxdepth', '-mindepth', '-depth', '-print',
        '-print0', '-prune', '-xdev', '-o', '-a', '-not', '-empty', '-mtime', '-size', '-L', '-P', '-H'} for w in a),
    'rg': lambda a: _options_known(a, r'-[inuclwFvheS]+|--(?:files|hidden|no-heading|line-number|fixed-strings)'),
    'grep': lambda a: _options_known(a, r'-[iRrnclwFvheEH]+|--(?:line-number|fixed-strings|recursive)'),
    'git': lambda a: not _git_may_write(a),
    'command': lambda a: a[:1] in (('-v',), ('-V',)),
    'env': lambda a: a in ((), ('-0',), ('--null',)),
    'date': lambda a: all(w in ('-u', '-R') or w.startswith('+') for w in a),
    **dict.fromkeys(('node', 'npm', 'python'), lambda a: a == ('--version',)),
}


def neutral_predecessor(words, functions):
    if not words:
        return True  # A redirect-only segment has already been inspected.
    if words[:1] == ('rtk',) and 'rtk' not in functions:
        words = words[2:] if words[1:2] == ('proxy',) else words[1:]
    if not words or words[0] in functions:
        return False
    tool = words[0]
    if tool.startswith(('/bin/', '/usr/bin/')):
        tool = tool.removeprefix('/usr').removeprefix('/bin/')
    if PYTHON.fullmatch(tool):
        tool = 'python'
    if any('$' in w or '`' in w or w.startswith(('*', '?', '[', '{', '~', '=')) for w in words[1:]):
        return False  # Expansion could supply an option or execute code, even to an otherwise safe reader.
    check = _ROUTE_NEUTRAL.get(tool)
    return check is not None and check(words[1:])


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
