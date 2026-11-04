"""Version-neutral offsite state writes and reads for the compatibility test.

Stdlib only and duck-typed on the fixture, so the same steps run through the
current fixture and, copied into an extracted older release, through its own.
"""
import json


def writes(f, now):
    """One history row per outcome kind, rates for two sources, and remote check evidence."""
    f.reload()
    f.state['progress_events'] = [
        {'message_type': 'status', 'bytes_done': 0, 'total_bytes': 61000, 'seconds_elapsed': 0},
        {'message_type': 'status', 'bytes_done': 61000, 'total_bytes': 61000, 'seconds_elapsed': 61}]
    f.state['sample_times'] = [now, now, now + 61, now + 100, now + 100, now + 161]
    f.save()
    results = [f.run('backup', '--label', f.label, '--only', 'borg', '--only', 'exfat512')]
    f.reload()
    f.state['sample_times'] = [now + 200, now + 200, now + 261]
    f.save()
    results.append(f.run('backup', '--remote', '--label', f.label, '--only', 'borg'))
    f.reload()
    f.state.pop('sample_times')
    f.state['backup_failure'] = True
    f.save()
    failed = f.run('backup', '--label', f.label, '--only', 'movingparts')
    f.reload()
    f.state.pop('backup_failure')
    f.save()
    results.append(f.run('check', '--remote', '--label', f.label))
    return results, failed


def readers(f):
    value = {'status': json.loads(f.run('status').stdout), 'dashboard': json.loads(f.run('dashboard').stdout)}
    value['rates'] = {source: json.loads(f.run('rates', source).stdout) for source in f.cfg.sources()}
    return value
