"""Incremental SHA-256 proofs shared by catch-up and final verification."""
from contextlib import contextmanager
import time
from typing import TypedDict

from icloud_inventory import remote_fingerprint
import icloud_uplink_hold as uplink_hold
from time_machine_store import atomic_json, read_json

# The checkpoint file's name in the local store; staging cleanup protects it too.
CHECKPOINT = 'verified-objects.json'


class VerifiedObject(TypedDict):
    expected: dict
    remote: dict
    verified_at: float


def checkpoint_stamps(checkpoint):
    """Each checkpointed object's verified_at; {} when the file is missing or unreadable."""
    try:
        cache = read_json(checkpoint, {})
    except (OSError, ValueError):
        return {}
    if not isinstance(cache, dict):
        return {}
    return {digest: proof.get('verified_at') for digest, proof in cache.items() if isinstance(proof, dict)}


@contextmanager
def advancement_from_checkpoints(checkpoint):
    """Count a newly checkpointed proof as this run's durable advancement.

    Compares the checkpoint file before and after the block, in a finally, so
    a proof the (possibly separate) verifier wrote counts even when the block
    then fails; lossy progress telemetry never decides it.
    """
    before = checkpoint_stamps(checkpoint)
    try:
        yield
    finally:
        after = checkpoint_stamps(checkpoint)
        if any(stamp != before.get(digest) for digest, stamp in after.items()):
            uplink_hold.note_advancement()


class ObjectVerifier:
    def __init__(self, expected, checkpoint):
        if checkpoint.is_symlink():
            raise ValueError('unsafe verification checkpoint')
        self.expected = expected
        self.checkpoint = checkpoint
        self.cache = read_json(checkpoint, {})
        if not isinstance(self.cache, dict):
            raise ValueError('invalid verification checkpoint')
        self.verified = {}
        self.total = sum(row['bytes'] for row in expected.values())

    def observe(self, rows):
        self.verified = {}
        for digest, entry in self.expected.items():
            old = self.cache.get(digest)
            fp = remote_fingerprint(rows.get(digest))
            if (fp is not None and fp['size'] == entry['bytes'] and isinstance(old, dict)
                    and old.get('expected') == entry and old.get('remote') == fp):
                self.verified[digest] = old

    def counters(self):
        return {'verified_files': len(self.verified), 'verification_total_files': len(self.expected),
                'verified_bytes': sum(self.expected[k]['bytes'] for k in self.verified),
                'verification_total_bytes': self.total}

    def available(self, digest, rows):
        fp = remote_fingerprint(rows.get(digest))
        return digest in self.expected and fp is not None and fp['size'] == self.expected[digest]['bytes']

    def verify(self, digest, row, download):
        fp = remote_fingerprint(row)
        if fp is None or fp['size'] != self.expected[digest]['bytes']:
            raise RuntimeError('cloud object missing or wrong size')
        if digest in self.verified and self.verified[digest]['remote'] == fp:
            return
        if download(digest) != self.expected[digest]:
            raise RuntimeError('cloud object failed downloaded SHA-256 check')
        self.accept_hash(digest, row, self.expected[digest]['sha256'])

    def accept_hash(self, digest, row, downloaded_sha256):
        """Accept only a complete downloaded hash, never a provider checksum."""
        fp = remote_fingerprint(row)
        if fp is None or fp['size'] != self.expected[digest]['bytes']:
            raise RuntimeError('cloud object missing or wrong size')
        if downloaded_sha256 != self.expected[digest]['sha256']:
            raise RuntimeError('cloud object failed downloaded SHA-256 check')
        proof: VerifiedObject = {'expected': self.expected[digest], 'remote': fp, 'verified_at': time.time()}
        self.cache[digest] = proof
        atomic_json(self.checkpoint, self.cache)
        self.verified[digest] = proof

    def invalidate_changed(self, rows):
        changed = [k for k in self.verified if remote_fingerprint(rows.get(k)) != self.verified[k]['remote']]
        if changed:
            for digest in changed:
                self.cache.pop(digest, None)
                self.verified.pop(digest, None)
            atomic_json(self.checkpoint, self.cache)
        return changed
