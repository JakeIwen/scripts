#!/usr/bin/python3
"""Keep the list of terms Jacob already knows, so agents stop defining them.

The global "Teaching as you go" rule ends some replies with a **Terms**
block. Agents read this list (~/.claude/KNOWN-TERMS.md) before writing one
and leave out every term on it. Jacob adds terms with `+known <term>, ...`.

`--hook` reads a Claude Code or Codex SessionStart or UserPromptSubmit event
on stdin. It answers a `+known` prompt itself (see `read_command`) only when
the prompt is one line and every listed term is recognised (a term name in
the local glossary store or already on this list) or written in double
quotes: it records them, or lists the file for a bare `+known`, and blocks
the prompt with a confirmation naming them and how to undo one, so no model
turn runs. Any other prompt reaches the model untouched with nothing stored;
a one-line `+known` list held back only by unrecognised terms also gets
context saying which, and how to record them if they are terms. The hook
then adds, as context, the terms this session hasn't been shown: the whole
list at session start (including after a compaction), then only terms added
since. Agents skipped reading the file before writing Terms when told to,
hence the injection.

The list lives on both machines. With a peer (the Mac's default is the Pi,
over `vanpi.lan` so it never crosses a metered uplink), every change merges
the two copies and writes the result to both; when the peer is unreachable
the change stays local and the next one carries it. The Pi has no route to
the Mac, so its own changes reach the Mac at the Mac's next change or `sync`.
Standard library only; runs on the MacBook (/usr/bin/python3 3.9) and the Pi.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
import sys
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

import peer_copy

# Relative to the home directory on each machine.
LIST_PATH = ".claude/KNOWN-TERMS.md"
# Per-session record of the terms already put in that session's context.
SEEN_DIR = ".claude/known-terms-seen"
DEFAULT_PEER = "pi@vanpi.lan" if sys.platform == "darwin" else ""
# No DOTALL: the command is one line. A prompt that only started with +known
# (a term, then a question on the next line) used to be swallowed whole and
# its text stored, synced and injected into every later session.
TRIGGER = re.compile(r"^\+known(?:[ \t]+(.*))?$")
SEPARATORS = re.compile(r"[,;\n]")
# Bounds on what the hook records, since every stored term is injected into
# every session's context. The longest of ~430 glossary terms is 40 characters
# and 5 words; these leave room without admitting a sentence.
MAX_TERM_CHARS = 60
MAX_TERM_WORDS = 6
MAX_TERMS = 20
# Longer than any valid command (20 quoted 60-character terms with their
# separators come to about 1,300), checked before any pattern runs, so no
# prompt can make the parse slow.
MAX_COMMAND_CHARS = 2000
# Another + command token anywhere in the list, quoted or not, after a space,
# a list separator or a quote (`+known x +lex y`, `+known x,+lex y`): the
# message is more than a term list.
OTHER_COMMAND = re.compile(r"(?:^|[\s,;\"])\+[A-Za-z]")
# The glossary store (glossary.py's STORE_PATH, home-relative): term names
# agents have defined, which the glossary page's +known copy button sends.
# Read here rather than through glossary.py, which imports this module.
GLOSSARY_PATH = ".agent/glossary/terms.jsonl"
# Read limits for the store (about 300 KB and 800-byte records today). Past
# the store limit recognition gives up, so every unquoted item goes to the
# model; an oversized record is skipped like a corrupt one.
MAX_GLOSSARY_BYTES = 8 * 1024 * 1024
MAX_GLOSSARY_RECORD_BYTES = 16 * 1024
# Control, format, private-use, unassigned and line/paragraph separator
# characters; str.splitlines() would split a stored term on several of them.
FORBIDDEN_CATEGORIES = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}
# Prompts another UserPromptSubmit hook answers itself by blocking them, each
# matched the way that hook matches it (the whole stripped prompt). A blocked
# prompt's hook context never reaches the model, so injecting there would mark
# terms seen that the session never got. Everything else, slash commands with
# a model turn included, gets the injection.
ANSWERED_ELSEWHERE = re.compile(r"^(?:\+cmd|\+links|\+terms(?:\s.*)?|/rm|/rm-cancel|/restart)$", re.DOTALL)
# The hook's own time limit, under the 20 s UserPromptSubmit timeout in the
# Claude and Codex hook configs; a killed hook loses its confirmation.
HOOK_BUDGET_S = 17.0
# Enough left for peer_copy's two ssh calls (8 s each, at worst). With less,
# a change waits on nobody: it stays local and the next change syncs it.
PEER_BUDGET_S = 16.5
LOCK_POLL_S = 0.05
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


def valid_term(term: str) -> bool:
    """A short, single-line term with no control or invisible characters."""
    return (len(term) <= MAX_TERM_CHARS and len(term.split()) <= MAX_TERM_WORDS
            and not any(unicodedata.category(char) in FORBIDDEN_CATEGORIES for char in term))


class Command(NamedTuple):
    """A one-line +known list: its terms, and those neither recognised nor quoted."""
    terms: list[str]
    unrecognised: list[str]


def parse_items(rest: str) -> list[tuple[str, bool]] | None:
    """(term, quoted) for each comma- or semicolon-separated item, None if malformed.

    An item is a double-quoted term (taken as given, separators allowed inside)
    or plain text up to the next comma or semicolon; empty plain items are
    skipped. A stray or unterminated quote, or text after a closing quote
    before the next separator, makes the list malformed. One left-to-right
    pass with no backtracking, so its time is linear in the line.
    """
    items, position, size = [], 0, len(rest)
    while True:
        while position < size and rest[position].isspace():
            position += 1
        if position >= size:
            return items
        if rest[position] == '"':
            end = rest.find('"', position + 1)
            term = rest[position + 1:end].strip() if end > 0 else ""
            if not term:
                return None
            items.append((term, True))
            position = end + 1
            while position < size and rest[position].isspace():
                position += 1
            if position < size and rest[position] not in ",;":
                return None
        else:
            end = position
            while end < size and rest[end] not in ',;"':
                end += 1
            if end < size and rest[end] == '"':
                return None
            term = rest[position:end].strip().strip("`")
            if term:
                items.append((term, False))
            position = end
        position += 1  # past the separator, or past the end


def normalised(term: str) -> str:
    return term.strip().casefold()


def glossary_names(path: Path) -> set[str] | None:
    """Normalised term names in the glossary store.

    Empty when the store is missing or unreadable (nothing recognised); None
    when it is larger than MAX_GLOSSARY_BYTES, so recognition cannot finish
    and the caller treats every unquoted item as unrecognised. A record that
    is oversized, not JSON or too deeply nested to decode is skipped.
    """
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_GLOSSARY_BYTES + 1)
    except OSError:
        return set()
    if len(data) > MAX_GLOSSARY_BYTES:
        return None
    names = set()
    for line in data.splitlines():
        if len(line) > MAX_GLOSSARY_RECORD_BYTES:
            continue
        try:
            entry = json.loads(line.decode("utf-8"))
        except (ValueError, RecursionError):
            continue
        if isinstance(entry, dict) and isinstance(entry.get("term"), str):
            names.add(normalised(entry["term"]))
    return names


def read_command(prompt: str, path: Path, glossary: Path) -> Command | None:
    """The prompt as a +known list, or None when it is not one.

    A list is one line: `+known` alone (no terms, meaning "list"), or followed
    by at most MAX_TERMS comma- or semicolon-separated items, each a plain or
    double-quoted term within valid_term()'s bounds, with no other + command
    token. A plain term is recognised when, ignoring case and surrounding
    space, it names a glossary term or is already on the list at `path`; a
    quoted one is taken as given. The hook records a Command only when
    `unrecognised` is empty; the glossary is read only for list-shaped prompts.
    """
    line = prompt.strip()
    if len(line) > MAX_COMMAND_CHARS:
        return None
    match = TRIGGER.match(line)
    if not match:
        return None
    rest = match.group(1) or ""
    items = None if OTHER_COMMAND.search(rest) else parse_items(rest)
    if items is None or len(items) > MAX_TERMS or not all(valid_term(term) for term, _ in items):
        return None
    terms = [term for term, _ in items]
    plain = [term for term, quoted in items if not quoted]
    if not plain:
        return Command(terms, [])
    names = glossary_names(glossary)
    if names is None:
        return Command(terms, plain)
    try:
        known = {normalised(term) for term in read_local(path)}
    except OSError:
        known = set()
    known |= names
    # The glossary page's copy button sends one term verbatim, and a glossary
    # term can hold a comma ("additive increase, decrease on success").
    whole = rest.strip().strip("`")
    if '"' not in rest and len(items) > 1 and valid_term(whole) and normalised(whole) in known:
        return Command([whole], [])
    unrecognised = [term for term in plain if normalised(term) not in known]
    return Command(terms, unrecognised)


class Busy(Exception):
    """Another change held the list's lock past the caller's deadline."""


@contextmanager
def locked(path: Path, deadline: float | None = None):
    # Two +known runs (or a hook and an `add`) at once would each read the
    # list and write back their own merge, losing the other's term. Adapted
    # from glossary.py's `locked` (which imports this module), plus a
    # deadline: a hook that waited unbounded could outlive its timeout.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("a") as handle:
        if deadline is None:
            fcntl.flock(handle, fcntl.LOCK_EX)
        else:
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise Busy from None
                    time.sleep(LOCK_POLL_S)
        yield


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
    text = peer_copy.read_peer(peer, LIST_PATH)
    return parse(text) if text is not None else None


def write_peer(peer: str, terms: list[str]) -> bool:
    return peer_copy.write_peer(peer, LIST_PATH, render(terms))


def change(path: Path, peer: str, added: list[str] = (), removed: list[str] = (),
           deadline: float | None = None) -> tuple[list[str], str]:
    """Apply the change locally and, when reachable, on the peer; return (terms, sync note).

    The lock covers the peer exchange too, so this machine's writers also take
    turns on the peer's copy (whose remote write stages through a fixed name).
    Only +known and the CLI take it; the per-prompt injection never does.
    With a deadline (time.monotonic()), the lock wait raises Busy past it, and
    a change left too little time for the peer stays local.
    """
    with locked(path, deadline):
        local = read_local(path)
        if peer and deadline is not None and deadline - time.monotonic() < PEER_BUDGET_S:
            terms = merge(local, list(added), removed=list(removed))
            write_local(path, terms)
            return terms, f"no time left to reach {peer}; kept local, the next change syncs it"
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


def cli_command(action: str, terms: list[str]) -> str:
    """A shell-safe command line running this script's `add` or `remove` on these exact terms."""
    return shlex.join([sys.executable, str(Path(__file__).resolve()), action, "--", *terms])


