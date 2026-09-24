#!/usr/bin/env python3
"""Repair reviewed moved-script references in active floating menus/named triggers."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time

from btt_common import ROOT, backup_configuration, connect, current_database, records

WORKER = Path(__file__).with_suffix('.js')
MOVED_SCRIPTS = (
    ('sh/sns.sh', 'macbook/scripts/sns.sh'),
    ('sh/show_note.sh', 'macbook/scripts/show_note.sh'),
    ('sh/show_note_folder.sh', 'macbook/scripts/show_note_folder.sh'),
    ('automation/wake_device.py', 'macbook/scripts/wake_device.py'),
    ('automation/display_settings.scpt', 'macbook/applescript/display_settings.scpt'),
    ('automation/sonosAudio.scpt', 'macbook/applescript/sonosAudio.scpt'),
    ('sync_scripts.sh', 'pi/sync_scripts.sh'),
)
ACTION_FIELDS = {206: 'BTTShellTaskActionScript', 246: 'BTTTerminalCommand'}
TOUCH_BAR_TYPES = {629, 630, 642}


def replace_path(command, old, new):
    # Match a complete path, including when quoted. Never rewrite sns.sh.old,
    # a nested path, or a similarly named unrelated checkout.
    pattern = r'(?<![\w./~+-])'+re.escape(old)+r'(?![\w./@+-])'
    return re.subn(pattern, lambda _: new, command)


def rewrite_command(command, root=ROOT):
    updated = command
    replacements = []
    for old_relative, new_relative in MOVED_SCRIPTS:
        for prefix in (str(root), '~/dev/scripts'):
            old, new = prefix+'/'+old_relative, prefix+'/'+new_relative
            candidate, count = replace_path(updated, old, new)
            if not count:
                continue
            if not (root/new_relative).is_file():
                raise RuntimeError('Reviewed replacement is missing: '+new_relative)
            updated = candidate
            replacements.append({'old': old, 'new': new, 'occurrences': count})
    # The migrated wake helper uses only Python's standard library. Do not
    # replace Python interpreters for arbitrary scripts or virtualenvs.
    if any(x['new'].endswith('/macbook/scripts/wake_device.py') for x in replacements):
        old, new = '/usr/local/opt/python@3.9/bin/python3', '/usr/bin/python3'
        candidate, count = replace_path(updated, old, new)
        if count and not Path(old).exists() and Path(new).is_file():
            updated = candidate
            replacements.append({'old': old, 'new': new, 'occurrences': count})
    return updated, replacements


def build_plan(database, root=ROOT):
    with closing(connect(database)) as connection:
        rows = {row['Z_PK']: dict(row) for row in connection.execute('''SELECT
            Z_PK,ZUNIQUEIDENTIFIER,ZPARENT,ZGESTURETYPE,ZBELONGSTOPRESET2,
            ZENABLEDNEW,ZACTIVATED,ZACTION,ZLAUNCHPATH FROM ZBTTBASEENTITY''')}
    targets = []
    for row in rows.values():
        if not row['ZUNIQUEIDENTIFIER'] or row['ZACTION'] not in ACTION_FIELDS:
            continue
        preset = rows.get(row['ZBELONGSTOPRESET2'], {})
        if not preset.get('ZACTIVATED') or not isinstance(row['ZLAUNCHPATH'], str):
            continue
        chain, current, seen = [], row, set()
        while current and current['Z_PK'] not in seen:
            seen.add(current['Z_PK'])
            chain.append(current)
            current = rows.get(current['ZPARENT'])
        if current or not chain or chain[-1]['ZGESTURETYPE'] not in (767, 643):
            continue
        if any(r['ZGESTURETYPE'] in TOUCH_BAR_TYPES or r['ZENABLEDNEW'] == 0 for r in chain):
            continue
        after, replacements = rewrite_command(row['ZLAUNCHPATH'], root)
        if replacements:
            targets.append({'uuid': row['ZUNIQUEIDENTIFIER'], 'action': row['ZACTION'],
                            'field': ACTION_FIELDS[row['ZACTION']], 'before': row['ZLAUNCHPATH'],
                            'after': after, 'replacements': replacements})
    return {'targets': sorted(targets, key=lambda t: t['uuid'])}


def worker(mode, path):
    result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', str(WORKER), mode,
                             str(ROOT), str(path)], capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise RuntimeError('BTT path-update '+mode+' failed: '+result.stderr.strip()[-1000:])
    return result.stdout.strip()


def saved_report(database, before, plan):
    after = records(database)
    expected = {t['uuid']: t['after'] for t in plan['targets']}
    pending = [uid for uid, text in expected.items() if after.get(uid, {}).get('ZLAUNCHPATH') != text]
    unexpected = []
    for uid, previous in before.items():
        actual = after.get(uid)
        if actual is None:
            unexpected.append(uid+': removed')
            continue
        for key, value in previous.items():
            if uid in expected and key == 'ZLAUNCHPATH':
                if actual[key] not in (value, expected[uid]):
                    unexpected.append(uid+': command changed outside requested replacements')
            elif key in ('ZICONDATA3', 'config'):
                # Ignore only BTT's volatile change stamps, not menu styling.
                def stable(config):
                    return {k: v for k, v in config.items() if k not in ('BTTLastChangeUUID', 'BTTLastUpdatedAt')}
                if key == 'config' and stable(actual[key]) != stable(value):
                    unexpected.append(uid+': menu configuration changed')
            elif actual[key] != value:
                unexpected.append(uid+': '+key+' changed')
    if after.keys()-before.keys():
        unexpected.append('unexpected new records')
    return pending, unexpected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--inspect', action='store_true')
    mode.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    database = current_database()
    plan = build_plan(database)
    print(str(len(plan['targets']))+' active actions with stale script paths.')
    mappings = sorted({(x['old'], x['new']) for t in plan['targets'] for x in t['replacements']})
    for old, new in mappings:
        print(old+' -> '+new)
    if not plan['targets'] or not args.apply:
        if plan['targets']:
            print('Inspection only. Layout, labels, ordering, disabled entries and Touch Bar records are not changed.')
        return
    os.umask(0o077)
    before = records(database)
    with tempfile.TemporaryDirectory(prefix='btt-path-plan-') as directory:
        path = Path(directory)/'plan.json'
        path.write_text(json.dumps(plan), encoding='utf-8')
        plan['snapshot'] = json.loads(worker('snapshot', path))
    backup = backup_configuration(database, plan, prefix='btt-paths-backup-', filename='plan.json')
    print('Verified pre-update configuration backup: '+str(backup.parent), flush=True)
    if build_plan(database)['targets'] != plan['targets'] or records(database) != before:
        raise RuntimeError('Saved configuration changed during backup; no changes made.')
    print(worker('apply', backup), flush=True)
    deadline = time.monotonic()+5
    while True:
        pending, unexpected = saved_report(database, before, plan)
        if unexpected:
            raise RuntimeError('Other saved values changed during update; no rollback attempted:\n'+
                               '\n'.join(unexpected[:8])+'\nBackup: '+str(backup.parent))
        if not pending:
            print('Verified saved command paths; all other saved records unchanged. No buttons executed or restart performed.')
            return
        if time.monotonic() >= deadline:
            print('API readback verified. BTT has not yet saved '+str(len(pending))+' command updates to disk.')
            print('Do not restart yet. Check saved state later with:')
            print('/usr/bin/python3 -B '+str(ROOT/'macbook/bettertouchtool/btt.py')+' repair paths')
            return
        time.sleep(.25)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
