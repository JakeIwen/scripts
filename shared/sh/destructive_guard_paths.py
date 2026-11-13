"""Path rules for destructive_command_guard.py: protected folders and what a target names.

Exports PROTECTED_PATHS, DEFAULT_HOMES, home_directory, absolute_path,
folder_after_cd, deletion_target, execdir_folders and has_glob. Deployed beside the guard:
shared/sh/ on the Mac, the flat /home/pi/scripts/ on the Pi.
"""

from fnmatch import fnmatchcase
import functools
import os
import posixpath
import re

from destructive_guard_flow import TOP
from destructive_guard_shell import LITERAL


PROTECTED_PATHS = frozenset({
    '/', '~', '/Users/jacobr', '/home/pi', '/Users', '/home', '/root', '/var/root',
    '/System', '/Library', '/Applications', '/usr', '/etc', '/var', '/opt',
    '/bin', '/sbin', '/private', '/Volumes', '/boot', '/lib', '/srv',
    '/mnt', '/media', '/dev',
})
# Used when HOME is unset or relative; the hook runs as Jacob on each host.
DEFAULT_HOMES = {'darwin': '/Users/jacobr', 'linux': '/home/pi'}
NAMED_HOME = re.compile(r'~[A-Za-z0-9_][A-Za-z0-9_.-]*(?=/|$)')
# A last component made only of glob patterns (*, .*, .[!.]*, ?*) removes the
# directory's contents, so it is judged by the directory itself.
BROAD_GLOB = re.compile(r'\.?(?:\*|\?|\[[^\]/]*\])+')
# An unquoted glob character; a quoted one carries the LITERAL marker before it.
LIVE_GLOB = re.compile(r'(?<!' + re.escape(LITERAL) + r')[*?\[]')
MARKED = re.compile(re.escape(LITERAL) + r'(.)')


def has_glob(text):
    return bool(LIVE_GLOB.search(text))


def pattern(part):
    """An fnmatch pattern for one path component, quoted glob characters escaped."""
    return MARKED.sub(lambda match: '[' + match.group(1) + ']' if match.group(1) in '*?[' else match.group(1), part)


def glob_matches(parts, candidates):
    """Whether the component patterns in parts match one of the candidate component tuples."""
    patterns = [pattern(part) for part in parts]
    return any(len(candidate) == len(patterns) and
               all(fnmatchcase(name, glob) for name, glob in zip(candidate, patterns))
               for candidate in candidates)


def home_directory(platform):
    home = os.environ.get('HOME', '')
    if home.startswith('/') and '\x00' not in home:
        return posixpath.normpath('/' + home.lstrip('/'))
    return DEFAULT_HOMES.get(platform, '/')


def expand_home(target, home):
    # Expand ~ before resolving .., so ~/../../usr is /usr, not ../usr.
    if target == '~' or target.startswith('~/'):
        return home + target[1:]
    named = NAMED_HOME.match(target)
    if named and os.path.expanduser(named.group(0)) != named.group(0):
        return os.path.expanduser(named.group(0)) + target[named.end():]
    return target  # ~nobody stays literal, as in the shell, when no such user exists.


def absolute_path(target, cwd, home):
    """Lexical absolute path of target; None or TOP when it hangs on that folder."""
    target = expand_home(target, home)
    if not target.startswith('/'):
        if cwd is None or cwd == TOP:
            return cwd
        target = cwd + '/' + target
    # Symlinks and remote paths cannot be resolved safely on this machine.
    return posixpath.normpath('/' + target.lstrip('/'))


def folder_after_cd(target, cwd, home):
    """Where cd target (or env -C, sudo -D, sudo -i's home) leaves the shell."""
    if not target:
        return None
    if expand_home(target, home).startswith('~'):
        return TOP  # Another user's home this host cannot resolve: it could be anywhere.
    return absolute_path(target, cwd, home)


def protected_path(path, home):
    # Home and everything above it: deleting an ancestor deletes home.
    return path in PROTECTED_PATHS or path == home or home.startswith(path.rstrip('/') + '/')


@functools.lru_cache(maxsize=8)
def protected_parts(home):
    """Component tuples of every protected folder: the list, home, home's ancestors."""
    home_parts = tuple(part for part in home.split('/') if part)
    paths = {tuple(part for part in path.split('/') if part)
             for path in PROTECTED_PATHS if path.startswith('/')}
    return frozenset(paths | {home_parts[:index] for index in range(len(home_parts) + 1)})


@functools.lru_cache(maxsize=8)
def protected_suffixes(home):
    """Tails that name a protected folder from some folder: (usr,), (Users, jacobr), (jacobr,)."""
    return frozenset(parts[index:] for parts in protected_parts(home) for index in range(len(parts)))


def protected_glob(path, home):
    """Whether an absolute path whose components hold globs can match a protected folder."""
    return glob_matches([part for part in path.split('/') if part], protected_parts(home))


def anywhere_dangerous(target, home):
    """Whether a relative target, globs included, is protected from some folder (cwd TOP)."""
    relative = posixpath.normpath(target)
    if relative in {'.', '..'} or relative.startswith('../'):
        return True
    parts = relative.split('/')
    candidates = [parts]
    if BROAD_GLOB.fullmatch(parts[-1]):
        if len(parts) == 1:
            return True
        candidates.append(parts[:-1])  # The contents of a folder named by the rest.
    return any(glob_matches(candidate, protected_suffixes(home)) for candidate in candidates)


def deletion_target(target, cwd, home):
    if not target:
        return False
    path = absolute_path(target, cwd, home)
    if path is None:
        return False  # No payload cwd: relative targets are not judged.
    if path == TOP:
        return anywhere_dangerous(expand_home(target, home), home)
    if protected_path(path, home) or (has_glob(path) and protected_glob(path, home)):
        return True
    parent, name = posixpath.split(path)
    return bool(BROAD_GLOB.fullmatch(name)) and (
        protected_path(parent, home) or (has_glob(parent) and protected_glob(parent, home)))


def execdir_folders(root, cwd, home):
    """Folders a find -execdir or -okdir clause may run in: those holding each match."""
    path = absolute_path(root, cwd, home)
    if path is None or path == TOP:
        return (path,)
    folders = {posixpath.dirname(path) or '/', path}
    below = path.rstrip('/') + '/'
    # A deeper match runs in its own folder, which could be a protected one below the root.
    if any(protected.startswith(below) for protected in PROTECTED_PATHS | {home}):
        folders.add(TOP)
    return tuple(folders)
