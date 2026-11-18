"""Which processes host terminals or LLM sessions: the structural half of the signal policy.

A name list cannot cover every terminal emulator, multiplexer or IDE helper, so the policy
also asks what a process has beneath it. A process "hosts" a unit when that unit is among
its descendants: a terminal (a tty any descendant is attached to, other than the process's
own tty) or a session, infra or compute group root. Every group root counts on its own,
whatever tty it shares: a supervisor on the same terminal as the session it watches (a
timeout wrapper forwarding its signal) would end that session too.
"""
from process_table import ProcessTable
from resource_groups import Grouping


def hosted_units(table: ProcessTable, grouping: Grouping) -> dict[int, int]:
    """pid -> number of terminals and LLM group roots among its descendants (zero omitted)."""
    roots = {group.root.pid: gid for gid, group in grouping.groups.items()}
    units: dict[int, set] = {}
    for pid, row in table.rows.items():
        found = [('tty', row.tty)] if row.tty is not None else []
        if pid in roots:
            found.append(('group', roots[pid]))
        if not found:
            continue
        for ancestor in table.ancestors(pid):
            if ancestor in table.rows:
                units.setdefault(ancestor, set()).update(found)
    counts = {}
    for pid, found in units.items():
        own = table.rows[pid].tty
        count = len(found - {('tty', own)}) if own is not None else len(found)
        if count:
            counts[pid] = count
    return counts
