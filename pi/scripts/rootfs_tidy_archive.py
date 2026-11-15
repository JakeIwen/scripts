#!/usr/bin/python3
"""Move old Codex archive files off the SD root onto the exFAT archive volume.

rootfs_tidy_sweeps.sh runs this as `pi` with `/usr/bin/python3 -I` (Pi Python
3.11; stdlib only, 3.9-compatible so the Mac suite runs it). Dry run unless
--apply. Exit 0 when everything moved or the step was skipped, 1 when any file
was kept for a reason that needs a look, 2 on usage errors.

Files under ~/.codex/archived_sessions/ and ~/.codex/migration-backups/ whose
mtime is older than the cutoff go to <mount>/vanpi-root-archive/codex/<rel>.
Live sessions/ is never opened. Every operation is descriptor-relative and
refuses symlinks, so renaming or swapping any parent directory mid-run cannot
redirect a read, write or delete: the walk holds each parent's fd.

Per file: open the source O_NOFOLLOW and confirm it is the scanned inode; copy
from that fd into an O_EXCL temp under .tidy-tmp/, hashing while reading;
fsync, re-read and re-hash the temp; publish with rename-no-replace (Linux
renameat2 RENAME_NOREPLACE, verified on the Pi's exFAT driver on 2026-11-14,
where hard links fail with EPERM, so link-then-unlink is not an option; macOS
renameatx_np RENAME_EXCL for the suite). Then quarantine the source by renaming
it to .tidy-moving-<name> in its own directory, confirm the quarantined entry is
still the opened inode with unchanged size and mtime, re-hash it against the
archived copy, and only then unlink it. Any mismatch renames it back (or leaves
it quarantined, reported, when the name was re-created) and never deletes.

Residual window: a writer that already held the file open and appends between
the last check and the unlink loses those bytes. Codex never writes a session
once it is in archived_sessions/, nor migration backups, and only files idle
for the whole cutoff (30 days) are candidates, so that window is accepted.
A writer that opens the file by name after the quarantine creates a new file
at the original name, which survives.
"""
import argparse
import ctypes
import errno
import fcntl
import hashlib
import os
import secrets
import stat
import subprocess
import sys
import time

DEFAULT_MOUNT = "/mnt/EXFAT512"
# Identity of the archive volume (findmnt and /proc/self/mountinfo, read live
# on 2026-11-14): the exFAT flash drive mounted uid=1000.
DEFAULT_FSTYPE = "exfat"
DEFAULT_UUID = "122C-A219"
DEFAULT_FINDMNT = "/usr/bin/findmnt"
DEFAULT_MOUNTINFO = "/proc/self/mountinfo"
DEFAULT_DAYS = 30
ARCHIVE_DIR = "vanpi-root-archive"
ARCHIVE_SUBDIR = "codex"
TMP_DIR = ".tidy-tmp"
QUARANTINE_PREFIX = ".tidy-moving-"
# Live sessions/ is deliberately absent.
SUBTREES = ("archived_sessions", "migration-backups")
CHUNK = 1 << 20
FINDMNT_TIMEOUT = 10
LABEL = "Codex archive"

O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | O_CLOEXEC | getattr(os, "O_NONBLOCK", 0)
RENAME_NOREPLACE = 1  # Linux renameat2
RENAME_EXCL = 4  # macOS renameatx_np
# exFAT refuses control characters and these in names, and strips trailing dots.
EXFAT_FORBIDDEN = frozenset(b'"*/:<>?\\|%')


class Kept(Exception):
    """A file stays on the SD card; the message says why."""


class Skip(Exception):
    """The whole step is skipped (volume absent or not the expected one)."""


def report(message, stream=sys.stdout):
    print(f"{LABEL}: {message}", file=stream, flush=True)


def shown(rel):
    """A relative path for messages, with control characters made visible."""
    return "/".join(rel).encode("unicode_escape", "backslashreplace").decode("ascii")


# --- descriptors and names ---------------------------------------------------

def open_dir_path(path):
    """Open an absolute path one component at a time, refusing any symlink."""
    if not os.path.isabs(path):
        raise OSError(errno.EINVAL, "not an absolute path", path)
    fd = os.open("/", DIR_FLAGS)
    try:
        for part in [p for p in path.split("/") if p]:
            if part in (".", ".."):
                raise OSError(errno.EINVAL, "path is not normalized", path)
            child = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def same_inode(a, b):
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def unchanged(a, b):
    return same_inode(a, b) and a.st_size == b.st_size and a.st_mtime_ns == b.st_mtime_ns


