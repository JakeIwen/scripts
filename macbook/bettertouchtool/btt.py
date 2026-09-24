#!/usr/bin/env python3
"""BTT maintenance: inspect by default; explicit --apply for live changes."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

from btt_common import MEDIA, backup_configuration, current_database, records

DIRECTORY = Path(__file__).resolve().parent


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest='command')
    commands.add_parser('inspect', help='read-only saved Media configuration summary; no Apple Events')
    commands.add_parser('backup', help='create a private SQLite configuration snapshot; no live changes')
    media_restore = commands.add_parser('restore-media', help='recover a deleted Media tree only; inspect by default')
    media_restore.add_argument('--source', type=Path, help='appearance backup.json; defaults to newest')
    media_restore.add_argument('--apply', action='store_true')
    media_restore.add_argument('--resume', action='store_true', help='continue a verified disabled partial recovery')
    style = commands.add_parser('style', help='inspect/update existing Media appearance')
    style.add_argument('scope', choices=('main', 'transport', 'status'), nargs='?', default='main')
    style.add_argument('--apply', action='store_true')
    for name, description in [('status', 'playback status script and compact row spacing'),
                              ('notes', 'Recent/Pinned Notes menu setup'),
                              ('escape', 'visible-dropdown-only Escape dismissal')]:
        command = commands.add_parser(name, help='inspect/update '+description)
        command.add_argument('--apply', action='store_true')
        if name == 'notes':
            command.add_argument('--labels-only', action='store_true')
    repair = commands.add_parser('repair', help='targeted recovery; persistence recovery restarts BTT')
    repair.add_argument('target', choices=('rps', 'persistence', 'sizes', 'paths'))
    repair.add_argument('--apply', action='store_true')
    repair.add_argument('--with-escape', action='store_true', help='RPS repair only')
    repair.add_argument('--full-height-dropdowns', action='store_true', help='persistence repair only')
    restore = commands.add_parser('restore-sizes', help='restore only sizes from a verified sizing backup')
    restore.add_argument('backup', type=Path)
    restore.add_argument('--apply', action='store_true', required=True)
    return result


def command_for(args):
    """Return a fixed executable/argument list; never shell-interpolate user input."""
    def python(name, *extra): return [sys.executable, '-B', str(DIRECTORY/name), *extra]
    def jxa(name, *extra): return ['/usr/bin/osascript', '-l', 'JavaScript', str(DIRECTORY/name), *extra]
    inspect = [] if args.apply else ['--inspect']
    if args.command == 'restore-media':
        return python('restore_media.py', *(['--source', str(args.source.expanduser().resolve())] if args.source else []),
                      *(['--resume'] if args.resume else []),
                      *(['--apply'] if args.apply else []))
    if args.command == 'style':
        option = {'main': ['--main-style'], 'transport': [], 'status': ['--status-style']}[args.scope]
        return python('port_media_icons.py', *option, *inspect)
    if args.command == 'status':
        return jxa('tune_media_status.js', *inspect)
    if args.command == 'notes':
        return jxa('install_notes.js', *(['--labels-only'] if args.apply and args.labels_only else inspect))
    if args.command == 'escape':
        return python('install_menu_escape.py', *inspect)
    if args.command == 'restore-sizes':
        return jxa('fix_menu_sizes.js', '--restore', str(args.backup.expanduser().resolve()))
    if args.command == 'repair':
        if args.with_escape and args.target != 'rps':
            raise ValueError('--with-escape is only valid for repair rps')
        if args.full_height_dropdowns and args.target != 'persistence':
            raise ValueError('--full-height-dropdowns is only valid for repair persistence')
        if args.target == 'paths':
            return python('repair_script_paths.py', *(['--apply'] if args.apply else ['--inspect']))
        if args.target == 'rps':
            return python('repair_rps.py', *inspect, *(['--with-escape'] if args.with_escape else []))
        if args.target == 'persistence':
            return python('stabilize_media.py', *inspect,
                          *(['--full-height-dropdowns'] if args.full_height_dropdowns else []))
        return jxa('fix_menu_sizes.js', *inspect)
    raise ValueError('Unknown maintenance command')


def inspect_saved(database):
    saved = records(database)
    if MEDIA not in saved:
        raise RuntimeError('The expected Media menu is not present in this BTT configuration.')
    root = saved[MEDIA]
    children = sorted((r for r in saved.values() if r['parent'] == MEDIA), key=lambda r: r['ZORDER'] or 0)
    child_counts = Counter(r['parent'] for r in saved.values())
    unassigned = [{'uuid': r['ZUNIQUEIDENTIFIER'], 'identifier': r['config'].get('BTTMenuElementIdentifier')}
                  for r in children if r['ZGESTURETYPE'] == 773 and r['ZACTION'] in (None, 366)
                  and not child_counts[r['ZUNIQUEIDENTIFIER']]]
    return {'database': Path(database).name, 'menu_uuid': MEDIA, 'items': len(children),
            'vertical_spacing': root['config'].get('BTTMenuVerticalSpacing'),
            'drag_lock': root['config'].get('BTTMenuDisableDrag', 0),
            'buttons_without_saved_actions': unassigned,
            'note': 'Saved configuration only; this does not run buttons or verify runtime visibility.'}


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    if args.command is None:
        cli.print_help()
        return 0
    if args.command == 'inspect':
        print(json.dumps(inspect_saved(current_database()), ensure_ascii=False, indent=2))
        return 0
    if args.command == 'backup':
        database = current_database()
        path = backup_configuration(database, {'kind': 'sqlite-configuration-snapshot',
            'source_database': database.name, 'includes_external_assets': False, 'includes_preferences': False})
        print('Verified private database snapshot: '+str(path.parent))
        print('This is not a full application restore point: preset assets and preferences are not included.')
        return 0
    try:
        command = command_for(args)
    except ValueError as error:
        cli.error(str(error))
    if args.command == 'repair' and args.target == 'persistence' and args.apply:
        print('This recovery will restart BTT to verify persistence; it will not run speaker actions.', flush=True)
    return subprocess.run(command, check=False).returncode


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
