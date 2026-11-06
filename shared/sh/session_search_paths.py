#!/usr/bin/env python3
"""List Claude and Codex session transcripts in newest-activity order."""

import argparse
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
from typing import Iterable, List, Optional, Sequence, Tuple


PathRecord = Tuple[int, bytes, str, Path]


def cwd_within(cwd: Optional[str], root: Optional[str]) -> bool:
    """True when a session's directory is `root` or below it (no `root`: always)."""
    if not root:
        return True
    if not cwd:
        return False
    cwd, root = os.path.realpath(cwd), os.path.realpath(root)
    return cwd == root or cwd.startswith(root.rstrip("/") + "/")


def claude_project_name(cwd: str) -> str:
    """Claude Code names a project folder after its cwd with every other character as `-`."""
    return re.sub(r"[^a-zA-Z0-9]", "-", cwd)


def claude_project_may_be_within(name: str, root: str) -> bool:
    # The folder name loses the path's punctuation, so this only rules folders
    # out; the extractor checks the cwd each transcript records.
    root = os.path.realpath(root)
    return name == claude_project_name(root) or \
        name.startswith(claude_project_name(root.rstrip("/") + "/"))


def codex_session_cwd(path: Path) -> Optional[str]:
    """The cwd from a transcript's leading session_meta line, or None when unknown."""
    try:
        with path.open("rb") as source:
            event = json.loads(source.readline().decode("utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    if not isinstance(event, dict) or event.get("type") != "session_meta":
        return None
    payload = event.get("payload")
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    return cwd if isinstance(cwd, str) else None


def claude_paths(root: Path, under: Optional[str] = None) -> Iterable[Path]:
    projects = root / "projects"
    if not projects.is_dir():
        return
    try:
        project_entries = list(os.scandir(projects))
    except OSError as error:
        raise RuntimeError(f"cannot read Claude projects directory {projects}: {error}") from error
    for project in project_entries:
        try:
            if not project.is_dir(follow_symlinks=False):
                continue
            if under and not claude_project_may_be_within(project.name, under):
                continue
            sessions = os.scandir(project.path)
        except OSError as error:
            raise RuntimeError(f"cannot read Claude project directory {project.path}: {error}") from error
        try:
            with sessions:
                for session in sessions:
                    if session.name.endswith(".jsonl") and session.is_file(follow_symlinks=False):
                        yield Path(session.path)
        except OSError as error:
            raise RuntimeError(f"cannot read Claude project directory {project.path}: {error}") from error


def codex_paths(root: Path, under: Optional[str] = None) -> Iterable[Path]:
    sessions = root / "sessions"
    if not sessions.is_dir():
        return

    def raise_walk_error(error: OSError) -> None:
        raise error

    try:
        for directory, names, files in os.walk(
                sessions, topdown=True, onerror=raise_walk_error, followlinks=False):
            # os.walk does not descend through directory symlinks when followlinks is
            # false, but pruning them also avoids reporting errors from their targets.
            names[:] = [name for name in names
                        if not os.path.islink(os.path.join(directory, name))]
            for name in files:
                if not name.endswith(".jsonl"):
                    continue
                path = Path(directory, name)
                if path.is_symlink():
                    continue
                # A transcript whose first line names no cwd is kept for the
                # extractor to judge, like one Claude's folder name can't rule out.
                if under:
                    cwd = codex_session_cwd(path)
                    if cwd is not None and not cwd_within(cwd, under):
                        continue
                yield path
    except OSError as error:
        raise RuntimeError(f"cannot read Codex sessions directory {sessions}: {error}") from error


def sorted_records(sources: Sequence[Tuple[str, Path]],
                   under: Optional[str] = None) -> List[PathRecord]:
    records: List[PathRecord] = []
    for provider, root in sources:
        paths = (claude_paths if provider == "claude" else codex_paths)(root, under)
        for path in paths:
            try:
                metadata = os.stat(path, follow_symlinks=False)
            except FileNotFoundError:
                # An active session store may rotate a transcript while enumerating.
                continue
            except OSError as error:
                raise RuntimeError(f"cannot stat session transcript {path}: {error}") from error
            if not stat.S_ISREG(metadata.st_mode):
                continue
            encoded_path = os.fsencode(path)
            records.append((metadata.st_mtime_ns, encoded_path, provider, path))
    records.sort(key=lambda record: (-record[0], record[1], record[2].encode("ascii")))
    return records


def parse_arguments(arguments: Sequence[str]) -> Tuple[Sequence[Tuple[str, Path]], Optional[str]]:
    parser = argparse.ArgumentParser(
        description="emit NUL-delimited provider/path pairs, newest first",
        usage="%(prog)s [--under DIR] PROVIDER ROOT [PROVIDER ROOT ...]",
    )
    parser.add_argument("--under", metavar="DIR",
                        help="leave out transcripts known to come from outside DIR")
    parser.add_argument("sources", nargs="+")
    parsed = parser.parse_args(arguments)
    if len(parsed.sources) % 2:
        parser.error("each provider must be followed by its session root")
    sources = []
    for offset in range(0, len(parsed.sources), 2):
        provider, root = parsed.sources[offset:offset + 2]
        if provider not in ("claude", "codex"):
            parser.error(f"unsupported provider: {provider}")
        sources.append((provider, Path(root)))
    return sources, parsed.under


def parse_sources(arguments: Sequence[str]) -> Sequence[Tuple[str, Path]]:
    """The provider/root pairs alone, for the fork lister, which walks whole stores."""
    return parse_arguments(arguments)[0]


def main(arguments: Sequence[str] = None) -> int:
    sources, under = parse_arguments(sys.argv[1:] if arguments is None else arguments)
    try:
        records = sorted_records(sources, under)
    except RuntimeError as error:
        print(f"session_search_paths: {error}", file=sys.stderr)
        return 1
    output = sys.stdout.buffer
    for _, encoded_path, provider, _ in records:
        output.write(provider.encode("ascii") + b"\0" + encoded_path + b"\0")
    return 0


if __name__ == "__main__":
    # Ctrl+C ends the search without a traceback; the calling script reports it.
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    raise SystemExit(main())
