#!/usr/bin/python3
"""Save taught Terms blocks so Jacob can review definitions and drill them offline.

Stop hooks from Claude Code and Codex record definitions without a model turn.
The +terms UserPromptSubmit hook merges the Mac/Pi copies and opens an offline
searchable glossary. Stop failures must never interrupt the agent's session.
Standard library only; supports Mac /usr/bin/python3 3.9 and Pi Python 3.11.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import TypedDict
from urllib.parse import quote

import known_terms
import peer_copy
import glossary_page

STORE_PATH = ".agent/glossary/terms.jsonl"
PAGE_PATH = ".agent/glossary/glossary.html"
PI_MOUNT_ON_MAC = Path("/Volumes/pihome")
HOST = "mac" if sys.platform == "darwin" else "pi"
TRIGGER = re.compile(r"^\+terms(?:\s+(.*))?$", re.DOTALL)
OPENER = re.compile(r"^\s*(?:\*\*Terms:?\*\*:?|#{1,6}\s+Terms:?(?:\s+#+)?)\s*$", re.I)
BULLET = re.compile(r"^([ \t]*)[-*]\s+\*\*(.+?)\*\*(.*)$")
SEPARATOR = re.compile(r"^\s*(?::\s*|[—-]\s+)(.*)$")
FILE_URL = re.compile(r'''file://[^\s<>"`\)]+''')


class Entry(TypedDict):
    id: str
    term: str
    definition: str
    at: str
    host: str
    cwd: str
    repo: str | None
    session: str
    link: str | None


def parse_terms(message: str) -> list[tuple[str, str]]:
    """Only defined bullets in explicit Terms blocks, not following prose lists."""
    result = []
    active, fence = False, ""
    term, lines, indent = "", [], 0
    for line in message.splitlines() + [""]:
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker and not active:
            token = marker.group(1)
            if not fence:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = ""
            continue
        if fence:
            continue
        opener = bool(OPENER.fullmatch(line))
        bullet = BULLET.match(line) if active and not opener else None
        continuation = (active and term and line.strip() and
                        len(line) - len(line.lstrip()) > indent)
        if active and not opener and not bullet and (not line.strip() or continuation):
            if term:
                lines.append(line)
            continue
        if term:
            definition = "\n".join(lines).strip()
            if definition:
                result.append((term, definition))
            term, lines = "", []
        if opener:
            active = True
        elif bullet:
            raw, tail = bullet.group(2).strip(), bullet.group(3)
            separator = SEPARATOR.match(tail)
            if raw.endswith(":"):
                term, definition = raw[:-1].strip(), tail.lstrip()
            elif separator:
                term, definition = raw, separator.group(1)
            else:
                active = False
                continue
            indent = len(bullet.group(1))
            lines = [definition]
        else:
            active = False
    if term and "\n".join(lines).strip():
        result.append((term, "\n".join(lines).strip()))
    return result


def parse_store(text: str) -> list[Entry]:
    entries = []
    required = ("id", "term", "definition", "at", "host", "cwd", "session")
    for line in text.splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if not isinstance(entry, dict) or any(not isinstance(entry.get(key), str) for key in required):
            raise ValueError("invalid glossary entry")
        if any(entry.get(key) is not None and not isinstance(entry[key], str) for key in ("repo", "link")):
            raise ValueError("invalid glossary context")
        if not entry["id"] or not entry["term"].strip() or not entry["definition"].strip():
            raise ValueError("empty glossary entry")
        if datetime.fromisoformat(entry["at"]).tzinfo is None:
            raise ValueError("glossary timestamp needs a timezone")
        entries.append({**entry, "repo": entry.get("repo"), "link": entry.get("link")})
    return entries


def read_store(path: Path) -> list[Entry]:
    try:
        return parse_store(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


def serialize(entries: list[Entry]) -> str:
    return "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked(path: Path):
    # Different agent sessions can finish at once; serialize the read/replace.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def repository(cwd: str) -> str | None:
    if not cwd or not Path(cwd).is_absolute():
        return None
    location = Path(cwd)
    for parent in (location, *location.parents):
        if (parent / ".git").exists():
            return parent.name
    return None


def record(path: Path, event: dict) -> None:
    message, session = event.get("last_assistant_message"), event.get("session_id")
    if not isinstance(message, str) or not isinstance(session, str) or not session:
        return
    definitions = parse_terms(message)
    if not definitions:
        return
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else ""
    link = FILE_URL.search(message)
    at = datetime.now().astimezone().isoformat()
    with locked(path):
        entries = read_store(path)
        seen = {(entry["session"], entry["term"].casefold()) for entry in entries}
        for term, definition in definitions:
            key = (session, term.casefold())
            if key in seen:
                continue
            identity = json.dumps([session, term.casefold(), definition], ensure_ascii=False)
            entries.append(Entry(id=hashlib.sha1(identity.encode("utf-8")).hexdigest(), term=term,
                                 definition=definition, at=at, host=HOST, cwd=cwd, repo=repository(cwd),
                                 session=session, link=link.group(0).rstrip(".,;") if link else None))
            seen.add(key)
        write_text(path, serialize(entries))


def change(path: Path, peer: str, removed: list[str] = ()) -> tuple[list[Entry], str]:
    with locked(path):
        local = read_store(path)
        remote = peer_copy.read_peer(peer, STORE_PATH) if peer else None
        drop = {term.casefold() for term in removed}
        merged = {}
        for entry in local + (parse_store(remote) if remote is not None else []):
            if entry["term"].casefold() not in drop:
                merged.setdefault(entry["id"], entry)
        entries = newest(list(merged.values()))
        text = serialize(entries)
        write_text(path, text)
        if not peer:
            return entries, ""
        if remote is not None and peer_copy.write_peer(peer, STORE_PATH, text):
            return entries, f"synced with {peer}"
        return entries, f"{peer} unreachable; kept local, the next sync carries it"


def newest(entries: list[Entry]) -> list[Entry]:
    return sorted(entries, key=lambda entry: datetime.fromisoformat(entry["at"]).timestamp(), reverse=True)


def unique(entries: list[Entry]) -> list[Entry]:
    terms = {}
    for entry in newest(entries):
        terms.setdefault(entry["term"].casefold(), entry)
    return list(terms.values())


def count(entries: list[Entry]) -> str:
    amount = len(unique(entries))
    return f"{amount} term" + ("" if amount == 1 else "s")


def render_page(entries: list[Entry], known_file: Path, page: Path) -> str:
    known = known_terms.read_local(known_file)
    write_text(page, glossary_page.render(newest(entries), known))
    target = page.absolute()
    if HOST == "pi":
        try:
            target = PI_MOUNT_ON_MAC / target.relative_to(Path.home())
        except ValueError:
            pass
    return target.as_uri()


def hook(path: Path, peer: str, known_file: Path, page: Path) -> int:
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            return 0
        kind = event.get("hook_event_name")
        if kind == "Stop":
            record(path, event)
        elif kind == "UserPromptSubmit" and isinstance(event.get("prompt"), str):
            match = TRIGGER.fullmatch(event["prompt"].strip())
            if match:
                entries, note = change(path, peer)
                url = render_page(entries, known_file, page)
                query = (match.group(1) or "").strip()
                if query:
                    url += "#q=" + quote(query, safe="")
                reason = f"{count(entries)}. {url}" + (f"\n{note}." if note else "")
                json.dump({"decision": "block", "reason": reason}, sys.stdout)
    except (OSError, ValueError, TypeError) as error:
        print(f"glossary: {str(error).splitlines()[0]}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--file", type=Path, default=Path.home() / STORE_PATH)
    ap.add_argument("--peer", default=known_terms.DEFAULT_PEER,
                    help="ssh peer, '' disables syncing (default %(default)r)")
    ap.add_argument("--known-file", type=Path, default=Path.home() / known_terms.LIST_PATH)
    ap.add_argument("--page", type=Path, default=Path.home() / PAGE_PATH)
    ap.add_argument("--hook", action="store_true", help="handle Stop and +terms hook events")
    sub = ap.add_subparsers(dest="action")
    sub.add_parser("list", help="list terms, newest first (default)")
    sub.add_parser("remove", help="remove all definitions of comma-separated terms").add_argument("terms", nargs="+")
    sub.add_parser("render", help="write the offline page and print its file URL")
    sub.add_parser("sync", help="merge the local and peer stores")
    args = ap.parse_args(argv)
    path, known_file, page = (value.expanduser() for value in (args.file, args.known_file, args.page))
    if args.hook:
        return hook(path, args.peer, known_file, page)
    try:
        if args.action in ("remove", "sync"):
            if args.action == "sync" and not args.peer:
                print("no peer to sync with", file=sys.stderr)
                return 1
            removed = known_terms.split_terms(",".join(args.terms)) if args.action == "remove" else []
            entries, note = change(path, args.peer, removed)
            print(count(entries) + (f"; {note}." if note else "."))
            return 1 if args.action == "sync" and not note.startswith("synced") else 0
        entries = read_store(path)
        if args.action == "render":
            print(render_page(entries, known_file, page))
        else:
            for entry in unique(entries):
                print(f"{entry['at'][:10]} · {entry['repo'] or '—'} · {entry['term']}")
        return 0
    except (OSError, ValueError) as error:
        print(f"glossary: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
