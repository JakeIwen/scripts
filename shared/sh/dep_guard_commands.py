"""Turn direct manager invocations into explicit, canonical requests. No resolver or network."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dep_guard_options import scan
from dep_guard_shell import simple_commands
from dep_guard_specs import NPM, PYPI, canonical_name, npm_spec, pypi_spec, with_requirements


@dataclass(frozen=True)
class Invocation:
    ecosystem: str
    tool: str
    name: str
    pin: Optional[str]
    cwd: Optional[Path]
    argv: tuple
    env: dict
    executable: str
    override: bool
    local: bool


_NODE = {'npm', 'npx', 'pnpm', 'pnpx', 'yarn', 'bun', 'bunx'}
_PYTHON = re.compile(r'python(?:[23](?:\.\d+)?)?t?')
_PIP = re.compile(r'pip(?:[23](?:\.\d+)?)?')
# Each mode describes which operands denote packages, not which ones a tool runs.
_VERBS = {
    'npm': {**dict.fromkeys('install i in isntall add install-test it'.split(), 'add'),
            'exec': 'npm-exec', 'x': 'npm-exec', 'create': 'create', 'init': 'create'},
    'pnpm': {'add': 'add', 'install': 'add', 'i': 'add', 'dlx': 'exec'},
    'yarn': {'add': 'add', 'global add': 'add', 'dlx': 'exec'},
    'bun': {'add': 'add', 'install': 'add', 'i': 'add', 'x': 'exec'},
    'pip': {'install': 'add'}, 'pipx': {'install': 'add', 'run': 'exec', 'inject': 'inject'},
    'poetry': {'add': 'add'}, 'uv': {'add': 'add', 'pip install': 'add', 'tool install': 'tool-add',
                                   'tool run': 'exec', 'run': 'with-only'},
}
_DIRECT = {'npx': 'npm', 'pnpx': 'pnpm', 'bunx': 'bun', 'uvx': 'uv'}


def _package_command(argv):
    executable = argv[0].rsplit('/', 1)[-1]
    start = 1
    if _PYTHON.fullmatch(executable):
        # -c, a script path, or -m some_other_module isn't the pip CLI.
        while start < len(argv) and re.fullmatch(r'-[BIOPqsSEuvb]+', argv[start]):
            start += 1
        if start < len(argv) and re.fullmatch(r'-[BIOPqsSEuvb]*mpip', argv[start]):
            return 'pip', start+1, None
        if (start+1 < len(argv) and re.fullmatch(r'-[BIOPqsSEuvb]*m', argv[start]) and
                argv[start+1] == 'pip'):
            return 'pip', start+2, None
        return None
    if _PIP.fullmatch(executable):
        return 'pip', start, None
    if executable in _DIRECT:
        return _DIRECT[executable], start, executable
    if executable in _VERBS:
        return executable, start, None
    return None


def _operation(argv, tool, start, direct):
    options, uncertain = [], False
    if direct:
        return 'exec', direct if direct in ('npx', 'bunx') else tool, start, options, uncertain
    first = scan(argv[start:], tool, first=True)
    if not first.operands:
        return None
    verb = first.operands[0]
    start += first.consumed
    options.extend(first.options)
    uncertain |= first.uncertain
    if (tool, verb) in {('uv', 'pip'), ('uv', 'tool'), ('yarn', 'global')}:
        second = scan(argv[start:], tool, first=True)
        if not second.operands:
            return None
        verb += ' ' + second.operands[0]
        start += second.consumed
        options.extend(second.options)
        uncertain |= second.uncertain
    mode = _VERBS[tool].get(verb)
    if not mode:
        return None
    style = {'dlx': 'yarn-dlx'}.get(verb, tool) if tool == 'yarn' else tool
    if tool == 'bun' and verb == 'x':
        style = 'bunx'
    return mode, style, start, options, uncertain


def _operands(argv, start, mode, style):
    args = argv[start:]
    if mode in ('npm-exec', 'create') and '--' in args:
        # npm parses its own options even after the first positional, until --.
        end = args.index('--')
        result = scan(args[:end], style)
        if not result.operands:
            result.operands.extend(args[end+1:end+2])
        result.consumed = min(end+2, len(args))
        return result
    return scan(args, style, first=mode in ('exec', 'with-only'))


def _requests(tool, style, mode, operands, options):
    with_specs = [value for key, value in options if key in ('--with', '--with-executables-from') and value]
    if tool == 'uv':
        for value in with_specs:
            for spec in with_requirements(value):
                yield spec, False
    if mode == 'with-only':
        return
    if mode in ('exec', 'npm-exec'):
        keys = {'uv': {'--from'}, 'pipx': {'--spec'}}.get(tool, {'--package'})
        if style in ('npx', 'yarn-dlx', 'bunx'):
            keys = keys | {'-p'}
        selected = [value for key, value in options if key in keys]
        if selected:
            for spec in selected:
                yield spec or '', False
        elif operands and not any(k in ('-c', '--call') for k, _ in options):
            global_mode = any(k == '--global' or k.startswith('-') and not k.startswith('--') and 'g' in k[1:]
                              for k, _ in options)
            yield operands[0], tool == 'npm' and not global_mode
        return
    if mode in ('tool-add', 'create'):
        operands = operands[:1]
    if mode == 'inject':
        operands = operands[1:]
    for i, spec in enumerate(operands):
        # Even a shell-split PEP 508 direct URL must not disclose the project
        # name. Properly quoted direct refs are filtered by the spec parser.
        if tool not in _NODE and operands[i+1:i+2] == ['@']:
            continue
        yield spec, False


def _local_bin(cwd, name):
    if cwd is None or '/' in name:
        return False
    try:
        return any((p / 'node_modules' / '.bin' / name).is_file() for p in (cwd, *cwd.parents))
    except (OSError, ValueError):
        return False


def parse_commands(command: str, cwd: Path):
    for simple in simple_commands(command, cwd):
        argv = simple.argv
        parsed = _package_command(argv)
        if not parsed:
            continue
        tool, start, direct = parsed
        op = _operation(argv, tool, start, direct)
        if not op:
            continue
        mode, style, start, options, uncertain = op
        result = _operands(argv, start, mode, style)
        options += result.options
        manager_argv = argv[:start+result.consumed]
        dynamic_route = any('$' in a or '`' in a for a in manager_argv)
        directory = None if uncertain or result.uncertain or dynamic_route else simple.cwd
        ecosystem = NPM if tool in _NODE else PYPI
        overrides = {canonical_name(ecosystem, n.strip()) for n in simple.marker.split(',')}
        for raw, maybe_local in _requests(tool, style, mode, result.operands, options):
            spec = (npm_spec(raw, initializer=mode == 'create') if ecosystem == NPM else
                    pypi_spec(raw, poetry=tool == 'poetry', tool_version=tool == 'uv' and mode in ('exec', 'tool-add')))
            if spec:
                local = maybe_local and raw == spec.name and _local_bin(directory, spec.name)
                yield Invocation(ecosystem, tool, spec.name, spec.pin, directory,
                                 manager_argv, simple.env, argv[0], spec.name in overrides, local)
