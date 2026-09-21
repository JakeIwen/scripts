#!/usr/bin/env python3
"""Reuse preserved Touch Bar transport artwork on Media, changing appearance only."""
from __future__ import annotations

import argparse
import base64
from contextlib import closing
import json
from pathlib import Path
import os
import sqlite3
import subprocess
import sys
import tempfile
import time

from stabilize_media import ROOT, MEDIA, connect, current_database, records, disk_errors
from repair_rps import backup_configuration

WORKER = Path(__file__).with_suffix('.js')
STATUS_BUTTON = 'BDFEAEF1-6961-4B05-9A62-E70C54332C4C'
# The 5-minute seeks were long-press actions on the old 20-second buttons.
TARGETS = [
    ('1CA9A4F9-E433-4BBD-889A-26E06BD6A81E', '3082D16A-182C-4CE3-A7CC-A6BC81AAA513', 'Stop playback', ''),
    ('EDDC9F10-1447-433F-BEF0-0DC6723F5719', '7D68EDF1-F7DD-49A8-9AD4-803FE25F317C', 'Resume playback', ''),
    ('F93A8FAF-E10E-4C67-9F71-3111E0C66246', '3EC18A70-124C-416C-8A0E-99D915ADB080', 'Previous', ''),
    ('CF2B9321-9BAA-482F-BD85-0E67BC9F467C', 'AEA79159-1D93-4E61-B984-8F5CB2FAC5C5', 'Back 5 minutes', '5m'),
    ('FC94EEEC-A80C-4C7F-992F-52879C1D7119', 'AEA79159-1D93-4E61-B984-8F5CB2FAC5C5', 'Back 20 seconds', '20s'),
    ('3D724AF3-FC5D-49B8-ACB8-0514A2D641F2', '8E2C4915-05AD-41AA-8663-70B9E7C459EE', 'Forward 20 seconds', '20s'),
    ('24A0F3B3-AFD6-4895-B321-549495228BB7', '8E2C4915-05AD-41AA-8663-70B9E7C459EE', 'Forward 5 minutes', '5m'),
    ('5848A067-8BD4-4C1D-9298-43F538061ED9', '90C60839-E847-4368-994F-FDD0F8D965C0', 'Next', ''),
]
SPEAKER_TARGETS = [
    ('1CEF0D9C-AB16-48F2-AF26-AE3740217CEA', 'F1421700-6FED-417E-A7EA-3839429F1D89', 'Join Sonos speakers', ''),
    ('17FA4EAA-CEEA-461C-8E14-FAA303CA7B86', 'E874904C-31E5-49A1-9666-0B17E0982070', 'Unjoin Sonos speakers', ''),
    ('9276BF9D-AC65-4EFC-91F5-4F1978AC70EA', '6E3C36A1-C29B-49A5-A345-F504C2BE798D', 'Volume down', ''),
    ('4B84AEC2-5164-4947-966C-A673CACCE5BB', '9035C24C-D813-47A7-B5C4-56DA5BAF1D31', 'Volume up', ''),
]


def image_bytes(blob):
    if not blob:
        raise RuntimeError('Original Touch Bar image is missing; no changes made.')
    value = bytes(blob)
    if value.startswith(b'\x01'):
        value = value[1:]  # Core Data's inline-data prefix, not part of the image.
    if not value.startswith((b'\x89PNG\r\n\x1a\n', b'MM\x00*', b'II*\x00')):
        raise RuntimeError('Unexpected original image format; no changes made.')
    return value


def icon_patch(data, label, caption):
    # Valid empty RTF removes the emoji without changing the scripting identifier.
    rtf = '{\\rtf1\\ansi{\\fonttbl{\\f0 Helvetica;}}{\\colortbl;\\red0\\green0\\blue0;}' + \
        '\\pard\\qc\\f0\\fs18\\cf1 ' + caption + '}'
    patch = {'BTTMenuAttributedText': rtf, 'BTTMenuAttributedTextDark': rtf,
             'BTTMenuItemShowIcon': 1, 'BTTMenuElementTooltip': label,
             'BTTMenuItemShowIdentifierAsTooltip': 2}
    encoded = base64.b64encode(data).decode('ascii')
    for suffix in ('', 'Dark'):
        for key, value in {
            'IconType': 1, 'Image': encoded, 'ImageChangeColor': 1,
            'IconColor1': '0, 0, 0, 255', 'IconColor1Hover': '0, 0, 0, 255',
            'IconPosition': 6 if caption else 4, 'ResizeImage': 0,
            'ImageWidth': 22, 'ImageHeight': 18 if caption else 22,
        }.items():
            patch['BTTMenuItem'+key+suffix] = value
    return patch


def preset_path(database):
    saved = records(database)
    with closing(connect(database)) as connection:
        row = connection.execute('SELECT ZUNIQUEIDENTIFIER FROM ZBTTBASEENTITY WHERE Z_PK=?',
                                 (saved[MEDIA]['ZBELONGSTOPRESET2'],)).fetchone()
    if not row or not row[0]:
        raise RuntimeError('Cannot identify Media preset assets.')
    bundles = Path.home()/'Library/Application Support/BetterTouchTool/PresetBundles'
    matches = [path for path in bundles.iterdir() if path.is_dir() and path.name.startswith(row[0])]
    if len(matches) != 1:
        raise RuntimeError('Cannot uniquely locate Media preset assets.')
    return str(matches[0].resolve())


def normalize_configs(database, pairs):
    if not pairs:
        return []
    with tempfile.TemporaryDirectory(prefix='btt-icon-verification-') as directory:
        path = Path(directory)/'input.json'
        path.write_text(json.dumps({'presetPath': preset_path(database), 'pairs': pairs}))
        result = json.loads(worker('normalize-configs', path))
        if not isinstance(result, list) or len(result) != len(pairs) or not all(isinstance(x, dict) for x in result):
            raise RuntimeError('Invalid image verification result; refusing to assume icons match.')
        return result