def archive_name(name):
    """The exFAT-safe archive name: forbidden bytes and a trailing dot become %XX.

    `%` itself is encoded too, so names decode back unambiguously.
    """
    raw = os.fsencode(name)
    try:
        raw.decode("utf-8")
        utf8 = True
    except UnicodeDecodeError:
        utf8 = False
    out = []
    for index, byte in enumerate(raw):
        last = index == len(raw) - 1
        if (byte < 0x20 or byte == 0x7F or byte in EXFAT_FORBIDDEN
                or (byte >= 0x80 and not utf8) or (last and byte == 0x2E)):
            out.append(b"%%%02X" % byte)
        else:
            out.append(bytes((byte,)))
    return os.fsdecode(b"".join(out))


def _libc():
    return ctypes.CDLL(None, use_errno=True)


def rename_noreplace(src_dir_fd, src, dst_dir_fd, dst):
    """Rename that fails with EEXIST instead of replacing an existing entry."""
    libc = _libc()
    args = (src_dir_fd, os.fsencode(src), dst_dir_fd, os.fsencode(dst))
    if sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        result = libc.renameat2(*args, RENAME_NOREPLACE)
    elif sys.platform == "darwin" and hasattr(libc, "renameatx_np"):
        result = libc.renameatx_np(*args, RENAME_EXCL)
    else:
        raise OSError(errno.ENOSYS, "no rename-without-replace on this platform")
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), dst)


def hash_fd(fd):
    digest = hashlib.sha256()
    offset = 0
    while True:
        chunk = os.pread(fd, CHUNK, offset)
        if not chunk:
            return digest.hexdigest(), offset
        digest.update(chunk)
        offset += len(chunk)


def open_child_dir(parent_fd, name, expected=None):
    fd = os.open(name, DIR_FLAGS, dir_fd=parent_fd)
    if expected is not None and not same_inode(os.fstat(fd), expected):
        os.close(fd)
        raise Kept(f"{name} was replaced while being scanned")
    return fd


def ensure_child_dir(parent_fd, name, device):
    try:
        os.mkdir(name, 0o755, dir_fd=parent_fd)
    except FileExistsError:
        pass
    fd = os.open(name, DIR_FLAGS, dir_fd=parent_fd)
    if os.fstat(fd).st_dev != device:
        os.close(fd)
        raise OSError(errno.EXDEV, "another filesystem is mounted inside the archive", name)
    return fd


# --- archive volume identity ---------------------------------------------------

def unescape_mountinfo(field):
    """mountinfo octal-escapes space, tab, newline and backslash."""
    out, index = bytearray(), 0
    raw = field.encode()
    while index < len(raw):
        if raw[index:index + 1] == b"\\" and raw[index + 1:index + 4].isdigit():
            out.append(int(raw[index + 1:index + 4], 8))
            index += 4
        else:
            out.append(raw[index])
            index += 1
    return os.fsdecode(bytes(out))


def mountinfo_entry(path, mount):
    """(device, fstype) of the topmost mount at `mount`, or None."""
    found = None
    with open(path, encoding="utf-8", errors="surrogateescape") as handle:
        for line in handle:
            fields = line.split()
            if "-" not in fields:
                continue
            sep = fields.index("-")
            if sep < 5 or len(fields) < sep + 2 or unescape_mountinfo(fields[4]) != mount:
                continue
            major, minor = fields[2].split(":")
            found = (os.makedev(int(major), int(minor)), fields[sep + 1])
    return found


def unescape_findmnt(value):
    """findmnt -r writes unsafe characters as \\xHH."""
    out, index = bytearray(), 0
    raw = value.encode()
    while index < len(raw):
        if raw[index:index + 2] == b"\\x" and len(raw) >= index + 4:
            out.append(int(raw[index + 2:index + 4], 16))
            index += 4
        else:
            out.append(raw[index])
            index += 1
    return os.fsdecode(bytes(out))


