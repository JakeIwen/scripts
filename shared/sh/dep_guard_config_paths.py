"""Configuration filenames consulted during package source and executable discovery."""

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
BUN_DOTENV = tuple(NAMES.dotenv + suffix for suffix in ('', '.local', '.development', '.production', '.test'))
