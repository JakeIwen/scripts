"""Owned Samba capture gate; never force-close an image with live handles."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile

from time_machine_store import atomic_json, inventory


PAUSE_MESSAGE = ('Time Machine has open image handles. Run the Time Machine source from the Mac with '
                 'zsh macbook/scripts/offsite_tm_refresh.zsh --label LABEL: it pauses the hourly '
                 'com.jacobr.vanpi-time-machine agent (sudo tmutil disable does nothing here), '
                 'waits for the running backup, detaches the image and the share, waits for zero '
                 'open handles, then starts the run.')


def handle_state(commands, source):
    value = json.loads(commands.run('smbstatus', '--json'))
    if not isinstance(value, dict) or not isinstance(value.get('open_files'), dict) or not isinstance(value.get('tcons'), dict):
        raise RuntimeError('Cannot establish Samba handle state')
    for row in value['open_files'].values():
        if not isinstance(row, dict) or not isinstance(row.get('service_path'), str) or not isinstance(row.get('filename'), str):
            raise RuntimeError('Malformed Samba handle evidence')
        path = os.path.normpath(os.path.join(row['service_path'], row['filename']))
        if path == str(source) or path.startswith(str(source) + '/'):
            raise RuntimeError(PAUSE_MESSAGE)
    return [row for row in value['tcons'].values() if row.get('service') == source.parent.name]


def publish_gate(cfg, marker):
    parent = cfg.drain.parent
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != cfg.owner or info.st_mode & 0o022:
        raise RuntimeError('Unsafe or missing Samba drain directory')
    # A populated, no-replace gate is the existing capture protocol. The ordinary
    # shell drain writes an empty shutdown gate that mount reconciliation clears.
    with tempfile.NamedTemporaryFile(dir=parent) as temporary:
        atomic_json(Path(temporary.name), marker)
        try:
            os.link(temporary.name, cfg.drain)
        except FileExistsError as exc:
            raise RuntimeError('Time Machine share is already draining; preserve its owner') from exc


@contextmanager
def quiesced(repo, source):
    cfg, commands = repo.cfg, repo.commands
    marker = {'owner': 'vanpi-offsite', 'pid': os.getpid(), 'nonce': secrets.token_hex(16)}
    content = json.dumps(marker, sort_keys=True) + '\n'
    published = False
    try:
        publish_gate(cfg, marker)
        published = True
        def guard():
            commands.allowed()
            if cfg.drain.is_symlink() or not cfg.drain.is_file() or cfg.drain.read_text() != content:
                raise RuntimeError('Time Machine capture lost its Samba drain gate')
            repo.exact_mount(source.parent.name)
            handle_state(commands, source)
        guard()
        # Existing idle tree connections bypass preexec; close only after proving
        # the image has no handles, never use drain as a forced detach mechanism.
        commands.run('samba', 'close', source.parent.name)
        if handle_state(commands, source):
            raise RuntimeError('Time Machine tree connections remain after closing the idle share')
        before = inventory(source)
        yield guard
        guard()
        if inventory(source) != before:
            raise RuntimeError('Time Machine image changed during backup; snapshot remains incomplete')
    finally:
        if published:
            if not cfg.drain.is_symlink() and cfg.drain.is_file() and cfg.drain.read_text() == content:
                cfg.drain.unlink()
            # A shutdown gate may replace ours on ignition: it belongs to the
            # disk lifecycle caller and must survive our exit.
