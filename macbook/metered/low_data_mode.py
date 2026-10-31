"""Ownership-aware constrained flag control; never changes expensive or native LDM."""
from dataclasses import replace
import hashlib
import json
import re
import subprocess

from shared.metered.records import Actual
from shared.metered.store import RecordStore
from .config import STATE_TTL
from .network import INTERFACE, command
from .records import Ownership


def constrained(text, interface):
    if not re.search(r'^' + re.escape(interface) + r': flags=[0-9a-f]+<', text, re.M):
        raise ValueError('unrecognized ifconfig output')
    # macOS puts CONSTRAINED in extended flags, visible with -v, not flags=.
    flags = re.findall(r'(?:^|\s)(?:e|x)?flags=[0-9a-f]+<([^>]*)>', text)
    return any('CONSTRAINED' in group.split(',') for group in flags)


def identity(network):
    if not network.connected:
        return None
    if network.ssid and network.bssid:
        value = [network.ssid, network.bssid.lower(), network.gateway_mac]
    elif network.gateway and network.gateway_mac:
        value = [network.gateway, network.gateway_mac]
    else:
        return None
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


class LowDataMode:
    def __init__(self, paths, run=command):
        self.store = RecordStore(paths.file('low-data-ownership'), Ownership, Ownership)
        self.run = run

    def flag(self, interface):
        result = self.run(['/sbin/ifconfig', '-v', interface])
        if result.returncode:
            raise RuntimeError('cannot read Wi-Fi interface flags')
        return constrained(result.stdout, interface)

    def set_flag(self, interface, enabled):
        result = self.run(['/usr/bin/sudo', '-n', '/sbin/ifconfig', interface,
                           'constrained' if enabled else '-constrained'])
        if result.returncode or self.flag(interface) != enabled:
            raise RuntimeError('Low Data Mode write was not confirmed; check scoped sudoers')

    def reconcile(self, permitted, network, enabled, now, restore=False):
        if not enabled:
            return Actual.NA, 'Low Data Mode control disabled; Time Machine test required'
        interface, key = network.interface, identity(network)
        if not interface or not INTERFACE.fullmatch(interface) or key is None:
            self.store.save_locked(Ownership())
            return Actual.UNKNOWN, 'Wi-Fi network identity unavailable'
        try:
            return self._reconcile(permitted, interface, key, now, restore)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            return Actual.UNKNOWN, str(error)

    def _reconcile(self, permitted, interface, key, now, restore):
        owner = self.store.load()
        owned = (owner.interface == interface and owner.identity == key and
                 0 <= now - owner.seen_at <= STATE_TTL)
        flag = self.flag(interface)
        if not owned:
            owner = Ownership()
            self.store.save_locked(owner)
        if not permitted:
            if not flag:
                self.set_flag(interface, True)
                owner = Ownership(interface, key, now, now)
                owned = True
            if owned:
                self.store.save_locked(replace(owner, seen_at=now))
            return Actual.HELD, 'Wi-Fi constrained' if owned else 'Native Low Data Mode preserved'
        if not flag:
            self.store.save_locked(Ownership())
            return Actual.RUNNING, 'Wi-Fi is not constrained'
        if not owned:
            return Actual.MANUAL, 'Native or unowned Low Data Mode preserved'
        self.store.save_locked(replace(owner, seen_at=now))
        # The bit has no writer identity. A native preference changed while it was
        # set is indistinguishable, so automatic clearing cannot prove ownership.
        if not restore:
            return Actual.HELD, 'Approval granted; confirm Restore owned flag to clear Low Data Mode'
        self.set_flag(interface, False)
        self.store.save_locked(Ownership())
        return Actual.RUNNING, 'Owned Wi-Fi flag restored by explicit request'
