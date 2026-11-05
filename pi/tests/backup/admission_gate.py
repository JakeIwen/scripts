"""Deterministically suspend a real admission immediately before its legacy flock."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import select

FLOCK_GATE = '''#!/bin/bash
if [[ "$*" == '-n 9' || "$*" == '-n -s 9' ]]; then
  printf 'ready\\n' > "$ADMISSION_READY"
  read -r ignored < "$ADMISSION_GO"
fi
exec /usr/bin/flock "$@"
'''


@contextmanager
def admission_gate(root):
    root = Path(root)
    ready, go, command = (root / name for name in ('admission-ready', 'admission-go', 'gated-flock'))
    for path in (ready, go):
        os.mkfifo(path)
    command.write_text(FLOCK_GATE)
    command.chmod(0o755)
    read_fd = os.open(ready, os.O_RDWR | os.O_NONBLOCK)
    go_fd = os.open(go, os.O_RDWR | os.O_NONBLOCK)
    env = {'VANPI_BACKUP_JOB_LOCK_FLOCK': str(command), 'ADMISSION_READY': str(ready), 'ADMISSION_GO': str(go)}

    def interrupt(legacy, state):
        if not select.select([read_fd], [], [], 20)[0]:
            raise AssertionError('admission did not reach the pre-flock boundary')
        os.read(read_fd, 64)
        fd = os.open(legacy, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state.mkdir(exist_ok=True)
            (state / 'pending').write_text('version=2\nid=20261104T000000Z-42\nphase=publishing\n')
        finally:
            os.close(fd)  # a killed installer releases the fence but leaves pending
        os.write(go_fd, b'go\n')

    try:
        yield env, interrupt
    finally:
        os.write(go_fd, b'cleanup\n')
        os.close(read_fd)
        os.close(go_fd)
        ready.unlink()
        go.unlink()
