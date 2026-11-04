"""Append-only completion records: encrypted snapshots, never retagging source data."""
import json
import re
import time
from pathlib import Path

from backup_locks import DISK_LABELS, LockClass
from offsite_config import CompletionReceipt, HOST, INCOMPLETE_TAG, RECEIPT_FILE, RECEIPT_TAG, REPO_ID_PATTERN, SCHEMA
from offsite_repository import durable_json, private_directory


def full_snapshot(repo, snapshot):
    rows = json.loads(repo.restic('snapshots', '--json', snapshot))
    if not isinstance(rows, list) or len(rows) != 1 or not re.fullmatch(REPO_ID_PATTERN, rows[0].get('id', '')):
        raise RuntimeError('Cannot resolve exact snapshot identity')
    return rows[0]


def complete(repo, source, snapshot, summary):
    row = full_snapshot(repo, snapshot)
    if row.get('hostname') != HOST or not {source, INCOMPLETE_TAG} <= set(row.get('tags', [])):
        raise RuntimeError('Unexpected source snapshot metadata')
    receipt: CompletionReceipt = {'schema': SCHEMA, 'repo_id': repo.marker['repo_id'], 'source': source,
               'snapshot': row['id'], 'completed': time.time(),
               'logical_bytes_processed': summary.get('total_bytes_processed'),
               'newly_added_packed_bytes': summary.get('data_added_packed')}
    text = repo.restic('backup', '--json', '--host', HOST, '--tag', RECEIPT_TAG,
                       '--stdin', '--stdin-filename', RECEIPT_FILE,
                       input_text=json.dumps(receipt), timeout=None)
    summaries = [value for value in map(json.loads, text.splitlines())
                 if value.get('message_type') == 'summary']
    if len(summaries) != 1:
        raise RuntimeError('Completion receipt did not finish')
    saved = full_snapshot(repo, summaries[0].get('snapshot_id', ''))
    actual = read_receipt(repo, saved['id'])
    if actual != receipt:
        raise RuntimeError('Completion receipt readback differs')
    return {'snapshot': row['id'], 'receipt': saved['id']}


def read_receipt(repo, receipt_id):
    if not re.fullmatch(REPO_ID_PATTERN, receipt_id):
        raise ValueError('Receipt must be a full snapshot ID')
    row = full_snapshot(repo, receipt_id)
    if row.get('hostname') != HOST or RECEIPT_TAG not in row.get('tags', []):
        raise ValueError('Not an offsite completion receipt')
    record = json.loads(repo.restic('dump', receipt_id, '/' + RECEIPT_FILE, timeout=None))
    if (not isinstance(record, dict) or type(record.get('schema')) is not int or record['schema'] != SCHEMA
            or record.get('repo_id') != repo.marker['repo_id'] or record.get('source') not in repo.cfg.sources()
            or not re.fullmatch(REPO_ID_PATTERN, str(record.get('snapshot', '')))
            or type(record.get('completed')) not in (int, float)):
        raise ValueError('Invalid completion receipt')
    source = full_snapshot(repo, record['snapshot'])
    if source.get('hostname') != HOST or not {record['source'], INCOMPLETE_TAG} <= set(source.get('tags', [])):
        raise ValueError('Receipt references an unexpected source snapshot')
    return record


def list_receipts(repo):
    repo.verify()
    rows = json.loads(repo.restic('snapshots', '--json', '--host', HOST, '--tag', RECEIPT_TAG))
    return [{'receipt': row['id'], **read_receipt(repo, row['id'])} for row in rows]


def evidence_operation(repo, operation, callback, **details):
    private_directory(repo.cfg.state, repo.cfg.owner)
    record = {'schema': SCHEMA, 'operation': operation, 'label': repo.label,
              'repo_id': repo.marker['repo_id'] if repo.marker else None, 'started': time.time(), **details}
    try:
        result = callback()
        record['result'] = 'ok'
        return result
    except (OSError, ValueError, RuntimeError) as exc:
        record.update(result='failed', cause=str(exc))
        raise
    finally:
        record['repo_id'] = repo.marker['repo_id'] if repo.marker else None
        record['finished'] = time.time()
        record['duration_seconds'] = record['finished'] - record['started']
        # Separate check/readback evidence does not overwrite the last backup.
        durable_json(repo.cfg.state / ('remote-' + operation + '.json'), record)


def restore_disk(repo, target):
    """The managed backup disk whose mounted filesystem holds target; None for the root filesystem."""
    mount = Path(repo.commands.run('findmnt', '-rn', '-T', target, '-o', 'TARGET').strip())
    if mount == Path('/'):
        # A disk mounted over its mount point later would cover this directory,
        # and restic would then write onto that disk without its lock.
        if target.is_relative_to(repo.cfg.mount_root):
            raise ValueError(f'Restore test target is inside {repo.cfg.mount_root} but on the root filesystem '
                             '(its disk is not mounted); mount that disk first or choose a target elsewhere')
        return None
    if mount.parent == repo.cfg.mount_root and mount.name in DISK_LABELS:
        return mount.name
    raise ValueError('Restore test target must be on the root filesystem or a managed backup disk')


def restore_test(repo, receipt_id, include, target):
    target = Path(target)
    if (not include.startswith('/') or any(part == '..' for part in include.split('/'))
            or not target.is_absolute() or target.resolve() != target or not target.is_dir() or any(target.iterdir())):
        raise ValueError('Restore test needs an absolute snapshot path and a separate empty real directory')
    label = restore_disk(repo, target)
    def operation():
        if label:
            # Held from before the mount is re-verified until the restore ends,
            # so a drain of that disk stops this command first.
            repo.commands.locks.take(LockClass.DISK, label, exclusive=False)
        try:
            if label:
                if restore_disk(repo, target) != label:
                    raise RuntimeError('Restore test target moved to another filesystem')
                repo.exact_mount(label)
            repo.verify()
            receipt = read_receipt(repo, receipt_id)
            result = repo.restic('restore', receipt['snapshot'], '--target', target,
                                 '--include', include, '--verify', timeout=None)
            if not any(path.is_file() and not path.is_symlink() for path in target.rglob('*')):
                raise RuntimeError('Restore test produced no regular file; check the include path')
            return result
        finally:
            if label:
                repo.commands.locks.close(LockClass.DISK, label)
    return evidence_operation(repo, 'restore-test', operation, receipt=receipt_id, include=include)
