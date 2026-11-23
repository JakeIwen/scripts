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
import sys
from typing import Mapping

from dep_guard_config_paths import BUN_DOTENV, NAMES
from dep_guard_specs import NPM, PYPI


_ETC = Path('/etc')
_MAC_LIBRARY = Path('/Library')
_SYSTEM_BIN = Path('/usr/bin')
_XCODE_SELECTION = Path('/var/db/xcode_select_link')
_DARWIN = sys.platform == 'darwin'
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


def read_source_text(path):
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


def _npmrc(text, name, indirect, prefixes, modes=None):
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(('#', ';')):
            continue
        key, _, value = line.partition('=')
        key = key.strip().lower().replace('_', '-')
        if any(char in key for char in '\\$\"\'[]'):
            return False  # Quoted/dynamic keys need npm's full INI dialect.
        if modes is not None and key in {'global', 'location'}:
            modes.setdefault(key, set()).add(value.strip() if '=' in line else 'true')
        if key in {'prefix', 'globalconfig', 'userconfig'}:
            # Distribution npmrc files (notably Debian's) set a literal prefix
            # and /etc/npmrc. Inspect that path too, but never expand variables.
            if not re.fullmatch(r'/[A-Za-z0-9_./+-]+', value.strip()):
                return False
            path = _absolute(value.strip())
            if key == 'prefix':
                prefixes.add(path)
                path = path / 'etc' / NAMES.npm_system
            indirect.add(path)
            continue
        if key in _NPM_REDIRECT:
            return False
        # Selecting an existing executable needs npm's mode and prefix, not
        # permission to disclose any name. Public routing checks run separately.
        if modes is None and (key in {'offline', 'prefer-offline'} or
                              _registry_key(key, name) and not _explicit_public(NPM, value)):
            return False
    return True


def _pip_conf(text):
    if not text.strip():
        return True
    conf = configparser.RawConfigParser()
    conf.read_string(text)
    for section in conf:
        for raw, value in conf[section].items():
            key = raw.replace('_', '-').removeprefix('--')  # pip's _normalize_name
            if key in _PIP_ENV_OPTIONS or (key in _INDEX and not _explicit_public(PYPI, value)):
                return False
    return True


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


def _npm_files(inv, env, home, roots, xdg, modes=None):
    prefixes = set()
    npmrc = {r / NAMES.npm_project for r in roots | {home}}
    npmrc.add(_ETC / NAMES.npm_system)
    yarnrc = {_ETC / NAMES.yarn_system, xdg / 'yarn' / NAMES.yarn_xdg}
    if inv.tool == 'pnpm':
        npmrc.update({xdg / 'pnpm' / NAMES.pnpm_rc, home / 'Library/Preferences/pnpm' / NAMES.pnpm_rc,
                      home / 'Library/pnpm/config' / NAMES.pnpm_rc, home / '.config/pnpm' / NAMES.pnpm_rc})
    for exe in _tool_paths(inv, env, ('npm', 'node')):
        npmrc.add(exe.parent.parent / 'etc' / NAMES.npm_system)
        yarnrc.add(exe.parent.parent / 'etc' / NAMES.yarn_system)
        target = exe.resolve()
        if target.name == 'npm-cli.js':
            npmrc.add(target.parent.parent / NAMES.npm_system)  # npm's built-in config
        elif target.parent.name == 'bin':
            npmrc.add(target.parent.parent / 'etc' / NAMES.npm_system)
    seen = set()
    while npmrc:
        p = npmrc.pop()
        if p in seen:
            continue
        seen.add(p)
        if len(seen) > 128 or not _npmrc(read_source_text(p), inv.name, npmrc, prefixes, modes):
            raise ValueError('indeterminate npm configuration')
    return prefixes, yarnrc


def npm_config_prefixes(inv, env, home, *, modes=None, global_only=False):
    """Possible literal npmrc prefixes for implicit local bins. Unproved routes raise."""
    effective = dict(env, **inv.env)
    xdg = _absolute(effective['XDG_CONFIG_HOME']) if 'XDG_CONFIG_HOME' in effective else home / '.config'
    roots = set() if global_only else _roots(inv.cwd)
    prefixes, _ = _npm_files(inv, effective, home, roots, xdg, modes)
    return prefixes


