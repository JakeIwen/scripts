#!/usr/bin/env python3
"""Read-only broad-sync guard; first compute cutover is supervised on the Mac."""
from pathlib import Path
import re
import subprocess
import sys


GUIDANCE = (
    "Compute provider preflight failed; no sync changes were made. "
    "In the owner's Terminal, run the reviewed checkout's "
    "./macbook/scripts/install_van_compute_worker.zsh first, then retry sync. "
    "See pi/docs/compute/VAN_COMPUTE.md."
)


def check_provider(root=Path('/home/pi/van_compute'),
                   queue=Path('/home/pi/dev/obd-things/tmp/compute'),
                   old=Path('/home/pi/scripts/compute')):
    # Inspect the provider already installed, not the new checkout's fingerprint:
    # routine sync must work with an older compatible provider and an offline Mac.
    if root.resolve(strict=True) != root or not root.is_dir():
        raise ValueError('unsafe compute root')
    if (root / 'releases').is_symlink() or not (root / 'current').is_symlink():
        raise ValueError('missing or unsafe compute release link')
    target = (root / 'current').readlink()
    if not re.fullmatch(r'releases/[0-9a-f]{24}', str(target)):
        raise ValueError('unexpected compute release target')
    release = root / target
    if release.resolve(strict=True) != release or not release.is_dir():
        raise ValueError('missing or unsafe compute release')
    for marker in (queue / '.maintenance.json',
                   root / 'scripts/.van-compute-upgrade-owner',
                   old / '.van-compute-upgrade-owner'):
        if marker.exists() or marker.is_symlink():
            raise ValueError('compute cutover is still fenced or in maintenance')
    markers = [root / 'deployment.sha256', release / 'source.sha256']
    for path in [*markers, release / 'van_compute/metrics.py',
                 release / 'van_compute/__init__.py']:
        if path.resolve(strict=True) != path or not path.is_file():
            raise ValueError('unsafe or missing compute provider file')
        # Opening rather than access() also detects permissions/read failures.
        path.read_bytes()
    installed, source = [path.read_text().strip() for path in markers]
    if (not re.fullmatch('[0-9a-f]{64}', source) or installed != source or
            release.name != source[:24]):
        raise ValueError('inconsistent compute source markers')
    subprocess.run(
        [sys.executable, '-I', '-B', '-c',
         'import sys; sys.path.insert(0, sys.argv[1]); '
         'import van_compute.metrics as m; '
         'from van_compute.metrics import ComputeMetricsError, ComputeMetricsReader; '
         'assert m.__file__ == sys.argv[1] + "/van_compute/metrics.py"', str(release)],
        check=True, capture_output=True, timeout=10,
    )


if __name__ == '__main__':
    try:
        check_provider()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'{GUIDANCE}\n{error}', file=sys.stderr)
        raise SystemExit(1)
