"""Explicit torrent delivery to qBittorrent; never download during a check."""
import os
import re
import tempfile
from dataclasses import replace
from . import config, state, transport
from .records import CANDIDATE_DISMISSED, CANDIDATE_DOWNLOADED, LOGIN_NEEDED, LOGIN_UNKNOWN
from .site import Site


def find(torrent_id):
    if not isinstance(torrent_id, str) or not re.fullmatch(r'\d+', torrent_id):
        raise ValueError('invalid torrent id')
    rows = state.candidates()
    match = next((row for row in rows if row.torrent_id == torrent_id), None)
    if match is None:
        raise ValueError('unknown torrent id')
    return rows, match


def save(rows, updated):
    state.save_candidates([updated if row.torrent_id == updated.torrent_id else row for row in rows])
    return updated


def filename(name):
    value = re.sub(r'[/\\\x00-\x1f\x7f]', '_', name).strip().strip('.')
    if not value:
        raise ValueError('invalid torrent filename')
    # Leave room for the suffix on filesystems with a 255-byte component limit.
    value = value.encode('utf-8')[:230].decode('utf-8', errors='ignore')
    return value + ' [IPT].torrent'


def publish(path, body):
    fd, temporary = tempfile.mkstemp(prefix='.ipt-', suffix='.part', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        # Link publishes a complete file without replacing a different queued torrent.
        os.link(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)


def download(torrent_id, site=None):
    with state.operation():
        rows, candidate = find(torrent_id)
        if candidate.status == CANDIDATE_DOWNLOADED:
            return candidate
        if candidate.status == CANDIDATE_DISMISSED:
            raise ValueError('torrent has been dismissed')
        try:
            if not candidate.download_path.startswith(f'/download.php/{torrent_id}/'):
                raise ValueError('invalid torrent download path')
            body = (site or Site()).download(candidate.download_path)
            body = transport.decode_body(body, config.MAX_TORRENT_BYTES)
            if not body.startswith(b'd') or b'4:info' not in body or not body.endswith(b'e'):
                raise ValueError('download was not a torrent; login or browser verification may be needed')
            path = config.TFILES_DIR / filename(candidate.name)
            publish(path, body)
            candidate = replace(candidate, status=CANDIDATE_DOWNLOADED, downloaded_at=state.now(),
                                file_path=str(path), error=None)
            save(rows, candidate)
            state.log(f'Downloaded torrent {torrent_id}')
            return candidate
        except Exception as error:
            save(rows, replace(candidate, error=str(error)))
            if isinstance(error, (transport.LoginNeeded, transport.SiteUnavailable)):
                state.metadata(login=LOGIN_NEEDED if isinstance(error, transport.LoginNeeded) else LOGIN_UNKNOWN,
                               last_error=str(error))
            state.log(f'Download failed for {torrent_id}: {error}')
            raise


def dismiss(torrent_id):
    with state.operation():
        rows, candidate = find(torrent_id)
        return save(rows, replace(candidate, status=CANDIDATE_DISMISSED))
