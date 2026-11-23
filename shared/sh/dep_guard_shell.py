"""Simple commands and their literal execution contexts, built only from the shared lexer."""

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from dep_guard_config_paths import ConfigWrites
from dep_guard_shell_context import executable_tokens, neutral_predecessor, redirects_change_config, routing_assignment
from destructive_guard_shell import command_segments, parse_script, MAX_DEPTH

_ASSIGNMENT = re.compile(r'([A-Za-z_][A-Za-z_0-9]*)=(.*)', re.S)
_CONTROL = {'if', 'then', 'elif', 'else', 'while', 'until', 'do', '{', '!'}
_SHELLS = {'sh', 'bash', 'zsh'}
_MARKER = 'DEP_GUARD_ALLOW'
_CHANGE_ENV = {'HOME', 'XDG_CONFIG_HOME', 'CDPATH', 'PATH', 'ZDOTDIR'}
_SHELL_STARTUP_ENV = {'BASH_ENV', 'ENV'}
_WRAPPERS = {'sudo', 'env', 'command', 'nice', 'nohup', 'time', 'timeout', 'exec', 'rtk', 'corepack'}
_WRAPPER_VALUES = {
    'sudo': {'-u', '--user', '-g', '--group', '-h', '--host', '-p', '--prompt', '-C', '--close-from',
             '-D', '--chdir', '-R', '--chroot', '-r', '--role', '-t', '--type'},
    'env': {'-u', '--unset', '-C', '--chdir'}, 'nice': {'-n', '--adjustment'},
    'time': {'-f', '--format', '-o', '--output'}, 'timeout': {'-s', '--signal', '-k', '--kill-after'},
    'exec': {'-a'}, 'corepack': {'--install-directory'},
}
_WRAPPER_UNCERTAIN = {'sudo': {'-u', '--user', '-g', '--group', '-H', '--set-home', '-i', '--login',
                                '-s', '--shell', '-D', '--chdir', '-R', '--chroot', '-h', '--host'},
                      'env': {'-', '-i', '--ignore-environment', '-C', '--chdir', '-u', '--unset'},
                      'command': {'-p'}, 'exec': {'-c', '-l'}, 'corepack': {'--install-directory'}}


@dataclass(frozen=True)
class Simple:
    argv: tuple
    cwd: Optional[Path]
    env: dict
    marker: str
    writes: frozenset = frozenset()


def _assign(word, env):
    match = _ASSIGNMENT.fullmatch(word)
    if match:
        env[match[1]] = match[2]
    return match is not None


def _wrapper(args, i, tool):
    values = _WRAPPER_VALUES.get(tool, ())
    uncertain = _WRAPPER_UNCERTAIN.get(tool, ())
    changed = tool == 'sudo'  # Even without -u, sudo selects the default target user.
    while i < len(args) and args[i].startswith('-'):
        raw = args[i]
        i += 1
        if raw == '--':
            break
        key, eq, _ = raw.partition('=')
        attached = False
        if raw.startswith('-') and not raw.startswith('--'):
            for pos, ch in enumerate(raw[1:], 1):
                if '-' + ch in values:
                    key = '-' + ch
                    attached = pos + 1 < len(raw)
                    break
        changed |= key in uncertain or (key.startswith('-') and not key.startswith('--') and
                                        any('-'+flag in uncertain for flag in key[1:]))
        if (tool == 'command' and key in ('-v', '-V') or tool == 'env' and key in ('-S', '--split-string')
                or tool == 'sudo' and key in ('-V', '-l', '--list', '-e', '--edit')):
            return len(args), True
        if key in values and not eq and not attached:
            i += 1
    if tool == 'timeout':
        i += 1  # DURATION, not the command.
    if tool == 'rtk' and args[i:i+1] == ('proxy',):
        i += 1
    return i, changed


def _unwrap(args, cwd, inherited):
    env = inherited.copy()
    i = 0
    while i < len(args):
        if _assign(args[i], env) or args[i] in _CONTROL:
            i += 1
            continue
        tool = args[i].rsplit('/', 1)[-1]
        if tool not in _WRAPPERS:
            break
        i, changed = _wrapper(args, i+1, tool)
        if changed:
            cwd = None
            env.pop(_MARKER, None)
        if tool == 'corepack' and args[i:i+1] not in {('pnpm',), ('yarn',)}:
            return Simple((), None, {}, '')
    if env.keys() & _CHANGE_ENV:
        cwd = None
    return Simple(args[i:], cwd, env, env.get(_MARKER, ''))


def _shell_bodies(args, redirects):
    flags = set()
    for i, option in enumerate(args[1:], 2):
        if option in ('--login', '--interactive', '--noexec'):
            flags.add({'--login': 'l', '--interactive': 'i', '--noexec': 'n'}[option])
        elif re.fullmatch(r'-[a-zA-Z]+', option):
            flags.update(option[1:])
        else:
            break
        if 'n' in flags:
            return
        if 'c' in flags:
            if i < len(args):
                yield args[i], bool(flags & {'i', 'l'})
            return
    else:
        # Here-docs elsewhere are data, not shell commands. A login shell
        # explicitly reading stdin executes its content after loading startup files.
        if flags & {'i', 'l'}:
            for fd, op, word in redirects:
                if fd == 0 and op in {'<<', '<<-'} and word.heredoc:
                    yield word.heredoc.script(), True
                elif fd == 0 and op == '<<<':
                    yield word.text, True


