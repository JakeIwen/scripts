"""Explicit, immutable known-good checkpoints with verified recovery artifacts."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import time
from uuid import uuid4

from ..btt_common import ROOT, backup_configuration, connect, current_database
from . import api
from .audit import compare
from .database import read_snapshot
from .definitions import build_definitions, validate_definitions
from .model import AuditStatus, CheckpointManifest, SCHEMA_VERSION
from .storage import file_hash, load_json, private_dir, write_bytes, write_json

DEFAULT_STATE = ROOT/'.local/btt-guard'
ACTIVE_FILE = 'known-good.json'
CHECKPOINT_ID = re.compile(r'cp-[0-9TZ]+-[a-z0-9_]+\Z')


def preset_names(database, preset_ids):
    with closing(connect(database)) as connection:
        rows = connection.execute('SELECT ZUNIQUEIDENTIFIER,ZNAME3 FROM ZBTTBASEENTITY WHERE ZNAME3 IS NOT NULL')
        names = {row[0]: row[1] for row in rows if row[0] in preset_ids}
    if set(names) != set(preset_ids):
        raise ValueError('A checkpoint preset is missing; refusing to guess its replacement.')
    return names


def _embed_preset_images(definitions, directory, names):
    result = deepcopy(definitions)
    bundles = Path.home()/'Library/Application Support/BetterTouchTool/PresetBundles'
    for uid, node in result.items():
        config = node.get('BTTMenuConfig', {})
        for suffix in ('', 'Dark'):
            if config.get('BTTMenuItemIconType'+suffix) != 7:
                continue
            value = config.get('BTTMenuItemIconPresetPath'+suffix)
            if not isinstance(value, str):
                raise ValueError('Preset image reference is missing.')
            if value.startswith('BTT_PRESET_PATH/'):
                matching = [p for p in bundles.iterdir() if p.is_dir() and any(
                    p.name == key+name and name == node.get('BTTTriggerBelongsToPreset') for key, name in names.items())]
                if len(matching) != 1:
                    raise ValueError('Cannot resolve preset-relative icon.')
                path = matching[0]/value[len('BTT_PRESET_PATH/'):]
            else:
                path = Path(value)
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(bundles.resolve()) or resolved.stat().st_size > 16*1024*1024:
                raise ValueError('Unsafe or oversized preset icon.')
            data = resolved.read_bytes()
            if not data.startswith((b'\x89PNG\r\n\x1a\n', b'II\x2a\x00', b'MM\x00\x2a', b'\xff\xd8')):
                raise ValueError('Unsupported preset image format.')
            asset_name = hashlib.sha256(uid.encode('utf-8')).hexdigest()+suffix+'.image'
            asset = directory/'assets'/asset_name
            write_bytes(asset, data)
            config['BTTMenuItemIconType'+suffix] = 1
            config['BTTMenuItemImage'+suffix] = base64.b64encode(data).decode('ascii')
            config.pop('BTTMenuItemIconPresetPath'+suffix, None)
    return result


def _assert_stable(expected, current):
    result = compare(expected, current, 'capture')
    if result['status'] != AuditStatus.HEALTHY or set(expected['records']) != set(current['records']):
        raise RuntimeError('Configuration changed during capture; checkpoint was not promoted.')


def _capture_definitions(snapshot, names, state):
    exports = api.export_graph(snapshot)
    try:
        return exports, build_definitions(exports, snapshot, names)
    except (ValueError, KeyError, TypeError):
        directory = private_dir(state/'rejected-captures'/str(uuid4()))
        write_json(directory/'snapshot.json', snapshot)
        write_json(directory/'api-export.json', exports)
        raise ValueError('Runtime export disagrees with saved state; no checkpoint promoted. '
                         'Private diagnostic capture: '+str(directory)) from None


def capture(state_dir=DEFAULT_STATE, *, accept_current=False, replace=False, roots=None):
    if not accept_current:
        raise ValueError('Checkpoint promotion requires --accept-current after checking the menu.')
    state = private_dir(state_dir)
    if (state/ACTIVE_FILE).exists() and not replace:
        raise ValueError('A known-good checkpoint exists. Intentional replacement also requires --replace.')
    database = current_database()
    snapshot = read_snapshot(database, roots=roots)
    if not snapshot['roots'] or any(not snapshot['records'].get(uid, {}).get('enabled')
                                   for uid in snapshot['roots']) or not all(snapshot['presets'].values()):
        raise ValueError('Protected roots and their presets must be present and enabled before promotion.')
    if any(snapshot['records'][uid]['parent'] is not None for uid in snapshot['roots']):
        raise ValueError('Protected root menus must be top-level, not nested or cyclic.')
    if snapshot['findings']:
        raise ValueError('Current configuration has '+str(len(snapshot['findings']))+
                         ' unresolved findings. Refusing to call it known-good; inspect first.')
    names = preset_names(database, snapshot['presets'])
    exports, definitions = _capture_definitions(snapshot, names, state)
    _assert_stable(snapshot, read_snapshot(database, roots=snapshot['roots']))
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    directory = backup_configuration(database, snapshot, prefix='cp-'+stamp+'-', filename='snapshot.json',
                                     directory_root=private_dir(state/'checkpoints')).parent
    # Make this owned backup standalone before hashing; never alter the live DB.
    with closing(sqlite3.connect(directory/'configuration.sqlite')) as connection:
        connection.execute('PRAGMA journal_mode=DELETE')
    _assert_stable(snapshot, read_snapshot(directory/'configuration.sqlite', roots=snapshot['roots']))
    definitions = _embed_preset_images(definitions, directory, names)
    write_json(directory/'definitions.json', definitions)
    write_json(directory/'api-export.json', exports)
    files = {str(p.relative_to(directory)): file_hash(p) for p in directory.rglob('*') if p.is_file()}
    manifest: CheckpointManifest = dict(schema_version=SCHEMA_VERSION, checkpoint_id=directory.name,
        created_at=time.time(), btt_version=snapshot['btt_version'], roots=snapshot['roots'], files=files)
    write_json(directory/'manifest.json', manifest)
    load_checkpoint(state, directory.name)
    _assert_stable(snapshot, read_snapshot(database, roots=snapshot['roots']))
    write_json(state/ACTIVE_FILE, {'schema_version': SCHEMA_VERSION, 'checkpoint_id': directory.name})
    return manifest


def load_checkpoint(state_dir=DEFAULT_STATE, checkpoint_id=None):
    state = Path(state_dir)
    if checkpoint_id is None:
        pointer = load_json(state/ACTIVE_FILE)
        if not isinstance(pointer, dict) or pointer.get('schema_version') != SCHEMA_VERSION:
            raise ValueError('Invalid known-good checkpoint pointer.')
        checkpoint_id = pointer.get('checkpoint_id')
    if not isinstance(checkpoint_id, str) or not CHECKPOINT_ID.fullmatch(checkpoint_id):
        raise ValueError('Invalid checkpoint identifier.')
    directory = state/'checkpoints'/checkpoint_id
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Checkpoint directory is missing or symlinked.')
    manifest = load_json(directory/'manifest.json')
    if (not isinstance(manifest, dict) or manifest.get('schema_version') != SCHEMA_VERSION or
            manifest.get('checkpoint_id') != checkpoint_id or not isinstance(manifest.get('files'), dict)):
        raise ValueError('Invalid checkpoint manifest.')
    required = {'snapshot.json', 'definitions.json', 'configuration.sqlite', 'api-export.json'}
    if not required <= manifest['files'].keys():
        raise ValueError('Incomplete checkpoint artifacts.')
    for name, expected_hash in manifest['files'].items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or not relative.parts:
            raise ValueError('Invalid checkpoint artifact path.')
        path = directory/name
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError('Unsafe checkpoint artifact.')
        if not isinstance(expected_hash, str) or file_hash(path) != expected_hash:
            raise ValueError('Checkpoint artifact integrity check failed.')
    snapshot = load_json(directory/'snapshot.json')
    definitions = load_json(directory/'definitions.json')
    if (snapshot.get('schema_version') != SCHEMA_VERSION or snapshot.get('findings') or
            not snapshot.get('records') or set(snapshot['records']) != set(definitions) or
            snapshot.get('roots') != manifest.get('roots')):
        raise ValueError('Checkpoint graph is invalid or was not captured healthy.')
    validate_definitions(definitions, snapshot)
    return manifest, snapshot, definitions
