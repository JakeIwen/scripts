"""Normalize reviewed BTT Sonos callers without executing their actions."""
from __future__ import annotations

import shlex
import subprocess

from btt_common import ROOT, records


LAUNCHER = 'macbook/scripts/sns.sh'
LEGACY_LAUNCHERS = ('sh/sns.sh', 'pi/sns.sh', LAUNCHER)
SHELL_PREFIXES = {'.', 'source', 'sh', '/bin/sh', 'bash', '/bin/bash', 'zsh', '/bin/zsh'}
SSH_HOSTS = {'pi@vanpi.local', 'pi@vanpi.lan', 'vanpi', 'vanpi.lan'}
TASK_REPLACEMENTS = {
    ('pink', 'noise'): ('pink_noise',),
    ('brown', 'noise'): ('brown_noise',),
    ('sonos_vol_50_all',): ('vol_eql_all', '50'),
}


def rewrite_sonos_command(command, root=ROOT):
    """Accept only a single known invocation; retain an explicit remote choice."""
    tokens = shlex.split(command)
    remote = []
    if len(tokens) == 3 and tokens[0] == 'ssh' and tokens[1] in SSH_HOSTS:
        tokens = shlex.split(tokens[2])
        remote = ['--pi']
    if tokens and tokens[0] in SHELL_PREFIXES:
        tokens = tokens[1:]
    paths = {prefix + '/' + relative for prefix in (str(root), '~/dev/scripts')
             for relative in LEGACY_LAUNCHERS}
    if not tokens or tokens[0] not in paths | {'sns'}:
        return command, []
    arguments = tokens[1:]
    # Never turn a compound shell program into arguments to a different command.
    if any(token in {';', '&&', '||', '|', '&', '>', '>>', '<'} for token in arguments):
        raise RuntimeError('Compound Sonos caller requires manual review')
    for old, new in TASK_REPLACEMENTS.items():
        if tuple(arguments[:len(old)]) == old:
            arguments = list(new) + arguments[len(old):]
            break
    if arguments[:2] == ['adjust_volume', 'vanFront']:
        arguments[1] = 'vonFront'
    launcher = root / LAUNCHER
    if not launcher.is_file():
        raise RuntimeError('Sonos launcher is missing: ' + str(launcher))
    after = shlex.join([str(launcher), *remote, *arguments])
    changes = [] if after == command else [{'old': command, 'new': after, 'occurrences': 1}]
    return after, changes


def sonos_plan(database, action_fields, root=ROOT):
    targets, checked = [], set()
    for uid, row in records(database).items():
        command = row['ZLAUNCHPATH']
        # Action 248 stores a named-trigger reference, not shell source.
        if row['ZACTION'] not in action_fields or not isinstance(command, str):
            continue
        if 'sns' not in command:
            continue
        after, replacements = rewrite_sonos_command(command, root)
        if after != command or after.startswith(str(root / LAUNCHER)):
            args = shlex.split(after)[1:]
            if args[:1] == ['--pi']:
                args = args[1:]
            key = tuple(args)
            if key not in checked:
                result = subprocess.run([str(root / LAUNCHER), '--check', *args],
                                        capture_output=True, text=True, timeout=10)
                if result.returncode:
                    raise RuntimeError('Invalid Sonos caller ' + uid + ': ' + result.stdout + result.stderr)
                checked.add(key)
        if replacements:
            targets.append({'uuid': uid, 'action': row['ZACTION'],
                            'field': action_fields[row['ZACTION']], 'before': command,
                            'after': after, 'replacements': replacements})
    return {'targets': sorted(targets, key=lambda target: target['uuid'])}
