#!/usr/bin/python3
"""List the page links in this session's replies, for +links.

Agents hand Jacob pages to open (+art summaries, long replies, reports) as
file:// URLs and hosted claude.ai Artifact links, and later output pushes them
off the screen before he opens them. As a Claude Code and Codex
UserPromptSubmit hook, a bare `+links` reads the session's transcript and
blocks the prompt with every link the replies carried, so it costs no model
turn. Every other prompt passes through, and failures never block. Standard
library only; supports Mac /usr/bin/python3 3.9 and Pi Python 3.11.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import html
import json
from pathlib import Path
import re
import sys
from typing import Iterator, NamedTuple
from urllib.parse import quote, unquote, urlparse

TRIGGER = "+links"
FILE_URL = r"""file://[^\s<>"'`)\]]+"""
HOSTED_URL = r"https://claude\.ai/(?:code/)?artifact/[A-Za-z0-9-]+"
# Codex replies from before the file:// rule link pages as [label](/path).
# Only pages: Codex links code the same way, as [x.py](/path/x.py:12).
PAGE_PATH = r"(?<=\]\()/[^\s)]+\.html?(?=\))"
LINK = re.compile(f"{FILE_URL}|{HOSTED_URL}|{PAGE_PATH}")
# Prose punctuation and Markdown emphasis that can trail a bare URL.
TRAILING = ".,;:*_"
TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
# The hook shows the newest links; the CLI prints them all.
HOOK_LIMIT = 20
INDEX_PATH = ".agent/artifacts/index.html"
# Pi pages open on the Mac through its SMB mount of the Pi's home.
PI_HOME = Path("/home/pi")
PI_MOUNT_ON_MAC = Path("/Volumes/pihome")
ON_PI = sys.platform != "darwin"


class Link(NamedTuple):
    url: str
    at: datetime | None
    label: str


def replies(transcript: Path) -> Iterator[tuple[datetime | None, str]]:
    """Text of each reply Jacob saw, oldest first: Claude Code assistant rows
    outside subagent sidechains, and Codex assistant response items."""
    with transcript.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            # Transcripts run to tens of megabytes; parse only rows with a link.
            if "file://" not in line and "claude.ai/" not in line and "](/" not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            for text in reply_texts(row):
                yield timestamp(row.get("timestamp")), text


def reply_texts(row: dict) -> list[str]:
    if row.get("type") == "assistant" and not row.get("isSidechain"):
        message, kind = row.get("message"), "text"
    elif row.get("type") == "response_item":
        message, kind = row.get("payload"), "output_text"
        if not isinstance(message, dict) or message.get("role") != "assistant":
            return []
    else:
        return []
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    return [block["text"] for block in content if isinstance(block, dict)
            and block.get("type") == kind and isinstance(block.get("text"), str)]


def timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def local_path(url: str) -> Path:
    path = Path(unquote(urlparse(url).path))
    if ON_PI and PI_MOUNT_ON_MAC in path.parents:
        return PI_HOME / path.relative_to(PI_MOUNT_ON_MAC)
    return path


def clickable(path: Path) -> str:
    """The URL Jacob clicks, on the Mac either way."""
    if ON_PI and PI_HOME in path.parents:
        path = PI_MOUNT_ON_MAC / path.relative_to(PI_HOME)
    return "file://" + quote(str(path))


def page_title(path: Path) -> str:
    try:
        exists = path.is_file()
    except OSError:
        exists = False
    if not exists:
        return f"{path.name} (not found)"
    if path.suffix.lower() in (".html", ".htm"):
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                match = TITLE.search(handle.read(16384))
        except OSError:
            match = None
        if match and match.group(1).strip():
            return html.unescape(" ".join(match.group(1).split()))
    return path.name


def collect(transcript: Path) -> list[Link]:
    """Each link once, at its first mention: carried-forward reports repeat
    them. A hosted link is named after the local page in the same reply."""
    seen: dict[str, Link] = {}
    for at, text in replies(transcript):
        found = [("file://" + quote(raw) if raw.startswith("/") else raw.rstrip(TRAILING))
                 for raw in LINK.findall(text)]
        files = {url: local_path(url) for url in found if url.startswith("file://")}
        titles = {url: page_title(path) for url, path in files.items()}
        page = next((titles[url] for url in titles if files[url].suffix == ".html"), None)
        for url in found:
            key = str(files[url]) if url in files else url
            if key in seen:
                continue
            if url in files:
                seen[key] = Link(clickable(files[url]), at, titles[url])
            else:
                seen[key] = Link(url, at, f"Hosted: {page}" if page else "Hosted Artifact")
    return list(seen.values())


def when(at: datetime | None, today: datetime) -> str:
    if at is None:
        return "     "
    if at.date() == today.date():
        return f"{at:%H:%M}"
    return f"{at:%b} {at.day} {at:%H:%M}"


def render(links: list[Link], limit: int | None) -> str:
    today = datetime.now().astimezone()
    shown = links[-limit:] if limit else links
    if not links:
        lines = ["+links: no links in this session's replies yet."]
    else:
        noun = "link" if len(links) == 1 else "links"
        lines = [f"+links: {len(links)} {noun} from this session's replies, oldest first"]
        if len(shown) < len(links):
            lines.append(f"({len(links) - len(shown)} earlier ones left out; the index below lists every saved page)")
        start = len(links) - len(shown) + 1
        for number, link in enumerate(shown, start):
            lines.append(f"{number:2}. {when(link.at, today)}  {link.label}")
            lines.append(f"    {link.url}")
    index = Path.home() / INDEX_PATH
    if index.is_file():
        lines.append(f"All saved pages: {clickable(index)}")
    return "\n".join(lines)


def hook() -> int:
    try:
        event = json.load(sys.stdin)
        if (not isinstance(event, dict) or event.get("hook_event_name") != "UserPromptSubmit"
                or not isinstance(event.get("prompt"), str) or event["prompt"].strip() != TRIGGER):
            return 0
        transcript = event.get("transcript_path")
        if isinstance(transcript, str) and transcript and Path(transcript).is_file():
            reason = render(collect(Path(transcript)), HOOK_LIMIT)
        else:
            reason = "+links: this session has no transcript on disk yet."
        json.dump({"decision": "block", "reason": reason}, sys.stdout)
    except (OSError, ValueError, TypeError) as error:
        print(f"reply_links: {str(error).splitlines()[0]}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hook", action="store_true", help="handle a UserPromptSubmit hook event")
    ap.add_argument("transcript", nargs="?", type=Path,
                    help="a Claude Code transcript or Codex rollout (.jsonl)")
    args = ap.parse_args(argv)
    if args.hook:
        return hook()
    if not args.transcript:
        ap.error("give a transcript path, or --hook")
    try:
        print(render(collect(args.transcript.expanduser()), None))
    except OSError as error:
        print(f"reply_links: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
