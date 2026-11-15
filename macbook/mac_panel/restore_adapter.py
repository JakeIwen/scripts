"""The llm-restore CLI owns its crontab block, snapshot state and saved session sets."""
from datetime import datetime
import os
from pathlib import Path
import re
import stat

from cli_runner import checked, json_result
from log_redaction import redact
from panel_contracts import ApiError, Reply, RestorePayload, RestoreStatus, SavedSet
from panel_paths import RESTORE, RESTORE_SAVED
from wire_validation import confirm, field, object_fields

# Conservative on purpose: the CLI accepts more, but a name is also a file name and an argv word.
SAVE_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
SNAPSHOT_ACTIONS = frozenset(('preview', 'restore', 'save', 'delete', 'overwrite'))
# Deleted and replaced sets move here: the CLI lists only `saved/*.sh`, so they leave the listing but stay recoverable.
BACKUP_DIR = '.deleted'
# Reopening dozens of sessions as iTerm tabs outlasts the runner's default.
RESTORE_TIMEOUT = 120


def decode_restore(value) -> RestoreStatus:
    object_fields(value, ('enabled', 'installed', 'up_to_date', 'last_snapshot', 'sessions_open'), status=502)
    for key in ('enabled', 'installed', 'up_to_date'):
        field(value, key, bool)
    field(value, 'last_snapshot', float, nullable=True)
    field(value, 'sessions_open', int, nullable=True)
    return value


def decode_saved(value) -> list[SavedSet]:
    if not isinstance(value, list):
        raise ApiError(502, 'Invalid saved sets')
    for item in value:
        object_fields(item, ('name', 'path', 'saved_at', 'sessions'), status=502)
        field(item, 'name', str)
        field(item, 'path', str)
        field(item, 'saved_at', float)
        if field(item, 'sessions', int) < 0:
            raise ApiError(502, 'Invalid sessions field')
    return value


def report_text(result) -> str:
    return '\n'.join(part.strip() for part in (result.stdout, result.stderr) if part.strip())