def quoted_list(terms: list[str]) -> str:
    return ", ".join(json.dumps(term, ensure_ascii=False) for term in terms)


def unrecognised_note(command: Command) -> str:
    """Context for a +known list the hook left to the model because of some of its terms."""
    return (f"Jacob's +known message asked to mark these terms as known: "
            f"{quoted_list(command.terms)}. The known-terms hook recorded none of them, because it "
            f"did not recognise these (not glossary terms, not already known, not in double "
            f"quotes): {quoted_list(command.unrecognised)}. If the message is only that term "
            f"list, record all of the requested terms, the recognised ones included, with "
            f"`{cli_command('add', command.terms)}` and confirm in one line; where you cannot "
            f'write that file, ask him to resend the list with those items in double quotes '
            f'(+known "term", "term"). Otherwise treat the message as an ordinary request.')


def hook(path: Path, peer: str, seen_dir: Path, budget: float = HOOK_BUDGET_S,
         glossary: Path | None = None) -> int:
    deadline = time.monotonic() + budget
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    # Check the event's shape before anything reads or marks the seen state:
    # a malformed payload that was injected into would use up unseen terms.
    if not isinstance(event, dict) or not isinstance(event.get("session_id") or "", str):
        return 0
    kind = event.get("hook_event_name")
    session = re.sub(r"[^A-Za-z0-9_.-]", "", event.get("session_id") or "")
    if kind == "SessionStart":
        # A fresh, resumed, cleared or compacted context has seen none of the list.
        return emit("SessionStart", [unseen_terms(path, seen_dir, session, reset=True)])
    if kind != "UserPromptSubmit" or not isinstance(event.get("prompt"), str):
        return 0
    prompt = event["prompt"].strip()
    command = read_command(prompt, path, glossary or Path.home() / GLOSSARY_PATH)
    if command is not None and not command.unrecognised:
        record(path, peer, command.terms, deadline)
        return 0
    if ANSWERED_ELSEWHERE.match(prompt):
        return 0
    # All or nothing: a list with any unrecognised term reaches the model whole.
    note = unrecognised_note(command) if command is not None else None
    return emit("UserPromptSubmit", [unseen_terms(path, seen_dir, session), note])


