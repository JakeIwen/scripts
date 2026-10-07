"""Filesystem fixtures shared by storage deployment tests."""
from pathlib import Path
from unittest import mock


def monitor_fixture(test, deploy):
    targets = {source: str(test.root / destination.lstrip('/'))
               for source, destination in deploy.dependencies.MONITOR_DEPENDENCIES.items()}
    for destination in targets.values():
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# reviewed system monitor fixture\n')
    patch = mock.patch.object(deploy.dependencies, 'MONITOR_DEPENDENCIES', targets)
    patch.start()
    test.addCleanup(patch.stop)
    return {path: deploy.base.digest(path) for path in targets.values()}


def create_frontend(root, server='existing server'):
    frontend = root / 'frontend'
    for name in ('old', 'older'):
        path = frontend / 'releases' / name
        path.mkdir(parents=True)
        (path / 'index.html').write_text(name)
        (path / 'react_dashboard_preview.py').write_text(server)
    (frontend / 'current').symlink_to('releases/old')
    (frontend / 'previous').symlink_to('releases/older')
    return frontend
