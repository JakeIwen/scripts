"""Build and publish the compatibility media links without changing seed data.

``build_links(source, mode, output_root=temporary_directory)`` (or the environment
variable VAN_ALIAS_OUTPUT_ROOT) writes ``temporary_directory/links`` only. This
verification mode never extracts archives, takes the production lock, contacts the
video library, or scans the mirror. Existing extracted videos may still be used.
An archive lacking an extracted video fails the build rather than extracting it.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import traceback
from urllib.error import URLError
from urllib.request import Request, urlopen

from . import config
from .alias_names import clean_title


def _walk(folder: Path):
    """Depth-first directory order, without following symlinks (GNU find default)."""
    with os.scandir(folder) as entries:
        for entry in entries:
            path = Path(entry.path)
            if entry.is_dir(follow_symlinks=False):
                yield path, True
                yield from _walk(path)
            elif entry.is_file(follow_symlinks=False):
                yield path, False


def _directories(folder: Path, depth: int):
    if not folder.is_dir():
        return
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                path = Path(entry.path)
                yield path
                if depth > 1:
                    yield from _directories(path, depth - 1)


def _extracted_video(folder: Path) -> Path | None:
    for path, directory in _walk(folder):
        if not directory and path.suffix in config.VIDEO_EXTENSIONS:
            if path.stat().st_size > config.ALIAS_EXTRACTED_MIN_BYTES:
                return path
    return None


def _archive_video(archive: Path, extract: bool) -> Path:
    found = _extracted_video(archive.parent)
    if found is None:
        if not extract:
            raise RuntimeError(f"verification will not extract archive: {archive}")
        print(f"extracting archive {archive}", flush=True)
        # Torrent payloads remain immutable seed data (75426ed); never delete
        # archives or other siblings, even when extraction has already succeeded.
        subprocess.run(["unrar", "x", "-r", "-inul", str(archive)],
                       cwd=archive.parent, check=False)
        found = _extracted_video(archive.parent)
        if found is None:
            raise RuntimeError("did not find vidfile (>300M, mkv/avi/mp4) "
                               f"after extracting archive: {archive}")
    else:
        print("found existing extracted file!", flush=True)
    print(f"vidfile {found.name}", flush=True)
    # Preserve handle_rars' basename projection, including a nested match; a
    # change to archive layout/target policy is separate from this output rework.
    return archive.parent / found.name


def _group_links(folder: Path, output: Path, handle_archives: bool, extract: bool):
    if not folder.is_dir():
        return
    # Snapshot before extraction, just like the old find -print0 listing.
    paths = [path for path, directory in _walk(folder) if not directory
             and path.suffix in config.VIDEO_EXTENSIONS + config.ARCHIVE_EXTENSIONS
             and not any(part.startswith(".") for part in path.parts)
             and "sample" not in str(path).lower()]
    failure = None
    for path in paths:
        try:
            # The strict 70 MB floor is historical sample filtering, not >= 70 MB.
            if path.stat().st_size <= config.MIN_MEDIA_BYTES:
                continue
            if path.suffix in config.ARCHIVE_EXTENSIONS and handle_archives:
                path = _archive_video(path, extract)
            title = clean_title(str(path), path.suffix)
            if title in ("", ".", ".."):
                raise ValueError(f"unsafe empty media title: {path}")
            output.mkdir(parents=True, exist_ok=True)
            link = output / title
            if link.is_symlink() or link.exists():
                link.unlink()  # ln -sf: the last encountered collision wins.
            link.symlink_to(path)
        except (OSError, RuntimeError, ValueError) as error:
            failure = error
            traceback.print_exc()
    if failure is not None:
        raise failure


def _populate(source: Path, stage: Path, mode: str, extract: bool):
    torrent = source / config.TORRENT_DIR
    for name in (config.INCOMPLETE_DIR, config.NEW_DIR):
        (stage / name).mkdir()
        _group_links(torrent / name, stage / name, name == config.NEW_DIR, extract)
    if mode == config.ALIAS_MODE_NEW:
        return
    for name in config.CATEGORIES:
        (stage / name).mkdir()
    for show in _directories(torrent / config.CATEGORY_TV, 1):
        _group_links(show, stage / config.CATEGORY_TV / show.name.replace(" ", "_"),
                     True, extract)
    print("TV", flush=True)
    # Historical documentaries are flat, and only files under subdirectories
    # participate; retain the maxdepth-two folder passes and their collision order.
    for folder in _directories(torrent / config.CATEGORY_DOCUMENTARIES, 2):
        _group_links(folder, stage / config.CATEGORY_DOCUMENTARIES, True, extract)
    print("Docu", flush=True)
    _group_links(torrent / config.CATEGORY_MOVIES, stage / config.CATEGORY_MOVIES,
                 True, extract)
    print("Movies", flush=True)


def _permissions(stage: Path, mode: str):
    # Shared shell/SMB consumers historically need writable directories (b764e53).
    # GNU chmod -R ignores symlinks encountered during traversal: do not chmod
    # their torrent targets, especially in the read-only verification mode.
    if mode == config.ALIAS_MODE_FULL:
        stage.chmod(config.ALIAS_DIRECTORY_MODE)
    for path, directory in _walk(stage):
        if directory:
            path.chmod(config.ALIAS_DIRECTORY_MODE)


def _exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _remove_tree(path: Path):
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        if path.is_mount():
            raise OSError(f"refusing to remove mounted links directory: {path}")
        shutil.rmtree(path)


def _swap(replacements: list[tuple[Path, Path, Path]]):
    """Publish one tree or both incremental trees, rolling back rename failures."""
    if any(_exists(old) for _, _, old in replacements):
        raise FileExistsError("refusing stale alias rollback path")
    moved, published = [], []
    try:
        for _, live, old in replacements:
            if _exists(live):
                live.rename(old)
                moved.append((live, old))
        for stage, live, _ in replacements:
            stage.rename(live)
            published.append(live)
    except BaseException:
        for live in reversed(published):
            _remove_tree(live)
        for live, old in reversed(moved):
            old.rename(live)
        raise
    for _, old in moved:
        _remove_tree(old)


def _output_directory(source: Path, output_root: Path | str | None):
    override = output_root if output_root is not None else os.environ.get(
        config.ALIAS_OUTPUT_ROOT_ENV)
    if override is None:
        if source not in (config.MEDIA_ROOT, config.MIRROR_ROOT):
            raise ValueError(f"refusing unexpected media source: {source}")
        return source, False
    output = Path(override).resolve()
    # An override is a scratch root, never the source itself or an ancestor: it
    # must not redirect cleanup/publication back into either production tree.
    for protected in (source, config.MEDIA_ROOT, config.MIRROR_ROOT):
        root = protected.resolve()
        if output == root or root in output.parents or output in root.parents:
            raise ValueError(f"verification output overlaps media source: {output}")
    output.mkdir(parents=True, exist_ok=True)
    return output, True


def build_links(source: Path | str, mode: str = config.ALIAS_MODE_FULL, *,
                output_root: Path | str | None = None) -> Path:
    """Build synchronously; the detached worker owns locking and notification."""
    if mode not in config.ALIAS_MODES:
        raise ValueError(f"invalid alias mode: {mode}")
    source = Path(source).absolute()
    output, verification = _output_directory(source, output_root)
    links = output / config.LINKS_DIR
    if verification and (links.is_symlink() or links.is_mount()):
        raise ValueError(f"verification links must be a private directory: {links}")
    print(f"start {source}", flush=True)
    if mode == config.ALIAS_MODE_NEW:
        links.mkdir(exist_ok=True)
    parent = links if mode == config.ALIAS_MODE_NEW else output
    prefix = ".alias-new." if mode == config.ALIAS_MODE_NEW else ".links-stage."
    stage = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
    try:
        _populate(source, stage, mode, extract=not verification)
        _permissions(stage, mode)
        if mode == config.ALIAS_MODE_FULL:
            replacements = [(stage, links, output / f".links-old.{os.getpid()}")]
        else:
            replacements = [(stage / name, links / name,
                             links / f".{name}.old.{os.getpid()}")
                            for name in (config.INCOMPLETE_DIR, config.NEW_DIR)]
        _swap(replacements)
    finally:
        _remove_tree(stage)
    print(f"done {source}", flush=True)
    return links


@contextmanager
def alias_lock():
    """Queue each detached request so completion scans cannot lose a wakeup."""
    with config.LOCK_PATH.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def source_is_mounted(source: Path) -> bool:
    if source == config.MEDIA_ROOT:
        mount = source
    elif source == config.MIRROR_ROOT:
        mount = config.MIRROR_MOUNT
    else:
        raise ValueError(f"refusing unexpected media source: {source}")
    try:
        result = subprocess.run(["/usr/bin/mountpoint", "-q", str(mount)], check=False)
    except FileNotFoundError:
        return False  # Like a failed shell mount probe, never infer a mounted source.
    return result.returncode == 0 and source.is_dir()


def notify_video_library():
    request = Request(config.ALIAS_RECONCILE_URL, data=b"", method="POST",
                      headers={"X-Van-Video": "1"})
    try:
        with urlopen(request, timeout=config.ALIAS_NOTIFY_TIMEOUT):
            pass
    except (OSError, URLError):
        pass  # Notification has always been best-effort, bounded loopback I/O.


def _build_one(source: Path, mode: str) -> int:
    try:
        build_links(source, mode)
        return 0
    except Exception:
        traceback.print_exc()
        return 1


def run_locked(mode: str) -> int:
    """Run a scheduled request, including both mounted sources in full mode."""
    if os.environ.get(config.ALIAS_OUTPUT_ROOT_ENV) is not None:
        # Verification is deliberately foreground/no-lock, movingparts only.
        return _build_one(config.MEDIA_ROOT, mode)
    with alias_lock():
        print(f"{datetime.now().astimezone()}: running alias_media {mode}", flush=True)
        candidates = ((config.MEDIA_ROOT,) if mode == config.ALIAS_MODE_NEW else
                      (config.MIRROR_ROOT, config.MEDIA_ROOT))
        sources = [source for source in candidates if source_is_mounted(source)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda source: _build_one(source, mode), sources))
        status = int(any(statuses))
        if sources and not status:
            notify_video_library()
        print(f"alias media end {datetime.now().astimezone()}, status={status}", flush=True)
        return status
