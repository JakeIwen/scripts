#!/usr/bin/python3
"""Age-based SD-root sweeps for rootfs_tidy.sh.

rootfs_tidy.sh runs this as the data owner (`pi`, through setpriv when it runs
as root) with `/usr/bin/python3 -I` (Pi Python 3.11; stdlib only and
3.9-compatible so the Mac suite runs it). Dry run unless --apply. Exit 0 when
every sweep finished or was skipped, 1 when something could not be inspected
or deleted, 2 on usage errors.

- Codex sessions: regular files under ~/.codex/archived_sessions/ older than
  30 days are deleted. Live sessions/ and migration-backups/ are never opened.
- obd tmp: `suite-*` directories idle for 2 days and `playwright-*` directories
  idle for 14 days in ~/dev/obd-things/tmp are removed whole (idle means no
  entry anywhere inside is newer). tmp/playwright itself and every other name
  are never matched.
- caches: files in ~/.npm/_cacache and ~/.cache/pip older than 30 days.
- agent-shared: files over 200 MB and older than 14 days are listed, never
  deleted.

Every root is opened one component at a time from / with
O_DIRECTORY|O_NOFOLLOW, so a root reached through a symlink is skipped, and
every later open, lstat, unlink and rmdir is relative to a held directory
descriptor: replacing a root or any directory with a symlink mid-run cannot
redirect a delete.

Nothing on another mount is entered or deleted. On Linux every directory
opened, roots included, must have the home's mount id (`mnt_id` in
/proc/self/fdinfo), which is fixed for a held descriptor and also tells a
same-device bind mount apart, wherever and whenever it appears. Everywhere,
an entry whose st_dev differs from the home's is skipped, and mountinfo is
re-read before each root and before each scratch directory is removed: a root
with a mount point on its path below the home, and a scratch directory that
is or contains a mount point, are left alone.

Before each unlink the entry is lstat-ed again. A session or cache file is
kept unless it is still the same inode with the same size and mtime. A
scratch directory is rechecked right before removal (its own mtime, then each
directory's before it is entered, each other entry's before it is unlinked);
anything newer than the cutoff is kept, so its parents' rmdir fails and they
stay. Residual windows: an entry swapped, or appended to by a writer that
already held it open, between that last lstat and the unlink; and, only where
there is no /proc (the Mac suite), a same-device bind mount made after the
last mountinfo read. Every candidate has been idle for the whole cutoff, so
those windows are accepted.
"""
import argparse
import errno
import os
import re
import stat
import subprocess
import sys
import time

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
DAY_NS = 86400 * 10**9
MIB = 1 << 20
DEFAULT_MOUNTINFO = "/proc/self/mountinfo"
# Opening a symlink with O_NOFOLLOW fails ELOOP (Linux); with O_DIRECTORY macOS
# can say ENOTDIR, as it does for a component that is not a directory at all.
SYMLINKED = (errno.ELOOP, errno.ENOTDIR)
# Test seam: an executable run as `HOOK <stage> <relative path>` at the last
# point before a delete, so the suite can race a change against it.
TEST_HOOK = "ROOTFS_TIDY_TEST_HOOK"
MOUNTINFO_ESCAPE = re.compile(r"\\([0-7]{3})")


def env_int(name, default):
    return int(os.environ.get(name, default))


def report(label, message, error=False):
    print(f"{label}: {message}", file=sys.stderr if error else sys.stdout, flush=True)


def shown(rel):
    """A relative path for messages, with control characters made visible."""
    return "/".join(rel).encode("unicode_escape", "backslashreplace").decode("ascii")


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


def read_mount_points(path):
    """Mount points (mountinfo field 5, octal-escaped). Only a non-Linux host
    may lack the file; there st_dev alone separates filesystems."""
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as handle:
            lines = handle.read().splitlines()
    except FileNotFoundError:
        if sys.platform.startswith("linux"):
            raise
        return frozenset()
    points = set()
    for line in lines:
        fields = line.split(" ")
        if len(fields) > 4:
            points.add(MOUNTINFO_ESCAPE.sub(lambda m: chr(int(m.group(1), 8)), fields[4]))
    return frozenset(points)


