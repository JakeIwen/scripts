#!/usr/bin/env python3
"""Stream grep results from ordered paths, parsing each transcript only once."""

import argparse
import calendar
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Iterator, Optional

from session_search_display import SearchRecord, header_color, record_text


EXCERPT_PROGRAM = Path(__file__).with_name("session_search_excerpt.awk")


@dataclass
class Session:
    provider: str
    path: Path
    identifier: str
    cwd: Optional[str] = None
    custom: str = ""
    ai: str = ""


@dataclass(frozen=True)
class Match:
    text: bytes
    timestamp: object = None


def events(path: Path, tolerant: bool = False) -> Iterator[object]:
    """Claude skips malformed lines; Codex's JSON reader stops at the first one."""
    try:
        # Decode one complete line only for parsing: a TextIOWrapper can retain
        # very large four-byte Unicode line buffers between successive events.
        with path.open("rb") as source:
            for line in source:
                if not line.strip():
                    continue
                try:
                    yield json.loads(line.decode("utf-8", errors="replace"))
                except ValueError:
                    if not tolerant:
                        return
    except OSError:
        return


def strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)
    elif isinstance(value, dict):
        for child in value.values():
            yield from strings(child)


def content_strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for child in value:
            yield from content_strings(child)
    elif isinstance(value, dict):
        kind = value.get("type")
        if kind in ("text", "thinking"):
            text = value.get(kind)
            if isinstance(text, str):
                yield text
        elif kind == "tool_use":
            yield from strings(value.get("input"))
        elif kind == "tool_result":
            yield from content_strings(value.get("content"))


def searchable_strings(event: object, provider: str) -> Iterator[str]:
    if provider == "codex":
        yield from strings(event)
    elif isinstance(event, dict):
        kind = event.get("type")
        # jq's `not` regards only null and false as false, not empty containers.
        non_meta = event.get("isMeta") is None or event.get("isMeta") is False
        if kind == "assistant" or (kind == "user" and non_meta):
            message = event.get("message")
            if isinstance(message, dict):
                yield from content_strings(message.get("content"))
        elif kind == "attachment":
            attachment = event.get("attachment")
            if isinstance(attachment, dict) and attachment.get("type") == "queued_command":
                yield from content_strings(attachment.get("prompt"))


def metadata(session: Session, event: object) -> None:
    if not isinstance(event, dict):
        return
    if session.provider == "claude":
        if session.cwd is None and isinstance(event.get("cwd"), str):
            session.cwd = event["cwd"]
        for kind, field, attribute in (("custom-title", "customTitle", "custom"),
                                       ("ai-title", "aiTitle", "ai")):
            if event.get("type") == kind and isinstance(event.get(field), str) and event[field]:
                setattr(session, attribute, event[field])
    elif event.get("type") == "session_meta" and session.cwd is None:
        payload = event.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        identifier = payload.get("session_id")
        if identifier is None or identifier is False:
            identifier = payload.get("id")
        session.identifier = (identifier or session.identifier).rstrip("\n")
        session.cwd = (payload.get("cwd") or "").rstrip("\n")


def codex_titles(root: Path) -> dict:
    titles = {}
    for event in events(root / "session_index.jsonl"):
        if isinstance(event, dict) and event.get("thread_name") is not None:
            # The old tail -n 1 selects the final physical line of a title.
            name = event["thread_name"]
            if isinstance(name, str):
                titles[event.get("id")] = name.split("\n")[-1]
    return titles


def extract(session: Session, query: re.Pattern, title_only: bool, titles: dict):
    matches = []
    for event in events(session.path, tolerant=session.provider == "claude"):
        metadata(session, event)
        if not title_only:
            text = next((text for text in searchable_strings(event, session.provider)
                         if query.search(text)), None)
            if text is not None:
                timestamp = event.get("timestamp") if isinstance(event, dict) else None
                # UTF-8 storage avoids retaining four-byte Python strings for
                # entire tool logs just because they contain one emoji.
                matches.append(Match(text.encode("utf-8"), timestamp))
    if session.provider == "claude":
        title = re.sub(r"[^\S\x1c-\x1f]+", " ", session.custom or session.ai)
        # The shell metadata reader consumed two physical lines, even when a
        # cwd contained a newline. Preserve that quirk rather than renaming hits.
        session.cwd, title = ((session.cwd or "") + "\n" + title).split("\n")[:2]
    else:
        title = titles.get(session.identifier, "")
    if title_only and title and query.search(title):
        matches = [Match(title.encode("utf-8"))]
    return title, matches


def feed_excerpts(pipe, matches) -> None:
    # Huge tool results must not be copied into a second whole-file payload.
    # Drain stdout concurrently so even large context output cannot deadlock.
    try:
        with pipe:
            for match in matches:
                pipe.write(b"T\nL")
                for offset in range(0, len(match.text), 65536):
                    text = match.text[offset:offset + 65536].replace(b"\n", b"\nL")
                    pipe.write(text)
                pipe.write(b"\nE\n")
    except BrokenPipeError:
        # Native awk rejects some -v query escapes; the original renderer
        # reports its diagnostic and simply produces no matching excerpts.
        pass