def _cd(args, cwd, hook_env):
    if args[:1] == ('--',):
        args = args[1:]
    if (cwd is None or len(args) != 1 or not args[0] or args[0].startswith(('-', '+', '~', '=')) or
            any(ch in args[0] for ch in '$`*?[]{}\x00')):
        return None
    target = Path(args[0])
    if not target.is_absolute():
        if cwd is None or hook_env.get('CDPATH') and not args[0].startswith('.'):
            return None
        target = cwd / target
    try:
        logical = Path(os.path.normpath(target))
        if target.is_dir() and os.access(target, os.X_OK) and target.resolve(strict=True) == logical.resolve(strict=True):
            return logical  # Source routing checks both the logical and physical ancestors.
        return None
    except (OSError, ValueError, RuntimeError):
        return None


def _cd_after(words, cwd, preceding, following, complex_list, hook_env):
    """Only assert a new location for an unconditional, successful literal cd."""
    clean = tuple(words)
    controlled = False
    while clean and (clean[0] in _CONTROL or clean[0] in {'builtin', 'command'}):
        controlled |= clean[0] in _CONTROL
        clean = clean[1:]
        if clean[:1] == ('--',):
            clean = clean[1:]
        elif clean[:1] == ('-p',):
            clean = clean[1:]
    if clean[:1] in {('popd',), ('source',), ('.',), ('eval',), ('typeset',), ('declare',),
                      ('alias',), ('function',), ('read',), ('readarray',), ('local',)}:
        return None
    if 'cd' not in clean and not any(w in ('pushd', 'popd') for w in clean):
        return cwd
    if not clean or clean[0] not in ('cd', 'pushd'):
        # A conditional/control prefix might execute cd; a program argument cannot.
        return None if clean and (clean[0] in _CONTROL or _ASSIGNMENT.fullmatch(clean[0])) else cwd
    if controlled or complex_list or preceding in ('&&', '||', '|', '|&') or following in ('||', '|', '|&', '&'):
        return None
    return _cd(clean[1:], cwd, hook_env)


def _segments(tokens, source, cwd, inherited, depth, mutations, functions, hook_env):
    if depth > MAX_DEPTH:
        return
    # The lexical reader deliberately flattens control flow. Do not infer a cd
    # from inside one branch/group for a command that could run outside it.
    tokens = executable_tokens(tokens, source, functions)
    complex_list = any(t.operator and t.text in {'(', ')', '||', '|', '|&', '&'} or
                       not t.operator and t.text in {'if', 'while', 'until', 'for', 'case', '{'} for t in tokens)
    persistent = inherited.copy()
    previous = None
    for segment in command_segments(tokens):
        args = tuple(t.text for t in segment.words)
        for nested in segment.substitutions:
            yield from _segments(nested, source, cwd, persistent, depth+1, mutations, functions.copy(), hook_env)
        invocation = _unwrap(args, cwd, persistent)
        # Redirections take effect before the program starts, but the program's
        # other effects invalidate only *later* proofs, not its own install.
        def record(word, *, parents=False):
            mutations.record(word, invocation.cwd, parents=parents)

        if not mutations.config:
            if redirects_change_config(segment.redirects, record) or (invocation.argv and invocation.argv[0] in functions):
                mutations.config = True
        if mutations.config:
            cwd = None
            invocation = replace(invocation, cwd=None)
        elif mutations.destinations:
            invocation = replace(invocation, writes=frozenset(mutations.destinations))
        shell = invocation.argv[0].rsplit('/', 1)[-1] if invocation.argv else ''
        if shell in _SHELLS:
            for body, startup in _shell_bodies(invocation.argv, segment.redirects):
                if depth < MAX_DEPTH:
                    child_env = {k: v for k, v in invocation.env.items() if k != _MARKER}
                    if startup or shell == 'zsh' or _SHELL_STARTUP_ENV & (hook_env.keys() | child_env.keys()):
                        # Startup files can change the child route or write a config
                        # that a later command in the parent shell will discover.
                        mutations.config = True
                    child_cwd = None if mutations.config else invocation.cwd
                    yield from _segments(parse_script(body, depth+1), body, child_cwd, child_env,
                                         depth+1, mutations, functions.copy(), hook_env)
        elif invocation.argv:
            yield invocation
        if not mutations.config and not neutral_predecessor(args, record, functions):
            mutations.config = True
        # Earlier exports can select a private index, but cannot authorize an
        # override. Conditional exports/unsets cannot positively establish a route.
        state = list(args)
        while state and state[0] in _CONTROL | {'builtin', 'command', '--'}:
            state.pop(0)
        changed = state[1:] if state[:1] in (['export'], ['unset']) else state
        if changed and (state[:1] in (['export'], ['unset']) or all(_ASSIGNMENT.fullmatch(w) for w in state)):
            for word in changed:
                if not _assign(word, persistent):
                    cwd = None
            persistent.pop(_MARKER, None)
            if any(routing_assignment(w) for w in changed) or complex_list or previous in ('&&', '||'):
                cwd = None
        cwd = _cd_after(args, cwd, previous, segment.boundary, complex_list, hook_env)
        previous = segment.boundary


def simple_commands(command, cwd, *, env=None):
    return _segments(parse_script(command), command, cwd, {}, 0, ConfigWrites(), set(), os.environ if env is None else env)