def _js_files(inv, env, home, roots, xdg):
    _, yarnrc = _npm_files(inv, env, home, roots, xdg)
    tool = inv.tool
    if tool == 'yarn':
        files = yarnrc | {r / f for r in roots | {home} for f in (NAMES.yarn_modern, NAMES.yarn_classic)}
    elif tool == 'bun':
        # Bun loads dotenv before resolving install settings. Do not inspect a
        # general-purpose secrets file just to try to prove this name is public.
        if any(_present(r / f) for r in roots for f in BUN_DOTENV):
            return False
        files = [*(r / NAMES.bun_project for r in roots), home / NAMES.bun_user, xdg / NAMES.bun_user]
    elif tool == 'pnpm':
        files = [*(r / NAMES.pnpm_workspace for r in roots), xdg / 'pnpm' / NAMES.pnpm_yaml,
                 home / 'Library/Preferences/pnpm' / NAMES.pnpm_yaml]
    else:
        return True
    return all(_no_markers(read_source_text(p), _JS_ROUTING[tool]) for p in files)


def _apple_python_prefix(env):
    # /usr/bin/python3 and pip3 are xcrun launchers, not the interpreter.
    # Resolve the selected developer tree without invoking xcrun (which can
    # prompt to install developer tools) or executing a project interpreter.
    developer = (_absolute(env['DEVELOPER_DIR']) if 'DEVELOPER_DIR' in env else _XCODE_SELECTION).resolve(strict=True)
    if developer.suffix == '.app':
        developer /= 'Contents/Developer'
    if developer != _MAC_LIBRARY / 'Developer/CommandLineTools' and not (
            developer.name == 'Developer' and developer.parent.name == 'Contents'
            and developer.parent.parent.suffix == '.app'):
        raise ValueError('unknown developer tree')
    python = (developer / 'usr/bin/python3').resolve(strict=True)
    prefix = python.parent.parent
    versions = developer / 'Library/Frameworks/Python3.framework/Versions'
    if (prefix.parent != versions or not re.fullmatch(r'3\.\d+', prefix.name)
            or python.parent.name != 'bin' or not python.is_file()):
        raise ValueError('unknown Apple interpreter layout')
    return prefix


def _known_prefix(executable, env):
    """Only trusted system launchers with a known prefix; never infer it from a shim's shebang."""
    actual = executable.resolve(strict=True)
    system = _SYSTEM_BIN.resolve(strict=True)
    if not actual.is_file() or not os.access(actual, os.X_OK):
        raise ValueError('unresolved interpreter')
    if _DARWIN:
        prefix = _apple_python_prefix(env)
        if (actual in {system / 'python3', system / 'pip3'} or
                (actual.parent == prefix / 'bin' and re.fullmatch(r'python3(?:\.\d+)?', actual.name))):
            return prefix
    elif actual.parent == system:
        if re.fullmatch(r'pip3(?:\.\d+)?', actual.name):
            # Distribution pip launchers are scripts. A different shebang can
            # select a different prefix even when pip itself is in /usr/bin.
            line = read_source_text(actual).partition('\n')[0]
            target = Path(line[2:]) if line.startswith('#!') else None
            if target is None or target.parent != system:
                raise ValueError('unproved pip interpreter')
            actual = target.resolve(strict=True)
        if actual.parent == system and re.fullmatch(r'python3(?:\.\d+)?', actual.name):
            return system.parent
    raise ValueError('unproved launcher prefix')


def _pipx_target(inv, env, home):
    # The parser excludes --global targets and identifies the environment after
    # manager options. A name we cannot map to one literal directory is private.
    if not inv.inject_env or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', inv.inject_env):
        raise ValueError('unproved pipx target')
    bases = ([_absolute(env['PIPX_HOME'])] if 'PIPX_HOME' in env else
             [home / '.local/pipx', home / '.local/share/pipx', home / 'Library/Application Support/pipx'])
    existing = [p for p in bases if _present(p)]
    if len(existing) != 1:
        raise ValueError('unproved pipx home')
    return existing[0] / 'venvs' / inv.inject_env


