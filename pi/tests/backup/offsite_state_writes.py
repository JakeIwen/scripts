"""Version-neutral offsite state writes and reads for the compatibility test.

Stdlib only and duck-typed on the fixture, so the same steps run through the
current fixture and, copied into an extracted older release, through its own.
"""
import json
import os
import select
import subprocess
import sys


OBSERVE_STDIN_READ = '''
import os, runpy, sys
ready = int(sys.argv.pop(1))
original = sys.stdin
class ObservedInput:
    def read(self, *args, **kwargs):
        os.write(ready, b'R')
        return original.read(*args, **kwargs)
sys.stdin = ObservedInput()
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
'''


def assert_stdin_consumed(f, name):
    """Observe a real read entry before sending input, not a sleep during interpreter startup."""
    requests = {'metered': ('metered', ['admit', 'pi.offsite_backup'], '{}'),
                'sfdisk': ('fake-sfdisk', ['--wipe', 'always', str(f.device)], 'label: gpt\n, , L\n')}
    executable, args, payload = requests[name]
    read_fd, write_fd = os.pipe()
    try:
        with subprocess.Popen([sys.executable, '-S', '-c', OBSERVE_STDIN_READ, str(write_fd),
                               str(f.bin / executable), *args], env=f.env, cwd=f.root,
                              pass_fds=(write_fd,), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as child:
            os.close(write_fd)
            write_fd = None
            try:
                assert select.select([read_fd], [], [], 5)[0], f'{name} never reached stdin'
                assert os.read(read_fd, 1) == b'R', f'{name} exited without reading stdin'
                assert child.poll() is None, f'{name} exited before input/EOF'
                out, err = child.communicate(input=payload, timeout=5)
                assert child.returncode == 0, out + err
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate(timeout=5)
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)


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
