#!/usr/bin/env python3
"""Repair reviewed moved-script references in active floating menus/named triggers."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import time

from btt_common import MEDIA_TOOLS, ROOT, backup_configuration, connect, current_database, records

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
MEDIA_BUILD_ENTRIES = frozenset((
    'Performance Audio.app', 'bpm-venv', 'performance-audio-jobs',
    'video-conversion-jobs', 'conversion-editor-jobs', 'conversion-reports',
    'custom-conversion', 'bpm-cache', 'bpm-analysis-jobs', 'rhythm-analysis-jobs',
))
MEDIA_SCRIPT_NAMES = frozenset((
    'performance_audio.py', 'performance_audio_effects.py', 'performance_audio_presets.json',
    'convert_video.py', 'conversion_editor.py', 'conversion_jobs.py', 'conversion_reports.py',
    'conversion_settings.py', 'conversion_storage.py', 'conversion_worker.py',
    'media_formats.py', 'media_selection.py', 'media_task_control.py', 'video_export.py',
    'bpm_over_time.py', 'rhythm_practice.py', 'setup_bpm_analysis.py',
    'build_performance_audio_app.py',
))
SHELL_SAFE_MEDIA_ROOT = re.compile(r'^[A-Za-z0-9_./+@-]+$')


def rewrite_media_command(command, root=ROOT, media_tools=MEDIA_TOOLS):
    """Move only reviewed suite paths; shell spelling and unrelated paths survive."""
    media_tools = Path(media_tools)
    if not SHELL_SAFE_MEDIA_ROOT.fullmatch(str(media_tools)):
        raise RuntimeError('Media tools destination is not safe for shell substitution: '+str(media_tools))
    old_roots = (root/'macbook', Path('~/dev/scripts/macbook'))
    tokens = shlex.split(command)
    mapped = {}
    for token in dict.fromkeys(tokens):
        relative = None
        for old_root in old_roots:
            try:
                relative = Path(token).relative_to(old_root)
                break
            except ValueError:
                pass
        if relative is None:
            continue
        parts = relative.parts
        allowed = (len(parts) >= 2 and parts[0] == 'build' and parts[1] in MEDIA_BUILD_ENTRIES)
        allowed |= len(parts) == 2 and parts[0] == 'scripts' and parts[1] in MEDIA_SCRIPT_NAMES
        if not allowed:
            continue
        destination = media_tools/relative
        if '..' in parts or not destination.exists():
            raise RuntimeError('Reviewed media replacement is missing or unsafe: '+str(destination))
        mapped[token] = str(destination)
    updated, replacements = command, []
    for token, destination in mapped.items():
        candidate, count = replace_path(updated, token, destination)
        if not count:
            raise RuntimeError('Cannot safely rewrite shell spelling for media path: '+token)
        updated = candidate
        replacements.append({'old': token, 'new': destination, 'occurrences': count})
    if shlex.split(updated) != [mapped.get(token, token) for token in tokens]:
        raise RuntimeError('Media path rewrite changed shell argument boundaries; no changes made.')
    return updated, replacements


def media_plan(database, root=ROOT, media_tools=MEDIA_TOOLS):
    # Include disabled legacy copies so re-enabling them cannot restore stale paths.
    # Their activation, parent, order and appearance fields remain untouched.
    targets = []
    old_roots = (str(root/'macbook'), '~/dev/scripts/macbook')
    for uid, row in records(database).items():
        command = row['ZLAUNCHPATH']
        if not isinstance(command, str) or not any(prefix in command for prefix in old_roots):
            continue
        after, replacements = rewrite_media_command(command, root, media_tools)
        if not replacements:
            continue
        if row['ZACTION'] not in ACTION_FIELDS:
            raise RuntimeError('Unsupported media command action; no changes made: '+uid)
        targets.append({'uuid': uid, 'action': row['ZACTION'],
                        'field': ACTION_FIELDS[row['ZACTION']], 'before': command,
                        'after': after, 'replacements': replacements})
    return {'targets': sorted(targets, key=lambda t: t['uuid'])}


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
    parser.add_argument('--media-tools', action='store_true',
                        help='move reviewed media-suite paths, including disabled legacy copies')
    args = parser.parse_args(argv)
    database = current_database()
    planner = media_plan if args.media_tools else build_plan
    plan = planner(database)
    scope = 'media-suite actions' if args.media_tools else 'active actions'
    print(str(len(plan['targets']))+' '+scope+' with stale script paths.')
    for target in plan['targets'] if args.media_tools else ():
        print(target['uuid']+'\n  OLD '+target['before']+'\n  NEW '+target['after'])
    mappings = sorted({(x['old'], x['new']) for t in plan['targets'] for x in t['replacements']})
    for old, new in mappings:
        print(old+' -> '+new)
    if not plan['targets'] or not args.apply:
        if plan['targets']:
            print('Inspection only. Only reviewed command paths may change; activation, layout, labels and ordering are preserved.')
        return
    os.umask(0o077)
    before = records(database)
    with tempfile.TemporaryDirectory(prefix='btt-path-plan-') as directory:
        path = Path(directory)/'plan.json'
        path.write_text(json.dumps(plan), encoding='utf-8')
        plan['snapshot'] = json.loads(worker('snapshot', path))
    backup = backup_configuration(database, plan, prefix='btt-paths-backup-', filename='plan.json')
    print('Verified pre-update configuration backup: '+str(backup.parent), flush=True)
    if planner(database)['targets'] != plan['targets'] or records(database) != before:
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
            if args.media_tools:
                print('/usr/bin/python3 -B '+str(Path(__file__))+' --media-tools --inspect')
            else:
                print('/usr/bin/python3 -B '+str(ROOT/'macbook/bettertouchtool/btt.py')+' repair paths')
            return
        time.sleep(.25)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
