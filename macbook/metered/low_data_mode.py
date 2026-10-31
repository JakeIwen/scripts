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
    # SSID + gateway, not BSSID: roaming between access points is the same network.
    if network.ssid:
        value = [network.ssid, network.gateway_mac]
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
        """`restore` is accepted for the CLI's apply-now path; clearing is automatic."""
        if not enabled:
            return Actual.NA, 'Low Data Mode control disabled; Time Machine test required'
        interface = network.interface
        if not interface or not INTERFACE.fullmatch(interface):
            return Actual.UNKNOWN, 'Wi-Fi interface unavailable'
        try:
            return self._reconcile(permitted, interface, identity(network), now)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            return Actual.UNKNOWN, str(error)

    def _reconcile(self, permitted, interface, key, now):
        owner = self.store.load()
        flag = self.flag(interface)
        # Owned means this job set the bit and has not seen it clear since. Sleep
        # gaps and network changes keep ownership: an orphaned bit would pin Low
        # Data Mode on an unmetered network. Only a bit set without our record
        # (native per-network Low Data Mode) is left alone.
        owned = flag and owner.interface == interface and owner.set_at > 0
        if not flag and owner.set_at > 0:
            self.store.save_locked(Ownership())
        if not permitted:
            if not flag:
                self.set_flag(interface, True)
                self.store.save_locked(Ownership(interface, key, now, now))
            elif owned:
                self.store.save_locked(replace(owner, identity=key, seen_at=now))
            else:
                return Actual.HELD, 'Native Low Data Mode preserved'
            return Actual.HELD, 'Wi-Fi constrained'
        if not flag:
            return Actual.RUNNING, 'Wi-Fi is not constrained'
        if not owned:
            return Actual.MANUAL, 'Native or unowned Low Data Mode preserved'
        self.set_flag(interface, False)
        self.store.save_locked(Ownership())
        return Actual.RUNNING, 'Low Data Mode cleared'
