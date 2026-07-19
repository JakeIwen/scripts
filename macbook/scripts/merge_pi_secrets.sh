#!/bin/bash
# Merge pi/secrets/.bash_variables across ~/dev/scripts* clones.
set -euo pipefail

python_bin=${PYTHON3:-$(command -v python3 || true)}
if [ -z "$python_bin" ]; then
  echo "merge_pi_secrets: python3 not found" >&2
  exit 1
fi

# Preserve the caller's stdin for conflict prompts; Python reads its program
# from the heredoc on stdin.
exec 3<&0
exec "$python_bin" - "$@" <<'PY'
from __future__ import annotations

import argparse
import fcntl
import functools
import hashlib
import operator
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


EXPORT_RE = re.compile(r"^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
PROMPT_INPUT = os.fdopen(3, "r", encoding="utf-8", closefd=False)


class MergeError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceSnapshot:
    repo: Path
    path: Path
    content: bytes | None
    mode: int | None


@dataclass(frozen=True)
class Candidate:
    value: str
    origins: tuple[str, ...]


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge pi/secrets/.bash_variables across ~/dev/scripts* clones."
    )
    parser.add_argument(
        "--dev-dir",
        type=Path,
        default=Path.home() / "dev",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="resolve and report without writing"
    )
    return parser.parse_args()


def discover(dev_dir: Path) -> list[SourceSnapshot]:
    repos = sorted(
        path
        for path in dev_dir.glob("scripts*")
        if path.is_dir() and not path.name.endswith(".bak")
    )
    if not repos:
        raise MergeError(f"no non-.bak scripts* directories found under {dev_dir}")

    snapshots: list[SourceSnapshot] = []
    for repo in repos:
        path = repo / "pi" / "secrets" / ".bash_variables"
        try:
            content = path.read_bytes()
            mode = path.stat().st_mode & 0o777
        except FileNotFoundError:
            content = None
            mode = None
        except OSError as error:
            raise MergeError(f"could not read {path}: {error}") from error
        snapshots.append(SourceSnapshot(repo, path, content, mode))

    if not any(snapshot.content is not None for snapshot in snapshots):
        raise MergeError("none of the discovered repositories has a secrets file")
    return snapshots


def collect(
    snapshots: list[SourceSnapshot],
) -> tuple[list[str], dict[str, list[Candidate]]]:
    key_order: list[str] = []
    values: dict[str, dict[str, list[str]]] = {}
    for snapshot in snapshots:
        if snapshot.content is None:
            continue
        try:
            text = snapshot.content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise MergeError(f"{snapshot.path} is not valid UTF-8") from error
        for line_number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = EXPORT_RE.match(line)
            if not match:
                raise MergeError(
                    f"{snapshot.path}:{line_number}: unsupported line; "
                    "expected 'export NAME=value'"
                )
            key, value = match.groups()
            if key not in values:
                values[key] = {}
                key_order.append(key)
            origin = f"{snapshot.repo.name} (line {line_number})"
            values[key].setdefault(value, []).append(origin)

    candidates = {
        key: [Candidate(value, tuple(origins)) for value, origins in choices.items()]
        for key, choices in values.items()
    }
    return key_order, candidates


def choose_value(key: str, choices: list[Candidate]) -> str:
    if len(choices) == 1:
        return choices[0].value

    print(f"\nConflict for {key}:")
    for index, candidate in enumerate(choices, 1):
        print(f"  {index}) {candidate.value}")
        print(f"     from: {', '.join(candidate.origins)}")
    while True:
        print(f"Select value for {key} [1-{len(choices)}]: ", end="", flush=True)
        answer = PROMPT_INPUT.readline()
        if answer == "":
            raise MergeError(f"conflict for {key} requires interactive input")
        answer = answer.strip()
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return choices[int(answer) - 1].value
        print("Invalid selection.", file=sys.stderr)


def strictest_mode(snapshots: list[SourceSnapshot]) -> int:
    modes = [snapshot.mode for snapshot in snapshots if snapshot.mode is not None]
    if not modes:
        raise MergeError("no existing permissions to preserve")
    return functools.reduce(operator.and_, modes)


def verify_unchanged(snapshots: list[SourceSnapshot]) -> None:
    for snapshot in snapshots:
        try:
            current = snapshot.path.read_bytes()
        except FileNotFoundError:
            current = None
        except OSError as error:
            raise MergeError(f"could not recheck {snapshot.path}: {error}") from error
        if current != snapshot.content:
            raise MergeError(f"source changed during merge: {snapshot.path}")


def stage_files(
    snapshots: list[SourceSnapshot], content: bytes, mode: int
) -> list[tuple[Path, Path]]:
    staged: list[tuple[Path, Path]] = []
    try:
        for snapshot in snapshots:
            snapshot.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".bash_variables.merge.", dir=snapshot.path.parent
            )
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(temporary_path, mode)
            except BaseException:
                temporary_path.unlink(missing_ok=True)
                raise
            staged.append((temporary_path, snapshot.path))
    except BaseException:
        for temporary_path, _ in staged:
            temporary_path.unlink(missing_ok=True)
        raise
    return staged


def install_staged(staged: list[tuple[Path, Path]]) -> None:
    try:
        for temporary_path, destination in staged:
            os.replace(temporary_path, destination)
    finally:
        for temporary_path, _ in staged:
            temporary_path.unlink(missing_ok=True)


def main() -> int:
    args = arguments()
    lock_path = Path(tempfile.gettempdir()) / f"merge_pi_secrets.{os.getuid()}.lock"
    with lock_path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        snapshots = discover(args.dev_dir.expanduser())
        key_order, candidates = collect(snapshots)
        selected = {
            key: choose_value(key, candidates[key])
            for key in key_order
        }
        output = "".join(f"export {key}={selected[key]}\n" for key in key_order).encode()
        mode = strictest_mode(snapshots)

        included = ", ".join(snapshot.repo.name for snapshot in snapshots)
        missing = [snapshot.repo.name for snapshot in snapshots if snapshot.content is None]
        print(f"Repositories: {included}")
        print(f"Variables: {len(selected)}")
        print(f"Output permissions: {mode:03o}")
        if missing:
            print(f"New files: {', '.join(missing)}")
        if args.dry_run:
            print("Dry run: no files changed")
            return 0

        verify_unchanged(snapshots)
        staged = stage_files(snapshots, output, mode)
        install_staged(staged)
        print(f"Updated {len(snapshots)} secrets files")
    return 0


try:
    raise SystemExit(main())
except MergeError as error:
    print(f"merge_pi_secrets: {error}", file=sys.stderr)
    raise SystemExit(1)
PY
