#!/usr/bin/python3
"""Keep the list of terms Jacob already knows, so agents stop defining them.

The global "Teaching as you go" rule ends some replies with a **Terms**
block. Agents read this list (~/.claude/KNOWN-TERMS.md) before writing one
and leave out every term on it. Jacob adds terms with `+known <term>, ...`.

`--hook` reads a Claude Code or Codex SessionStart or UserPromptSubmit event
on stdin. A prompt starting with `+known` records the terms after it (or
lists the file when there are none) and is blocked with a confirmation as
the reason, so no model turn runs. Otherwise the hook adds, as context, the
terms this session hasn't been shown: the whole list at session start
(including after a compaction), then only terms added since. Agents skipped
reading the file before writing Terms when told to, hence the injection.

The list lives on both machines. With a peer (the Mac's default is the Pi,
over `vanpi.lan` so it never crosses a metered uplink), every change merges
the two copies and writes the result to both; when the peer is unreachable
the change stays local and the next one carries it. The Pi has no route to
the Mac, so its own changes reach the Mac at the Mac's next change or `sync`.
Standard library only; runs on the MacBook (/usr/bin/python3 3.9) and the Pi.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Relative to the home directory on each machine.
LIST_PATH = ".claude/KNOWN-TERMS.md"
# Per-session record of the terms already put in that session's context.
SEEN_DIR = ".claude/known-terms-seen"
DEFAULT_PEER = "pi@vanpi.lan" if sys.platform == "darwin" else ""
TRIGGER = re.compile(r"^\+known(?:\s+(.*))?$", re.DOTALL)
SEPARATORS = re.compile(r"[,;\n]")
SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=3"]
# Imported into CLAUDE.md files would drop a leading `---`; keep the header plain.
HEADER = """# Known terms

Terms Jacob already knows. Agents leave these out of **Terms** blocks (see
TEACHING.md). Add with `+known <term>, <term>`; edit with known_terms.py.

