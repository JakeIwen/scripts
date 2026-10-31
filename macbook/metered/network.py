"""Bounded macOS observations; commands can be supplied by public-boundary tests."""
import ipaddress
import re
import subprocess
import time

from .records import Network
from .wifi_helper.reader import read_wifi

INTERFACE = re.compile(r'en[0-9]+\Z')


def command(argv, **kwargs):
    return subprocess.run(argv, capture_output=True, text=True, timeout=kwargs.pop('timeout', 5),
                          **kwargs)


def output(run, argv):
    try:
        result = run(argv)
        return result.stdout if result.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def wifi_interface(run=command):
    ports = output(run, ['/usr/sbin/networksetup', '-listallhardwareports'])
    match = re.search(r'Hardware Port: Wi-Fi\nDevice: (en[0-9]+)\n', ports)
    return match[1] if match else None


def gateway(run=command):
    route = output(run, ['/sbin/route', '-n', 'get', 'default'])
    match = re.search(r'^\s*gateway: (\S+)$', route, re.M)
    if not match:
        return None, None
    try:
        address = str(ipaddress.IPv4Address(match[1]))
    except ValueError:
        return None, None
    arp = output(run, ['/usr/sbin/arp', '-n', address])
    match = re.search(r' at ((?:[0-9a-fA-F]{1,2}:){5}[0-9a-fA-F]{1,2}) ', arp)
    mac = ':'.join(f'{int(part, 16):02x}' for part in match[1].split(':')) if match else None
    return address, mac


def collect(run=command, wifi=read_wifi, clock=time.time):
    reading = wifi()
    interface = reading.get('interface')
    if not isinstance(interface, str) or not INTERFACE.fullmatch(interface):
        interface = wifi_interface(run)
    address, mac = gateway(run)
    good = reading.get('status') == 'ok'
    return Network(interface, reading.get('ssid') if good else None,
                   reading.get('bssid') if good else None, address, mac,
                   bool(address) and reading.get('status') != 'no_wifi', clock())