def findmnt_entry(findmnt, mount):
    """(device, fstype, uuid) findmnt reports for the mount point, or None.

    Raw mode (-r) separates columns with exactly one space and escapes spaces
    inside values, so an empty UUID still leaves four fields. The default
    aligned format pads columns with extra spaces and is never parsed here.
    """
    try:
        result = subprocess.run(
            [findmnt, "-rn", "-o", "TARGET,MAJ:MIN,FSTYPE,UUID", "--mountpoint", mount],
            capture_output=True, text=True, timeout=FINDMNT_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = result.stdout.splitlines()
    if result.returncode != 0 or len(lines) != 1:
        return None
    fields = lines[0].split(" ")
    if len(fields) != 4 or unescape_findmnt(fields[0]) != mount:
        return None
    try:
        major, minor = (int(part) for part in fields[1].split(":"))
    except ValueError:
        return None
    return os.makedev(major, minor), fields[2], unescape_findmnt(fields[3])


def open_archive_mount(config):
    """The mount point's fd, after both views of the mount table agree on it."""
    mount = config.mount
    try:
        fd = open_dir_path(mount)
    except OSError as error:
        raise Skip(f"{mount} cannot be opened without following symlinks ({error.strerror})")
    try:
        device = os.fstat(fd).st_dev
        try:
            info = mountinfo_entry(config.mountinfo, mount)
        except (OSError, ValueError):
            info = None
        found = findmnt_entry(config.findmnt, mount)
        want = (device, config.fstype)
        if info != want or found is None or found != want + (config.uuid,):
            raise Skip(f"{mount} is not the expected {config.fstype} volume {config.uuid}")
        return fd
    except BaseException:
        os.close(fd)
        raise


# --- one file -----------------------------------------------------------------

class Run:
    def __init__(self, config):
        self.config = config
        self.apply = config.apply
        self.cutoff_ns = time.time_ns() - config.days * 86400 * 10**9
        self.hook = os.environ.get("ROOTFS_TIDY_ARCHIVE_TEST_HOOK", "")
        self.count = 0
        self.moved = 0
        self.failed = False
        self.device = None
        self.archive_fd = None
        self.tmp_fd = None
        self.dest_fds = {}

    def stage(self, name, rel):
        """Test seam: lets the suite race the run at fixed points. Unset in cron."""
        if self.hook:
            subprocess.run([self.hook, name, "/".join(rel)], check=True)

    def dest_dir(self, rel_dir):
        """Archive directory for a source directory, created on first use."""
        key = tuple(rel_dir)
        if key not in self.dest_fds:
            parent = self.archive_fd if not key else self.dest_dir(key[:-1])
            name = ARCHIVE_SUBDIR if not key else archive_name(key[-1])
            self.dest_fds[key] = ensure_child_dir(parent, name, self.device)
        return self.dest_fds[key]

    def close(self):
        for fd in [self.archive_fd, self.tmp_fd] + list(self.dest_fds.values()):
            if fd is not None:
                os.close(fd)


def open_source(parent_fd, name, scanned):
    fd = os.open(name, FILE_FLAGS, dir_fd=parent_fd)
    opened = os.fstat(fd)
    if not stat.S_ISREG(opened.st_mode) or not same_inode(opened, scanned):
        os.close(fd)
        raise Kept("it was replaced before it could be opened")
    return fd, opened


def existing_copy(dest_fd, dest_name):
    """Hash of a regular file already at the destination, None when absent."""
    try:
        fd = os.open(dest_name, FILE_FLAGS, dir_fd=dest_fd)
    except FileNotFoundError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Kept("the archive destination is not a regular file")
        return hash_fd(fd)[0]
    finally:
        os.close(fd)


def copy_to_temp(run, src_fd, opened):
    """Copy the opened source into a fresh exclusive temp; return (name, sha256)."""
    name = f"copy-{os.getpid()}-{secrets.token_hex(8)}"
    out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | O_CLOEXEC,
                  0o600, dir_fd=run.tmp_fd)
    digest, offset = hashlib.sha256(), 0
    try:
        while True:
            chunk = os.pread(src_fd, CHUNK, offset)
            if not chunk:
                break
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                view = view[os.write(out, view):]
            offset += len(chunk)
        os.utime(out, ns=(opened.st_atime_ns, opened.st_mtime_ns))
        os.fsync(out)
    except BaseException:
        os.close(out)
        os.unlink(name, dir_fd=run.tmp_fd)
        raise
    os.close(out)
    return name, digest.hexdigest()


def discard_temp(run, name):
    try:
        os.unlink(name, dir_fd=run.tmp_fd)
    except FileNotFoundError:
        pass


