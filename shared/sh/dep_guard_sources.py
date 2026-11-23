"""Decide whether it is OK to disclose one install operand to a public registry.

This is not a package-manager config resolver: an ambiguous, unreadable or
conflicting source is reason to abstain, never to try the name on the network.
No config content, environment values, or exception messages leave this module.
"""

import configparser
import os
from pathlib import Path
import re
import shutil
import stat
from typing import Mapping

from dep_guard_specs import NPM, PYPI


_ETC = Path('/etc')
_MAC_LIBRARY = Path('/Library')
_PIP_SHARE_DIRS = (Path('/usr/share'), Path('/usr/local/share'))
_MAX_CONFIG = 256 * 1024
_PUBLIC = {
    NPM: re.compile(r'https://registry\.npmjs\.org(?::443)?/?', re.I | re.ASCII),
    PYPI: re.compile(r'https://(?:pypi\.org|files\.pythonhosted\.org)(?::443)?(?:/simple)?/?', re.I | re.ASCII),
}
_INDEX = {'index-url', 'default-index'}
_OTHER_INDEX = {'index', 'extra-index-url', 'find-links', 'no-index', 'offline', 'source',
                'requirement', 'requirements', 'constraint', 'constraints', 'build-constraint', 'override',
                'index-location', 'with-requirements'}
# npm's `python` selects node-gyp's build interpreter, not a package index.
_NPM_REDIRECT = {'config', 'userconfig', 'globalconfig', 'config-file', 'use-yarnrc', 'rc-file',
                 'cwd', 'directory', 'project', 'prefix', 'local-prefix', 'global-dir', 'dir',
                 'lockfile-dir', 'workspace-dir', 'workspace', 'workspace-root', 'filter', 'env-file', 'scope'}
_REDIRECT = _NPM_REDIRECT | {'python', 'pip-args', 'isolated', 'pipfile', 'use-pip', 'use-uv'}
_PIP_ENV_OPTIONS = {'no-index', 'find-links', 'requirement', 'constraint', 'build-constraint',
                    'python', 'config-file', 'extra-index-url'}
# No dependency on a TOML or YAML parser at the system Python 3.9 floor. Look
# for routing keys, including quoted, dotted and inline-table spellings. More
# elaborate key encodings must abstain rather than silently hide such a key.
_JS_ROUTING = {
    'yarn': re.compile(r'npmregistryserver|npmscopes|npmregistries|(?:^|[^\w])registry|'
                       r'yarn[-_]?path|offline|networksettings|enablenetwork|injectenvironmentfiles|plugins', re.I),
    'bun': re.compile(r'registry|scopes|offline|prefer\s*[\"\']?\s*=\s*[\"\']offline', re.I),
    'pnpm': re.compile(r'registr(?:y|ies)|offline|configdependencies|pnpmfile|catalog', re.I),
}
_UV_ROUTING = re.compile(r'(?<![\w-])(?:index(?:-url|-strategy)?|default-index|extra-index-url|'
                         r'find-links|sources|no-index|offline|environments-file)(?![\w-])', re.I)
_POETRY_ROUTING = re.compile(r'(?<![\w-])(?:source|sources|repositories)(?![\w-])', re.I)


