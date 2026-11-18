"""The single server-side decision of which processes the panel may signal.

The same function fills the payload's `blocked` fields and is re-run inside every mutation,
on the fresh row, immediately before each signal. Nothing the page sends is authorization:
its copy of these reasons only explains disabled buttons.
"""
from dataclasses import dataclass
from enum import Enum
import os

from process_table import SHELLS, ProcessRow, ProcessTable
from resource_groups import SESSION_KINDS, Kind


class Scope(str, Enum):
    PROCESS = 'process'
    TREE = 'tree'


SYSTEM_PROTECTED = frozenset(('launchd', 'loginwindow', 'WindowServer', 'Dock', 'Finder',
                              'SystemUIServer', 'ControlCenter', 'NotificationCenter',
                              'coreaudiod', 'cfprefsd', 'runningboardd', 'tccd'))
# Ending one of these ends every shell and session inside it.
TERMINAL_HOSTS = frozenset(('iTerm2', 'Terminal', 'tmux', 'login', 'sshd'))
PROTECTED = SYSTEM_PROTECTED | TERMINAL_HOSTS

OTHER_USER = "Another user's process; the panel runs without sudo"
SYSTEM_PID = 'The kernel or launchd'
PANEL = 'The panel itself'
PANEL_ANCESTOR = 'Runs the panel; stopping it would stop the panel'
PANEL_DESCENDANT = 'Started by the panel'
PROTECTED_REASON = 'A system or terminal process the panel never signals'
SHELL = 'A shell may be hosting a live terminal session; end its session instead'
TREE_ONLY_SESSIONS = 'Whole-tree actions exist only for Claude and Codex sessions'
KIND_REASONS = {Kind.INFRA: 'Shared by every session; restart it from Services',
                Kind.COMPUTE: 'Owned by van-compute; killing a job here would skip its cleanup'}


@dataclass(frozen=True)
class PanelLineage:
    """The panel's own pid with its ancestors and descendants in one table."""
    pid: int
    ancestors: frozenset
    descendants: frozenset


class SignalPolicy:
    def __init__(self, uid=None, panel_pid=None):
        self.uid = os.getuid() if uid is None else uid
        self.panel_pid = os.getpid() if panel_pid is None else panel_pid

    def lineage(self, table: ProcessTable) -> PanelLineage:
        return PanelLineage(self.panel_pid, frozenset(table.ancestors(self.panel_pid)),
                            frozenset(row.pid for row, _ in table.descendants(self.panel_pid)))

    def blocked(self, row: ProcessRow, kind: Kind, scope: Scope, lineage: PanelLineage) -> str | None:
        """None when allowed; otherwise the reason the page shows."""
        if row.uid != self.uid:
            return OTHER_USER
        if row.pid <= 1:
            return SYSTEM_PID
        if row.pid == lineage.pid:
            return PANEL
        if row.pid in lineage.ancestors:
            return PANEL_ANCESTOR
        if row.pid in lineage.descendants:
            return PANEL_DESCENDANT
        if row.exe in PROTECTED:
            return PROTECTED_REASON
        if scope is Scope.TREE:
            # A session's own command shells are members of its tree and end with it.
            return None if kind in SESSION_KINDS else TREE_ONLY_SESSIONS
        # The owner's reason says more than "a shell" for a job's or gateway's own shell.
        if kind in KIND_REASONS:
            return KIND_REASONS[kind]
        return SHELL if row.exe in SHELLS else None


def nested_reason(names) -> str:
    return f"Contains nested sessions ({', '.join(names)}); end those first"


def tree_blocked(policy: SignalPolicy, group, lineage: PanelLineage, names=None) -> str | None:
    """Why a group has no whole-tree action, or None for a session tree that may be ended.

    A session with another session nested inside it is refused as a whole, so the count a
    confirmation shows is always the set of processes that would be signalled.
    """
    reason = policy.blocked(group.root, group.kind, Scope.TREE, lineage)
    if reason or not group.nested:
        return reason
    names = names or {}
    return nested_reason([names.get(gid, gid) for gid in group.nested])
