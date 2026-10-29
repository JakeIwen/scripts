"""Confinement checks shared by plans, relocation and rollback."""
from pathlib import Path
from . import config


def component(value: str) -> str:
    if (not isinstance(value, str) or not value.strip() or value.startswith('.')
            or value != value.strip() or '/' in value or '\\' in value
            or any(ord(char) < 32 for char in value)):
        raise ValueError('unsafe media name')
    return value


def confined(path: Path, root: Path) -> Path:
    path, root = Path(path), Path(root)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('media path must be absolute without parent traversal')
    path.relative_to(root)
    # Even an in-tree symlink changes which payload a later rename would touch.
    current = root
    if root.is_symlink():
        raise ValueError('media root cannot be a symlink')
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('media path contains a symlink')
    path.resolve().relative_to(root.resolve())
    return path


def destination(path: Path) -> Path:
    path = confined(Path(path), config.MEDIA_ROOT / config.TORRENT_DIR)
    parts = path.relative_to(config.MEDIA_ROOT / config.TORRENT_DIR).parts
    if not parts or parts[0] not in config.CATEGORIES:
        raise ValueError('destination must be TV, Documentaries or Movies')
    expected = 1 if parts[0] == config.CATEGORY_MOVIES else 2
    if len(parts) != expected:
        raise ValueError('destination requires exactly one show folder (Movies is flat)')
    for part in parts:
        component(part)
    return path


def new_directory() -> Path:
    return config.MEDIA_ROOT / config.TORRENT_DIR / config.NEW_DIR


def require_media() -> None:
    if not config.MEDIA_ROOT.is_mount():
        raise RuntimeError('media filesystem is not mounted')
    confined(new_directory(), config.MEDIA_ROOT)
    if not new_directory().is_dir():
        raise RuntimeError('torrent/New is unavailable')
