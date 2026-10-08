import fcntl
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from .test_time_machine_icloud import GEN, source_fixture, store
import time_machine_staging as staging


class StagingCleanupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / 'store'; self.root.mkdir(mode=0o700)
        (self.root / 'generations').mkdir(mode=0o700)
        store.atomic_json(self.root / 'OWNER.json', {'owner': store.OWNER})
        self.source = source_fixture(self.base)
        self.manifest = store.freeze(self.source, self.root, GEN, 72, 0, lambda: None, lambda *args: None)
        self.lock_path = self.base / 'backup.lock'
        self.lock = self.lock_path.open('w+')
        self.addCleanup(self.lock.close)
        fcntl.flock(self.lock, fcntl.LOCK_EX)
        self.lock_args = {'lock_fd': self.lock.fileno(), 'lock_path': self.lock_path}

    def orphan(self, data=b'obsolete encrypted chunk'):
        path = self.root / 'objects' / hashlib.sha256(data).hexdigest()
        path.write_bytes(data); path.chmod(0o400)
        return path

    def plan(self):
        return staging.plan_cleanup(self.root, lambda: None, **self.lock_args)

    def apply(self, plan, check=lambda: None):
        return staging.apply_cleanup(plan, check, **self.lock_args)

    def test_dry_run_and_apply_preserve_all_live_and_archived_data(self):
        orphan = self.orphan()
        original = {p: p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        archive = self.base / 'archived.sparsebundle'; archive.mkdir(); (archive / 'keep').write_bytes(b'old backup')
        plan = self.plan()
        self.assertEqual(plan.summary()['objects'], 1)
        self.assertTrue(orphan.exists())
        self.assertGreater(plan.summary()['allocated_bytes'], 0)
        self.assertEqual(self.apply(plan)['objects'], 1)
        self.assertFalse(orphan.exists())
        for path, data in original.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertEqual((archive / 'keep').read_bytes(), b'old backup')
        for entry in self.manifest['files'].values():
            self.assertTrue((self.root / 'objects' / entry['sha256']).is_file())

    def test_pending_and_published_manifests_protect_objects_not_in_current_index(self):
        generation = self.root / 'generations' / GEN; generation.mkdir(mode=0o700)
        store.atomic_json(generation / 'manifest.json', self.manifest)
        protected = self.manifest['files']['bands/0']['sha256']
        (self.source / 'bands/0').write_bytes(b'new native backup band')
        (self.root / 'pending.json').unlink()
        store.freeze(self.source, self.root, GEN, 72, 0, lambda: None, lambda *args: None)
        self.orphan()
        self.apply(self.plan())
        self.assertTrue((self.root / 'objects' / protected).is_file())

    def test_upload_list_and_verification_checkpoint_are_protected(self):
        upload = self.orphan(b'upload'); verified = self.orphan(b'verified')
        (self.root / 'upload-files.txt').write_text(upload.name + '\n')
        store.atomic_json(self.root / 'verified-objects.json', {verified.name: {}})
        self.assertEqual(self.plan().summary()['objects'], 0)

    def test_pending_manifest_alone_protects_an_object(self):
        index = store.read_json(self.root / 'capture-index.json')
        protected = index.pop('bands/0')['entry']['sha256']
        store.atomic_json(self.root / 'capture-index.json', index)
        self.orphan()
        self.apply(self.plan())
        self.assertTrue((self.root / 'objects' / protected).is_file())

    def test_corrupt_generation_refuses_cleanup(self):
        orphan = self.orphan()
        generation = self.root / 'generations' / GEN; generation.mkdir(mode=0o700)
        store.atomic_json(generation / 'manifest.json', {'owner': store.OWNER})
        with self.assertRaises(ValueError): self.plan()
        self.assertTrue(orphan.exists())

    def test_reference_change_invalidates_prepared_plan(self):
        orphan = self.orphan(); plan = self.plan()
        store.atomic_json(self.root / 'capture-index.json', {})
        with self.assertRaisesRegex(RuntimeError, 'plan changed'):
            self.apply(plan)
        self.assertTrue(orphan.exists())

    def test_reference_change_during_deletion_stops_before_unlink(self):
        orphan = self.orphan(); plan = self.plan()
        calls = 0
        def changed():
            nonlocal calls
            calls += 1
            if calls == 3:
                store.atomic_json(self.root / 'capture-index.json', {})
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            self.apply(plan, changed)
        self.assertTrue(orphan.exists())

    def test_mount_guard_failure_preserves_candidates(self):
        orphan = self.orphan(); plan = self.plan()
        def missing_mount():
            raise RuntimeError('mount identity changed')
        with self.assertRaisesRegex(RuntimeError, 'mount identity'):
            self.apply(plan, missing_mount)
        self.assertTrue(orphan.exists())

    def test_symlink_unknown_and_hardlinked_objects_refuse_all_cleanup(self):
        orphan = self.orphan()
        unsafe = self.root / 'objects' / ('f' * 64)
        unsafe.symlink_to(self.source / 'token')
        with self.assertRaises(ValueError): self.plan()
        unsafe.unlink()
        os.link(self.source / 'token', unsafe)
        with self.assertRaises(ValueError): self.plan()
        unsafe.unlink()
        unsafe = self.root / 'objects' / 'unknown'; unsafe.write_text('unknown')
        with self.assertRaises(ValueError): self.plan()
        self.assertTrue(orphan.exists())

    def test_abandoned_capture_temporary_file_is_removed_under_worker_lock(self):
        temporary = self.root / 'objects' / '.capture-ltpuret4'
        temporary.write_bytes(b'partial metadata'); temporary.chmod(0o600)
        plan = self.plan()
        self.assertEqual(plan.summary()['temporary_objects'], 1)
        self.apply(plan)
        self.assertFalse(temporary.exists())
        for entry in self.manifest['files'].values():
            self.assertTrue((self.root / 'objects' / entry['sha256']).exists())
        self.assertEqual(self.plan().summary()['objects'], 0)

    def test_interrupted_first_file_before_index_can_be_cleaned(self):
        for path in (self.root / 'objects').iterdir():
            path.unlink()
        (self.root / 'capture-index.json').unlink()
        (self.root / 'pending.json').unlink()
        temporary = self.root / 'objects' / '.capture-ab12_c34'
        temporary.write_bytes(b'partial'); temporary.chmod(0o600)
        self.apply(self.plan())
        self.assertFalse(temporary.exists())

    def test_temporary_name_does_not_bypass_link_or_permission_checks(self):
        temporary = self.root / 'objects' / '.capture-abcdefgh'
        temporary.symlink_to(self.source / 'token')
        with self.assertRaises(ValueError): self.plan()
        temporary.unlink()
        os.link(self.source / 'token', temporary)
        with self.assertRaises(ValueError): self.plan()
        temporary.unlink()
        temporary.write_bytes(b'partial'); temporary.chmod(0o666)
        with self.assertRaises(ValueError): self.plan()

    def test_corrupt_or_missing_metadata_preserves_orphans(self):
        orphan = self.orphan()
        index = self.root / 'capture-index.json'
        index.write_text('{broken')
        with self.assertRaises(ValueError): self.plan()
        index.write_text('{"same": {}, "same": {}}')
        with self.assertRaisesRegex(ValueError, 'duplicate'): self.plan()
        index.unlink()
        with self.assertRaisesRegex(ValueError, 'index missing'): self.plan()
        self.assertTrue(orphan.exists())

    def test_owner_and_directory_replacement_are_rejected(self):
        orphan = self.orphan(); plan = self.plan()
        objects = self.root / 'objects'
        objects.rename(self.root / 'old-objects')
        objects.mkdir(mode=0o700)
        with self.assertRaises(ValueError): self.apply(plan)
        objects.rmdir(); (self.root / 'old-objects').rename(objects)
        store.atomic_json(self.root / 'OWNER.json', {'owner': 'other'})
        with self.assertRaisesRegex(ValueError, 'owner mismatch'): self.plan()
        self.assertTrue(orphan.exists())

    def test_second_lock_descriptor_cannot_enter_while_worker_owns_lock(self):
        with self.lock_path.open('r') as other:
            with self.assertRaises(BlockingIOError):
                staging.plan_cleanup(self.root, lambda: None, lock_fd=other.fileno(), lock_path=self.lock_path)

    def test_retries_reclaim_superseded_capture_chunks_before_success(self):
        (self.root / 'pending.json').unlink()
        prior = self.manifest['files']['bands/0']['sha256']
        (self.source / 'bands/0').write_bytes(b'changed while previous attempt was stopped')
        store.freeze(self.source, self.root, GEN, 72, 0, lambda: None, lambda *args: None)
        (self.root / 'pending.json').unlink()
        plan = self.plan()
        self.assertIn(prior, {entry.name for entry in plan.candidates})
        self.apply(plan)
        self.assertFalse((self.root / 'objects' / prior).exists())
        resumed = store.freeze(self.source, self.root, GEN, 72, 0, lambda: None, lambda *args: None)
        self.assertEqual(resumed['generation'], GEN)


if __name__ == '__main__':
    unittest.main()
