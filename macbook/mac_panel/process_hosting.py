"""Which processes host terminals or LLM sessions: the structural half of the signal policy.

A name list cannot cover every terminal emulator, multiplexer or IDE helper, so the policy
also asks what a process has beneath it. A process "hosts" a unit when that unit is among
its descendants: a terminal (a tty any descendant is attached to, other than the process's
own tty) or a session, infra or compute group root that has no tty of its own (a root on a
tty is already counted through that tty). Ending the host could end every unit below it.
"""
from process_table import ProcessTable
from resource_groups import Grouping


def hosted_units(table: ProcessTable, grouping: Grouping) -> dict[int, int]:
    """pid -> number of terminals and LLM group roots among its descendants (zero omitted)."""
    roots = {group.root.pid: gid for gid, group in grouping.groups.items()}
    units: dict[int, set] = {}
    for pid, row in table.rows.items():
        if row.tty is not None:
            unit = ('tty', row.tty)
        elif pid in roots:
            unit = ('group', roots[pid])
        else:
            continue
        for ancestor in table.ancestors(pid):
            if ancestor in table.rows:
                units.setdefault(ancestor, set()).add(unit)
    counts = {}
    for pid, found in units.items():
        own = table.rows[pid].tty
        count = len(found - {('tty', own)}) if own is not None else len(found)
        if count:
            counts[pid] = count
    return counts