def mount_id(fd):
    """The mount id of an open descriptor; None only where there is no /proc."""
    try:
        with open(f"/proc/self/fdinfo/{fd}", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("mnt_id:"):
                    return int(line.split()[1])
    except FileNotFoundError:
        if sys.platform.startswith("linux"):
            raise
        return None
    raise OSError(errno.EINVAL, "no mnt_id in fdinfo", fd)


def within(path, point):
    return path == point or path.startswith(point.rstrip("/") + "/")


def unchanged(a, b):
    return ((a.st_dev, a.st_ino, a.st_size, a.st_mtime_ns)
            == (b.st_dev, b.st_ino, b.st_size, b.st_mtime_ns))


class Tidy:
    """Settings and outcome shared by every sweep of one run."""

    def __init__(self, apply, home, device, mount, mountinfo):
        self.apply = apply
        self.home = home
        self.device = device
        self.mount = mount
        self.mountinfo = mountinfo
        self.mounts = read_mount_points(mountinfo)
        self.now_ns = time.time_ns()
        self.hook = os.environ.get(TEST_HOOK)
        self.failed = False

    def cutoff(self, days):
        return self.now_ns - days * DAY_NS

    def foreign(self, st, path):
        return st.st_dev != self.device or path in self.mounts

    def refresh_mounts(self):
        self.mounts = read_mount_points(self.mountinfo)

    def same_mount(self, fd):
        st = os.fstat(fd)
        return st.st_dev == self.device and mount_id(fd) == self.mount

    def mounted_below(self, path):
        prefix = path.rstrip("/") + "/"
        return any(point.startswith(prefix) for point in self.mounts)

    def mount_on_path(self, path):
        """A mount point at `path` or on its ancestry below the home, if any."""
        for point in self.mounts:
            if within(path, point) and not within(self.home, point):
                return point
        return None

    def stage(self, name, rel):
        if self.hook:
            subprocess.run([self.hook, name, shown(rel)], check=True)


class Walk:
    """One sweep's traversal: no symlink followed, one filesystem, errors counted."""

    def __init__(self, tidy, label):
        self.tidy = tidy
        self.label = label
        self.errors = 0
        self.mounted = 0

    def problem(self, rel, message):
        report(self.label, f"skipped {shown(rel)}: {message}", error=True)
        self.errors += 1

    def note(self, rel, message):
        report(self.label, f"skipped {shown(rel)} ({message})")

    def names(self, dir_fd, rel):
        try:
            with os.scandir(dir_fd) as entries:
                return sorted(entry.name for entry in entries)
        except OSError as error:
            self.problem(rel, error.strerror)
            return None

    def lstat(self, dir_fd, name, rel):
        try:
            return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError as error:
            self.problem(rel, error.strerror)
            return None

    def open_dir(self, parent_fd, name, scanned, rel):
        """The child's fd, only if it is still the directory that was lstat-ed."""
        try:
            fd = os.open(name, DIR_FLAGS, dir_fd=parent_fd)
        except OSError as error:
            self.problem(rel, error.strerror)
            return None
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (scanned.st_dev, scanned.st_ino):
            os.close(fd)
            self.problem(rel, "it was replaced while being scanned")
            return None
        if not self.tidy.same_mount(fd):
            os.close(fd)
            self.note(rel, "another filesystem is mounted there")
            self.mounted += 1
            return None
        return fd

    def kind(self, st, path, rel):
        """'dir' or 'file', or None after saying why the entry is skipped."""
        if stat.S_ISLNK(st.st_mode):
            self.note(rel, "symlink")
        elif self.tidy.foreign(st, path):
            self.note(rel, "another filesystem is mounted there")
        elif stat.S_ISDIR(st.st_mode):
            return "dir"
        elif stat.S_ISREG(st.st_mode):
            return "file"
        else:
            self.note(rel, "not a regular file")
        return None

    def files(self, dir_fd, path, rel):
        """Yield (dir_fd, name, lstat, rel) for every regular file below dir_fd."""
        for name in self.names(dir_fd, rel) or ():
            child, child_path = rel + (name,), os.path.join(path, name)
            st = self.lstat(dir_fd, name, child)
            kind = st and self.kind(st, child_path, child)
            if kind == "file":
                yield dir_fd, name, st, child
            elif kind == "dir":
                fd = self.open_dir(dir_fd, name, st, child)
                if fd is None:
                    continue
                try:
                    yield from self.files(fd, child_path, child)
                finally:
                    os.close(fd)


def open_root(tidy, label, path):
    """The root's fd, or None once a skip is reported; other errors fail the run."""
    try:
        tidy.refresh_mounts()
    except OSError as error:
        report(label, f"failed: cannot read the mount table ({error})", error=True)
        tidy.failed = True
        return None
    point = tidy.mount_on_path(path)
    if point is not None:
        report(label, f"skipped ({point} is a mount point on the way to {path})")
        return None
    try:
        fd = open_dir_path(path)
    except FileNotFoundError:
        report(label, f"skipped ({path} does not exist)")
        return None
    except OSError as error:
        if error.errno in SYMLINKED:
            report(label, f"skipped ({path} is reached through a symlink or is not a directory)")
        else:
            report(label, f"failed: cannot open {path} ({error.strerror})", error=True)
            tidy.failed = True
        return None
    if not tidy.same_mount(fd):
        os.close(fd)
        report(label, f"skipped ({path} is on another mount)")
        return None
    return fd


# --- old files: Codex sessions and download caches ------------------------------

def delete_if_unchanged(tidy, walk, dir_fd, name, selected, rel):
    """Unlink `name` only if it is still exactly the file that was selected."""
    tidy.stage("before-unlink", rel)
    try:
        current = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as error:
        walk.problem(rel, error.strerror)
        return False
    if not unchanged(current, selected):
        report(walk.label, f"kept {shown(rel)}: it changed after it was selected")
        return False
    try:
        os.unlink(name, dir_fd=dir_fd)
    except FileNotFoundError:
        return False
    except OSError as error:
        report(walk.label, f"kept {shown(rel)}: {error.strerror}", error=True)
        walk.errors += 1
        return False
    return True


def delete_old_files(tidy, label, root, days, prefix=(), each=True):
    """Delete regular files below `root` older than `days`; `each` logs every file."""
    fd = open_root(tidy, label, root)
    if fd is None:
        return
    walk = Walk(tidy, label)
    cutoff = tidy.cutoff(days)
    count = deleted = size = 0
    try:
        for dir_fd, name, st, rel in walk.files(fd, root, prefix):
            if st.st_mtime_ns >= cutoff:
                continue
            count += 1
            if not tidy.apply:
                size += st.st_size
                if each:
                    report(label, f"would delete {shown(rel)} ({st.st_size} bytes)")
            elif delete_if_unchanged(tidy, walk, dir_fd, name, st, rel):
                deleted += 1
                size += st.st_size
                if each:
                    report(label, f"deleted {shown(rel)} ({st.st_size} bytes)")
    finally:
        os.close(fd)
    verb, done = ("deleted", deleted) if tidy.apply else ("would delete", count)
    report(label, f"{count} file(s) older than {days} days, {verb} {done}, {size} bytes")
    tidy.failed |= walk.errors > 0


# --- obd-things scratch directories --------------------------------------------

SCRATCH_PREFIXES = (
    ("suite-", "ROOTFS_TIDY_SUITE_DAYS", 2),
    ("playwright-", "ROOTFS_TIDY_PLAYWRIGHT_DAYS", 14),
)


def scratch_days(name):
    for prefix, variable, default in SCRATCH_PREFIXES:
        if name.startswith(prefix):
            return env_int(variable, default)
    return None


def idle_state(tidy, walk, dir_fd, path, rel, cutoff):
    """'idle', 'recent', 'mounted' or 'unknown' (an entry could not be read)."""
    errors, mounted = walk.errors, walk.mounted
    names = walk.names(dir_fd, rel)
    for name in names or ():
        child, child_path = rel + (name,), os.path.join(path, name)
        st = walk.lstat(dir_fd, name, child)
        if st is None:
            continue
        if tidy.foreign(st, child_path):
            return "mounted"
        if st.st_mtime_ns >= cutoff:
            return "recent"
        if stat.S_ISDIR(st.st_mode):
            fd = walk.open_dir(dir_fd, name, st, child)
            if fd is None:
                if walk.mounted > mounted:
                    return "mounted"
                continue
            try:
                state = idle_state(tidy, walk, fd, child_path, child, cutoff)
            finally:
                os.close(fd)
            if state != "idle":
                return state
    return "unknown" if names is None or walk.errors > errors else "idle"


def remove_contents(tidy, walk, dir_fd, path, rel, cutoff):
    """Delete everything below dir_fd that is still old; True when it is empty."""
    names = walk.names(dir_fd, rel)
    empty = names is not None
    for name in names or ():
        child, child_path = rel + (name,), os.path.join(path, name)
        st = walk.lstat(dir_fd, name, child)
        if st is None:
            continue
        # A directory is checked before it is entered (entering it changes
        # nothing; the unlinks inside are what update its mtime).
        if tidy.foreign(st, child_path) or st.st_mtime_ns >= cutoff:
            empty = False
            continue
        is_dir = stat.S_ISDIR(st.st_mode)
        try:
            if is_dir:
                fd = walk.open_dir(dir_fd, name, st, child)
                if fd is None:
                    empty = False
                    continue
                try:
                    removed = remove_contents(tidy, walk, fd, child_path, child, cutoff)
                finally:
                    os.close(fd)
                if removed:
                    os.rmdir(name, dir_fd=dir_fd)
                else:
                    empty = False
            else:
                os.unlink(name, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        except OSError as error:
            if error.errno != errno.ENOTEMPTY:
                walk.problem(child, error.strerror)
            empty = False
    return empty


def remove_checked(tidy, walk, root_fd, fd, path, name, cutoff):
    """Remove an idle candidate after rechecking mounts and its own freshness."""
    label = walk.label
    tidy.stage("before-remove", (name,))
    try:
        tidy.refresh_mounts()
    except OSError as error:
        report(label, f"kept {shown((name,))} (cannot read the mount table: {error})", error=True)
        walk.errors += 1
        return
    if tidy.mount_on_path(path) or tidy.mounted_below(path):
        report(label, f"kept {shown((name,))} (a filesystem is mounted there)")
    elif os.fstat(fd).st_mtime_ns >= cutoff:
        report(label, f"kept {shown((name,))} (recent writes inside)")
    elif remove_contents(tidy, walk, fd, path, (name,), cutoff):
        os.rmdir(name, dir_fd=root_fd)
        report(label, f"removed {shown((name,))}")
    else:
        report(label, f"kept {shown((name,))} (it changed while being removed)")


def remove_idle_dir(tidy, walk, root_fd, root, name, st, cutoff):
    label = walk.label
    path = os.path.join(root, name)
    if tidy.foreign(st, path) or tidy.mounted_below(path):
        report(label, f"kept {shown((name,))} (a filesystem is mounted there)")
        return
    if st.st_mtime_ns >= cutoff:
        report(label, f"kept {shown((name,))} (recent writes inside)")
        return
    fd = walk.open_dir(root_fd, name, st, (name,))
    if fd is None:
        return
    try:
        state = idle_state(tidy, walk, fd, path, (name,), cutoff)
        if state == "unknown":
            report(label, f"kept {shown((name,))} (could not inspect it completely)", error=True)
        elif state == "mounted":
            report(label, f"kept {shown((name,))} (a filesystem is mounted inside)")
        elif state == "recent":
            report(label, f"kept {shown((name,))} (recent writes inside)")
        elif not tidy.apply:
            report(label, f"would remove {shown((name,))}")
        else:
            remove_checked(tidy, walk, root_fd, fd, path, name, cutoff)
    except OSError as error:
        walk.problem((name,), error.strerror)
    finally:
        os.close(fd)


def sweep_scratch(tidy, root, label="obd tmp"):
    fd = open_root(tidy, label, root)
    if fd is None:
        return
    walk = Walk(tidy, label)
    try:
        for name in walk.names(fd, ()) or ():
            days = scratch_days(name)
            st = walk.lstat(fd, name, (name,)) if days else None
            if st is not None and stat.S_ISDIR(st.st_mode):
                remove_idle_dir(tidy, walk, fd, root, name, st, tidy.cutoff(days))
    finally:
        os.close(fd)
    tidy.failed |= walk.errors > 0


# --- agent-shared report ----------------------------------------------------------

def report_big_files(tidy, root, label="agent-shared"):
    """Report only: these are user media and evidence, so a person decides."""
    fd = open_root(tidy, label, root)
    if fd is None:
        return
    walk = Walk(tidy, label)
    big = env_int("ROOTFS_TIDY_BIG_BYTES", 200 * MIB)
    days = env_int("ROOTFS_TIDY_BIG_DAYS", 14)
    cutoff = tidy.cutoff(days)
    report(label, f"files over {big // MIB} MB and older than {days} days (report only)")
    try:
        for _, _, st, rel in walk.files(fd, root, ()):
            if st.st_size > big and st.st_mtime_ns < cutoff:
                day = time.strftime("%Y-%m-%d", time.localtime(st.st_mtime))
                print(f"  {shown(rel)}  {st.st_size} bytes, modified {day}", flush=True)
    finally:
        os.close(fd)
    if walk.errors:
        report(label, "some paths could not be read; the list may be incomplete")


# --- entry ------------------------------------------------------------------------

def home_identity(home):
    """The home's (st_dev, mount id), or None when it is absent or symlinked."""
    try:
        fd = open_dir_path(home)
    except FileNotFoundError:
        return None
    except OSError as error:
        if error.errno in SYMLINKED:
            return None
        raise
    try:
        return os.fstat(fd).st_dev, mount_id(fd)
    finally:
        os.close(fd)


def run(config):
    home = config.home
    try:
        identity = home_identity(home)
        if identity is not None:
            tidy = Tidy(config.apply, home, *identity,
                        os.environ.get("ROOTFS_TIDY_MOUNTINFO", DEFAULT_MOUNTINFO))
    except OSError as error:
        report("sweeps", f"failed: {error}", error=True)
        return 1
    if identity is None:
        report("sweeps", f"skipped ({home} is missing or reached through a symlink)")
        return 0
    delete_old_files(tidy, "Codex sessions", os.path.join(home, ".codex", "archived_sessions"),
                     env_int("ROOTFS_TIDY_CODEX_DAYS", 30), prefix=("archived_sessions",))
    sweep_scratch(tidy, os.environ.get("ROOTFS_TIDY_OBD_TMP",
                                       os.path.join(home, "dev", "obd-things", "tmp")))
    # Content-addressed download caches only (a missing file is a refetch). Never
    # ~/.cache wholesale: it also holds state such as hook-trust and Firefox data.
    for rel in (".npm/_cacache", ".cache/pip"):
        delete_old_files(tidy, f"cache {rel}", os.path.join(home, rel),
                         env_int("ROOTFS_TIDY_CACHE_DAYS", 30), each=False)
    report_big_files(tidy, os.environ.get("ROOTFS_TIDY_AGENT_SHARED",
                                          os.path.join(home, "Documents", "agent-shared")))
    return 1 if tidy.failed else 0


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="delete")
    mode.add_argument("--dry-run", action="store_true", help="only report (the default)")
    parser.add_argument("--home", default="/home/pi", help="the data owner's home")
    return parser.parse_args(argv)


def main(argv=None):
    return run(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    sys.exit(main())