def emit(event: str, contexts: list[str | None]) -> int:
    """Print the non-empty contexts as this event's hook context."""
    context = "\n\n".join(text for text in contexts if text)
    if context:
        json.dump({"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}, sys.stdout)
    return 0


def unseen_terms(path: Path, seen_dir: Path, session: str, reset: bool = False) -> str | None:
    """Context naming the terms this session hasn't seen yet, which marks them seen."""
    try:
        terms = read_local(path)
    except OSError:
        return None
    state = seen_dir / f"{session}.json"
    seen: set[str] = set()
    if session and not reset:
        try:
            seen = set(json.loads(state.read_text()))
        except (OSError, ValueError, TypeError):
            pass
    unseen = [term for term in terms if term.casefold() not in seen]
    if not unseen:
        return None
    if session:
        try:
            seen_dir.mkdir(parents=True, exist_ok=True)
            prune(seen_dir)
            state.write_text(json.dumps(sorted(seen | {term.casefold() for term in terms})))
        except OSError:
            pass  # Unrecorded means injected again next prompt, which is harmless.
    lead = "Jacob also marked these terms as known" if seen else "Terms Jacob already knows"
    return f"{lead} ({LIST_PATH}); leave them out of any **Terms** block: {', '.join(unseen)}."


def prune(seen_dir: Path, days: int = 30) -> None:
    cutoff = time.time() - days * 86400
    for old in seen_dir.glob("*.json"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass


def record(path: Path, peer: str, new: list[str], deadline: float | None = None) -> None:
    """Answer a +known prompt: add its terms, or list them, and block the prompt."""
    if new:
        try:
            before = {term.casefold() for term in read_local(path)}
            terms, note = change(path, peer, added=new, deadline=deadline)
        except Busy:
            reason = "Another known-terms change is still running; nothing stored. Send it again."
        except OSError as error:
            reason = f"Could not update {path}: {error}. Nothing stored."
        else:
            fresh = [term for term in new if term.casefold() not in before]
            reason = f"Marked known: {', '.join(new)}"
            if len(fresh) < len(new):
                reason += f" ({len(new) - len(fresh)} already listed)"
            reason += f". {count(terms)}" + (f"; {note}." if note else ".")
            if fresh:
                reason += f" Undo with: {cli_command('remove', fresh)}"
    else:
        try:
            reason = listing(read_local(path))
        except OSError as error:
            reason = f"Could not read {path}: {error}."
    json.dump({"decision": "block", "reason": reason}, sys.stdout)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--file", type=Path, default=Path.home() / LIST_PATH,
                    help="the list (default %(default)s)")
    ap.add_argument("--peer", default=DEFAULT_PEER,
                    help="ssh host holding the other copy, '' for none (default %(default)r)")
    ap.add_argument("--seen-dir", type=Path, default=Path.home() / SEEN_DIR, help=argparse.SUPPRESS)
    ap.add_argument("--hook-budget", type=float, default=HOOK_BUDGET_S, help=argparse.SUPPRESS)
    ap.add_argument("--glossary", type=Path, default=Path.home() / GLOSSARY_PATH, help=argparse.SUPPRESS)
    ap.add_argument("--hook", action="store_true",
                    help="act as a UserPromptSubmit hook: handle +known, pass anything else")
    sub = ap.add_subparsers(dest="action")
    sub.add_parser("list", help="print the known terms (default)")
    for name in ("add", "remove"):
        sub.add_parser(name, help=f"{name} terms, one per argument, taken literally (commas "
                       "and quotes included); put -- first if a term starts with -").add_argument(
                           "terms", nargs="+")
    sub.add_parser("sync", help="merge with the peer's copy")
    args = ap.parse_args(argv)
    path = args.file.expanduser()
    if args.hook:
        return hook(path, args.peer, args.seen_dir.expanduser(), args.hook_budget, args.glossary.expanduser())
    if args.action in ("add", "remove"):
        # One argument is one term, so a shell-quoted term with a comma or
        # semicolon in it stays whole; nothing is split.
        terms = [term.strip() for term in args.terms if term.strip()]
        bad = [term for term in terms if not valid_term(term)] if args.action == "add" else []
        if bad:
            # Agents add terms too (TEACHING.md); hold them to the hook's bounds.
            print(f"add: refused {', '.join(map(repr, bad))}: a term is one line of at most "
                  f"{MAX_TERM_CHARS} characters and {MAX_TERM_WORDS} words", file=sys.stderr)
            return 2
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
