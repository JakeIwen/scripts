"""Completion hook: failures become JSON evidence, never torrent-hook exceptions."""
from dataclasses import replace
from . import records, plans, link_rebuild, runs
from .activity_log import log
from .apply import apply_plan
from .busy import operation
from .locking import media_lock
from .scan import scan


def post_download(settings):
    result = {'plan': None, 'run': None, 'alias': None, 'error': None}
    try:
        with operation(records.JOB_PROPOSE):
            # The snapshot/classifier and apply each take the shared lock. Apply
            # revalidates sources after the potentially long classifier call.
            with media_lock():
                entries, folders = scan()
                log(f'scan entries={len(entries)}')
            plan = plans.create_plan(settings, entries, folders)
            result['plan'] = plan.summary()
            log(f'propose plan={plan.id}')
            if settings.auto_apply and any(row.auto_eligible for row in plan.rows):
                run = apply_plan(plan, records.RUN_AUTO, replace(settings, run_alias_after_apply=False),
                                 wait_for_backup=True)
                result['run'] = run.summary()
                result['plan'] = plans.load(plan.id).summary()
                log(f'apply run={run.id}')
            result['alias'] = link_rebuild.schedule()
            if result['run']:
                run.alias_status = result['alias']
                runs.save(run)
                result['run'] = run.summary()
            log(f'alias status={result["alias"]}')
            if result['alias'] == records.ALIAS_FAILED:
                result['error'] = 'link rebuild could not be scheduled'
    except Exception as error:
        result['error'] = str(error)
        try:
            log('post-download failed')
        except OSError:
            pass
    return result
