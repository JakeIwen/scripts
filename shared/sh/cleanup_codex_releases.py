#!/usr/bin/python3
"""Preview old Codex standalone releases; --apply deletes eligible directories.

Keep the three highest stable version numbers, plus current/running releases.
Unknown names, preview versions, and unfamiliar package layouts are never deleted.
Only the invoking account's ~/.codex/packages/standalone/releases is supported,
on macOS (the MacBook) and Linux (vanpi). On Linux, root may pass --owner: every
file operation then runs with that account's effective IDs, and root privilege is
resumed only to read /proc for processes that hide it from their own account.
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
import stat
import subprocess
import sys
import time


KEEP = 3
# Only this host's own package target is recognized; anything else is left alone.
RELEASE_TARGETS = {
    "darwin": r"[A-Za-z0-9_]+-apple-darwin",
    "linux": r"[A-Za-z0-9_]+-unknown-linux-(?:musl|gnu)",
}
PROC = Path("/proc")


def release_name(platform=sys.platform):
    return re.compile(r"(\d+)\.(\d+)\.(\d+)-(" + RELEASE_TARGETS[platform] + ")")


def check_owned(path, directory=False):
    """Reject links, foreign ownership, and paths writable by other accounts."""
    info = path.lstat()
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected_type(info.st_mode) or info.st_uid != os.geteuid()
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
        # Linux keeps them independent, so there both are needed.
        fcntl.lockf(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if sys.platform == "linux":
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
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
    pattern = release_name()
    for path in sorted(root.iterdir()):
        match = pattern.fullmatch(path.name)
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
    if not link.is_symlink() or link.lstat().st_uid != os.geteuid():
        raise RuntimeError("Expected an owned standalone/current symlink")
    target = link.resolve(strict=True)
    if target.parent != root or not target.is_dir():
        raise RuntimeError("Current release is not a direct child of releases")
    return target


def release_of(root, path):
    prefix = str(root) + "/"
    return root / path[len(prefix):].split("/", 1)[0] if path.startswith(prefix) else None


def darwin_running_releases(root):
    # Query all executable mappings for this account, including code-mode hosts.
    # -w drops warnings such as "can't stat() smbfs", which an attached SMB
    # share prints on every run; the exit status still reports real failures.
    result = subprocess.run(
        ["/usr/sbin/lsof", "-w", "-nP", "-a", "-u", str(os.geteuid()), "-d", "txt", "-Fn"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0 or result.stderr.strip() or not result.stdout.startswith("p"):
        raise RuntimeError("Cannot reliably inspect running executables; refusing cleanup")
    return {release for line in result.stdout.splitlines()
            if line.startswith("n") and (release := release_of(root, line[1:]))}


@contextmanager
def root_effective_uid():
    """Under --owner, resume root only while reading another process's /proc entries."""
    if os.getuid() != 0:
        yield
        return
    owner = os.geteuid()
    try:
        os.seteuid(0)
        yield
    finally:
        try:
            os.seteuid(owner)
        except BaseException:
            # Never let lock cleanup or deletion continue with root's identity,
            # even when the diagnostic itself cannot be written.
            try:
                print("STOP: could not drop root privileges", file=sys.stderr, flush=True)
            finally:
                os._exit(70)


def process_mapped_paths(process):
    """Executable and mapped paths of every live thread; [] once the process is gone.

    A leader whose main thread exited loses its exe link while worker threads keep
    their mappings, so each task's maps is read rather than trusting the leader.
    """
    paths = []
    try:
        paths.append(os.readlink(process / "exe"))
    except FileNotFoundError:
        pass  # exited leader, zombie or kernel thread; surviving tasks are read below
    try:
        tasks = [task.path for task in os.scandir(process / "task")]
    except FileNotFoundError:
        return paths
    for task in tasks:
        try:
            with open(Path(task) / "maps", encoding="utf-8", errors="surrogateescape") as maps:
                paths += [fields[5] for fields in (line.split(None, 5) for line in maps)
                          if len(fields) == 6]
        except (FileNotFoundError, ProcessLookupError):
            continue  # that thread exited
    return paths


def linux_running_releases(root, proc=PROC):
    """Releases holding a process executable or file mapping; complete or refused.

    Non-dumpable processes (sshd, ssh-agent, sd-pam, smbd, possibly a hardened
    Codex) hide exe and maps from their own account, and nothing else proves what
    they map, so only root can see the whole table: as root every process counts.
    The process list itself is read as root too, in case /proc uses hidepid.
    """
    everyone = os.getuid() == 0
    account = os.geteuid()
    found = set()
    with root_effective_uid():
        try:
            pids = [entry for entry in os.scandir(proc) if entry.name.isdigit()]
        except OSError as error:
            raise RuntimeError("Cannot list processes; refusing cleanup") from error
        if not pids:
            raise RuntimeError("Process table is empty; refusing cleanup")
        for entry in pids:
            try:
                if not everyone and entry.stat(follow_symlinks=False).st_uid != account:
                    continue
                paths = process_mapped_paths(Path(entry.path))
            except (FileNotFoundError, ProcessLookupError):
                continue  # the whole process exited
            except OSError as error:
                raise RuntimeError(
                    f"Cannot inspect process {entry.name}; refusing cleanup "
                    "(on Linux run as root with --owner)") from error
            found.update(release for path in paths
                         if (release := release_of(root, path.strip())))
    return found


