"""Serialized, journaled relocation of selected plan rows."""
from pathlib import Path
from . import records, plans, runs, link_rebuild
from .busy import operation
from .locking import media_lock
from .paths import require_media
from .qbittorrent_client import Client
from .relocation import verify_source, ensure_absent, make_item, move_payload, mirror_move, PENDING
from .storage import new_id, now


def apply_row(row, run, settings, client):
    target = plans.target_path_for(row.decision, row.entry)
    item = make_item(row, target)
    run.items.append(item)
    runs.save(run)
    try:
        verify_source(row, client)
        ensure_absent(Path(target))
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError, RuntimeError) as error:
        item.error = str(error)
        runs.save(run)
        return
    # Write-ahead intent also makes interrupted or unconfirmed moves inspectable.
    run.undoable = True
    runs.save(run)
    try:
        move_payload(item, client)
    except (OSError, ValueError, RuntimeError) as error:
        item.error = f'{PENDING}: {error}'
        runs.save(run)
        return
    item.status, item.error = records.ITEM_MOVED, None
    runs.save(run)
    if settings.mirror_rename:
        try:
            mirror_move(item, run, Path(row.entry.path), Path(target))
        except (OSError, ValueError, RuntimeError) as error:
            item.error = f'Media moved; mirror unchanged or unconfirmed: {error}'
    runs.save(run)


def apply_plan(plan, mode, settings, client=None):
    if mode not in records.RUN_MODES:
        raise ValueError('unknown apply mode')
    with operation(records.JOB_APPLY):
        with media_lock():
            require_media()
            plan = plans.load(plan.id)
            rows = [row for row in plans.open_rows(plan) if row.decision and row.decision.accepted
                    and not row.entry.skip_reason
                    and (mode != records.RUN_AUTO or row.auto_eligible)]
            if not rows:
                raise ValueError('no open accepted rows to apply')
            run = records.Run(new_id(), plan.id, mode, now(), None)
            runs.save(run)
            # Consume the plan before the first side effect; a crash cannot replay it.
            plan.status = records.PLAN_PARTIAL
            plan.run_ids.append(run.id)
            plans.save(plan)
            for row in rows:
                apply_row(row, run, settings, client or Client())
            moved = sum(item.status == records.ITEM_MOVED for item in run.items)
            pending = any(item.error and item.error.startswith(PENDING) for item in run.items)
            run.undoable = bool(moved or pending)
            run.finished_at = now()
            plan.status = plans.status_after_run(plan)
            plans.save(plan)
            runs.save(run)
        run.alias_status = (link_rebuild.schedule() if settings.run_alias_after_apply
                            else records.ALIAS_SKIPPED)
        runs.save(run)
        return run