def excerpts(matches, provider: str, args):
    # Retain native awk's locale/escape semantics, particularly Codex's character
    # windows versus Claude's widened UTF-8 byte windows (3719779).
    environment = dict(os.environ, SESSION_SEARCH_CONTEXT_QUERY=args.query)
    if provider == "claude":
        environment["LC_ALL"] = "C"
    command = ["awk", "-v", "query=" + args.query,
               "-v", "widen=" + str(provider == "claude").lower(),
               "-v", "requested=" + str(args.context and not args.title).lower(),
               "-v", "before=" + args.before, "-v", "after=" + args.after,
               "-f", str(EXCERPT_PROGRAM)]
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          env=environment) as process, ThreadPoolExecutor(max_workers=1) as writer:
        pending = writer.submit(feed_excerpts, process.stdin, matches)
        output = process.stdout.read()
        pending.result()
    return output.decode("utf-8", errors="surrogateescape").split("\0")[:-1] or [""] * len(matches)


def quote_word(word: str) -> str:
    if word and re.fullmatch(r"[a-zA-Z0-9_./-]+", word):
        return word
    return "'" + word.replace("'", "'\\''") + "'"


def resume_command(session: Session) -> str:
    cwd = session.cwd or ""
    home = os.environ["HOME"]
    prefix = ""
    if cwd:
        if cwd == home:
            prefix = "cd ~"
        elif cwd.startswith(home + "/"):
            prefix = "cd ~/" + quote_word(cwd[len(home) + 1:])
        else:
            prefix = ("cd -- " if cwd.startswith("-") else "cd ") + quote_word(cwd)
        prefix += " && "
    return prefix + session.provider + "pr " + quote_word(session.identifier)


def header(name: str, modified: int) -> str:
    elapsed = max(0, int(time.time()) - modified)
    count, unit = (elapsed // 3600, "hour") if elapsed < 86400 else (elapsed // 86400, "day")
    line = "{} {}{} ago | {}".format(count, unit, "" if count == 1 else "s", name)
    return re.sub(r"[\n\r\t]", " ", line)


def matched_time(timestamp: object, modified: int) -> int:
    if isinstance(timestamp, str):
        timestamp = re.sub(r"\.[0-9]+Z$", "Z", timestamp)
        try:
            return calendar.timegm(time.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ"))
        except ValueError:
            pass
    return modified


def print_session(session: Session, title: str, matches, args, color) -> None:
    if not matches:
        return
    modified = int(session.path.stat().st_mtime)
    name = title or (session.cwd or "").removesuffix("/").rsplit("/", 1)[-1]
    name = name or ("/" if session.cwd else "unknown")
    label, resume = header(name, modified), resume_command(session)
    if args.records:
        context = excerpts(matches[-1:], session.provider, args)[0]
        # jq replaced invalid UTF-8 at the old record boundary; plain output
        # deliberately retains Codex awk's potentially split multibyte bytes.
        context = context.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="replace")
        if context:
            record = SearchRecord(session.provider, session.cwd or "", modified,
                                  matched_time(matches[-1].timestamp, modified),
                                  len(matches), label, resume, context)
            print(json.dumps(asdict(record), ensure_ascii=False, separators=(",", ":")), flush=True)
        return
    # The original plain stream used NUL to delimit matches; preserve its
    # splitting of embedded NULs rather than silently changing event counts.
    plain_matches = [Match(part) for match in matches for part in match.text.split(b"\0")]
    contexts = [context for context in excerpts(plain_matches, session.provider, args) if context]
    if args.group and contexts:
        record = SearchRecord(session.provider, session.cwd or "", modified, modified,
                              len(contexts), label, resume, contexts[-1])
        sys.stdout.write(record_text(record, color))
    else:
        for context in reversed(contexts):
            sys.stdout.write("{}{}{}\n{}\n{}\n\n".format(color[0], label, color[1], resume, context))
    sys.stdout.flush()


def input_paths():
    # Each path is NUL-delimited; do not wait for enumeration to finish before
    # searching the first file. Path sorting itself is owned by paths.py.
    pending = b""
    provider = None
    while True:
        chunk = sys.stdin.buffer.read1(65536)
        if not chunk:
            break
        fields = (pending + chunk).split(b"\0")
        pending = fields.pop()
        for field in fields:
            if provider is None:
                provider = field.decode("ascii")
            else:
                yield provider, Path(os.fsdecode(field))
                provider = None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("group", "title", "context", "records"):
        parser.add_argument("--" + flag, action="store_true")
    parser.add_argument("--before", default="0")
    parser.add_argument("--after", default="0")
    parser.add_argument("--codex-root", type=Path, required=True)
    parser.add_argument("query")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="surrogateescape")
    query = re.compile(re.escape(args.query), re.IGNORECASE | re.ASCII)
    titles = codex_titles(args.codex_root)
    color = ("", "") if args.records else header_color(sys.stdout)
    for provider, path in input_paths():
        identifier = re.sub(r"^.*-([\da-fA-F]{8}(?:-[\da-fA-F]{4}){3}-[\da-fA-F]{12})$",
                            r"\1", path.stem) if provider == "codex" else path.stem
        session = Session(provider, path, identifier.rstrip("\n"))
        title, matches = extract(session, query, args.title, titles)
        print_session(session, title, matches, args, color)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
