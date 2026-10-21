#!/usr/bin/env python3
"""List Claude and Codex session transcripts in newest-activity order."""

import argparse
import os
from pathlib import Path
import stat
import sys
from typing import Iterable, List, Sequence, Tuple


PathRecord = Tuple[int, bytes, str, Path]


def claude_paths(root: Path) -> Iterable[Path]:
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


def codex_paths(root: Path) -> Iterable[Path]:
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
                if not path.is_symlink():
                    yield path
    except OSError as error:
        raise RuntimeError(f"cannot read Codex sessions directory {sessions}: {error}") from error


def sorted_records(sources: Sequence[Tuple[str, Path]]) -> List[PathRecord]:
    records: List[PathRecord] = []
    for provider, root in sources:
        paths = claude_paths(root) if provider == "claude" else codex_paths(root)
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


def parse_sources(arguments: Sequence[str]) -> Sequence[Tuple[str, Path]]:
    parser = argparse.ArgumentParser(
        description="emit NUL-delimited provider/path pairs, newest first",
        usage="%(prog)s PROVIDER ROOT [PROVIDER ROOT ...]",
    )
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
    return sources


def main(arguments: Sequence[str] = None) -> int:
    sources = parse_sources(sys.argv[1:] if arguments is None else arguments)
    try:
        records = sorted_records(sources)
    except RuntimeError as error:
        print(f"session_search_paths: {error}", file=sys.stderr)
        return 1
    output = sys.stdout.buffer
    for _, encoded_path, provider, _ in records:
        output.write(provider.encode("ascii") + b"\0" + encoded_path + b"\0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
