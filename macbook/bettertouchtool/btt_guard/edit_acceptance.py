"""Accept drift made inside a running BetterTouchTool; alert on drift from anywhere else.

The owner chose this rule: a change that appears while the same BTT process keeps
running (his own edit, an agent's or an installer's) becomes the known-good
checkpoint once it has been unchanged for SETTLE_SECONDS. Drift that first appears
after BTT relaunched or changed version (an update, a crash, a launch-time sync)
is never accepted here; it goes to the debounced ntfy alert. Capture keeps its
own refusals (empty active buttons, unresolved dependencies, disabled roots), and
a refused or failed capture is alerted rather than retried.

Only the scheduled monitor (`monitor --managed`) runs this, under guard.lock.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Optional, Tuple

from .checkpoint import capture
from .model import (AcceptanceStateKind, AcceptanceStatus, AuditReport, AuditStatus, BttProcess,
                    EditAcceptanceAction as Action, EditAcceptanceReason as Reason,
                    EditAcceptanceState, ExportUnavailableError)
from .monitor import report_key
from .storage import load_json, write_json

STATE_NAME = 'edit-acceptance.json'
STATE_VERSION = 1
# Long enough to finish an editing session in BTT's settings without a checkpoint per edit.
SETTLE_SECONDS = 600
BTT_EXECUTABLE = '.app/Contents/MacOS/BetterTouchTool'


def initial_state() -> EditAcceptanceState:
    return dict(version=STATE_VERSION, baseline=None, candidate=None, refused_key=None,
                last_accepted=None, last_refused=None)


def running_btt(run=subprocess.run) -> Optional[BttProcess]:
    """The main BTT process as pid plus start time; None when absent or ambiguous."""
    result = run(['/bin/ps', '-axo', 'pid=,lstart=,comm='], capture_output=True, text=True, check=False)
    if result.returncode:
        return None
    found = []
    for line in result.stdout.splitlines():
        # lstart is five fields ("Sat Oct 17 09:23:35 2026"); comm is the executable path.
        parts = line.split(None, 6)
        if len(parts) == 7 and parts[0].isdecimal() and parts[6].endswith(BTT_EXECUTABLE):
            found.append(BttProcess(pid=int(parts[0]), started=' '.join(parts[1:6])))
    return found[0] if len(found) == 1 else None


def _valid(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != set(initial_state()):
        return False
    if value['version'] != STATE_VERSION:
        return False
    baseline, candidate = value['baseline'], value['candidate']
    if baseline is not None and not (isinstance(baseline, dict) and isinstance(baseline.get('process'), dict)
                                     and isinstance(baseline.get('checkpoint_id'), str)):
        return False
    if candidate is not None and not (isinstance(candidate, dict) and isinstance(candidate.get('key'), str)
                                      and isinstance(candidate.get('since'), (int, float))):
        return False
    return value['refused_key'] is None or isinstance(value['refused_key'], str)


def _read(state_dir) -> Tuple[AcceptanceStateKind, Optional[EditAcceptanceState]]:
    try:
        value = load_json(Path(state_dir)/STATE_NAME)
    except FileNotFoundError:
        return AcceptanceStateKind.NONE, None
    except (OSError, ValueError):
        return AcceptanceStateKind.UNREADABLE, None
    return (AcceptanceStateKind.OK, value) if _valid(value) else (AcceptanceStateKind.UNREADABLE, None)


def load_state(state_dir) -> EditAcceptanceState:
    # Unreadable state forgets the baseline, which only makes drift ineligible (alerted).
    return _read(state_dir)[1] or initial_state()


def _same_session(state: EditAcceptanceState, report: AuditReport, process: Optional[BttProcess]) -> bool:
    baseline = state['baseline']
    return bool(baseline and process and baseline['process'] == process
                and baseline['checkpoint_id'] == report['checkpoint_id']
                and baseline['btt_version'] == report['btt_version'])


def decide(state: EditAcceptanceState, report: AuditReport, process: Optional[BttProcess],
           now: float) -> Action:
    """Advance `state` for one scheduled audit and say what to do with its drift."""
    if report['status'] == AuditStatus.HEALTHY:
        state['candidate'] = state['refused_key'] = None
        state['baseline'] = (dict(checkpoint_id=report['checkpoint_id'], btt_version=report['btt_version'],
                                  process=process) if process and report['checkpoint_id'] else None)
        return Action.NOT_NEEDED
    if report['status'] != AuditStatus.DRIFT or not _same_session(state, report, process):
        state['candidate'] = None
        return Action.INELIGIBLE
    key = report_key(report, AuditStatus.DRIFT, report['findings'])
    if state['refused_key'] == key:
        return Action.REFUSED
    candidate = state['candidate']
    if candidate is None or candidate['key'] != key or now < candidate['since']:
        state['candidate'] = dict(key=key, since=now)
        return Action.SETTLING
    return Action.ACCEPTED if now - candidate['since'] >= SETTLE_SECONDS else Action.SETTLING


def _accept(state_dir, state: EditAcceptanceState, report: AuditReport, process: BttProcess,
            now: float) -> Action:
    findings = len(report['findings'])
    try:
        manifest = capture(state_dir, accept_current=True, replace=True)
    except ExportUnavailableError:
        reason = Reason.EXPORT_UNAVAILABLE
    except ValueError:
        reason = Reason.CAPTURE_REFUSED
    except Exception:
        reason = Reason.CAPTURE_FAILED
    else:
        # Capture verified the current state, so the same session continues from the new checkpoint.
        state['baseline'] = dict(checkpoint_id=manifest['checkpoint_id'], btt_version=report['btt_version'],
                                 process=process)
        state['candidate'] = None
        state['last_accepted'] = dict(at=now, checkpoint_id=manifest['checkpoint_id'], reason=None,
                                      findings=findings)
        return Action.ACCEPTED
    state['refused_key'] = state['candidate']['key']
    state['candidate'] = None
    state['last_refused'] = dict(at=now, checkpoint_id=None, reason=reason.value, findings=findings)
    return Action.REFUSED


def observe(state_dir, report: AuditReport, now: float) -> Action:
    """One scheduled pass under guard.lock; after ACCEPTED the caller re-audits."""
    state = load_state(state_dir)
    process = running_btt()
    action = decide(state, report, process, now)
    if action == Action.ACCEPTED:
        action = _accept(state_dir, state, report, process, now)
    write_json(Path(state_dir)/STATE_NAME, state)
    return action


def status(state_dir) -> AcceptanceStatus:
    """Read-only summary; never creates or locks Guard state."""
    kind, value = _read(state_dir)
    candidate = value['candidate'] if value else None
    return AcceptanceStatus(state=kind.value, settle_seconds=SETTLE_SECONDS,
                            pending_since=candidate['since'] if candidate else None,
                            accepts_at=candidate['since'] + SETTLE_SECONDS if candidate else None,
                            last_accepted=value['last_accepted'] if value else None,
                            last_refused=value['last_refused'] if value else None)