def _content(path):
    """Absent = ordinary default. Oversized or special = indeterminate.

    O_NONBLOCK prevents a project-controlled FIFO from hanging the hook;
    fstat checks the descriptor actually opened rather than a pre-open guess.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except FileNotFoundError:
        if path.is_symlink():
            raise ValueError('dangling config') from None
        return ''
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_CONFIG:
            raise ValueError('unreadable config')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(_MAX_CONFIG + 1)
        if len(data) > _MAX_CONFIG or b'\0' in data:
            raise ValueError('unreadable config')
        return data.decode('utf-8-sig')
    finally:
        os.close(fd)


def _explicit_public(ecosystem, value):
    return bool(_PUBLIC[ecosystem].fullmatch(value.strip()))


def _registry_key(key, name):
    scope = name.lower().replace('_', '-').split('/', 1)[0]
    return key == 'registry' or (scope.startswith('@') and key == scope + ':registry')


def _npmrc(text, name, indirect):
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(('#', ';')):
            continue
        key, _, value = line.partition('=')
        key = key.strip().lower().replace('_', '-')
        if any(char in key for char in '\\$\"\'[]'):
            return False  # Quoted/dynamic keys need npm's full INI dialect.
        if key in {'prefix', 'globalconfig', 'userconfig'}:
            # Distribution npmrc files (notably Debian's) set a literal prefix
            # and /etc/npmrc. Inspect that path too, but never expand variables.
            if not re.fullmatch(r'/[A-Za-z0-9_./+-]+', value.strip()):
                return False
            path = _absolute(value.strip())
            indirect.add(path / 'etc/npmrc' if key == 'prefix' else path)
            continue
        if key in _NPM_REDIRECT or key in {'offline', 'prefer-offline'}:
            return False
        if _registry_key(key, name) and not _explicit_public(NPM, value):
            return False
    return True


def _pip_conf(text):
    if not text.strip():
        return True
    conf = configparser.RawConfigParser()
    conf.read_string(text)
    return all(key.replace('_', '-') not in _PIP_ENV_OPTIONS and
               (key.replace('_', '-') not in _INDEX or _explicit_public(PYPI, value))
               for section in conf for key, value in conf[section].items())


def _no_markers(text, pattern):
    for line in text.splitlines():
        if line.lstrip().startswith('#'):
            continue
        if pattern.search(line) or '\\' in line or '!!' in line:
            return False
    return True


def _cli_ok(inv):
    args = inv.argv
    for i, word in enumerate(args[1:], 2):
        if word == '--':
            break
        if word.startswith('--'):
            key, sep, value = word[2:].partition('=')
            key = key.lower().replace('_', '-')
            if (key in _REDIRECT or 'config' in key or key.startswith('use-yarnrc') or
                    key in _OTHER_INDEX or key in {'prefer-offline', 'no-network', 'no-registry'}):
                return False
            if _registry_key(key, inv.name) or key in _INDEX:
                if not _explicit_public(inv.ecosystem, value if sep else (args[i] if i < len(args) else '')):
                    return False
        elif word.startswith('-') and inv.ecosystem == PYPI:
            if re.match(r'^-[vq]*(?:[rcf]|[CP])', word) or (inv.tool == 'uv' and word.startswith('-p')):
                return False
            match = re.match(r'^-[vq]*i(.*)$', word)
            if match and not _explicit_public(PYPI, match[1] or (args[i] if i < len(args) else '')):
                return False
        elif word.startswith(('-C', '-w')) or (inv.tool == 'pnpm' and word.startswith('-F')):
            return False
    return True


def _env_ok(inv, env):
    for key, value in env.items():
        key = key.lower().replace('_', '-')
        if inv.ecosystem == NPM:
            if key.startswith('npm-config-'):
                option = key[len('npm-config-'):]
                if option in _NPM_REDIRECT or option in {'offline', 'prefer-offline'}:
                    return False
                if _registry_key(option, inv.name) and not _explicit_public(NPM, value):
                    return False
            if key in {'bun-config-registry', 'bun-config-registry-url', 'yarn-registry',
                       'yarn-npm-registry-server', 'corepack-npm-registry'} and not _explicit_public(NPM, value):
                return False
            if (key in {'prefix', 'destdir'} or key.startswith(('yarn-npm-scopes', 'yarn-npm-registries', 'pnpm-config-')) or
                    key in {'yarn-rc-filename', 'yarn-offline', 'yarn-enable-network', 'yarn-yarn-path',
                            'bun-config', 'bunfig', 'bun-install-registry', 'npm-config-local-prefix'}):
                return False
        else:
            if key.startswith(('pip-', 'uv-')):
                option = key.split('-', 1)[1]
                if option in _INDEX:
                    if not _explicit_public(PYPI, value):
                        return False
                elif (option in _OTHER_INDEX | _PIP_ENV_OPTIONS | _REDIRECT or
                      option.startswith('index-') or option in {'project-environment', 'working-directory'}):
                    return False
            if (key.startswith(('poetry-repositories-', 'poetry-source-', 'pipx-default-backend')) or
                    key in {'pipx-default-python', 'pipx-use-uv'}):
                return False
    return True


def _tool_paths(inv, env, names):
    # Preserve the lexical bin directory: resolving a venv's python symlink
    # first would inspect the system prefix's pip.conf instead of the venv's.
    path = env.get('PATH', os.defpath)
    if '$' in path or '~' in path:
        raise ValueError('dynamic path')
    search = os.pathsep.join(str(inv.cwd / entry) for entry in path.split(os.pathsep))
    for name in {inv.executable, *names}:
        name = str(name)
        if '/' in name:
            yield inv.cwd / name
        else:
            found = shutil.which(name, path=search)
            if found:
                yield Path(found)


def _roots(cwd):
    # A symlinked project may be reached using logical or physical parents.
    physical = cwd.resolve(strict=True)
    return {cwd, *cwd.parents, physical, *physical.parents}


def _present(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _js_files(inv, env, home, roots, xdg):
    npmrc = {r / '.npmrc' for r in roots | {home}}
    npmrc.add(_ETC / 'npmrc')
    yarnrc = {_ETC / 'yarnrc', xdg / 'yarn/config'}
    if inv.tool == 'pnpm':
        npmrc.update({xdg / 'pnpm/rc', home / 'Library/Preferences/pnpm/rc',
                      home / 'Library/pnpm/config/rc', home / '.config/pnpm/rc'})
    for exe in _tool_paths(inv, env, ('npm', 'node')):
        npmrc.add(exe.parent.parent / 'etc/npmrc')
        yarnrc.add(exe.parent.parent / 'etc/yarnrc')
        target = exe.resolve()
        if target.name == 'npm-cli.js':
            npmrc.add(target.parent.parent / 'npmrc')  # npm's built-in config
        elif target.parent.name == 'bin':
            npmrc.add(target.parent.parent / 'etc/npmrc')
    seen = set()
    while npmrc:
        p = npmrc.pop()
        if p in seen:
            continue
        seen.add(p)
        if len(seen) > 128 or not _npmrc(_content(p), inv.name, npmrc):
            return False
    tool = inv.tool
    if tool == 'yarn':
        files = yarnrc | {r / f for r in roots | {home} for f in ('.yarnrc.yml', '.yarnrc')}
    elif tool == 'bun':
        # Bun loads dotenv before resolving install settings. Do not inspect a
        # general-purpose secrets file just to try to prove this name is public.
        dotenv = ('.env', '.env.local', '.env.development', '.env.production', '.env.test')
        if any(_present(r / f) for r in roots for f in dotenv):
            return False
        files = [*(r / 'bunfig.toml' for r in roots), home / '.bunfig.toml', xdg / '.bunfig.toml']
    elif tool == 'pnpm':
        files = [*(r / 'pnpm-workspace.yaml' for r in roots), xdg / 'pnpm/config.yaml',
                 home / 'Library/Preferences/pnpm/config.yaml']
    else:
        return True
    return all(_no_markers(_content(p), _JS_ROUTING[tool]) for p in files)


def _python_files(inv, env, home, roots, xdg):
    xdg_dirs = [Path(p) for p in env.get('XDG_CONFIG_DIRS', str(_ETC / 'xdg')).split(os.pathsep)]
    if any(not p.is_absolute() for p in xdg_dirs):
        return False
    if {'XDG_DATA_HOME', 'XDG_DATA_DIRS'} & env.keys():
        return False  # Different pip/platformdirs releases resolve data roots differently.
    pip_files = {_ETC / 'pip.conf', _ETC / 'xdg/pip/pip.conf', home / '.local/share/pip/pip.conf',
                 home / '.pip/pip.conf', home / '.config/pip/pip.conf', xdg / 'pip/pip.conf',
                 _MAC_LIBRARY / 'Application Support/pip/pip.conf', home / 'Library/Application Support/pip/pip.conf'}
    pip_files.update(p / 'pip/pip.conf' for p in [*xdg_dirs, *_PIP_SHARE_DIRS])
    pip_mode = inv.tool == 'pip' or (inv.tool == 'uv' and 'pip' in inv.argv)
    interpreters = ('pip', 'python3', 'python') if inv.tool == 'uv' and pip_mode else ()
    for exe in _tool_paths(inv, env, interpreters):
        for candidate in {exe, exe.resolve()}:
            cfg = candidate.parent.parent / 'pip.conf'
            if pip_mode and _present(cfg):
                return False  # A selected interpreter has site-specific configuration.
            pip_files.add(cfg)
    if inv.tool == 'uv' and pip_mode and any(_present(r / '.venv/pip.conf') for r in roots):
        return False
    if 'VIRTUAL_ENV' in env:
        pip_files.add(_absolute(env['VIRTUAL_ENV']) / 'pip.conf')
    if not all(_pip_conf(_content(p)) for p in pip_files):
        return False
    if inv.tool in ('uv', 'uvx', 'pipx'):
        files = [*(r / f for r in roots for f in ('uv.toml', 'pyproject.toml')),
                 xdg / 'uv/uv.toml', home / '.config/uv/uv.toml', _ETC / 'uv/uv.toml']
        if not all(_no_markers(_content(p), _UV_ROUTING) for p in files):
            return False
    if inv.tool == 'poetry':
        return all(_no_markers(_content(r / f), _POETRY_ROUTING)
                   for r in roots for f in ('pyproject.toml', 'poetry.toml'))
    return True


def _absolute(value):
    p = Path(value)
    if not p.is_absolute() or '$' in value:
        raise ValueError('indeterminate path')
    return p


def public_source(inv, env: Mapping[str, str], home: Path) -> bool:
    """True only when this operand permits public lookups; never runs a manager."""
    try:
        if inv.ecosystem not in _PUBLIC or inv.cwd is None or not inv.cwd.is_absolute() or not inv.cwd.is_dir():
            return False
        if {'HOME', 'XDG_CONFIG_HOME'} & inv.env.keys():
            return False
        effective = dict(env, **inv.env)
        if (not home.is_absolute() or ('HOME' in effective and _absolute(effective['HOME']) != home) or
                not _cli_ok(inv) or not _env_ok(inv, effective)):
            return False
        xdg = _absolute(effective['XDG_CONFIG_HOME']) if 'XDG_CONFIG_HOME' in effective else home / '.config'
        checker = _js_files if inv.ecosystem == NPM else _python_files
        return checker(inv, effective, home, _roots(inv.cwd), xdg)
    except (OSError, ValueError, RuntimeError, configparser.Error):
        return False