def running_releases(root):
    if sys.platform == "linux":
        return linux_running_releases(root)
    return darwin_running_releases(root)


def mountinfo_path(field):
    """Decode the octal escapes mountinfo uses for spaces, tabs, newlines and backslashes."""
    return re.sub(rb"\\([0-7]{3})", lambda match: bytes([int(match[1], 8)]), field)


def refuse_mounts_within(path, mountinfo=PROC / "self/mountinfo"):
    """A same-device bind mount keeps st_dev, so only the mount table reveals it.

    Fields are split on ASCII LF and space only: Unicode-aware splitting would
    treat a non-breaking space inside a source path as a separator and misread
    which field is the mount point.
    """
    try:
        lines = [line for line in mountinfo.read_bytes().split(b"\n") if line]
    except OSError as error:
        raise RuntimeError("Cannot read the mount table; refusing cleanup") from error
    if not lines:
        raise RuntimeError("Mount table is empty; refusing cleanup")
    target = os.fsencode(path)
    for line in lines:
        fields = line.split(b" ")
        if len(fields) < 5 or not fields[4].startswith(b"/"):
            raise RuntimeError("Unparseable mount table; refusing cleanup")
        mount = mountinfo_path(fields[4])
        if mount == target or mount.startswith(target + b"/"):
            raise RuntimeError(f"Refusing mount at or beneath release: {os.fsdecode(mount)}")


def fail_walk(error):
    raise error


def tree_size(path, device):
    """Validate the entire tree before deletion; never traverse links or mounts."""
    if sys.platform == "linux":
        refuse_mounts_within(path)
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


DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def empty_directory(descriptor, device, display):
    """Check a directory's filesystem before reading it, then empty it depth first."""
    if os.fstat(descriptor).st_dev != device:
        raise RuntimeError(f"Refusing cross-device directory during deletion: {display}")
    with os.scandir(descriptor) as entries:
        children = [(entry.name, entry.is_dir(follow_symlinks=False)) for entry in entries]
    for name, is_directory in children:
        if not is_directory:
            os.unlink(name, dir_fd=descriptor)  # files and symlinks, never followed
            continue
        child = os.open(name, DIRECTORY_FLAGS, dir_fd=descriptor)
        try:
            empty_directory(child, device, f"{display}/{name}")
        finally:
            os.close(child)
        os.rmdir(name, dir_fd=descriptor)


def remove_tree(path, device):
    """Delete through directory descriptors with an rm --one-file-system boundary.

    Each directory is opened without following links and checked against the
    release filesystem before it is listed or descended into, so a mount that
    appears after validation stops deletion at its root; rmdir also cannot
    remove a mount point.
    """
    descriptor = os.open(path, DIRECTORY_FLAGS)
    try:
        empty_directory(descriptor, device, str(path))
    finally:
        os.close(descriptor)
    os.rmdir(path)


def cleanup(home, apply=False):
    root = release_root(home)
    root_info = root.stat()
    if not ({os.open, os.unlink, os.rmdir} <= os.supports_dir_fd
            and os.scandir in os.supports_fd):
        raise RuntimeError("Python lacks descriptor-relative deletion support")
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
                remove_tree(path, root_info.st_dev)
            count += 1
            total += size
        action = "Deleted" if apply else "Would delete"
        print(f"{action} {count} directories, about {total / 1024**3:.2f} GiB allocated.")


def become_owner(name):
    """Drop to the owner's effective IDs, keeping saved root for /proc reads."""
    account = pwd.getpwnam(name)
    if account.pw_uid == 0:
        raise ValueError("--owner must name an ordinary account")
    os.setgroups(os.getgrouplist(name, account.pw_gid))
    os.setegid(account.pw_gid)
    os.seteuid(account.pw_uid)
    return Path(account.pw_dir)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Actually delete old releases")
    mode.add_argument("--dry-run", action="store_true", help="Preview only (the default)")
    parser.add_argument("--owner", metavar="ACCOUNT",
                        help="Linux, as root: clean this account's releases")
    args = parser.parse_args()
    if sys.platform not in RELEASE_TARGETS:
        parser.error(f"Unsupported platform: {sys.platform}")
    if args.owner is not None:
        if sys.platform != "linux" or os.getuid() != 0 or os.geteuid() != 0:
            parser.error("--owner is only for root on Linux")
        try:
            home = become_owner(args.owner)
        except (KeyError, ValueError, OSError) as error:
            parser.error(f"Cannot act as {args.owner}: {error}")
    elif os.getuid() == 0 or os.getuid() != os.geteuid():
        parser.error("Run as the ordinary account that owns ~/.codex, without sudo")
    else:
        # Ignore HOME/CODEX_HOME overrides so cron cannot redirect this deletion.
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    print(datetime.now().astimezone().isoformat(timespec="seconds"), flush=True)
    try:
        cleanup(home, apply=args.apply)
    except (BlockingIOError, FileExistsError):
        print("SKIP: another installer or cleanup holds the lock; try again later.")
        return 75
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"STOP: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
