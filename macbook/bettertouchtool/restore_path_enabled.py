#!/usr/bin/env python3
"""Restore only accidental enabled-state drift from a path repair's own backup."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from btt_common import backup_configuration, current_database, records
from repair_script_paths import saved_report, worker


def inspect_backup(path, database):
    plan = json.loads(path.read_text())
    before = records(path.parent / 'configuration.sqlite')
    after = records(database)
    pending, unexpected = saved_report(database, before, plan)
    targets = {target['uuid']: target for target in plan['targets']}
    snapshots = {entry['uuid']: entry['trigger'] for entry in plan['snapshot']}
    if (len(targets) != len(plan['targets']) or len(snapshots) != len(plan['snapshot']) or
            snapshots.keys() != targets.keys()):
        raise RuntimeError('Incomplete or duplicate action backup')
    recoverable = []
    for uid, target in targets.items():
        if (uid not in before or uid not in after or
                snapshots[uid].get(target['field']) not in (target['before'], target['after']) or
                before[uid]['ZLAUNCHPATH'] != target['before']):
            raise RuntimeError('Backup command records do not agree: ' + uid)
        if before[uid]['ZENABLEDNEW'] == 0 and after[uid]['ZENABLEDNEW'] == 1:
            if snapshots[uid].get('BTTEnabled2', snapshots[uid].get('BTTEnabled')) != 0:
                raise RuntimeError('Backup enabled states do not agree: ' + uid)
            recoverable.append(uid)
    allowed = {uid + ': ZENABLEDNEW changed' for uid in recoverable}
    if pending or set(unexpected) != allowed:
        raise RuntimeError('Command or unrelated saved-state drift; refusing recovery: ' +
                           repr({'pending': pending, 'unexpected': unexpected}))
    return plan, before, recoverable


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('backup', type=Path, help='original btt-paths-backup-*/plan.json')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    path = args.backup.resolve()
    database = current_database()
    plan, before, recoverable = inspect_backup(path, database)
    print(f'{len(plan["targets"])} commands saved; {len(recoverable)} disabled states to restore.')
    if not args.apply:
        return 0
    if recoverable:
        snapshot = json.loads(worker('snapshot', path))
        backup = backup_configuration(database, {'source_backup': str(path), 'snapshot': snapshot},
                                      prefix='btt-path-enabled-backup-')
        print('Recovery backup: ' + str(backup), flush=True)
        inspect_backup(path, database)
        print(worker('restore-enabled', path), flush=True)
    else:
        print(worker('verify', path), flush=True)
    print('Runtime verified; waiting up to 90 seconds for BTT to save.', flush=True)
    deadline = time.monotonic() + 90
    while True:
        _, _, remaining = inspect_backup(path, database)
        if not remaining:
            print('Verified all command edits and original enabled states on disk. No actions executed.')
            print('BTT Guard checkpoint unchanged; recovery does not accept unrelated drift.')
            return 0
        if time.monotonic() >= deadline:
            raise RuntimeError('Runtime verified but saved state is pending; rerun inspection, not the original repair.')
        time.sleep(.25)


if __name__ == '__main__':
    raise SystemExit(main())
