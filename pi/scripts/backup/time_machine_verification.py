"""Incremental SHA-256 proofs shared by catch-up and final verification."""
import time
from typing import TypedDict

from icloud_inventory import remote_fingerprint
from time_machine_store import atomic_json, read_json


class VerifiedObject(TypedDict):
    expected: dict
    remote: dict
    verified_at: float


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