def _uv_venv(inv, env, roots):
    if '--system' in inv.argv or 'UV_SYSTEM_PYTHON' in env or 'CONDA_PREFIX' in env:
        raise ValueError('unproved uv environment')
    candidates = {(r / '.venv').resolve(strict=True) for r in roots if _present(r / '.venv')}
    if 'VIRTUAL_ENV' in env:
        candidates.add(_absolute(env['VIRTUAL_ENV']).resolve(strict=True))
    if len(candidates) != 1:
        raise ValueError('unproved uv environment')
    return candidates.pop()


def _pip_prefixes_ok(inv, env, home, roots):
    uv_pip = inv.operation == 'uv pip install'
    inject = inv.operation == 'pipx inject'
    if inv.tool != 'pip' and not uv_pip and not inject:
        return True
    if {'PYTHONHOME', 'PYTHONPATH', '__PYVENV_LAUNCHER__', 'PYTHONEXECUTABLE',
            'SDKROOT', 'TOOLCHAINS', 'XCRUN_TOOLCHAIN_NAME'} & env.keys():
        return False
    if inv.tool == 'pip':
        candidates = list(_tool_paths(inv, env, ()))
    else:
        # uv without an identified venv can select a managed interpreter that
        # has no connection to the uv executable's own prefix or to PATH.
        venv = _pipx_target(inv, env, home) if inject else _uv_venv(inv, env, roots)
        if not (venv / NAMES.venv).is_file():
            return False
        candidates = [venv / 'bin/python']
    for exe in candidates:
        # A venv's python can be a symlink to the OS interpreter. Keep both
        # site locations, and the resolved path at a prefix-install boundary.
        prefixes = {exe.parent.parent, exe.resolve(strict=True).parent.parent, _known_prefix(exe, env)}
        if any(_present(p / NAMES.pip) for p in prefixes):
            return False
    return bool(candidates)


def _python_files(inv, env, home, roots, xdg):
    xdg_dirs = [Path(p) for p in env.get('XDG_CONFIG_DIRS', str(_ETC / 'xdg')).split(os.pathsep)]
    if any(not p.is_absolute() for p in xdg_dirs):
        return False
    if {'XDG_DATA_HOME', 'XDG_DATA_DIRS'} & env.keys():
        return False  # Different pip/platformdirs releases resolve data roots differently.
    pip_files = {_ETC / NAMES.pip, _ETC / 'xdg/pip' / NAMES.pip, home / '.local/share/pip' / NAMES.pip,
                 home / '.pip' / NAMES.pip, home / '.config/pip' / NAMES.pip, xdg / 'pip' / NAMES.pip,
                 _MAC_LIBRARY / 'Application Support/pip' / NAMES.pip, home / 'Library/Application Support/pip' / NAMES.pip}
    pip_files.update(p / 'pip' / NAMES.pip for p in [*xdg_dirs, *_PIP_SHARE_DIRS])
    if not _pip_prefixes_ok(inv, env, home, roots):
        return False
    if 'VIRTUAL_ENV' in env:
        pip_files.add(_absolute(env['VIRTUAL_ENV']) / NAMES.pip)
    if not all(_pip_conf(read_source_text(p)) for p in pip_files):
        return False
    if inv.tool in ('uv', 'uvx', 'pipx'):
        files = [*(r / f for r in roots for f in (NAMES.uv, NAMES.pyproject)),
                 xdg / 'uv' / NAMES.uv, home / '.config/uv' / NAMES.uv, _ETC / 'uv' / NAMES.uv]
        if not all(_no_markers(read_source_text(p), _UV_ROUTING) for p in files):
            return False
    if inv.tool == 'poetry':
        return all(_no_markers(read_source_text(r / f), _POETRY_ROUTING)
                   for r in roots for f in (NAMES.pyproject, NAMES.poetry))
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
