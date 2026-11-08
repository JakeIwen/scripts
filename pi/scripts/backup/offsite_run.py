"""Per-source snapshots, progress and final outcome publication."""
from contextlib import nullcontext
import json
import os
import re
import time

from backup_locks import LockBusy, LockClass
from offsite_config import BORG_REPOSITORY_LOCK, BORG_SOURCE, HOST, INCOMPLETE_TAG, Result, SCHEMA
from offsite_config import StatusRecord, TargetKind
from offsite_dashboard import rate_sampler, record_outcome
from offsite_repository import Interrupted, durable_json, private_directory
from offsite_samba import quiesced


class Deferred(Exception):
    """The Borg repository is busy now; retry the borg source after the others."""


class Backup:
    def __init__(self, repo, selected):
        self.repo, self.cfg, self.commands = repo, repo.cfg, repo.commands
        self.sources = {name: self.cfg.sources()[name] for name in selected}
        self.locks = self.commands.locks
        self.target_owned = False
        self.target_verified = False
        self.deferred_releases = []  # labels a stopping phase mounted; cleanup() releases them
        self.remote = getattr(repo, 'remote', False)
        self.state: StatusRecord = {'schema': SCHEMA, 'pid': os.getpid(), 'label': repo.label,
                      'result': Result.RUNNING, 'source': None, 'bytes_done': 0,
                      'bytes_total': None, 'percent': None, 'rate': None, 'eta': None,
                      'started': time.time(), 'last_result': None,
                      'sources': {name: {'result': Result.NOT_RUN} for name in selected},
                      'target_kind': TargetKind.REMOTE if self.remote else TargetKind.LOCAL,
                      # Known once the repository configures its transport (R11).
                      'transport_kind': None}
        if self.remote and self.commands.remote_control is not None:
            self.state['control_revision'] = self.commands.remote_control.revision

    def save(self):
        self.state['updated'] = time.time()
        durable_json(self.cfg.state / 'status.json', self.state)

    def record_history(self, name, outcome):
        try:
            record_outcome(self.cfg, self.repo.label, name, outcome,
                           TargetKind.REMOTE if self.remote else TargetKind.LOCAL)
        except (OSError, ValueError, RuntimeError) as exc:
            self.state['history_error'] = str(exc)

    def progress(self, text):
        # Parse the raw line (redaction could corrupt JSON); print only redacted text.
        try:
            event = json.loads(text)
        except ValueError:
            print(self.repo.redact(text), flush=True)
            return
        kind = event.get('message_type')
        if kind == 'summary':
            self.summary = event
        elif kind == 'status':
            done, total = event.get('bytes_done', 0), event.get('total_bytes')
            elapsed = event.get('seconds_elapsed', 0)
            rate = done / elapsed if elapsed > 0 else None
            self.state.update(bytes_done=done, bytes_total=total,
                              percent=min(99.9, 100 * done / total) if total else None,
                              rate=rate, eta=max(0, total - done) / rate if total and rate else None)
            if self.sampler is not None:
                self.sampler.observe(self.cfg.state, done)
            self.save()
        elif kind == 'error':
            self.errors.append(event)
            print(self.repo.redact(text), flush=True)

    def phase(self, name, source, wait):
        """One source under its own locks: disk shared, then Borg's repository.

        Locks precede mounting and the ignition re-check follows them, as for
        Borg and EXFAT. Release runs in reverse: close our descriptors, then let
        the helper unmount only what this phase mounted, and only when no other
        job holds the disk. A stopping run defers that release to cleanup(),
        which first closes every descriptor, the target's included: a drain
        waits only briefly for the target, and an unmount can stall.
        """
        self.summary, self.errors = None, []
        self.sampler = rate_sampler(self.cfg, name)
        self.state.update(source=name, bytes_done=0, bytes_total=None, percent=None, rate=None, eta=None)
        self.save()
        mounted, stopping = [], False
        try:
            # A stop that arrived since the last phase (during its release, say) takes no new lock.
            self.commands.allowed()
            if name == BORG_SOURCE and wait is not None:
                # The deferred wait holds no bigboi lock, so a finishing nightly
                # Borg can still unmount bigboi and a bigboi drain during the
                # wait does not stop this run. Disk locks never wait, so taking
                # the repository first cannot deadlock.
                self.hold_borg_repository(wait)
            self.locks.take(LockClass.DISK, source.label, exclusive=False)
            if name == BORG_SOURCE and wait is None:
                self.hold_borg_repository(wait)
            self.commands.allowed()
            self.repo.mount_source(source.label, on_mount=mounted.append)
            return self.snapshot(name, source)
        except Interrupted:
            stopping = True
            raise
        finally:
            self.locks.close(LockClass.DATA, BORG_REPOSITORY_LOCK)
            self.locks.close(LockClass.DISK, source.label)
            if stopping or self.commands.stop_cause():
                self.deferred_releases.extend(mounted)
            else:
                for label in mounted:
                    self.release(label)

    def hold_borg_repository(self, wait):
        try:
            self.locks.take(LockClass.DATA, BORG_REPOSITORY_LOCK, exclusive=True, wait=wait,
                            allowed=self.commands.allowed)
        except LockBusy:
            if wait is None:
                raise Deferred() from None
            waited = f'{wait / 3600:g} h' if wait >= 3600 else f'{wait:g} s'
            raise RuntimeError(f'Borg repository stayed busy for {waited} (another job holds '
                               'data-borg-repo); borg was not copied this run') from None

    def release(self, label, release=None):
        """Run one guarded release (default: the mount helper's for label) and record its outcome."""
        try:
            note = release() if release else self.repo.release(label)
        except (OSError, RuntimeError) as exc:
            self.state.setdefault('cleanup_errors', []).append(f'{label}: {exc}')
            return
        if note:
            self.state.setdefault('cleanup_notes', []).append(note)

    def snapshot(self, name, source):
        mount = self.cfg.mount_root / source.label
        for path in source.paths:
            if not path.is_dir() or path.resolve() != path or not path.is_relative_to(mount):
                raise RuntimeError(f'Missing or unsafe source directory: {path}')
            actual = self.commands.run('findmnt', '-rn', '-T', path, '-o', 'SOURCE').strip()
            if actual != self.repo.exact_mount(source.label):
                raise RuntimeError(f'Source is on an unexpected nested filesystem: {path}')
        args = ['backup', '--json', '--host', HOST, '--tag', name, '--tag', INCOMPLETE_TAG,
                '--one-file-system', '--compression', source.compression]
        for exclude in source.excludes:
            args.extend(('--exclude', exclude))
        context = quiesced(self.repo, source.paths[0]) if name == 'time-machine' else nullcontext(None)
        with context as source_guard:
            def guard():
                self.repo.exact_mount()
                self.repo.exact_mount(source.label)
                if source_guard:
                    source_guard()
            self.repo.restic(*args, '--', *source.paths, line=self.progress, guard=guard, timeout=None)
            guard()
            if self.errors or not self.summary or not re.fullmatch(r'[0-9a-f]{8,64}', self.summary.get('snapshot_id', '')):
                raise RuntimeError('Restic did not report a complete error-free snapshot')
        snapshot = self.summary['snapshot_id']
        completion = {}
        if self.remote:
            from offsite_receipts import complete
            completion = complete(self.repo, name, snapshot, self.summary)
            snapshot = completion['snapshot']
        else:
            # Local v1 can rewrite tags; append-only remote repositories cannot.
            tagged = self.repo.restic('tag', '--json', '--remove', INCOMPLETE_TAG, snapshot)
            changes = [json.loads(line) for line in tagged.splitlines() if line.strip()]
            changes = [row for row in changes if row.get('message_type') == 'changed']
            if (len(changes) != 1 or changes[0].get('old_snapshot_id') != snapshot or
                    not re.fullmatch(r'[0-9a-f]{64}', changes[0].get('new_snapshot_id', ''))):
                raise RuntimeError('Cannot identify the completed snapshot after tagging')
            snapshot = changes[0]['new_snapshot_id']
        total = self.summary.get('total_bytes_processed', self.state['bytes_done'])
        self.state.update(percent=100, eta=0, rate=None, bytes_done=total, bytes_total=total)
        return {'result': Result.OK, 'snapshot': snapshot, **completion,
                'logical_bytes_processed': total,
                'newly_added_packed_bytes': self.summary.get('data_added_packed')}

    def run_source(self, name, source, wait=None):
        """Snapshot one source; False when a busy Borg repository defers it."""
        started = time.time()
        try:
            outcome = self.phase(name, source, wait)
        except Deferred:
            print(f'{name}: Borg repository is busy; deferred to the end of this run', flush=True)
            return False
        except Interrupted as exc:
            self.state['sources'][name] = {'result': Result.INTERRUPTED, 'cause': str(exc),
                                           'duration_seconds': time.time() - started}
            self.record_history(name, self.state['sources'][name])
            raise
        except (OSError, ValueError, RuntimeError) as exc:
            outcome = {'result': Result.FAILED, 'cause': str(exc)}
        outcome['duration_seconds'] = time.time() - started
        self.state['sources'][name] = outcome
        self.state['last_result'] = outcome
        self.record_history(name, outcome)
        self.save()
        return True

    def run_sources(self):
        deferred = [(name, source) for name, source in self.sources.items()
                    if not self.run_source(name, source)]
        for name, source in deferred:
            self.run_source(name, source, wait=self.cfg.borg_wait)
        self.state['result'] = (Result.OK if all(row['result'] == Result.OK for row in
                                               self.state['sources'].values())
                                and not self.state.get('cleanup_errors') else Result.FAILED)

    def cleanup(self):
        # Failed restic runs can leave packs too. Share while the verified target's
        # disk is still held, but never delay a stop/drain for a repository walk.
        if not self.remote and self.target_verified:
            try:
                self.commands.allowed()
                if self.state['result'] != Result.INTERRUPTED:
                    self.repo.share_for_smb()
            except Interrupted as exc:
                if self.state['result'] == Result.OK:
                    self.state.update(result=Result.INTERRUPTED, cause=str(exc))
                else:
                    self.state.setdefault('cleanup_notes', []).append(f'Repository SMB sharing skipped: {exc}')
            except Exception as exc:
                # Even an unexpected walk failure must release descriptors and publish status.
                self.state.setdefault('cleanup_errors', []).append(f'Repository SMB sharing: {exc}')
        # Every disk, data and slot descriptor closes before any release helper,
        # ntfy or other network call: the ordinary drain waits only briefly for
        # disk locks, and an unmount or a notification without uplink can stall.
        self.locks.close_resources()
        for label in self.deferred_releases:
            self.release(label)
        if self.target_owned or (self.target_verified and (
                self.state['result'] == Result.INTERRUPTED or self.commands.stop_cause())):
            self.release(self.repo.label, self.repo.unmount)
        if self.state.get('cleanup_errors') and self.state['result'] == Result.OK:
            self.state['result'] = Result.FAILED

    def notify(self):
        lines = [f"{name}: {row['result'].value}" + (f" — {row['cause']}" if 'cause' in row else '')
                 for name, row in self.state['sources'].items()]
        lines.extend(self.state.get('cleanup_errors', []))
        if self.state.get('cause'):
            lines.append(self.state['cause'])
        try:
            self.commands.run('notify', 'vanpi offsite ' + self.state['result'], '\n'.join(lines),
                              'default' if self.state['result'] == Result.OK else 'high', cleanup=True)
        except (OSError, RuntimeError) as exc:
            self.state['notification_error'] = str(exc)
            print('Notification failed: ' + str(exc), flush=True)

    def run(self):
        private_directory(self.cfg.state, self.cfg.owner)
        self.save()
        try:
            def own_target(label):
                self.target_owned = True
            self.repo.hold_target()
            self.commands.allowed()
            self.repo.mount_source(self.repo.label, on_mount=own_target)
            self.repo.verify()
            self.state['transport_kind'] = self.repo.transport_kind
            self.target_verified = True
            self.run_sources()
            # A stop seen only during the last source's release still interrupts
            # the run, so a pre-existing verified target is released below.
            self.commands.allowed()
        except (OSError, ValueError, RuntimeError) as exc:
            result = Result.INTERRUPTED if isinstance(exc, Interrupted) else Result.FAILED
            outcome = {'result': result, 'cause': str(exc)}
            self.state.update(result=result, cause=str(exc), last_result=outcome)
            name = self.state['source']
            if name and self.state['sources'][name]['result'] == Result.NOT_RUN:
                self.state['sources'][name] = outcome
                self.record_history(name, outcome)
        finally:
            self.cleanup()
            self.state.update(finished=time.time(), rate=None, eta=None)
            self.state['duration_seconds'] = self.state['finished'] - self.state['started']
            self.state['target_kind'] = TargetKind.REMOTE if self.remote else TargetKind.LOCAL
            self.state['transport_kind'] = self.repo.transport_kind
            self.state['progress_measure'] = 'logical source bytes, not network throughput'
            self.notify()
            self.save()
        print(json.dumps(self.state, sort_keys=True), flush=True)
        return 0 if self.state['result'] == Result.OK else (143 if self.state['result'] == Result.INTERRUPTED else 1)