def publish_copy(run, rel, src_fd, opened, dest_fd, dest_name):
    """Make a verified archive copy; return (sha256, published_by_this_run)."""
    present = existing_copy(dest_fd, dest_name)
    if present is not None:
        digest = hash_fd(src_fd)[0]
        if present != digest:
            raise Kept("the archive already holds a different file at that name")
        return digest, False
    temp, digest = copy_to_temp(run, src_fd, opened)
    try:
        run.stage("after-copy", rel)
        temp_fd = os.open(temp, FILE_FLAGS, dir_fd=run.tmp_fd)
        try:
            copied = hash_fd(temp_fd)
        finally:
            os.close(temp_fd)
        if copied != (digest, opened.st_size):
            raise Kept("the copy does not match the source")
        if not unchanged(os.fstat(src_fd), opened):
            raise Kept("it changed while being copied")
        try:
            rename_noreplace(run.tmp_fd, temp, dest_fd, dest_name)
        except FileExistsError:
            raise Kept("a file appeared at the archive destination during the copy")
    except BaseException:
        discard_temp(run, temp)
        raise
    os.fsync(dest_fd)
    if existing_copy(dest_fd, dest_name) != digest:
        raise Kept("the published copy does not verify")
    return digest, True


def withdraw_copy(dest_fd, dest_name, digest):
    """Remove this run's copy again after the source changed, so a later run retries."""
    try:
        if existing_copy(dest_fd, dest_name) == digest:
            os.unlink(dest_name, dir_fd=dest_fd)
            os.fsync(dest_fd)
    except (OSError, Kept):
        pass


def restore_name(parent_fd, quarantined, name):
    try:
        rename_noreplace(parent_fd, quarantined, parent_fd, name)
        return ""
    except OSError:
        return f"; left as {quarantined}"


def retire_source(run, rel, parent_fd, name, src_fd, opened, digest):
    """Quarantine, prove it is still the archived content, then unlink."""
    run.stage("before-quarantine", rel)
    quarantined = QUARANTINE_PREFIX + name
    try:
        rename_noreplace(parent_fd, name, parent_fd, quarantined)
    except OSError as error:
        raise Kept(f"it could not be set aside ({error.strerror})")
    try:
        held = os.stat(quarantined, dir_fd=parent_fd, follow_symlinks=False)
        if not same_inode(held, opened):
            raise Kept("another file took its name before it was set aside")
        run.stage("after-quarantine", rel)
        if not unchanged(os.fstat(src_fd), opened) or hash_fd(src_fd) != (digest, opened.st_size):
            raise Kept("it changed after it was copied")
        held = os.stat(quarantined, dir_fd=parent_fd, follow_symlinks=False)
        if not unchanged(os.fstat(src_fd), opened) or not same_inode(held, opened):
            raise Kept("it changed after it was copied")
        run.stage("before-unlink", rel)
        os.unlink(quarantined, dir_fd=parent_fd)
    except Kept as kept:
        raise Kept(f"{kept}{restore_name(parent_fd, quarantined, name)}")
    except BaseException:
        restore_name(parent_fd, quarantined, name)
        raise


def archive_file(run, rel, parent_fd, name, scanned):
    src_fd, opened = open_source(parent_fd, name, scanned)
    try:
        dest_fd = run.dest_dir(rel[:-1])
        dest_name = archive_name(name)
        digest, published = publish_copy(run, rel, src_fd, opened, dest_fd, dest_name)
        try:
            retire_source(run, rel, parent_fd, name, src_fd, opened, digest)
        except BaseException:
            if published:
                withdraw_copy(dest_fd, dest_name, digest)
            raise
    finally:
        os.close(src_fd)


# --- the walk -------------------------------------------------------------------

def restore_leftover(run, rel, parent_fd, name):
    """A .tidy-moving- entry an interrupted run left: put it back under its name."""
    original = name[len(QUARANTINE_PREFIX):]
    if not run.apply:
        report(f"would restore {shown(rel)} to {original!r} (left by an interrupted run)")
        return
    try:
        rename_noreplace(parent_fd, name, parent_fd, original)
        report(f"restored {shown(rel)} to its name (left by an interrupted run)")
    except OSError as error:
        report(f"kept {shown(rel)}: cannot restore its name ({error.strerror})", sys.stderr)
        run.failed = True


def visit_file(run, rel, parent_fd, name, scanned):
    if scanned.st_mtime_ns >= run.cutoff_ns:
        return
    run.count += 1
    if not run.apply:
        report(f"would move {shown(rel)}")
        return
    try:
        archive_file(run, rel, parent_fd, name, scanned)
        run.moved += 1
    except Kept as kept:
        report(f"kept {shown(rel)}: {kept}", sys.stderr)
        run.failed = True
    except (OSError, subprocess.CalledProcessError) as error:
        report(f"kept {shown(rel)}: {error}", sys.stderr)
        run.failed = True


def visit_dir(run, rel, parent_fd, name, scanned):
    try:
        fd = open_child_dir(parent_fd, name, scanned)
    except (OSError, Kept) as error:
        report(f"skipped {shown(rel)}: {error}", sys.stderr)
        run.failed = True
        return
    try:
        walk(run, rel, fd)
    finally:
        os.close(fd)
    if run.apply and scanned.st_mtime_ns < run.cutoff_ns:
        remove_if_empty(parent_fd, name, scanned)


