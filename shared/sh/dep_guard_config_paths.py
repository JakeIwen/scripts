"""Config filenames shared by source discovery and shell mutation detection."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple


class ConfigNames(NamedTuple):
    npm_project: str = '.npmrc'
    npm_system: str = 'npmrc'
    package: str = 'package.json'
    yarn_classic: str = '.yarnrc'
    yarn_modern: str = '.yarnrc.yml'
    yarn_system: str = 'yarnrc'
    yarn_xdg: str = 'config'
    pnpm_rc: str = 'rc'
    pnpm_yaml: str = 'config.yaml'
    pnpm_workspace: str = 'pnpm-workspace.yaml'
    bun_project: str = 'bunfig.toml'
    bun_user: str = '.bunfig.toml'
    dotenv: str = '.env'
    pip: str = 'pip.conf'
    venv: str = 'pyvenv.cfg'
    uv: str = 'uv.toml'
    pyproject: str = 'pyproject.toml'
    poetry: str = 'poetry.toml'


NAMES = ConfigNames()
_BASENAMES = frozenset(NAMES)
BUN_DOTENV = tuple(NAMES.dotenv + suffix for suffix in ('', '.local', '.development', '.production', '.test'))


def possible_config_path(word):
    # The directory can be relative to an earlier cd or contain expansion;
    # no need to prove that a generic basename (rc, config.yaml) is harmless.
    name = word.rsplit('/', 1)[-1]
    return (name in _BASENAMES or name.startswith((NAMES.yarn_classic, NAMES.dotenv + '.'))
            or any(c in name for c in '$`*?[]{}'))


@dataclass
class ConfigWrites:
    config: bool = False  # A known config basename or an unbounded shell state change.
    destinations: set = field(default_factory=set)  # None means an unresolved output.

    def record(self, word, cwd):
        if not word or word.startswith(('~', '=')) or any(c in word for c in '$`*?[]{}\x00'):
            self.destinations.add(None)
        else:
            try:
                path = Path(word)
                if not path.is_absolute() and cwd is None:
                    self.destinations.add(None)
                else:
                    dest = (path if path.is_absolute() else cwd / path).resolve()
                    # An earlier copy/move may replace this path (or a parent)
                    # with a link. The on-disk target is then only one possibility.
                    if previously_written(dest, self.destinations):
                        self.destinations.add(None)
                    self.destinations.add(dest)
            except (OSError, ValueError, RuntimeError):
                self.destinations.add(None)
        if len(self.destinations) > 128:
            self.config = True  # Bound the evidence copied per invocation.
            self.destinations.clear()


def previously_written(path, destinations):
    if not destinations:
        return False
    resolved = path.resolve()
    # A copy into a directory, or replacement of the directory itself, can
    # change the consulted file without naming it. Include the file's identity
    # too: a hard link or case alias is another name for the same contents.
    parents = {str(p).casefold() for p in (resolved, *resolved.parents)}
    for dest in destinations:
        if dest is not None:
            if str(dest).casefold() in parents:
                return True
            try:
                if dest.samefile(path):
                    return True
            except FileNotFoundError:
                pass
    return False
