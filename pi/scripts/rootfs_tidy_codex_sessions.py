#!/usr/bin/python3
"""Delete old Codex archived sessions from the SD root.

rootfs_tidy_sweeps.sh runs this as `pi` with `/usr/bin/python3 -I` (Pi Python
3.11; stdlib only, 3.9-compatible so the Mac suite runs it). Dry run unless
--apply. Exit 0 when the sweep finished (a file kept because it changed counts
as finished), 1 when something could not be inspected or deleted, 2 on usage
errors.

Regular files anywhere under ~/.codex/archived_sessions/ whose mtime is older
than the cutoff (30 days) are unlinked. Live sessions/ is never opened. The
walk opens each directory O_DIRECTORY|O_NOFOLLOW relative to its parent's
descriptor and lstats entries relative to it, so a renamed or symlink-swapped
directory cannot redirect a delete; symlinks, other non-regular files and
anything on another filesystem are skipped.

Each candidate is lstat-ed again immediately before unlinkat and kept unless it
is still the same inode with the same size and mtime, still older than the
cutoff. Residual window: an entry swapped between that re-stat and the unlink,
or a writer that already held the file open and appends in that moment. Codex
never writes a session after archiving it, and only files idle for the whole
cutoff qualify, so that window is accepted.
"""
import argparse
import os
import stat
import subprocess
import sys
import time

DEFAULT_DAYS = 30
SUBTREE = ("archived_sessions",)
LABEL = "Codex sessions"
# Test seam: an executable run as `HOOK <relative path>` between selection and
# the re-stat, so the suite can change a file at exactly that point.
TEST_HOOK = "ROOTFS_TIDY_CODEX_TEST_HOOK"

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def report(message, stream=sys.stdout):
    print(f"{LABEL}: {message}", file=stream, flush=True)


def shown(rel):
    """A relative path for messages, with control characters made visible."""
    return "/".join(rel).encode("unicode_escape", "backslashreplace").decode("ascii")


def open_dir_path(path):
    """Open an absolute path one component at a time, refusing any symlink."""
    if not os.path.isabs(path):
        raise OSError(f"not an absolute path: {path}")
    fd = os.open("/", DIR_FLAGS)
    try:
        for part in [p for p in path.split("/") if p]:
            if part in (".", ".."):
                raise OSError(f"path is not normalized: {path}")
            child = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def unchanged(a, b):
    return ((a.st_dev, a.st_ino, a.st_size, a.st_mtime_ns)
            == (b.st_dev, b.st_ino, b.st_size, b.st_mtime_ns))


class Run:
    def __init__(self, config):
        self.apply = config.apply
        self.cutoff_ns = time.time_ns() - config.days * 86400 * 10**9
        self.hook = os.environ.get(TEST_HOOK)
        self.device = None
        self.count = 0
        self.deleted = 0
        self.bytes = 0
        self.failed = False

    def problem(self, message):
        report(message, sys.stderr)
        self.failed = True


def delete_file(run, rel, dir_fd, name, selected):
    """Unlink `name` only if it is still exactly the file that was selected."""
    if run.hook:
        subprocess.run([run.hook, shown(rel)], check=True)
    try:
        current = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        report(f"kept {shown(rel)}: gone before it could be deleted")
        return
    if not unchanged(current, selected) or current.st_mtime_ns >= run.cutoff_ns:
        report(f"kept {shown(rel)}: it changed after it was selected")
        return
    try:
        os.unlink(name, dir_fd=dir_fd)
    except FileNotFoundError:
        report(f"kept {shown(rel)}: gone before it could be deleted")
        return
    except OSError as error:
        run.problem(f"kept {shown(rel)}: {error.strerror}")
        return
    run.deleted += 1
    run.bytes += selected.st_size
    report(f"deleted {shown(rel)} ({selected.st_size} bytes)")


def visit_file(run, rel, dir_fd, name, scanned):
    if scanned.st_mtime_ns >= run.cutoff_ns:
        return
    run.count += 1
    if not run.apply:
        run.bytes += scanned.st_size
        report(f"would delete {shown(rel)} ({scanned.st_size} bytes)")
        return
    delete_file(run, rel, dir_fd, name, scanned)


def visit_dir(run, rel, parent_fd, name, scanned):
    try:
        fd = os.open(name, DIR_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        run.problem(f"skipped {shown(rel)}: {error.strerror}")
        return
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (scanned.st_dev, scanned.st_ino):
            run.problem(f"skipped {shown(rel)}: it was replaced while being scanned")
            return
        walk(run, rel, fd)
    finally:
        os.close(fd)


def walk(run, rel, dir_fd):
    try:
        with os.scandir(dir_fd) as entries:
            names = sorted(entry.name for entry in entries)
    except OSError as error:
        run.problem(f"skipped {shown(rel)}: {error.strerror}")
        return
    for name in names:
        child = rel + (name,)
        try:
            scanned = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError as error:
            run.problem(f"skipped {shown(child)}: {error.strerror}")
            continue
        if stat.S_ISLNK(scanned.st_mode):
            report(f"skipped symlink {shown(child)}")
        elif scanned.st_dev != run.device:
            report(f"skipped {shown(child)} (on another filesystem)")
        elif stat.S_ISDIR(scanned.st_mode):
            visit_dir(run, child, dir_fd, name, scanned)
        elif stat.S_ISREG(scanned.st_mode):
            visit_file(run, child, dir_fd, name, scanned)
        else:
            report(f"skipped {shown(child)} (not a regular file)")


def run_sweep(config):
    run = Run(config)
    root = os.path.join(config.home, ".codex", *SUBTREE)
    try:
        root_fd = open_dir_path(root)
    except FileNotFoundError:
        report(f"skipped ({root} does not exist)")
        return 0
    except OSError as error:
        report(f"skipped ({root} cannot be opened without following symlinks:"
                f" {getattr(error, 'strerror', None) or error})")
        return 0
    try:
        run.device = os.fstat(root_fd).st_dev
        walk(run, SUBTREE, root_fd)
    finally:
        os.close(root_fd)
    verb = "deleted" if config.apply else "would delete"
    report(f"{run.count} file(s) older than {config.days} days,"
           f" {verb} {run.deleted if config.apply else run.count}, {run.bytes} bytes")
    return 1 if run.failed else 0


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="delete files")
    mode.add_argument("--dry-run", action="store_true", help="only list them (the default)")
    parser.add_argument("--home", default="/home/pi", help="the data owner's home")
    parser.add_argument("--days", type=int,
                        default=int(os.environ.get("ROOTFS_TIDY_CODEX_DAYS", DEFAULT_DAYS)))
    config = parser.parse_args(argv)
    if config.days < 1:
        parser.error("--days must be at least 1")
    return config


def main(argv=None):
    return run_sweep(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    sys.exit(main())
