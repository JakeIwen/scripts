"""Per-source snapshots, progress and final outcome publication."""
from contextlib import nullcontext
import json
import os
import re
import time

from offsite_config import HOST, INCOMPLETE_TAG, Result, SCHEMA, StatusRecord
from offsite_repository import Interrupted, durable_json, private_directory
from offsite_samba import quiesced


class Backup:
    def __init__(self, repo, selected):
        self.repo, self.cfg, self.commands = repo, repo.cfg, repo.commands
        self.sources = {name: self.cfg.sources()[name] for name in selected}
        self.owned_mounts = []
        self.target_owned = False
        self.target_verified = False
        self.state: StatusRecord = {'schema': SCHEMA, 'pid': os.getpid(), 'label': repo.label,
                      'result': Result.RUNNING, 'source': None, 'bytes_done': 0,
                      'bytes_total': None, 'percent': None, 'rate': None, 'eta': None,
                      'started': time.time(), 'last_result': None,
                      'sources': {name: {'result': Result.NOT_RUN} for name in selected}}

    def save(self):
        self.state['updated'] = time.time()
        durable_json(self.cfg.state / 'status.json', self.state)

    def progress(self, text):
        try:
            event = json.loads(text)
        except ValueError:
            print(text, flush=True)
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
            self.save()
        elif kind == 'error':
            self.errors.append(event)
            print(text, flush=True)

    def snapshot(self, name, source):
        self.summary, self.errors = None, []
        self.state.update(source=name, bytes_done=0, bytes_total=None, percent=None, rate=None, eta=None)
        self.save()
        self.repo.mount_source(source.label, on_mount=self.owned_mounts.append)
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
        # A changed image or interrupted run never acquires a completed tag state.
        tagged = self.repo.restic('tag', '--json', '--remove', INCOMPLETE_TAG, snapshot)
        changes = [json.loads(line) for line in tagged.splitlines() if line.strip()]
        changes = [row for row in changes if row.get('message_type') == 'changed']
        if (len(changes) != 1 or changes[0].get('old_snapshot_id') != snapshot or
                not re.fullmatch(r'[0-9a-f]{64}', changes[0].get('new_snapshot_id', ''))):
            raise RuntimeError('Cannot identify the completed snapshot after tagging')
        snapshot = changes[0]['new_snapshot_id']
        total = self.summary.get('total_bytes_processed', self.state['bytes_done'])
        self.state.update(percent=100, eta=0, rate=None, bytes_done=total, bytes_total=total)
        return {'result': Result.OK, 'snapshot': snapshot}

    def run_sources(self):
        for name, source in self.sources.items():
            try:
                outcome = self.snapshot(name, source)
            except Interrupted:
                raise
            except (OSError, ValueError, RuntimeError) as exc:
                outcome = {'result': Result.FAILED, 'cause': str(exc)}
            self.state['sources'][name] = outcome
            self.state['last_result'] = outcome
            self.save()
        self.state['result'] = (Result.OK if all(row['result'] == Result.OK for row in
                                               self.state['sources'].values()) else Result.FAILED)

    def cleanup(self):
        if self.target_owned or (self.target_verified and self.state['result'] == Result.INTERRUPTED):
            try:
                self.repo.unmount()
            except (OSError, RuntimeError) as exc:
                self.state.setdefault('cleanup_errors', []).append(f'{self.repo.label}: {exc}')
                if self.state['result'] == Result.OK:
                    self.state['result'] = Result.FAILED
        for label in reversed(self.owned_mounts):
            try:
                self.commands.run('mounts', 'release', label, cleanup=True)
            except (OSError, RuntimeError) as exc:
                self.state.setdefault('cleanup_errors', []).append(f'{label}: {exc}')
                if self.state['result'] == Result.OK:
                    self.state['result'] = Result.FAILED

    def notify(self):
        lines = [f"{name}: {row['result'].value}" + (f" — {row['cause']}" if 'cause' in row else '')
                 for name, row in self.state['sources'].items()]
        lines.extend(self.state.get('cleanup_errors', []))
        if self.state.get('cause'):
            lines.append(self.state['cause'])
        try:
            self.commands.run('notify', 'vanpi offsite ' + self.state['result'], '\n'.join(lines),
                              'min' if self.state['result'] == Result.OK else 'high', cleanup=True)
        except (OSError, RuntimeError) as exc:
            self.state['notification_error'] = str(exc)
            print('Notification failed: ' + str(exc), flush=True)

    def run(self):
        private_directory(self.cfg.state, self.cfg.owner)
        self.save()
        try:
            def own_target(label):
                self.target_owned = True
            self.repo.mount_source(self.repo.label, on_mount=own_target)
            self.repo.verify()
            self.target_verified = True
            self.run_sources()
        except (OSError, ValueError, RuntimeError) as exc:
            result = Result.INTERRUPTED if isinstance(exc, Interrupted) else Result.FAILED
            outcome = {'result': result, 'cause': str(exc)}
            self.state.update(result=result, cause=str(exc), last_result=outcome)
            name = self.state['source']
            if name and self.state['sources'][name]['result'] == Result.NOT_RUN:
                self.state['sources'][name] = outcome
        finally:
            self.cleanup()
            self.state.update(finished=time.time(), rate=None, eta=None)
            self.notify()
            self.save()
        print(json.dumps(self.state, sort_keys=True), flush=True)
        return 0 if self.state['result'] == Result.OK else (143 if self.state['result'] == Result.INTERRUPTED else 1)
