"""Classify local commands or script targets without executing project files or managers."""

import json
import os
import re
import shutil
from pathlib import Path

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


def _project_bin(cwd, name):
    roots = (cwd, *cwd.parents)
    if any((root / 'node_modules' / '.bin' / name).is_file() for root in roots):
        return True
    for root in roots:
        manifest = root / 'package.json'
        if manifest.exists() or manifest.is_symlink():
            return _manifest_bin(manifest, name)
        if (root / 'node_modules').is_dir():
            break  # npm's local prefix is the first package or node_modules root.
    return False


def _global_bin(inv, env, home):
    effective = dict(env, **inv.env)
    configured = npm_config_prefixes(inv, effective, home)
    if configured:
        matches = {(prefix / 'bin' / inv.name).is_file() for prefix in configured}
        if len(matches) != 1:
            raise ValueError('ambiguous configured npm global bin')
        return True in matches
    # npm's Unix default prefix derives from process.execPath, not every bin
    # directory on PATH. An arbitrary executable that shadows a name isn't proof.
    search = os.pathsep.join(str(inv.cwd / p) for p in effective.get('PATH', os.defpath).split(os.pathsep))
    node = shutil.which('node', path=search)
    if node and (Path(node).resolve(strict=True).parent / inv.name).is_file():
        return True
    if not node:
        exe = (inv.cwd / inv.executable if '/' in inv.executable else shutil.which(inv.executable, path=search))
        if exe and (Path(exe).parent / inv.name).is_file():
            raise ValueError('cannot establish the selected node prefix')
    return False


def local_execution(inv, raw, env, home):
    """True = existing target, False = not local, None = do not look up the name."""
    if inv.cwd is None:
        return None
    try:
        if inv.tool == 'pipx':
            return (inv.cwd / raw).is_file()
        if raw != inv.name or '/' in inv.name:
            return False
        return _project_bin(inv.cwd, inv.name) or _global_bin(inv, env, home)
    except (OSError, ValueError, RuntimeError):
        return None
