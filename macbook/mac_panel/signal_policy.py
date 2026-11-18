"""The single server-side decision of which processes the panel may signal.

The same function fills the payload's `blocked` fields and is re-run inside every mutation,
on a fresh table, immediately before each signal. Nothing the page sends is authorization:
its copy of these reasons only explains disabled buttons.
"""
from dataclasses import dataclass
from enum import Enum
import os

from process_hosting import hosted_units
from process_invocation import SHELLS
from process_table import KERNEL_NAME_LIMIT, ProcessRow, ProcessTable
from resource_groups import SESSION_KINDS, Grouping, Kind


class Scope(str, Enum):
    PROCESS = 'process'
    TREE = 'tree'


SYSTEM_PROTECTED = frozenset(('launchd', 'loginwindow', 'WindowServer', 'Dock', 'Finder',
                              'SystemUIServer', 'ControlCenter', 'NotificationCenter',
                              'coreaudiod', 'cfprefsd', 'runningboardd', 'tccd'))
# Ending one of these ends every shell and session inside it. The structural rule
# (`process_hosting`) also covers hosts this list does not name.
TERMINAL_HOSTS = frozenset(('iTerm2', 'Terminal', 'tmux', 'screen', 'SCREEN', 'zellij',
                            'Ghostty', 'ghostty', 'kitty', 'Alacritty', 'alacritty', 'WezTerm',
                            'wezterm-gui', 'Warp', 'Hyper', 'mosh-server', 'sshd',
                            'sshd-session', 'login'))
PROTECTED = SYSTEM_PROTECTED | TERMINAL_HOSTS
# The kernel's name is truncated, so it is compared with truncated entries.
PROTECTED_KERNEL_NAMES = frozenset(name[:KERNEL_NAME_LIMIT] for name in PROTECTED)

OTHER_USER = "Another user's process; the panel runs without sudo"
SYSTEM_PID = 'The kernel or launchd'
PANEL = 'The panel itself'
PANEL_ANCESTOR = 'Runs the panel; stopping it would stop the panel'
PANEL_DESCENDANT = 'Started by the panel'
PANEL_UNKNOWN = "The panel's own parent processes could not be read; refresh"
PROTECTED_REASON = 'A system or terminal process the panel never signals'
INCOMPLETE = 'Its parent processes could not be read; refresh'
SHELL = 'A shell may be hosting a live terminal session; end its session instead'
TREE_ONLY_SESSIONS = 'Whole-tree actions exist only for Claude and Codex sessions'
KIND_REASONS = {Kind.INFRA: 'Shared by every session; restart it from Services',
                Kind.COMPUTE: 'Owned by van-compute; killing a job here would skip its cleanup'}


def hosts_reason(count) -> str:
    noun = 'session' if count == 1 else 'sessions'
    return f'Hosts {count} terminal or LLM {noun}; end those first'


@dataclass(frozen=True)
class PolicyContext:
    """Everything the policy reads from one table: the panel's lineage and hosted units."""
    table: ProcessTable
    panel_pid: int
    panel_ancestors: frozenset
    panel_descendants: frozenset
    # False when the panel's row is present but its ancestry is broken: its ancestors are
    # then unknown, so nothing may be signalled from this table.
    panel_known: bool
    hosted: dict


def is_protected(row: ProcessRow) -> bool:
    return row.exe in PROTECTED or row.kernel_name in PROTECTED_KERNEL_NAMES


class SignalPolicy:
    def __init__(self, uid=None, panel_pid=None):
        self.uid = os.getuid() if uid is None else uid
        self.panel_pid = os.getpid() if panel_pid is None else panel_pid

    def context(self, table: ProcessTable, grouping: Grouping) -> PolicyContext:
        pid = self.panel_pid
        known = pid not in table.rows or table.complete_chain(pid) is not None
        return PolicyContext(table, pid, frozenset(table.ancestors(pid)),
                             frozenset(row.pid for row, _ in table.descendants(pid)), known,
                             hosted_units(table, grouping))

    def blocked(self, row: ProcessRow, kind: Kind, scope: Scope, context: PolicyContext) -> str | None:
        """None when allowed; otherwise the reason the page shows."""
        if row.uid != self.uid:
            return OTHER_USER
        if row.pid <= 1:
            return SYSTEM_PID
        return self.lineage_reason(row, context) or self.role_reason(row, kind, scope, context)

    def lineage_reason(self, row, context) -> str | None:
        if not context.panel_known:
            return PANEL_UNKNOWN
        if row.pid == context.panel_pid:
            return PANEL
        if row.pid in context.panel_ancestors:
            return PANEL_ANCESTOR
        if row.pid in context.panel_descendants:
            return PANEL_DESCENDANT
        if is_protected(row):
            return PROTECTED_REASON
        # Kind and membership come from the ancestry, so a gap in it decides nothing.
        if context.table.complete_chain(row.pid) is None:
            return INCOMPLETE
        return None

    def role_reason(self, row, kind, scope, context) -> str | None:
        if scope is Scope.TREE:
            # A session's own command shells are members of its tree and end with it.
            return None if kind in SESSION_KINDS else TREE_ONLY_SESSIONS
        # The owner's reason says more than "a shell" for a job's or gateway's own shell.
        if kind in KIND_REASONS:
            return KIND_REASONS[kind]
        if row.exe in SHELLS:
            return SHELL
        # A helper inside a session is that session's own; only outside processes host.
        if kind not in SESSION_KINDS and context.hosted.get(row.pid):
            return hosts_reason(context.hosted[row.pid])
        return None


def nested_reason(names) -> str:
    return f"Contains nested sessions ({', '.join(names)}); end those first"


def tree_blocked(policy: SignalPolicy, group, context: PolicyContext, names=None) -> str | None:
    """Why a group has no whole-tree action, or None for a session tree that may be ended.

    A session with another session nested inside it is refused as a whole, so the count a
    confirmation shows is always the set of processes that would be signalled.
    """
    reason = policy.blocked(group.root, group.kind, Scope.TREE, context)
    if reason or not group.nested:
        return reason
    names = names or {}
    return nested_reason([names.get(gid, gid) for gid in group.nested])
