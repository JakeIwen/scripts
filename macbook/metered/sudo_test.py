"""Read-only preflight and result checks for Jacob's privileged Time Machine test."""
import argparse
import plistlib
import re
from urllib.parse import urlsplit

from shared.metered.config import first_run_config
from shared.metered.records import SharedConfig
from shared.metered.store import RecordStore
from shared.python import tool_settings
from .config import Paths, SETTINGS_TOOL
from .low_data_mode import constrained, identity
from .network import collect, command


def checked(argv):
    result = command(argv, timeout=15)
    if result.returncode:
        raise RuntimeError('test prerequisite failed: ' + argv[0])
    return result.stdout


def idle():
    text = checked(['/usr/bin/tmutil', 'status'])
    if not re.search(r'\bRunning = 0;', text):
        raise RuntimeError('Time Machine is not idle; wait for its current job')
    return text


def preflight():
    if tool_settings.load(SETTINGS_TOOL)['enabled']:
        raise RuntimeError('Disable metered-mac control before running its Time Machine test')
    network = collect()
    config = RecordStore(Paths.default().file('config'), SharedConfig, first_run_config).load()
    home = config.home_network
    if network.gateway != home.gateway or network.gateway_mac != home.gateway_mac:
        raise RuntimeError('Time Machine test requires the verified van LAN gateway')
    if not network.interface or not identity(network):
        raise RuntimeError('Wi-Fi identity is unavailable')
    idle()
    destinations = plistlib.loads(checked(['/usr/bin/tmutil', 'destinationinfo', '-X']).encode())
    matches = [item for item in destinations.get('Destinations', [])
               if item.get('Kind') == 'Network' and
               urlsplit(item.get('URL', '')).hostname in ('vanpi._smb._tcp.local.', 'vanpi.lan')]
    if len(matches) != 1:
        raise RuntimeError('Expected exactly one Pi SMB Time Machine destination')
    destination = matches[0].get('ID', '')
    if not re.fullmatch(r'[A-Fa-f0-9-]{36}', destination):
        raise RuntimeError('Time Machine destination id is invalid')
    print(network.interface, destination, identity(network))


def flag(interface):
    text = checked(['/sbin/ifconfig', '-v', interface])
    print('1' if constrained(text, interface) else '0')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('preflight', 'identity', 'flag', 'idle'))
    parser.add_argument('interface', nargs='?')
    args = parser.parse_args()
    actions = {'preflight': preflight, 'identity': lambda: print(identity(collect())),
               'flag': lambda: flag(args.interface), 'idle': lambda: print(idle())}
    actions[args.action]()


if __name__ == '__main__':
    main()