"""


def parse(text: str) -> list[str]:
    return [line[2:].strip() for line in text.splitlines()
            if line.startswith("- ") and line[2:].strip()]


def render(terms: list[str]) -> str:
    return HEADER + "".join(f"- {term}\n" for term in terms)


def split_terms(raw: str) -> list[str]:
    return [term.strip().strip("`") for term in SEPARATORS.split(raw) if term.strip().strip("`")]


def merge(*lists: list[str], removed: list[str] = ()) -> list[str]:
    """Union of the lists, case-insensitive, first spelling kept, sorted."""
    drop = {term.casefold() for term in removed}
    seen: dict[str, str] = {}
    for terms in lists:
        for term in terms:
            key = term.casefold()
            if key not in drop and key not in seen:
                seen[key] = term
    return sorted(seen.values(), key=str.casefold)


def read_local(path: Path) -> list[str]:
    try:
        return parse(path.read_text())
    except FileNotFoundError:
        return []


def write_local(path: Path, terms: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as handle:
        handle.write(render(terms))
    os.replace(tmp, path)


def read_peer(peer: str) -> list[str] | None:
    """The peer's list, [] when it has none yet, None when unreachable."""
    command = f"cat {LIST_PATH} 2>/dev/null || true"
    try:
        result = subprocess.run(["ssh", *SSH_OPTIONS, peer, command],
                                capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse(result.stdout) if result.returncode == 0 else None


def write_peer(peer: str, terms: list[str]) -> bool:
    command = f"mkdir -p {Path(LIST_PATH).parent} && cat > {LIST_PATH}.tmp && mv {LIST_PATH}.tmp {LIST_PATH}"
    try:
        result = subprocess.run(["ssh", *SSH_OPTIONS, peer, command], input=render(terms),
                                capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def change(path: Path, peer: str, added: list[str] = (), removed: list[str] = ()) -> tuple[list[str], str]:
    """Apply the change locally and, when reachable, on the peer; return (terms, sync note)."""
    local = read_local(path)
    remote = read_peer(peer) if peer else None
    terms = merge(local, remote or [], list(added), removed=list(removed))
    write_local(path, terms)
    if not peer:
        return terms, ""
    if remote is not None and write_peer(peer, terms):
        return terms, f"synced with {peer}"
    return terms, f"{peer} unreachable; kept local, the next change syncs it"


def count(terms: list[str]) -> str:
    return f"{len(terms)} known term" + ("" if len(terms) == 1 else "s")


def listing(terms: list[str]) -> str:
    if not terms:
        return "No known terms yet. Add some with +known <term>, <term>."
    return f"Known terms ({len(terms)}): " + ", ".join(terms)


def hook(path: Path, peer: str, seen_dir: Path) -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    session = re.sub(r"[^A-Za-z0-9_.-]", "", str(event.get("session_id") or ""))
    if event.get("hook_event_name") == "SessionStart":
        # A fresh, resumed, cleared or compacted context has seen none of the list.
        return inject(path, seen_dir, session, "SessionStart", reset=True)
    match = TRIGGER.match(str(event.get("prompt", "")).strip())
    if not match:
        return inject(path, seen_dir, session, "UserPromptSubmit")
    record(path, peer, split_terms(match.group(1) or ""))
    return 0


def inject(path: Path, seen_dir: Path, session: str, event: str, reset: bool = False) -> int:
    """Add the terms this session's context hasn't seen yet as hook context."""
    terms = read_local(path)
    state = seen_dir / f"{session}.json"
    seen: set[str] = set()
    if session and not reset:
        try:
            seen = set(json.loads(state.read_text()))
        except (OSError, ValueError):
            pass
    unseen = [term for term in terms if term.casefold() not in seen]
    if not unseen:
        return 0
    if session:
        seen_dir.mkdir(parents=True, exist_ok=True)
        prune(seen_dir)
        state.write_text(json.dumps(sorted(seen | {term.casefold() for term in terms})))
    lead = "Jacob also marked these terms as known" if seen else "Terms Jacob already knows"
    context = f"{lead} ({LIST_PATH}); leave them out of any **Terms** block: {', '.join(unseen)}."
    json.dump({"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}, sys.stdout)
    return 0


def prune(seen_dir: Path, days: int = 30) -> None:
    cutoff = time.time() - days * 86400
    for old in seen_dir.glob("*.json"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass


def record(path: Path, peer: str, new: list[str]) -> None:
    """Answer a +known prompt: add its terms, or list them, and block the prompt."""
    if new:
        before = {term.casefold() for term in read_local(path)}
        terms, note = change(path, peer, added=new)
        fresh = [term for term in new if term.casefold() not in before]
        reason = f"Marked known: {', '.join(new)}"
        if len(fresh) < len(new):
            reason += f" ({len(new) - len(fresh)} already listed)"
        reason += f". {count(terms)}" + (f"; {note}." if note else ".")
    else:
        reason = listing(read_local(path))
    json.dump({"decision": "block", "reason": reason}, sys.stdout)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--file", type=Path, default=Path.home() / LIST_PATH,
                    help="the list (default %(default)s)")
    ap.add_argument("--peer", default=DEFAULT_PEER,
                    help="ssh host holding the other copy, '' for none (default %(default)r)")
    ap.add_argument("--seen-dir", type=Path, default=Path.home() / SEEN_DIR, help=argparse.SUPPRESS)
    ap.add_argument("--hook", action="store_true",
                    help="act as a UserPromptSubmit hook: handle +known, pass anything else")
    sub = ap.add_subparsers(dest="action")
    sub.add_parser("list", help="print the known terms (default)")
    for name in ("add", "remove"):
        sub.add_parser(name, help=f"{name} terms; commas separate several").add_argument("terms", nargs="+")
    sub.add_parser("sync", help="merge with the peer's copy")
    args = ap.parse_args(argv)
    path = args.file.expanduser()
    if args.hook:
        return hook(path, args.peer, args.seen_dir.expanduser())
    if args.action in ("add", "remove"):
        terms = split_terms(",".join(args.terms))
        key = "added" if args.action == "add" else "removed"
        result, note = change(path, args.peer, **{key: terms})
        print(f"{args.action}: {', '.join(terms)}. {count(result)}" + (f"; {note}." if note else "."))
        return 0
    if args.action == "sync":
        if not args.peer:
            print("no peer to sync with", file=sys.stderr)
            return 1
        result, note = change(path, args.peer)
        print(f"{count(result)}; {note}.")
        return 0 if note.startswith("synced") else 1
    print(listing(read_local(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
