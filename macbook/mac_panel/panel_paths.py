"""Fixed local identities; never resolve the venv interpreter symlink."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOME = Path.home()
DIRECTORY = ROOT / 'macbook/mac_panel'
DATA = ROOT / 'tmp/mac-panel'
RECORD = DATA / 'ownership.json'
LABEL = 'com.jacobr.mac-panel'
DEFAULT_PORT = 8769


def port_from_env(environ):
    """MAC_PANEL_PORT lets a worktree run a second instance; anything but plain digits keeps the default."""
    value = environ.get('MAC_PANEL_PORT', '')
    return int(value) if value.isascii() and value.isdecimal() and 0 < int(value) < 65536 else DEFAULT_PORT


# The LaunchAgent plist never sets the override, so the installed panel always serves DEFAULT_PORT.
PORT = port_from_env(os.environ)
HOST = f'127.0.0.1:{PORT}'
URL = f'http://{HOST}'
CHILD_PATH = '/usr/bin:/bin:/usr/sbin:/sbin'
PYTHON = HOME / '.local/share/mac-panel/venv/bin/python'
PLIST = HOME / 'Library/LaunchAgents' / f'{LABEL}.plist'
PREPARED = DATA / f'{LABEL}.plist'
LOG = DATA / 'panel.log'
DEFERRED = ('/bin/zsh', str(ROOT / 'macbook/scripts/deferred.zsh'))
RESTORE = ('/usr/bin/python3', '-I', '-B', str(ROOT / 'shared/sh/llm_restore.py'))
GUARD = ('/usr/bin/python3', '-B', str(ROOT / 'macbook/bettertouchtool/btt.py'), 'guard')
# Not -I: the script finds its log_rotate package beside itself on sys.path, and -I drops that.
LOG_ROTATE = ('/usr/bin/python3', '-B', str(ROOT / 'macbook/scripts/log_rotate.py'))
# What the LaunchAgent plist sets; passed explicitly so a panel started without it still works.
LOG_ROTATE_ENV = {'PYTHONPATH': str(ROOT / 'shared/python')}
LOG_ROTATE_LABEL = 'com.jacobr.log-rotate'
