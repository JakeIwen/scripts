"""Install only this login interval job; no listener, daemon or KeepAlive loop."""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess

from .config import INTERVAL, LABEL, Paths

REPO = Path(__file__).resolve().parents[2]


def configuration(repo=REPO, home=None):
    home = Path.home() if home is None else Path(home)
    template = Path(__file__).with_name(LABEL + '.plist').read_text()
    # Decode before substitution so a checkout containing XML characters stays valid.
    result = plistlib.loads(template.encode())
    def render(value):
        if isinstance(value, str):
            return value.replace('@REPO@', str(repo)).replace('@HOME@', str(home))
        if isinstance(value, dict):
            return {key: render(item) for key, item in value.items()}
        if isinstance(value, list):
            return [render(item) for item in value]
        return value
    result = render(result)
    result['StartInterval'] = INTERVAL
    return result


def owned(path, expected):
    if path.is_symlink():
        raise ValueError('refusing a symlink LaunchAgent')
    if path.exists():
        current = plistlib.loads(path.read_bytes())
        if any(current.get(key) != expected[key] for key in ('Label', 'WorkingDirectory', 'ProgramArguments')):
            raise ValueError('installed LaunchAgent belongs to a different checkout')


def launchctl(*args, check=True):
    return subprocess.run(['/bin/launchctl', *args], check=check, timeout=15)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'install', 'uninstall', 'status', 'start', 'stop'))
    args = parser.parse_args(argv)
    home, expected = Path.home(), configuration()
    installed = home / 'Library/LaunchAgents' / (LABEL + '.plist')
    domain = 'gui/' + str(os.getuid())
    service = domain + '/' + LABEL
    try:
        owned(installed, expected)
        if args.action == 'status':
            return launchctl('print', service, check=False).returncode if installed.exists() else 1
        if args.action in ('uninstall', 'stop'):
            if installed.exists():
                launchctl('disable', service)
                launchctl('bootout', domain, str(installed), check=False)
                if args.action == 'uninstall':
                    installed.unlink()
            return 0
        directory = Paths.default().directory
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        prepared = directory / (LABEL + '.plist')
        prepared.write_bytes(plistlib.dumps(expected))
        os.chmod(prepared, 0o600)
        subprocess.run(['/usr/bin/plutil', '-lint', str(prepared)], check=True, timeout=5)
        if args.action == 'prepare':
            print(prepared)
            return 0
        installed.parent.mkdir(parents=True, exist_ok=True)
        if not installed.exists():
            installed.write_bytes(prepared.read_bytes())
            os.chmod(installed, 0o644)
        elif plistlib.loads(installed.read_bytes()) != expected:
            raise ValueError('uninstall the old registration before changing its configuration')
        launchctl('enable', service)
        if launchctl('print', service, check=False).returncode:
            launchctl('bootstrap', domain, str(installed))
        return launchctl('print', service).returncode
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