def remove_if_empty(parent_fd, name, scanned):
    """rmdir refuses a non-empty directory, so a racing writer only keeps it."""
    try:
        if same_inode(os.stat(name, dir_fd=parent_fd, follow_symlinks=False), scanned):
            os.rmdir(name, dir_fd=parent_fd)
    except OSError:
        pass


def walk(run, rel, dir_fd):
    with os.scandir(dir_fd) as entries:
        names = sorted(entry.name for entry in entries)
    for name in names:
        child = rel + (name,)
        try:
            scanned = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError as error:
            report(f"skipped {shown(child)}: {error.strerror}", sys.stderr)
            run.failed = True
            continue
        if stat.S_ISLNK(scanned.st_mode):
            report(f"skipped symlink {shown(child)}")
        elif stat.S_ISDIR(scanned.st_mode):
            visit_dir(run, child, dir_fd, name, scanned)
        elif not stat.S_ISREG(scanned.st_mode):
            report(f"skipped {shown(child)} (not a regular file)")
        elif name.startswith(QUARANTINE_PREFIX):
            restore_leftover(run, child, dir_fd, name)
        else:
            visit_file(run, child, dir_fd, name, scanned)


def clear_stale_temps(run):
    """Temps from an interrupted run; the archive lock says none is in use."""
    with os.scandir(run.tmp_fd) as entries:
        names = [entry.name for entry in entries if entry.name.startswith("copy-")]
    for name in names:
        discard_temp(run, name)


def prepare_archive(run, mount_fd):
    run.device = os.fstat(mount_fd).st_dev
    run.archive_fd = ensure_child_dir(mount_fd, ARCHIVE_DIR, run.device)
    try:
        fcntl.flock(run.archive_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise Skip("another archive run holds the lock")
    run.tmp_fd = ensure_child_dir(run.archive_fd, TMP_DIR, run.device)
    clear_stale_temps(run)


def sweep(run, codex_fd):
    for subtree in SUBTREES:
        try:
            fd = os.open(subtree, DIR_FLAGS, dir_fd=codex_fd)
        except FileNotFoundError:
            continue
        except OSError as error:
            report(f"skipped {subtree} ({error.strerror}; a symlink is never followed)")
            continue
        try:
            walk(run, (subtree,), fd)
        finally:
            os.close(fd)


def run_archive(config):
    run = Run(config)
    mount_fd = None
    try:
        mount_fd = open_archive_mount(config)
        codex_path = os.path.join(config.home, ".codex")
        try:
            codex_fd = open_dir_path(codex_path)
        except OSError as error:
            raise Skip(f"{codex_path} cannot be opened without following symlinks"
                       f" ({error.strerror})")
        try:
            if run.apply:
                prepare_archive(run, mount_fd)
            sweep(run, codex_fd)
        finally:
            os.close(codex_fd)
    except Skip as skip:
        report(f"skipped ({skip})")
        return 0
    except OSError as error:
        report(f"failed: {error}", sys.stderr)
        return 1
    finally:
        run.close()
        if mount_fd is not None:
            os.close(mount_fd)
    report(f"{run.count} file(s) older than {config.days} days, {run.moved} moved")
    return 1 if run.failed else 0


def parse_args(argv):
    env = os.environ.get
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="move files")
    mode.add_argument("--dry-run", action="store_true", help="only list them (the default)")
    parser.add_argument("--home", default="/home/pi", help="the data owner's home")
    parser.add_argument("--days", type=int, default=int(env("ROOTFS_TIDY_CODEX_DAYS", DEFAULT_DAYS)))
    config = parser.parse_args(argv)
    config.mount = env("ROOTFS_TIDY_ARCHIVE_MOUNT", DEFAULT_MOUNT)
    config.fstype = env("ROOTFS_TIDY_ARCHIVE_FSTYPE", DEFAULT_FSTYPE)
    config.uuid = env("ROOTFS_TIDY_ARCHIVE_UUID", DEFAULT_UUID)
    config.findmnt = env("ROOTFS_TIDY_FINDMNT", DEFAULT_FINDMNT)
    config.mountinfo = env("ROOTFS_TIDY_MOUNTINFO", DEFAULT_MOUNTINFO)
    if config.days < 1:
        parser.error("--days must be at least 1")
    return config


def main(argv=None):
    return run_archive(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    sys.exit(main())
