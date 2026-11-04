"""Mirror a checkout's working tree and Git state onto the VM's own disk.

The VM reads the checkout through a read-only mount and copies it with rsync,
so tests never write to (or time against) the Mac's files. Only the files
run_tests.py would discover travel; the VM keeps its own runner state and venv.
"""
from pathlib import Path, PurePosixPath
import os
import pwd
import stat
import subprocess
import sys

# Mac-built dependencies and other runners' trees never reach the VM.
EXCLUDED_PREFIXES = ('.local/', '.claude/worktrees/', '.git/')
EXCLUDED_DIRECTORY = 'node_modules'
# Receiver-side paths the tree sync must never delete: the VM's Git copy (with
# its run_tests/ pass cache and timings) is maintained by the Git sync instead.
PROTECTED = ('/.git/', '/.local/', 'node_modules/')
# Per-worktree and per-runner Git state; HEAD and index come from the worktree.
GIT_EXCLUDES = ('/worktrees/', '/run_tests/', '/index', '/HEAD', '/ORIG_HEAD',
                '/FETCH_HEAD', '/hooks/', '*.lock')
WILDCARDS = '*?['
RSYNC = ('rsync', '-a', '--checksum', '--mkpath')


def validate_destination(destination):
    """Refuse redirects before rsync can delete anything outside the lane."""
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    expected = home / 'linux-test-lane/checkout'
    if Path(destination) != expected:
        raise ValueError(f'Unexpected VM checkout: {destination}')
    target = expected / '.git'
    device = home.stat().st_dev
    for path in (*reversed(target.parents), target):
        try:
            info = path.lstat()
        except FileNotFoundError:
            if path not in (home / 'linux-test-lane', expected, target):
                raise ValueError(f'Missing VM ancestor: {path}')
            path.mkdir(mode=0o755)
            info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or path.resolve() != path:
            raise ValueError(f'Unsafe VM sync directory: {path}')
        if path == home or home in path.parents:
            if info.st_dev != device or info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise ValueError(f'Non-private or redirected VM sync directory: {path}')


def destination_command(destination, arguments=()):
    # Run this same stdlib-only module on the guest, before provisioning or exec.
    return ['/usr/bin/python3', '-B', str(Path(__file__).resolve()), str(destination), *arguments]



def synced_paths(paths, root):
    """Repository paths to copy, as POSIX strings; deleted files drop out."""
    result = []
    for path in paths:
        text = PurePosixPath(path).as_posix()
        if text.startswith(EXCLUDED_PREFIXES) or EXCLUDED_DIRECTORY in PurePosixPath(text).parts:
            continue
        if os.path.lexists(Path(root) / text):
            result.append(text)
    return result


def rule_pattern(path):
    if any(character in path for character in '\n\r\\') or path != path.strip():
        raise ValueError(f'cannot express {path!r} as an rsync filter rule')
    if any(character in path for character in WILDCARDS):
        # rsync honours backslash escapes only in patterns holding a wildcard.
        path = ''.join('\\' + character if character in WILDCARDS else character
                       for character in path)
    return path


def filter_rules(paths):
    """rsync rules that copy exactly these paths and delete everything else."""
    directories = sorted({parent.as_posix() for path in paths
                          for parent in PurePosixPath(path).parents if parent.parts})
    lines = [f'P {pattern}' for pattern in PROTECTED]
    lines += [f'+ /{rule_pattern(directory)}/' for directory in directories]
    lines += [f'+ /{rule_pattern(path)}' for path in paths]
    lines.append('- *')
    return ''.join(line + '\n' for line in lines)


def tree_command(source, destination):
    # Rules arrive on stdin; --delete-excluded removes files no longer listed,
    # and the P rules above keep the VM's own state.
    return destination_command(destination, [*RSYNC, '--delete', '--delete-excluded', '--filter=. -',
                                       f'{source}/', f'{destination}/'])


def git_mirror_command(common_dir, destination):
    excludes = [f'--exclude={pattern}' for pattern in GIT_EXCLUDES]
    return destination_command(destination, [*RSYNC, '--delete', *excludes,
                                       f'{common_dir}/', f'{destination}/.git/'])


def git_state_command(git_dir, destination):
    """The checkout's own HEAD and index (a worktree's live in its git dir)."""
    return destination_command(destination, [*RSYNC, '--ignore-missing-args', f'{git_dir}/HEAD',
                                       f'{git_dir}/index', f'{destination}/.git/'])


def git_path(root, option):
    output = subprocess.run(['git', 'rev-parse', '--path-format=absolute', option],
                            cwd=root, check=True, capture_output=True, text=True).stdout
    return Path(output.strip())


def git_dirs(root):
    """(common dir, this checkout's git dir); equal outside a linked worktree."""
    return git_path(root, '--git-common-dir'), git_path(root, '--git-dir')


def source_mount(root):
    """The main checkout holding every worktree's files and the shared Git dir."""
    common, _ = git_dirs(root)
    if common.name != '.git':
        raise ValueError(f'unsupported Git layout: common dir {common}')
    mount = common.parent
    if mount != Path(root).resolve() and mount not in Path(root).resolve().parents:
        raise ValueError(f'{root} is outside {mount}, the only checkout the VM mounts')
    return mount


if __name__ == '__main__':
    validate_destination(sys.argv[1])
    if len(sys.argv) > 2:
        if sys.argv[2] != RSYNC[0]:
            raise ValueError('Only lane rsync commands are supported')
        os.execvp(sys.argv[2], sys.argv[2:])
