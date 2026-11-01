#!/usr/bin/env python3
"""Show each matched session's fork family: what it was forked from and its forks."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import signal
import sys
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple

from session_search_display import header_color
from session_search_extract import (Session, codex_titles, extract, header, resume_command,
                                    session_identifier, session_name)
from session_search_paths import claude_paths, codex_paths, parse_sources


# Claude Code's fork and /branch copy the source's rows, each marked with
# forkedFrom naming the session it was copied from, so a fork's first rows
# name its parent. Codex names the parent in the rollout's first session_meta.
CLAUDE_HEAD_LINES = 20
NO_MATCH = re.compile(r"(?!)")

Key = Tuple[str, str]


@dataclass(frozen=True)
class Transcript:
    provider: str
    identifier: str
    path: Path
    parent: Optional[str]
    # Codex subagent threads are forked from the thread that spawned them, but
    # they are delegated workers, not chats, so they are not listed as forks.
    subagent: bool = False

    @property
    def key(self) -> Key:
        return self.provider, self.identifier


def head_lines(path: Path, limit: int) -> Iterator[bytes]:
    try:
        with path.open("rb") as source:
            for number, line in enumerate(source):
                if number >= limit:
                    return
                yield line
    except OSError:
        return


def decoded(line: bytes) -> dict:
    try:
        event = json.loads(line.decode("utf-8", errors="replace"))
    except ValueError:
        return {}
    return event if isinstance(event, dict) else {}


def claude_transcript(path: Path) -> Transcript:
    parent = None
    for line in head_lines(path, CLAUDE_HEAD_LINES):
        origin = decoded(line).get("forkedFrom") if b'"forkedFrom"' in line else None
        if isinstance(origin, dict) and isinstance(origin.get("sessionId"), str):
            parent = origin["sessionId"]
            break
    return Transcript("claude", session_identifier("claude", path), path, parent)


def codex_transcript(path: Path) -> Transcript:
    payload: dict = {}
    for line in head_lines(path, 1):
        event = decoded(line)
        if event.get("type") == "session_meta" and isinstance(event.get("payload"), dict):
            payload = event["payload"]
    identifier = payload.get("session_id") or payload.get("id")
    if not isinstance(identifier, str) or not identifier:
        identifier = session_identifier("codex", path)
    parent = payload.get("forked_from_id")
    source = payload.get("source")
    subagent = payload.get("thread_source") == "subagent" or (
        isinstance(source, dict) and "subagent" in source)
    return Transcript("codex", identifier.rstrip("\n"), path,
                      parent if isinstance(parent, str) and parent else None, subagent)


def transcripts(sources: Sequence[Tuple[str, Path]]) -> Dict[Key, Transcript]:
    found: Dict[Key, Transcript] = {}
    for provider, root in sources:
        if provider == "claude":
            for path in claude_paths(root):
                transcript = claude_transcript(path)
                found[transcript.key] = transcript
        else:
            for path in codex_paths(root):
                transcript = codex_transcript(path)
                found[transcript.key] = transcript
    return found


def modified(transcript: Transcript) -> int:
    try:
        return int(transcript.path.stat().st_mtime)
    except OSError:
        return 0


def matched_sessions(data: bytes) -> List[Key]:
    """Distinct provider/id pairs from NUL-separated input, in input order."""
    fields = data.split(b"\0")
    keys: List[Key] = []
    for provider, identifier in zip(fields[0::2], fields[1::2]):
        key = (provider.decode("ascii", errors="replace"), os.fsdecode(identifier))
        if key not in keys:
            keys.append(key)
    return keys


class Families:
    def __init__(self, known: Dict[Key, Transcript], matched: Sequence[Key],
                 titles: dict, color: Tuple[str, str]) -> None:
        self.known = known
        self.matched = set(matched)
        self.titles = titles
        self.color = color
        self.children: Dict[Key, List[Transcript]] = {}
        for transcript in known.values():
            if transcript.parent is None:
                continue
            if transcript.subagent and transcript.key not in self.matched:
                continue
            parent = (transcript.provider, transcript.parent)
            self.children.setdefault(parent, []).append(transcript)
        for siblings in self.children.values():
            siblings.sort(key=lambda child: (-modified(child), child.identifier))

    def root(self, key: Key) -> Key:
        """The oldest ancestor, or the first one whose transcript is gone."""
        seen = {key}
        while key in self.known and self.known[key].parent is not None:
            parent = (key[0], self.known[key].parent)
            if parent in seen:
                break
            seen.add(parent)
            key = parent
        return key

    def members(self, root: Key) -> List[Key]:
        found: List[Key] = []
        pending = [root]
        while pending:
            key = pending.pop()
            if key in found:
                continue
            found.append(key)
            pending.extend(child.key for child in self.children.get(key, []))
        return found

    def describe(self, key: Key) -> Tuple[str, str]:
        transcript = self.known.get(key)
        if transcript is None:
            return "{} session {} (transcript not found)".format(*key), ""
        session = Session(transcript.provider, transcript.path, transcript.identifier)
        title, _ = extract(session, NO_MATCH, True, self.titles)
        return header(session_name(session, title), modified(transcript)), resume_command(session)

    def tree(self, key: Key, lead: str, stem: str, marked: bool,
             shown: Set[Key]) -> Iterator[str]:
        """`lead` prefixes this session's header; `stem` its other lines and forks."""
        shown.add(key)
        label, resume = self.describe(key)
        mark = " (match)" if marked and key in self.matched else ""
        yield lead + self.color[0] + label + self.color[1] + mark
        if resume:
            yield stem + resume
        children = [child for child in self.children.get(key, []) if child.key not in shown]
        for index, child in enumerate(children):
            last = index == len(children) - 1
            yield from self.tree(child.key, stem + ("└─ " if last else "├─ "),
                                 stem + ("   " if last else "│  "), marked, shown)

    def text(self, root: Key) -> str:
        family = self.members(root)
        lines = list(self.tree(root, "", "", len(family) > 1, set()))
        if len(family) == 1 and root in self.known:
            lines.append("no forks or branches")
        return "\n".join(lines) + "\n\n"


def main(arguments: Optional[Sequence[str]] = None) -> int:
    sources = parse_sources(sys.argv[1:] if arguments is None else arguments)
    matched = matched_sessions(sys.stdin.buffer.read())
    if not matched:
        return 0
    try:
        known = transcripts(sources)
    except RuntimeError as error:
        print(f"session_search_forks: {error}", file=sys.stderr)
        return 1
    codex_roots = [root for provider, root in sources if provider == "codex"]
    titles = codex_titles(codex_roots[0]) if codex_roots else {}
    families = Families(known, matched, titles, header_color(sys.stdout))
    shown: Set[Key] = set()
    for key in matched:
        root = families.root(key)
        if root not in shown:
            shown.add(root)
            sys.stdout.write(families.text(root))
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    # Ctrl+C ends the search without a traceback; the calling script reports it.
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    raise SystemExit(main())