def build_changes(database, targets=TARGETS):
    saved = records(database)
    preset = saved[MEDIA]['ZBELONGSTOPRESET2']
    candidates, pairs = [], []
    with closing(connect(database)) as connection:
        for target, source, label, caption in targets:
            button = saved.get(target)
            if not button or button['parent'] != MEDIA or button['ZGESTURETYPE'] != 773 or \
                    button['ZBELONGSTOPRESET2'] != preset:
                raise RuntimeError('Media transport button changed: '+label+'; no changes made.')
            config = button['config']
            if config.get('BTTMenuItemScriptActive'):
                raise RuntimeError('Transport button has an active content script: '+label+'; no changes made.')
            row = connection.execute('''SELECT ZGESTURETYPE,ZBELONGSTOPRESET2,ZICONDATA1
                FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER=?''', (source,)).fetchone()
            if not row or row['ZGESTURETYPE'] != 629 or row['ZBELONGSTOPRESET2'] != preset:
                raise RuntimeError('Original Touch Bar button changed: '+label+'; no changes made.')
            patch = icon_patch(image_bytes(row['ZICONDATA1']), label, caption)
            candidates.append({'uuid': target, 'source': source, 'label': label, 'patch': patch})
            pairs.append({'actual': config, 'expected': {**config, **patch}})
    normalized = normalize_configs(database, pairs)
    changes = []
    for candidate, config in zip(candidates, normalized):
        patch = {k: v for k, v in candidate['patch'].items() if config.get(k) != v}
        if patch:
            changes.append({**candidate, 'patch': patch})
    return changes


def appearance_disk_errors(database, plan):
    # Preserve the structural checks while interpreting proven image storage
    # conversions semantically rather than requiring the original file encoding.
    errors = disk_errors(database, {**plan, 'configChanges': []})
    saved = records(database)
    originals = {item['BTTUUID']: item for item in plan['media']['BTTMenuItems']}
    pairs, changes = [], []
    for change in plan['configChanges']:
        row = saved.get(change['uuid'])
        if not row:
            errors.append(change['uuid']+': not saved')
            continue
        expected = {**originals[change['uuid']]['BTTMenuConfig'], **change['patch']}
        pairs.append({'actual': row['config'], 'expected': expected})
        changes.append(change)
    for change, config in zip(changes, normalize_configs(database, pairs)):
        if any(config.get(k) != v for k, v in change['patch'].items()):
            errors.append(change['uuid']+': appearance not saved')
    return errors


def build_main_style_changes(database, status_only=False):
    saved = records(database)
    items = [{'uuid': uid, 'type': row['ZGESTURETYPE'], 'config': row['config']}
             for uid, row in saved.items() if row['parent'] == MEDIA and row['ZGESTURETYPE'] in (773, 774)
             and (not status_only or uid == STATUS_BUTTON)]
    if not items:
        raise RuntimeError('No main Media items found; no changes made.')
    inputs = {'items': items, 'icons': [] if status_only else build_changes(database, SPEAKER_TARGETS)}
    # Native RTF conversion preserves Unicode, fonts and paragraph styling. This
    # worker mode never opens BTT or sends Apple Events; it only builds patches.
    with tempfile.TemporaryDirectory(prefix='btt-main-style-plan-') as directory:
        path = Path(directory)/'input.json'
        path.write_text(json.dumps(inputs, ensure_ascii=False))
        return json.loads(worker('main-style-plan', path))


def worker(mode, path=''):
    result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', str(WORKER), mode, str(ROOT), str(path)],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'BTT appearance update failed.')
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect', action='store_true')
    style = parser.add_mutually_exclusive_group()
    style.add_argument('--main-style', action='store_true',
                        help='black main-bar text plus original Sonos join/unjoin and volume icons')
    style.add_argument('--status-style', action='store_true', help='black center play icon and status text only')
    args = parser.parse_args()
    os.umask(0o077)
    database = current_database()
    build = (lambda path: build_main_style_changes(path, status_only=True)) if args.status_style else \
        (build_main_style_changes if args.main_style else build_changes)
    changes = build(database)
    if args.inspect:
        print(json.dumps({'appearance_changes': len(changes), 'buttons': [c['label'] for c in changes],
                          'actions_changed': False}, indent=2))
        return
    if not changes:
        print('Requested main-menu appearance is already configured.')
        return
    media = json.loads(worker('snapshot'))
    plan = {'media': media, 'changes': changes, 'entities': [], 'presetPath': preset_path(database),
            'configChanges': [{'uuid': c['uuid'], 'patch': c['patch']} for c in changes]}
    path = backup_configuration(database, plan, prefix='btt-transport-icons-backup-')
    print('Verified full BTT backup: '+str(path.parent), flush=True)
    try:
        if build(database) != changes:
            raise RuntimeError('Transport configuration changed during backup; no changes made.')
        print(worker('apply', path), flush=True)
        deadline = time.monotonic()+5
        errors = appearance_disk_errors(database, plan)
        while errors and time.monotonic() < deadline:
            time.sleep(.1)
            errors = appearance_disk_errors(database, plan)
        if errors:
            raise RuntimeError('Icon settings did not persist: '+'; '.join(errors))
    except (RuntimeError, subprocess.SubprocessError) as error:
        raise RuntimeError(str(error)+'\nBackup: '+str(path.parent)) from error
    print('Verified saved appearance. Actions, button sizes/order, status script and menu layout are unchanged. No restart needed.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
