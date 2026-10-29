"""Read-only dashboard projection; no network traffic during status polling."""
import os
from . import config, state, wantlist
from .records import CANDIDATE_DISMISSED, CANDIDATE_NEW, LOGIN_UNKNOWN, Status


def candidates():
    rows = [row for row in state.candidates() if row.status != CANDIDATE_DISMISSED]
    return sorted(rows, key=lambda row: (row.status == CANDIDATE_NEW, row.first_seen, int(row.torrent_id)), reverse=True)


def status():
    data = state.metadata()
    wanted = wantlist.merged()
    rows = candidates()
    return Status(login=data.get('login', LOGIN_UNKNOWN), last_check=data.get('last_check'),
                  last_error=data.get('last_error'), checking=state.checking(), wanted_count=len(wanted),
                  new_candidate_count=sum(row.status == CANDIDATE_NEW for row in rows),
                  tfiles_writable=config.TFILES_DIR.is_dir() and os.access(config.TFILES_DIR, os.W_OK),
                  library_available=data.get('library_available', False),
                  wanted=[row.to_dict() for row in wanted], candidates=[row.to_dict() for row in rows])
