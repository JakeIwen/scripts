"""Fixed local identities; never resolve the venv interpreter symlink."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOME = Path.home()
DIRECTORY = ROOT / 'macbook/mac_panel'
DATA = ROOT / 'tmp/mac-panel'
RECORD = DATA / 'ownership.json'
LABEL = 'com.jacobr.mac-panel'
PORT = 8769
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
