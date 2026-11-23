"""Classify local commands or script targets without executing project files or managers."""

import json
import os
import re
import shutil
from pathlib import Path

from dep_guard_config_paths import NAMES
from dep_guard_options import npm_global_mode
from dep_guard_sources import npm_config_prefixes, read_source_text


def uv_script_route(cwd, mode, operands, options):
    scripts = [v for k, v in options if k == '--script' and v is not None]
    if mode == 'with-only' and operands:
        target = operands[0]
        forced = any(k in ('--script', '-s', '--gui-script') for k, _ in options)
        try:
            if (forced or target == '-' or target.endswith(('.py', '.pyw')) or '/' in target or
                    cwd is not None and (cwd / target).is_file()):
                scripts.append(target)
        except (OSError, ValueError):
            return None
    for script in scripts:
        if cwd is None or not script or script == '-' or '://' in script:
            return None
        try:
            file = (cwd / script).resolve(strict=True)
            if file.parent != cwd.resolve(strict=True) or not file.is_file():
                return None
            # PEP 723 metadata may configure an index independently of pyproject.
            if re.search(r'^#\s*///\s+script\s*$', read_source_text(file), re.M):
                return None
        except (OSError, ValueError, RuntimeError):
            return None
    return cwd


def _manifest_bin(file, name):
    text = read_source_text(file)
    if not text:
        return False
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError('indeterminate npm manifest')
    bins = obj.get('bin')
    if isinstance(bins, str):
        bins = {str(obj.get('name', '')): bins}
    elif isinstance(bins, list):
        bins = {k: k for k in bins if isinstance(k, str)}
    if isinstance(bins, dict):
        return any(k.replace('\\', '/').replace(':', '/').rsplit('/', 1)[-1] == name and
                   isinstance(v, str) and bool(v.strip('/')) for k, v in bins.items())
    if isinstance(obj.get('directories'), dict) and obj['directories'].get('bin'):
        raise ValueError('directory of executable names is not enumerated')
    return False


def _project_prefix(cwd):
    for root in (cwd, *cwd.parents):
        manifest = root / NAMES.package
        if manifest.exists() or manifest.is_symlink() or (root / 'node_modules').is_dir():
            return root  # npm's nearest package or node_modules root.
    return cwd


def _bin_tree(start, name):
    return any((root / 'node_modules/.bin' / name).is_file() for root in (start, *start.parents))


def _prefix_bin(prefix, project, name, mode):
    # npm exec supplies globalPath only when localPrefix differs from globalPrefix.
    if prefix.resolve() != project.resolve() and (prefix / 'bin' / name).is_file():
        return True
    if mode is not False and _bin_tree(prefix / 'lib', name):
        return mode  # The global .bin walk replaces, rather than extends, the project walk.
    return False


def _global_bin(inv, effective, configured, project, mode):
    # An npm_config_prefix overrides the default derived from Node, even for
    # a CLI whose bin directory is on PATH. Never guess a relative prefix.
    overrides = {v for k, v in effective.items() if k.lower() == 'npm_config_prefix' and v}
    if not overrides and not configured and effective.get('PREFIX'):
        overrides = {effective['PREFIX']}
    if overrides:
        if any(not Path(v).is_absolute() or '$' in v for v in overrides):
            raise ValueError('unproved global prefix')
        configured = {Path(v) for v in overrides}
    elif not configured and effective.get('DESTDIR'):
        raise ValueError('unproved global prefix')
    if configured:
        matches = {_prefix_bin(prefix, project, inv.name, mode) for prefix in configured}
        return matches.pop() if len(matches) == 1 else None
    # npm's Unix default prefix derives from process.execPath, not every bin
    # directory on PATH. An arbitrary executable that shadows a name isn't proof.
    search = os.pathsep.join(str(inv.cwd / p) for p in effective.get('PATH', os.defpath).split(os.pathsep))
    node = shutil.which('node', path=search)
    if node:
        return _prefix_bin(Path(node).resolve(strict=True).parent.parent, project, inv.name, mode)
    exe = (inv.cwd / inv.executable if '/' in inv.executable else shutil.which(inv.executable, path=search))
    if exe and (Path(exe).parent / inv.name).is_file():
        raise ValueError('cannot establish the selected node prefix')
    return False


def local_execution(inv, raw, env, home, options):
    """True = existing target, False = not local, None = do not look up the name."""
    # An earlier write or unclassified command invalidates cwd before this proof.
    if inv.cwd is None:
        return None
    try:
        if inv.tool == 'pipx':
            return (inv.cwd / raw).is_file()
        if raw != inv.name or '/' in inv.name:
            return False
        effective, file_modes = dict(env, **inv.env), {}
        # An explicit CLI/environment global mode skips the project npmrc in
        # npm itself. A prefix supplied only by that file cannot prove a bin.
        global_only = npm_global_mode(options, effective, {}) is True
        prefixes = npm_config_prefixes(inv, effective, home, modes=file_modes, global_only=global_only)
        project = _project_prefix(inv.cwd.resolve(strict=True))
        if _manifest_bin(project / NAMES.package, inv.name):
            return True  # libnpmexec checks the package's own bin in either mode.
        mode = npm_global_mode(options, effective, file_modes)
        project_match = mode is not True and _bin_tree(project, inv.name)
        if mode is False and project_match:
            return True
        global_match = _global_bin(inv, effective, prefixes, project, mode)
        if mode is None and project_match and not global_match:
            return None
        return global_match
    except (OSError, ValueError, RuntimeError):
        return None
