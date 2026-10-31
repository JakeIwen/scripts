"""Authenticated Pi transport and a crash-safe restricting-only replay queue."""
from dataclasses import replace
import json
import subprocess
import uuid

from shared.metered.config import first_run_config
from shared.metered.records import AddSSID, Host, HostState, SharedConfig, SharedOperation, SyncRequest, SyncResponse
from shared.metered.store import MAX_RECORD_BYTES, RecordStore, RevisionConflict
from shared.sh.peer_copy import SSH_OPTIONS
from .config import REMOTE_CLI, REMOTE_INTERVAL, SSH_TIMEOUT
from .network import command
from .records import Pending, SyncStatus, empty_state


class PiTransport:
    def __init__(self, run=command):
        self.run = run

    def request(self, verb, payload):
        try:
            result = self.run(['/usr/bin/ssh', *SSH_OPTIONS, 'vanpi', REMOTE_CLI, verb],
                              input=json.dumps(payload), timeout=SSH_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError('Pi sync unavailable: ' + type(error).__name__) from error
        if result.returncode == 75:
            raise RevisionConflict('Pi configuration changed; reload and retry')
        if result.returncode != 0:
            raise RuntimeError('Pi command unavailable (exit %s)' % result.returncode)
        if len(result.stdout.encode('utf-8')) > MAX_RECORD_BYTES:
            raise ValueError('Pi response is too large')
        return json.loads(result.stdout)


class Cache:
    """The controller's state lock serializes all writes, including CLI edits."""
    def __init__(self, paths, transport):
        self.config = RecordStore(paths.file('config'), SharedConfig, first_run_config)
        self.pending = RecordStore(paths.file('pending'), Pending, Pending)
        self.pi = RecordStore(paths.file('pi-state'), HostState, lambda: empty_state(Host.PI))
        self.status = RecordStore(paths.file('sync'), SyncStatus, SyncStatus)
        self.transport = transport

    def overlay(self):
        config = self.config.load()
        ssids = set(config.metered_ssids)
        ssids.update(op.ssid for op in self.pending.load().ops)
        return replace(config, metered_ssids=sorted(ssids))

    def add(self, ssid):
        pending = self.pending.load()
        operation = AddSSID(str(uuid.uuid4()), SharedOperation.ADD_SSID, ssid)
        self.pending.save_locked(Pending([*pending.ops, operation]))
        return operation

    def due(self, network, now):
        home = self.config.load().home_network
        on_lan = network.gateway == home.gateway and network.gateway_mac == home.gateway_mac
        last = self.status.load().attempted_at
        return on_lan or last is None or now < last or now - last >= REMOTE_INTERVAL

    def sync(self, report, now):
        previous = self.status.load()
        attempt = replace(previous, attempted_at=now)
        self.status.save_locked(attempt)
        pending = self.pending.load()
        try:
            response = SyncResponse.from_dict(self.transport.request(
                'sync', SyncRequest(pending.ops, report).as_dict()))
            self._accept(response, pending)
        except (OSError, ValueError, TypeError, RuntimeError) as error:
            self.status.save_locked(replace(attempt, error=str(error)))
            return False
        self.status.save_locked(SyncStatus(now, now, None))
        return True

    def _accept(self, response, pending):
        if response.pi_state.host != Host.PI:
            raise ValueError('Pi response contains a foreign host state')
        if response.config.revision < self.config.load().revision:
            raise ValueError('Pi configuration revision moved backwards')
        sent = {op.op_id for op in pending.ops}
        if set(response.applied) - sent:
            raise ValueError('Pi acknowledged an operation not sent')
        # Publish config before dropping acknowledged operations. A crash between
        # writes only retries an already-receipted operation; it cannot lose an add.
        self.config.save_locked(response.config)
        self.pi.save_locked(response.pi_state)
        self.pending.save_locked(Pending([op for op in pending.ops if op.op_id not in response.applied]))

    def remove(self, ssid, revision, report, now):
        if not self.sync(report, now):
            raise RuntimeError('Removing an SSID needs a live Pi round trip')
        if any(op.ssid == ssid for op in self.pending.load().ops):
            raise RuntimeError('SSID still has an unacknowledged addition')
        config = SharedConfig.from_dict(self.transport.request(
            'edit-ssids', {'op': 'remove', 'ssid': ssid, 'revision': revision}))
        if config.revision <= self.config.load().revision or ssid in config.metered_ssids:
            raise ValueError('Pi did not confirm SSID removal')
        self.config.save_locked(config)
        return config
