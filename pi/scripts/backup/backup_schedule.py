"""Migrate only the two owned cron entries to settings-aware hourly checks."""
import argparse
import os
import re
import subprocess
import tempfile

import backup_priority as priority


def scheduled_crontab(text):
    lines = text.splitlines(keepends=True)
    jobs = {'backup_window.sh': 0, 'openwrt_backup.sh': 10}
    for name, minute in jobs.items():
        matches = [i for i, line in enumerate(lines) if not line.lstrip().startswith('#')
                   and '/backup/' + name in line]
        if len(matches) != 1:
            raise ValueError('Expected exactly one scheduled ' + name + ' entry.')
        index = matches[0]
        pattern = (rf'{minute} (?:3-8|\*) \* \* \*([ \t]+"(?:\$scripts|/home/pi/scripts)/backup/'
                   + re.escape(name) + r'")(?: --(?:daily|scheduled))?([ \t]+>>[^\n]+)\n?')
        match = re.fullmatch(pattern, lines[index])
        if not match:
            raise ValueError('Backup cron entry was customized; review before migrating: ' + name)
        lines[index] = f'{minute} * * * *{match[1]} --scheduled{match[2]}\n'
    return ''.join(lines)


def migrate(command=subprocess.run, directory=priority.DIRECTORY):
    with priority.locked(directory):
        current = command(['/usr/bin/crontab', '-l'], capture_output=True, text=True, check=True).stdout
        updated = scheduled_crontab(current)
        if updated == current:
            return False
        fd, backup = tempfile.mkstemp(prefix='schedule-before-', suffix='.crontab', dir=directory)
        with os.fdopen(fd, 'w') as target:
            target.write(current)
            target.flush()
            os.fsync(target.fileno())
        # Compare again before replacing the table so an observed concurrent edit
        # is never silently discarded. The retained table is the rollback input.
        if command(['/usr/bin/crontab', '-l'], capture_output=True, text=True, check=True).stdout != current:
            raise ValueError('Crontab changed during schedule migration; retry after reviewing it.')
        try:
            command(['/usr/bin/crontab', '-'], input=updated, text=True, capture_output=True, check=True)
            installed = command(['/usr/bin/crontab', '-l'], capture_output=True, text=True, check=True).stdout
            if installed != updated:
                raise RuntimeError('Could not verify the backup schedule migration.')
        except (OSError, RuntimeError, subprocess.SubprocessError):
            command(['/usr/bin/crontab', backup], capture_output=True, text=True, check=True)
            raise
        return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.check == args.apply:
        parser.error('choose --check or --apply')
    if args.check:
        scheduled_crontab(subprocess.run(['/usr/bin/crontab', '-l'], capture_output=True,
                                        text=True, check=True).stdout)
    else:
        migrate()