class RestoreAdapter:
    def __init__(self, runner, saved_dir=RESTORE_SAVED):
        self.runner = runner
        self.saved_dir = Path(saved_dir)

    def saved(self) -> list[SavedSet]:
        return decode_saved(json_result(self.runner.run((*RESTORE, '--saved', '--json'))))

    def read(self) -> RestorePayload:
        status = decode_restore(json_result(self.runner.run((*RESTORE, '--cron', '--json'))))
        return {**status, 'saved': self.saved()}

    def toggle(self, body) -> RestoreStatus:
        object_fields(body, ('enabled',))
        enabled = field(body, 'enabled', bool, status=400)
        argv = (*RESTORE, '--cron', 'on' if enabled else 'off', '--json')
        return decode_restore(json_result(self.runner.run(argv)))

    def snapshots(self, body):
        """POST /api/llm-restore/snapshots. `target` is {"kind": "auto"} (the automatic restore list)
        or {"kind": "saved", "name": NAME}; only restore needs `confirmed: true`."""
        object_fields(body, ('action',), ('target', 'name', 'confirmed'))
        action = field(body, 'action', str, status=400)
        if action not in SNAPSHOT_ACTIONS:
            raise ApiError(400, 'Unsupported snapshot action')
        if action == 'save':
            return self.save(body)
        if action in ('delete', 'overwrite'):
            return self.change_saved(action, body)
        object_fields(body, ('action', 'target'), ('confirmed',))
        if action == 'restore':
            confirm(body)
        argv = (*RESTORE, *(('--dry-run',) if action == 'preview' else ()), *self.target_args(body['target']))
        result = checked(self.runner.run(argv, timeout=RESTORE_TIMEOUT if action == 'restore' else 30))
        return Reply({'message': 'Restore finished' if action == 'restore' else 'Preview ready',
                      'report': report_text(result) or 'Nothing to report.'})

    def target_args(self, target) -> tuple[str, ...]:
        object_fields(target, ('kind',), ('name',))
        if field(target, 'kind', str, status=400) == 'auto':
            object_fields(target, ('kind',))
            return ()
        match = self.saved_target(target)
        # The listed path, not the name: the CLI strips a trailing `.sh` from a bare name, so only a
        # path (any argument with a `/`) always reaches the exact file that was listed.
        return ('--', match['path'])

    def saved_target(self, target) -> SavedSet:
        object_fields(target, ('kind', 'name'))
        if field(target, 'kind', str, status=400) != 'saved':
            raise ApiError(400, 'Only a saved set can be targeted')
        name = field(target, 'name', str, status=400)
        match = next((item for item in self.saved() if item['name'] == name), None)
        if match is None:
            raise ApiError(400, 'Unknown saved set')
        return match

    def set_file(self, match) -> Path:
        """The listed file, only if it is a regular `.sh` directly inside the saved directory."""
        path = Path(match['path'])
        if (path.suffix != '.sh' or path.is_symlink() or not path.is_file()
                or path.parent.resolve() != self.saved_dir.resolve()):
            raise ApiError(400, 'Saved set is not a file in the saved directory')
        return path

    def backup_folder(self) -> Path:
        """`.deleted/`, created private; a symlink or plain file in its place is refused before any use."""
        folder = self.saved_dir / BACKUP_DIR
        try:
            mode = folder.lstat().st_mode
        except FileNotFoundError:
            folder.mkdir(mode=0o700)
        else:
            if not stat.S_ISDIR(mode):
                raise ApiError(400, f'{folder} is not a plain directory; move it aside before deleting or overwriting')
        folder.chmod(0o700)
        return folder

    def reserve_backup(self, path: Path) -> Path:
        """Claim a fresh `.deleted/<stem>-<time>.sh` as an empty exclusive placeholder, so two moves never share a slot."""
        folder = self.backup_folder()
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        for count in range(1000):
            slot = folder / (f'{path.stem}-{stamp}.sh' if not count else f'{path.stem}-{stamp}-{count}.sh')
            try:
                os.close(os.open(slot, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            except FileExistsError:
                continue
            return slot
        raise ApiError(500, 'Could not find a free backup name')

    def move_aside(self, path: Path) -> Path:
        """Atomically rename whatever version sits at `path` into a backup slot: nothing is copied or unlinked,
        so a save landing at the same moment ends up in exactly one of the two places."""
        slot = self.reserve_backup(path)
        try:
            os.replace(path, slot)
        except OSError as exc:
            slot.unlink(missing_ok=True)
            raise ApiError(502, f'Could not move {path} to {slot}: {exc.strerror or exc}') from None
        slot.chmod(0o600)  # a rename keeps the source's mode; the folder is 0700 meanwhile
        return slot

    def change_saved(self, action, body):
        """delete = move the set into `.deleted/`; overwrite = move it aside, then save under the same name."""
        object_fields(body, ('action', 'target'), ('confirmed',))
        confirm(body)
        match = self.saved_target(body['target'])
        path = self.set_file(match)
        name = match['name']
        if action == 'overwrite' and name.endswith('.sh'):
            raise ApiError(400, f'The CLI cannot address a set named {name!r} (it strips a trailing .sh); '
                                'delete it and take a new snapshot instead')
        slot = self.move_aside(path)
        if action == 'delete':
            return Reply({'message': f'Deleted {name}; kept at {slot}', 'report': f'Moved {path} to {slot}'})
        try:
            result = self.runner.run((*RESTORE, f'--save={name}'))
        except ApiError as exc:
            # A timed-out save may still finish and replace `path`, so a put-back copy could be clobbered: leave the old one in the slot.
            raise ApiError(exc.status, f'{exc}; the previous version is kept at {slot}') from None
        if result.returncode and 'already exists' in result.stderr:
            raise ApiError(409, f'Another save landed under {name} meanwhile; both versions are kept '
                                f'(new one at {path}, previous at {slot})')
        if result.returncode:
            raise ApiError(502, f'{redact(result.stderr.strip() or result.stdout.strip()) or "Save failed"}; '
                                f'{self.put_back(slot, path)}')
        return Reply({'message': f'Overwrote {name}; previous version kept at {slot}',
                      'report': '\n'.join(part for part in (report_text(result), f'Previous version: {slot}') if part)})

    @staticmethod
    def put_back(slot: Path, path: Path) -> str:
        """Return the old version after a failed save; a link fails if anything sits at `path`, so it never clobbers."""
        try:
            os.link(slot, path)
        except OSError:
            return f'the previous version is kept at {slot}'
        slot.unlink()
        return 'the previous version was restored'

    def save(self, body):
        object_fields(body, ('action',), ('name',))
        name = field(body, 'name', str, status=400) if 'name' in body else ''
        if name and (not SAVE_NAME.fullmatch(name) or name.endswith('.sh')):
            raise ApiError(400, 'Name must be letters, digits, . _ - (not ending in .sh) and start with a letter or digit (max 64)')
        result = self.runner.run((*RESTORE, f'--save={name}'))
        if result.returncode and 'already exists' in result.stderr:
            raise ApiError(409, 'A saved set with that name already exists; pick another name, or select that set and use Overwrite')
        checked(result)
        return Reply({'message': 'Snapshot saved', 'report': report_text(result)})
