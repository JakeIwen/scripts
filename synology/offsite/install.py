"""Explicit first installation; no DSM task, NAS login or live server is created."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from .binaries import fetch_verified, target_architecture
from .configuration import DEFAULT_ROOT, private_root, publish


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--prepare', action='store_true', help='print the architecture-specific plan without writing')
    args = parser.parse_args(argv)
    try:
        if sys.version_info < (3, 9) or os.geteuid() != 0:
            raise ValueError('DSM installer requires root and Python 3.9+')
        architecture = target_architecture()
        root = args.root
        if not root.is_absolute() or root.resolve() != root:
            raise ValueError('Installation root must be an absolute real path')
        if args.prepare:
            print('Install native rest-server 0.14.0 ({}) and receiver code at {}; no boot task/start'.format(architecture, root))
            return 0
        root.mkdir(mode=0o700, parents=False, exist_ok=True)
        private_root(root)
        if any(root.iterdir()):
            raise ValueError('First install requires an empty private directory; never overwrite an existing receiver')
        source = Path(__file__).resolve().parent
        repo = source.parents[1]
        with tempfile.TemporaryDirectory(prefix='.install-', dir=root) as work:
            stage = Path(work) / 'download'
            provenance = fetch_verified(architecture, stage)
            # Execute only the exact archive-verified binary, outside the archive directory.
            result = subprocess.run([str(stage / 'rest-server'), '--version'], cwd=root,
                                    check=True, capture_output=True, timeout=10, text=True)
            if '0.14.0' not in result.stdout:
                raise ValueError('Native rest-server version check failed')
            shutil.copy2(stage / 'rest-server', root / 'rest-server')
        package = root / 'receiver'
        package.mkdir(mode=0o700)
        for name in ('__init__.py', '__main__.py', 'control.py', 'configuration.py', 'runtime.py', 'mounts.py', 'binaries.py'):
            shutil.copyfile(source / name, package / name)
        # Shared pure records/validators and bounded private reader have one
        # canonical source; deployed flat beside the receiver, not copied forks.
        for name in ('offsite_config.py', 'protected_json.py'):
            shutil.copyfile(repo / 'pi' / 'scripts' / 'backup' / name, root / name)
        for name in ('watch.lock', 'control.lock'):
            fd = os.open(root / name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        publish(root / 'binary.json', provenance)
        print('Files installed and native version checked. NOT configured, started, or registered with DSM Task Scheduler.')
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print('offsite install refused or failed; no successful NAS installation is claimed', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
