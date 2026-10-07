"""Filesystem fixtures shared by storage deployment tests."""


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
