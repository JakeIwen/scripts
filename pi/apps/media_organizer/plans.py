"""Persisted proposals and validated reviewer decisions."""
from copy import deepcopy
from . import config, records
from . import classify, runs
from .busy import operation
from .paths import component, destination, new_directory, confined
from .scan import scan
from .relocation import PENDING
from .storage import new_id, now, read_json, record_path, write_json


def save(plan):
    write_json(record_path(config.PLANS_DIR, plan.id), plan.to_dict())


def load(plan_id):
    return records.Plan.from_dict(read_json(record_path(config.PLANS_DIR, plan_id)))


def list_plans():
    return sorted((records.Plan.from_dict(read_json(path)) for path in config.PLANS_DIR.glob('*.json')),
                  key=lambda plan: (plan.created_at, plan.id), reverse=True)


def target_path_for(decision, entry):
    component(entry.name)
    if entry.path != str(new_directory() / entry.name):
        raise ValueError('entry is not a top-level New payload')
    confined(new_directory() / entry.name, config.MEDIA_ROOT)
    if decision.category not in config.CATEGORIES or type(decision.accepted) is not bool:
        raise ValueError('invalid decision')
    target = config.MEDIA_ROOT / config.TORRENT_DIR / decision.category
    if decision.category != config.CATEGORY_MOVIES:
        target /= component(decision.show)
    elif decision.show is not None:
        raise ValueError('Movies cannot have a show folder')
    destination(target)
    return str(confined(target / entry.name, config.MEDIA_ROOT))


def auto_eligible(proposal, settings):
    return bool(proposal and proposal.confidence >= settings.auto_apply_min_confidence
                and (proposal.category == config.CATEGORY_MOVIES or proposal.existing_show
                     or settings.auto_apply_new_shows))


def create_plan(settings, entries=None, existing_shows=None, client=None):
    with operation(records.JOB_PROPOSE):
        if entries is None:
            entries, existing_shows = scan(client)
        proposals, classifier, error = classify.propose(entries, existing_shows, settings)
        plan = records.Plan(new_id(), now(), records.PLAN_PROPOSED, classifier, existing_shows,
                            classifier_error=error)
        for entry in entries:
            proposal = proposals.get(entry.name) if not entry.skip_reason else None
            decision = records.Decision(proposal.category, proposal.show, True) if proposal else None
            target = target_path_for(decision, entry) if decision else None
            plan.rows.append(records.PlanRow(entry, proposal, decision, target,
                                             auto_eligible(proposal, settings)))
        for previous in list_plans():
            if previous.status == records.PLAN_PROPOSED:
                previous.status = records.PLAN_DISCARDED
                save(previous)
        save(plan)
        return plan


def moved_names(plan):
    """Rows a run of this plan moved and confirmed."""
    return {item.name for run_id in plan.run_ids for item in runs.load(run_id).items
            if item.status == records.ITEM_MOVED}


def settled_names(plan):
    """Moved rows plus unconfirmed ones: neither is editable or applicable again."""
    return moved_names(plan) | {
        item.name for run_id in plan.run_ids for item in runs.load(run_id).items
        if (item.error or '').startswith(PENDING)}


def status_after_run(plan):
    """Applied once every candidate row is confirmed moved; otherwise partial."""
    moved = moved_names(plan)
    done = all(row.entry.name in moved for row in plan.rows if not row.entry.skip_reason)
    return records.PLAN_APPLIED if done else records.PLAN_PARTIAL


def open_rows(plan):
    """Rows still eligible for review or apply in a proposed or partial plan."""
    if plan.status not in (records.PLAN_PROPOSED, records.PLAN_PARTIAL):
        raise ValueError('only a proposed or partially applied plan can be edited or applied')
    settled = settled_names(plan)
    return [row for row in plan.rows if row.entry.name not in settled]


def decide(plan, decisions):
    with operation():
        plan = load(plan.id)
        # The hook auto-applies confident rows and leaves the rest here for
        # review; a partial plan therefore stays editable for its open rows.
        rows = {row.entry.name: row for row in open_rows(plan)}
        if not isinstance(decisions, dict):
            raise ValueError('decisions must be an object keyed by entry name')
        result = deepcopy(plan)
        edited = {row.entry.name: row for row in result.rows}
        for name, payload in decisions.items():
            if name not in rows or not isinstance(payload, dict):
                raise ValueError('unknown or already applied decision entry')
            row = edited[name]
            if row.entry.skip_reason or set(payload) != {'category', 'show', 'accepted'}:
                raise ValueError('invalid decision fields or skipped entry')
            decision = records.Decision.from_dict(payload)
            row.target_path = target_path_for(decision, row.entry)
            row.decision = decision
            # A human edit is never silently authorized by the original confidence.
            row.auto_eligible = False
        save(result)
        return result
