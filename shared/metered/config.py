"""Pi-owned shared configuration and deduplicated, restricting offline edits."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

from .records import AddSSID, HomeNetwork, Receipt, SharedConfig, ssid
from .store import RecordStore


MAX_RECEIPTS = 4096
MAX_SSIDS = 256


def first_run_config():
    # Only the seed knows the van's initial identities; the evaluator reads config.
    return SharedConfig(1, 0, ['denlink'],
                        HomeNetwork('dendelion', '192.168.6.1', 'd8:ec:5e:8d:6a:39'))


def config_path():
    return Path.home() / '.config/vanpi/metered.json'


class ConfigStore(RecordStore):
    def __init__(self, path=None):
        super().__init__(path or config_path(), SharedConfig, first_run_config)

    def edit_ssid(self, operation, value, revision):
        ssid(value)
        if operation not in ('add', 'remove'):
            raise ValueError('SSID operation must be add or remove')
        def transform(current):
            values = set(current.metered_ssids)
            {'add': values.add, 'remove': values.discard}[operation](value)
            if len(values) > MAX_SSIDS:
                raise ValueError('too many metered SSIDs')
            return replace(current, revision=current.revision + 1, metered_ssids=sorted(values))
        return self.change(revision, transform)

    def edit_home(self, home, revision):
        home = HomeNetwork.from_dict(home.as_dict())
        return self.change(revision, lambda current: replace(
            current, revision=current.revision + 1, home_network=home))

    def apply_ops(self, operations):
        if len(operations) > 128:
            raise ValueError('at most 128 operations per sync')
        operations = [AddSSID.from_dict(operation.as_dict()) for operation in operations]
        with self.locked():
            current = self.load()
            values, receipts = set(current.metered_ssids), list(current.receipts)
            known = {receipt.op_id: receipt.digest for receipt in receipts}
            for operation in operations:
                payload = operation.as_dict()
                digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
                if operation.op_id in known:
                    if known[operation.op_id] != digest:
                        raise ValueError('operation id was reused with different content')
                    continue
                if len(receipts) >= MAX_RECEIPTS:
                    raise ValueError('operation receipt capacity reached; no changes applied')
                values.add(ssid(operation.ssid))
                if len(values) > MAX_SSIDS:
                    raise ValueError('too many metered SSIDs')
                receipts.append(Receipt(operation.op_id, digest))
                known[operation.op_id] = digest
            if receipts != current.receipts:
                current = replace(current, revision=current.revision + 1,
                                  metered_ssids=sorted(values), receipts=receipts)
                self.save_locked(current)
            return current, [operation.op_id for operation in operations]
