"""Weekly offline check for retiring the Codex capacity-retry wrapper."""
import argparse
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from .retirement_actions import disable_mac, disable_pi, notify
from .retirement_probe import fetch_pr_status, run_probe
from .retirement_state import (Notice, PRStatus, ProbeOutcome, ProbeResult, decide, record_probe)
from .retirement_store import RetirementStore


@dataclass(frozen=True)
class Dependencies:
    probe: object = run_probe
    pr_status: object = fetch_pr_status
    disable_mac: object = disable_mac
    disable_pi: object = disable_pi
    notify: object = notify
    now: object = lambda: datetime.now(timezone.utc).isoformat()


def retirement_message(version, pi_status):
    return ("Codex {} now retries capacity errors itself; wrapper disabled on Mac "
            "(Pi: {}). Manual cleanup: remove _codex_tui from the shell launchers."
            .format(version, pi_status))


def retire(state, dependencies, store, actions):
    if not state.retired:
        dependencies.disable_mac()
        state.retired = True
        store.save(state)
        actions.append("Mac disabled")
    if not state.pi_disabled:
        try:
            dependencies.disable_pi()
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            actions.append("Pi failed: {}".format(error))
        else:
            state.pi_disabled = True
            store.save(state)
            actions.append("Pi disabled")
    return "ok" if state.pi_disabled else "failed"


def send_notices(state, notices, version, pi_status, dependencies, store, actions):
    messages = {
        Notice.RETIRED: retirement_message(version, pi_status),
        Notice.CLOSED_UNMERGED: "Codex PR #31058 closed unmerged; weekly probe continues.",
        Notice.PROBE_ERRORS: "Codex capacity retirement probe failed 3 consecutive times; "
                            "check the weekly cron log. Wrapper unchanged.",
    }
    for notice in notices:
        # Claim before delivery: even a crash after sending must never send it twice.
        setattr(state, notice.value, True)
        store.save(state)
        try:
            dependencies.notify(messages[notice])
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            actions.append("notification failed (not retried): {}".format(error))
        else:
            actions.append("notified " + notice.value)


def check_once(store, dependencies, dry_run=False):
    state = store.load()
    checked_at = dependencies.now()
    already_retired = state.retired
    if already_retired:
        previous = (state.last_run or {}).get("probe", {})
        probe = ProbeResult(ProbeOutcome.SHIPPED, previous.get("version", "unknown"))
        pr = PRStatus()
    else:
        probe = dependencies.probe()
        pr = dependencies.pr_status()
    decision = decide(state, probe, pr)
    actions = []
    if dry_run:
        if decision.retire:
            if not state.retired:
                actions.append("would disable Mac")
            if not state.pi_disabled:
                actions.append("would disable Pi")
        actions.extend("would notify " + notice.value for notice in decision.notices)
    else:
        if not already_retired:
            # Preserve the successful probe evidence once retirement is complete.
            record_probe(state, probe, pr, checked_at)
            store.save(state)
        pi_status = retire(state, dependencies, store, actions) if decision.retire else "unchanged"
        send_notices(state, decision.notices, probe.version, pi_status, dependencies, store, actions)
    return {"checked_at": checked_at, "already_retired": already_retired,
            "probe": probe.outcome.value, "version": probe.version,
            "turn_requests": probe.turn_requests, "exit": probe.returncode,
            "detail": probe.detail, "pr": pr.__dict__, "dry_run": dry_run,
            "actions": actions}, int(probe.outcome == ProbeOutcome.ERROR)


def run(store=None, dependencies=None, dry_run=False, emit=print):
    store = store or RetirementStore(
        Path.home() / ".local/state/codex-autocontinue/retirement.json")
    dependencies = dependencies or Dependencies()
    try:
        # Dry runs must not even create the state directory or lock file.
        with nullcontext() if dry_run else store.locked():
            summary, status = check_once(store, dependencies, dry_run)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        summary, status = {"error": str(error), "dry_run": dry_run}, 1
    emit("codex-retirement: " + json.dumps(summary, sort_keys=True))
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="probe and describe actions without saving, disabling or notifying")
    args = parser.parse_args(argv)
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
