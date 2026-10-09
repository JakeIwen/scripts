"""Real verifier processes and streaming clients against an isolated fake cloud."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import icloud_backup as cloud
from time_machine_pipeline import VerificationPipeline
from time_machine_verification import ObjectVerifier
from time_machine_store import atomic_json, read_json


def object_row(digest, data):
    return {'Path':digest,'IsDir':False,'Size':len(data),'ModTime':'stable','ID':digest}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.remote = self.root / 'remote'; self.remote.mkdir()
        self.lock_path = self.root / 'backup.lock'; self.lock_path.touch()
        self.lock = self.lock_path.open('r+')
        self.addCleanup(self.lock.close)
        fcntl.flock(self.lock, fcntl.LOCK_EX)
        self.a, self.b = b'completed earlier', b'next uploaded chunk'
        self.da, self.db = (hashlib.sha256(value).hexdigest() for value in (self.a,self.b))
        self.expected = {k:{'sha256':k,'bytes':len(v)} for k,v in ((self.da,self.a),(self.db,self.b))}
        (self.remote / self.da).write_bytes(self.a)
        self.rows = {self.da: object_row(self.da,self.a)}
        self.cfg = {'remote':'icloud:fake','bandwidth_limit':'8M', 'guard_interval_seconds':0.01,
                    'no_progress_timeout_seconds':5}
        self.real_run = cloud.run

    def fake_network(self, args, cfg, **kwargs):
        operation = args[1]
        if operation == 'lsjson':
            return json.dumps([object_row(p.name,p.read_bytes()) for p in self.remote.iterdir()])
        self.assertEqual(operation,'cat')
        self.assertTrue(kwargs['stream_hash'])
        digest = args[2].rsplit('/',1)[1]
        code = 'import pathlib,sys; sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())'
        return self.real_run([sys.executable,'-c',code,str(self.remote/digest)],cfg,parked=False,
                             stream_hash=True,progress=kwargs.get('progress'))

    def pipeline(self, network=None):
        patches = [mock.patch.object(cloud,'run',side_effect=network or self.fake_network),
                   mock.patch.object(cloud,'check_work_allowed')]
        for patch in patches:
            patch.start(); self.addCleanup(patch.stop)
        return VerificationPipeline(self.cfg,self.root,self.expected,self.rows,
                                    lock_fd=self.lock.fileno(),lock_path=self.lock_path)

    def until(self, pipeline, condition):
        deadline = time.monotonic()+8
        while not condition():
            pipeline.progress()
            if time.monotonic()>deadline: self.fail('verifier did not make expected progress')
            time.sleep(0.02)

    def checkpoints(self):
        return read_json(self.root/'verified-objects.json',{})

    def test_existing_chunks_then_new_completions_verify_before_upload_finishes(self):
        with self.pipeline() as follower:
            self.until(follower,lambda:self.da in self.checkpoints())
            self.assertFalse(follower.done.is_set())
            self.assertIsNone(cloud.CHILD)
            (self.remote/self.db).write_bytes(self.b)
            # Visibility alone does not admit an in-flight object.
            time.sleep(0.1)
            self.assertNotIn(self.db,self.checkpoints())
            follower.completed_object(self.db)
            self.until(follower,lambda:self.db in self.checkpoints())
            self.assertFalse(follower.done.is_set())
            follower.finish(lambda _:None)
        self.assertEqual(set(self.checkpoints()),set(self.expected))
        proof = self.checkpoints()[self.da]
        self.assertEqual(proof['expected'],self.expected[self.da])
        self.assertEqual(proof['remote'],cloud.remote_fingerprint(self.rows[self.da]))

    def test_wrong_hash_is_not_checkpointed_and_is_reported_to_uploader(self):
        atomic_json(self.root/'verified-objects.json', {self.da:{'expected':self.expected[self.da],
            'remote':cloud.remote_fingerprint(self.rows[self.da]),'verified_at':1}})
        (self.remote/self.db).write_bytes(b'X'*len(self.b))
        self.rows[self.db]=object_row(self.db,self.b)
        with self.pipeline() as follower:
            with self.assertRaisesRegex(RuntimeError,'SHA-256'):
                self.until(follower,lambda:False)
        self.assertNotIn(self.db,self.checkpoints())
        self.assertIn(self.da,self.checkpoints())

    def test_checkpoint_reuse_does_not_redownload_completed_objects(self):
        proof={'expected':self.expected[self.da],'remote':cloud.remote_fingerprint(self.rows[self.da]),'verified_at':1}
        atomic_json(self.root/'verified-objects.json',{self.da:proof})
        def reject_download(*_args,**_kwargs):
            raise RuntimeError('completed checkpoint was downloaded again')
        with self.pipeline(reject_download) as follower:
            self.until(follower,lambda:follower.progress().get('verified_files')==1)
            follower.finish(lambda _:None)
        self.assertEqual(self.checkpoints()[self.da],proof)

    def test_abort_stops_real_streaming_client_and_preserves_completed_checkpoint(self):
        proof={'expected':self.expected[self.da],'remote':cloud.remote_fingerprint(self.rows[self.da]),'verified_at':1}
        atomic_json(self.root/'verified-objects.json',{self.da:proof})
        (self.remote/self.db).write_bytes(self.b)
        self.rows[self.db]=object_row(self.db,self.b)
        pid_file=self.root/'client.pid'
        def slow_download(args,cfg,**kwargs):
            code='import pathlib,os,time; pathlib.Path('+repr(str(pid_file))+').write_text(str(os.getpid())); time.sleep(30)'
            return self.real_run([sys.executable,'-c',code],cfg,parked=False,stream_hash=True,progress=kwargs.get('progress'))
        with self.pipeline(slow_download) as follower:
            self.until(follower,pid_file.exists)
            pid=int(pid_file.read_text())
        with self.assertRaises(ProcessLookupError): os.kill(pid,0)
        self.assertEqual(self.checkpoints(),{self.da:proof})

    def test_verifier_retains_shared_lock_until_it_stops(self):
        with self.pipeline() as follower:
            self.until(follower,lambda:self.da in self.checkpoints())
            self.lock.close()
            with self.lock_path.open('r+') as contender:
                with self.assertRaises(BlockingIOError): fcntl.flock(contender,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with self.lock_path.open('r+') as contender:
            fcntl.flock(contender,fcntl.LOCK_EX|fcntl.LOCK_NB)

    def test_final_identity_change_invalidates_only_affected_proof(self):
        verifier=ObjectVerifier(self.expected,self.root/'verified-objects.json')
        self.rows[self.db]=object_row(self.db,self.b)
        for key in self.expected:
            verifier.verify(key,self.rows[key],lambda k:self.expected[k])
        changed={**self.rows,self.db:{**self.rows[self.db],'ID':'replacement'}}
        self.assertEqual(verifier.invalidate_changed(changed),[self.db])
        self.assertEqual(set(self.checkpoints()),{self.da})


if __name__=='__main__': unittest.main()
