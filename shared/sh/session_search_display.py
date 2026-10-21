#!/usr/bin/env python3
"""Render session-search JSON records in newest-first order."""

import argparse
import json
import shutil
import signal
import sys
import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, TextIO, Tuple


@dataclass(frozen=True)
class SearchRecord:
    provider: str
    cwd: str
    modified: int
    matched_at: int
    count: int
    header: str
    resume: str
    context: str


def parse_record(line: str, number: int) -> SearchRecord:
    try:
        value = json.loads(line)
    except json.JSONDecodeError as error:
        raise ValueError("line {} is not valid JSON: {}".format(number, error.msg))
    if not isinstance(value, dict):
        raise ValueError("line {} must contain a JSON object".format(number))

    strings = ("provider", "cwd", "header", "resume", "context")
    integers = ("modified", "matched_at", "count")
    for name in strings:
        if not isinstance(value.get(name), str):
            raise ValueError("line {} field {!r} must be a string".format(number, name))
    for name in integers:
        if isinstance(value.get(name), bool) or not isinstance(value.get(name), int):
            raise ValueError("line {} field {!r} must be an integer".format(number, name))
    if value["count"] < 0:
        raise ValueError("line {} field 'count' must not be negative".format(number))
    return SearchRecord(**{name: value[name] for name in strings + integers})


def latest_key(record: SearchRecord) -> Tuple[int, int, str]:
    return record.matched_at, record.modified, record.resume


def group_key(record: SearchRecord) -> Tuple[str, str, str]:
    session = record.resume if not record.cwd else ""
    return record.provider, record.cwd, session


def merge_record(previous: SearchRecord, current: SearchRecord) -> SearchRecord:
    latest = current if latest_key(current) > latest_key(previous) else previous
    return SearchRecord(
        provider=latest.provider,
        cwd=latest.cwd,
        modified=latest.modified,
        matched_at=latest.matched_at,
        count=previous.count + current.count,
        header=latest.header,
        resume=latest.resume,
        context=latest.context,
    )


def sorted_records(records: Iterable[SearchRecord]) -> List[SearchRecord]:
    directories: Dict[str, List[SearchRecord]] = {}
    for record in records:
        directories.setdefault(record.cwd, []).append(record)

    def directory_key(item: Tuple[str, List[SearchRecord]]) -> Tuple[int, str]:
        cwd, entries = item
        return -max(entry.modified for entry in entries), cwd

    answer: List[SearchRecord] = []
    for _, entries in sorted(directories.items(), key=directory_key):
        answer.extend(sorted(
            entries,
            key=lambda record: (
                -record.modified,
                record.provider,
                record.resume,
                record.header,
                record.context,
            ),
        ))
    return answer


def record_text(record: SearchRecord) -> str:
    return "{} matches | {}\n{}\n{}\n\n".format(
        record.count, record.header, record.resume, record.context
    )


def final_text(records: Iterable[SearchRecord]) -> str:
    return "".join(record_text(record) for record in sorted_records(records))


def character_width(character: str) -> int:
    if unicodedata.combining(character):
        return 0
    if unicodedata.east_asian_width(character) in ("F", "W"):
        return 2
    return 1


def safe_character(character: str) -> str:
    if character == "\t":
        return " "
    if unicodedata.category(character).startswith("C"):
        return "?"
    return character


def terminal_line(text: str, width: int) -> str:
    # Keep one column unused: writing the terminal's final column can trigger
    # an implicit wrap, making the next redraw erase a line above this frame.
    limit = max(1, width - 1)
    clean = "".join(safe_character(character) for character in text)
    if sum(character_width(character) for character in clean) <= limit:
        return clean
    content_limit = max(0, limit - 1)
    result = []
    columns = 0
    for character in clean:
        character_columns = character_width(character)
        if columns + character_columns > content_limit:
            break
        result.append(character)
        columns += character_columns
    return "".join(result).rstrip() + "…"


def live_frame(records: Iterable[SearchRecord], seen: int) -> str:
    size = shutil.get_terminal_size(fallback=(80, 24))
    width = max(2, size.columns)
    available = max(1, size.lines - 1)
    lines = ["Searching… {} session{} found; counts may increase".format(
        seen, "" if seen == 1 else "s"
    )]
    for record in sorted_records(records):
        block = record_text(record).rstrip("\n").splitlines() + [""]
        if len(lines) + len(block) > available:
            break
        lines.extend(block)
    return "\n".join(terminal_line(line, width) for line in lines) + "\n"


class TerminalRenderer:
    def __init__(self, output: TextIO) -> None:
        self.output = output
        self.lines = 0

    def update(self, frame: str) -> None:
        self.clear()
        self.output.write(frame)
        self.output.flush()
        self.lines = frame.count("\n")

    def clear(self) -> None:
        if not self.lines:
            return
        self.output.write("\r\x1b[{}A\x1b[J".format(self.lines))
        self.lines = 0

    def finish(self, text: str) -> None:
        self.clear()
        self.output.write(text)
        self.output.flush()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Sort and optionally group session-search JSONL records."
    )
    result.add_argument(
        "--group", action="store_true", required=True,
        help="combine sessions by provider and directory while records arrive",
    )
    return result


def run(source: TextIO, output: TextIO) -> int:
    groups: Dict[Tuple[str, str, str], SearchRecord] = {}
    renderer = TerminalRenderer(output) if output.isatty() else None
    seen = 0
    if renderer:
        renderer.update(live_frame(groups.values(), seen))
    try:
        for number, line in enumerate(source, 1):
            if not line.strip():
                continue
            record = parse_record(line, number)
            seen += 1
            key = group_key(record)
            groups[key] = merge_record(groups[key], record) if key in groups else record
            if renderer:
                renderer.update(live_frame(groups.values(), seen))
    except (KeyboardInterrupt, ValueError):
        if renderer:
            renderer.finish(final_text(groups.values()))
        raise
    text = final_text(groups.values())
    if renderer:
        renderer.finish(text)
    else:
        output.write(text)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser().parse_args(argv)
    interrupted = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = True
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGINT, stop)
    try:
        try:
            return run(sys.stdin, sys.stdout)
        except ValueError as error:
            print("session_search_display: {}".format(error), file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            return 130
    finally:
        signal.signal(signal.SIGINT, previous)
        if interrupted and sys.stdout.isatty():
            sys.stdout.write("\r\x1b[J")
            sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
