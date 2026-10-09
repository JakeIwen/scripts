#!/usr/bin/python3
"""Preview old Codex standalone releases; --apply deletes eligible directories.

Keep the three highest stable version numbers, plus current/running releases.
Unknown names, preview versions, and unfamiliar package layouts are never deleted.
Only the invoking account's ~/.codex/packages/standalone/releases is supported.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
import time


KEEP = 3
RELEASE_NAME = re.compile(r"(\d+)\.(\d+)\.(\d+)-([A-Za-z0-9_]+-apple-darwin)")


def check_owned(path, directory=False):
    """Reject links, foreign ownership, and paths writable by other accounts."""
    info = path.lstat()
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected_type(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o022 or (not directory and info.st_nlink != 1)):
        raise RuntimeError(f"Unsafe ownership, type, or permissions: {path}")
    return info


def release_root(home):
    path = home.resolve(strict=True)
    for component in (".codex", "packages", "standalone", "releases"):
        path = path / component
        check_owned(path, directory=True)
        if path.is_mount():
            raise RuntimeError(f"Refusing a mounted package directory: {path}")
    return path


@contextmanager
def installer_lock(root):
    """Coordinate with lockf, flock, and mkdir variants of the Codex installer."""
    path = root.parent / "install.lock"
    before = check_owned(path)
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    lock_dir = root.parent / "install.lock.d"
    owned_dir = None
    try:
        if not os.path.samestat(before, os.fstat(descriptor)):
            raise RuntimeError("Installer lock changed while opening it")
        # Darwin flock and POSIX locks interoperate, but taking both here
        # conflicts with our own lock. One lockf covers either installer form.
        fcntl.lockf(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_dir.mkdir(mode=0o700)
        owned_dir = lock_dir.stat()
        (lock_dir / "pid").write_text(str(os.getpid()) + "\n")
        (lock_dir / "started_at").write_text(str(int(time.time())) + "\n")
        yield
    finally:
        try:
            if owned_dir is not None:
                if not os.path.samestat(owned_dir, lock_dir.lstat()):
                    raise RuntimeError("Installer lock directory changed; leaving it alone")
                for name in ("pid", "started_at"):
                    (lock_dir / name).unlink(missing_ok=True)
                lock_dir.rmdir()
        finally:
            os.close(descriptor)


def recognized_releases(root):
    releases = []
    for path in sorted(root.iterdir()):
        match = RELEASE_NAME.fullmatch(path.name)
        if not match or path.is_symlink() or not path.is_dir():
            print(f"LEAVE unfamiliar entry: {path.name!r}")
            continue
        check_owned(path, directory=True)
        manifest = path / "codex-package.json"
        check_owned(manifest)
        if manifest.stat().st_size > 16384:
            raise RuntimeError(f"Oversized package manifest: {path.name}")
        metadata = json.loads(manifest.read_text())
        version = ".".join(match.groups()[:3])
        expected = {"layoutVersion": 1, "version": version,
                    "target": match[4], "variant": "codex", "entrypoint": "bin/codex"}
        if not isinstance(metadata, dict) or any(metadata.get(k) != v for k, v in expected.items()):
            raise RuntimeError(f"Unfamiliar package manifest: {path.name}")
        check_owned(path / "bin", directory=True)
        check_owned(path / "bin/codex")
        if not os.access(path / "bin/codex", os.X_OK):
            raise RuntimeError(f"Package executable is not executable: {path.name}")
        releases.append((tuple(int(n) for n in match.groups()[:3]), path))
    return [path for _, path in sorted(releases, reverse=True)]


def current_release(root):
    link = root.parent / "current"
    if not link.is_symlink() or link.lstat().st_uid != os.getuid():
        raise RuntimeError("Expected an owned standalone/current symlink")
    target = link.resolve(strict=True)
    if target.parent != root or not target.is_dir():
        raise RuntimeError("Current release is not a direct child of releases")
    return target


def running_releases(root):
    # Query all executable mappings for this account, including code-mode hosts.
    result = subprocess.run(
        ["/usr/sbin/lsof", "-nP", "-a", "-u", str(os.getuid()), "-d", "txt", "-Fn"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0 or result.stderr.strip() or not result.stdout.startswith("p"):
        raise RuntimeError("Cannot reliably inspect running executables; refusing cleanup")
    prefix = "n" + str(root) + "/"
    return {root / line[len(prefix):].split("/", 1)[0]
            for line in result.stdout.splitlines() if line.startswith(prefix)}


def fail_walk(error):
    raise error


def tree_size(path, device):
    """Validate the entire tree before deletion; never traverse links or mounts."""
    total = 0
    for directory, dirs, files in os.walk(path, followlinks=False, onerror=fail_walk):
        current = Path(directory)
        info = check_owned(current, directory=True)
        if info.st_dev != device or current.is_mount():
            raise RuntimeError(f"Refusing mounted directory: {current}")
        total += info.st_blocks * 512
        for name in dirs + files:
            child = current / name
            info = child.lstat()
            if info.st_dev != device:
                raise RuntimeError(f"Refusing cross-device entry: {child}")
            if not stat.S_ISDIR(info.st_mode):
                total += info.st_blocks * 512
    return total


def cleanup(home, apply=False):
    root = release_root(home)
    root_info = root.stat()
    if not shutil.rmtree.avoids_symlink_attacks:
        raise RuntimeError("Python lacks safe directory deletion support")
    with installer_lock(root):
        releases = recognized_releases(root)
        selected = current_release(root)
        if selected not in releases:
            raise RuntimeError("Current package layout is unfamiliar; refusing cleanup")
        protected = set(releases[:KEEP]) | {selected} | running_releases(root)
        candidates = [path for path in releases if path not in protected]
        # Validate every candidate before the first destructive operation.
        planned = [(path, path.lstat(), tree_size(path, root_info.st_dev))
                   for path in candidates]
        for path in releases:
            if path in protected:
                print(f"KEEP {path.name}")
        count = 0
        total = 0
        for path, identity, size in planned:
            if (release_root(home) != root
                    or not os.path.samestat(root_info, root.lstat())
                    or not os.path.samestat(identity, path.lstat())):
                raise RuntimeError("Release directory changed during cleanup")
            if path == current_release(root) or path in running_releases(root):
                print(f"KEEP now active: {path.name}")
                continue
            tree_size(path, root_info.st_dev)
            print(f"{'DELETE' if apply else 'WOULD DELETE'} {path.name}", flush=True)
            if apply:
                shutil.rmtree(path)
            count += 1
            total += size
        action = "Deleted" if apply else "Would delete"
        print(f"{action} {count} directories, about {total / 1024**3:.2f} GiB allocated.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Actually delete old releases")
    mode.add_argument("--dry-run", action="store_true", help="Preview only (the default)")
    args = parser.parse_args()
    if os.getuid() == 0 or os.getuid() != os.geteuid() or sys.platform != "darwin":
        parser.error("Run as your ordinary macOS user, without sudo")
    print(datetime.now().astimezone().isoformat(timespec="seconds"), flush=True)
    try:
        # Ignore HOME/CODEX_HOME overrides so cron cannot redirect this deletion.
        cleanup(Path(pwd.getpwuid(os.getuid()).pw_dir), apply=args.apply)
    except (BlockingIOError, FileExistsError):
        print("SKIP: another installer or cleanup holds the lock; try again later.")
        return 75
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"STOP: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
