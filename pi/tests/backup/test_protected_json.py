"""Protected reader contracts using only private temporary files."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import protected_json as records
from time_machine_store import atomic_json


class ProtectedJsonTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / 'record.json'
        self.row = {'version': 1, 'nested': {'generation': 'unchanged'}}
        atomic_json(self.path, self.row)
        self.error = 'unsafe test record'

    def read(self):
        return records.read_protected_json(self.path, self.error)

    def test_roundtrip_is_read_only_and_missing_is_distinct_from_json_null(self):
        original = self.path.read_bytes()
        self.assertEqual(self.read(), self.row)
        self.assertEqual(self.path.read_bytes(), original)
        atomic_json(self.path, None)
        self.assertIsNone(self.read())
        self.path.unlink()
        self.assertIs(self.read(), records.MISSING)
        self.assertFalse(self.path.exists())

    def test_rejects_public_permissions_other_owner_and_multiple_links(self):
        self.path.chmod(0o640)
        with self.assertRaisesRegex(ValueError, self.error): self.read()
        self.path.chmod(0o600)
        owner = os.geteuid()
        with mock.patch.object(records.os, 'geteuid', return_value=owner + 1):
            with self.assertRaisesRegex(ValueError, self.error): self.read()
        os.link(self.path, self.root / 'second-link')
        with self.assertRaisesRegex(ValueError, self.error): self.read()

    def test_rejects_symlinks_and_nonregular_files(self):
        self.path.unlink()
        self.path.symlink_to(self.root / 'missing')
        with self.assertRaisesRegex(ValueError, self.error): self.read()
        self.path.unlink()
        self.path.mkdir()
        with self.assertRaisesRegex(ValueError, self.error): self.read()
        self.path.rmdir()
        os.mkfifo(self.path, 0o600)
        with self.assertRaisesRegex(ValueError, self.error): self.read()

    def test_size_boundary_is_enforced_and_invalid_json_is_not_repaired(self):
        self.path.write_bytes(b'{}' + b' ' * (65536 - 2))
        self.assertEqual(self.read(), {})
        self.path.write_bytes(b'{}' + b' ' * (65537 - 2))
        with self.assertRaisesRegex(ValueError, self.error): self.read()
        self.path.write_text('{broken')
        with self.assertRaises(ValueError): self.read()
        self.assertEqual(self.path.read_text(), '{broken')

    def test_symlink_swap_between_metadata_and_open_is_rejected(self):
        target = self.root / 'target.json'
        atomic_json(target, {'must': 'not follow'})
        real_lstat = Path.lstat
        def inspect_then_replace(path):
            info = real_lstat(path)
            self.path.unlink()
            self.path.symlink_to(target)
            return info
        with mock.patch.object(Path, 'lstat', inspect_then_replace):
            with self.assertRaisesRegex(ValueError, self.error): self.read()
        self.assertEqual(target.read_text().strip(), '{"must": "not follow"}')

    def test_regular_file_swap_between_metadata_and_open_is_rejected(self):
        replacement = self.root / 'replacement.json'
        atomic_json(replacement, {'different': 'inode'})
        real_lstat = Path.lstat
        def inspect_then_replace(path):
            info = real_lstat(path)
            os.replace(replacement, self.path)
            return info
        with mock.patch.object(Path, 'lstat', inspect_then_replace):
            with self.assertRaisesRegex(ValueError, self.error): self.read()


if __name__ == '__main__':
    unittest.main()
