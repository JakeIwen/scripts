"""Read-only dashboard status, with failures distinct from empty libraries."""
from . import config, records, plans, runs, settings, busy
from .mirror_sync import pending_items
from .relocation import mirror_available
from .scan import scan
from .qbittorrent_client import Client


def status(client=None):
    client = client or Client()
    mounted = config.MEDIA_ROOT.is_mount()
    available, entries, error = False, [], None
    try:
        available = client.available()
        if mounted:
            entries, _ = scan(client)
        else:
            error = 'media filesystem is not mounted'
    except (OSError, ValueError, RuntimeError) as exc:
        error = str(exc)
    history = plans.list_plans()
    recent = runs.list_runs()[:10]
    return records.Status(
        mounted, mirror_available(), available,
        len(entries), sum(e.tracked for e in entries), sum(not e.tracked for e in entries),
        sum(e.skip_reason == records.SKIP_INCOMPLETE for e in entries),
        history[0].summary() if history else None, [run.summary() for run in recent],
        settings.load().to_dict(), busy.current(), error,
        mirror_pending=len(pending_items()),
    )
