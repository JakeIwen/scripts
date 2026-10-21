#!/usr/bin/python3
"""Identify LLM sessions responsible for uncommitted files in a Git checkout.

The tool compares porcelain status with recent Claude Code and Codex tool calls,
rolls subagents up to resumable parents, locates open sessions, and prints resume
commands for closed ones. Tool results are ignored. Standard library only; runs
on the MacBook (/usr/bin/python3 3.9) and the Pi.
"""
# codeshape: allow-size one file because flat Mac/Pi deployment must also work
# under python3 -I, where a sibling module is not importable.

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
from typing import Iterator

HOME = Path(os.environ.get("UNCOMMITTED_OWNERS_HOME", str(Path.home())))
CLAUDE_HOME = Path(os.environ.get("UNCOMMITTED_OWNERS_CLAUDE_HOME", str(HOME / ".claude")))
CODEX_HOME = Path(os.environ.get("UNCOMMITTED_OWNERS_CODEX_HOME", str(HOME / ".codex")))
LLM_RESTORE = Path(os.environ.get("UNCOMMITTED_OWNERS_LLM_RESTORE", str(Path(__file__).resolve().parent / "llm_restore.py")))
DEFAULT_DAYS = 21
EDIT_TOOLS = {"edit", "write", "multiedit", "notebookedit"}
STRENGTH_RANK = {None: 0, "name": 1, "mention": 2, "edit": 3}
ROLLOUT_RE = re.compile(r"rollout-.*-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")
PATCH_RE = re.compile(r"\*\*\* (?:Add File|Update File|Delete File|Move to):[ \t]*(.*?)(?=\\[rn]|\r|\n|$)")
JOB_SUFFIX_RE = re.compile(r' \(".*"\)$')


@dataclass(frozen=True)
class DirtyFile:
    """One uncommitted repository path and its attribution metadata."""

    path: str
    status: str
    mtime: float
    last_commit: float
    previous_path: str | None = None
    size: int = 0
    additions: int | None = None
    deletions: int | None = None
    strength: str | None = None


@dataclass
class SessionOwner:
    """One resumable parent session associated with one or more dirty files."""

    tool: str
    session_id: str
    title: str = ""
    cwd: str = ""
    last_event: float = 0.0
    last_user_prompt: str = ""
    last_assistant_text: str = ""
    files: list[DirtyFile] = field(default_factory=list)
    is_open: bool | None = False
    pid: int | None = None
    status: str | None = None
    location: str | None = None
    resume_command: str = ""


class ToolError(RuntimeError):
    pass


def _run(command: list[str], cwd: Path | None = None, check: bool = True, text: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=text)
    if check and result.returncode:
        stderr = result.stderr.strip() if text else result.stderr.decode(errors="replace").strip()
        raise ToolError(stderr or f"{' '.join(command)} exited {result.returncode}")
    return result


def _repo_root(value: str | None) -> Path:
    start = Path(value).expanduser() if value else Path.cwd()
    result = _run(["git", "-C", str(start), "rev-parse", "--show-toplevel"])
    return Path(result.stdout.strip()).resolve()


def _last_commit(repo: Path, paths: list[str]) -> float:
    newest = 0
    for path in paths:
        result = _run(["git", "-C", str(repo), "log", "-1", "--format=%ct", "--", path])
        text = result.stdout.strip()
        if text.isdigit():
            newest = max(newest, int(text))
    return float(newest)


def _numstat(repo: Path, paths: list[str]) -> tuple[int | None, int | None]:
    has_head = _run(["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"], check=False).returncode == 0
    commands = (["git", "-C", str(repo), "diff", "--numstat", "HEAD", "--", *paths],) if has_head else (
        ["git", "-C", str(repo), "diff", "--numstat", "--cached", "--", *paths],
        ["git", "-C", str(repo), "diff", "--numstat", "--", *paths],
    )
    additions = deletions = 0
    found = False
    for command in commands:
        for line in _run(command).stdout.splitlines():
            columns = line.split("\t", 2)
            if len(columns) < 2:
                continue
            if not columns[0].isdigit() or not columns[1].isdigit():
                return None, None
            additions += int(columns[0])
            deletions += int(columns[1])
            found = True
    return (additions, deletions) if found else (None, None)


def _status_records(raw: bytes) -> Iterator[tuple[str, str, str | None]]:
    records = raw.split(b"\0")
    index = 0
    while index < len(records) and records[index]:
        record = os.fsdecode(records[index])
        index += 1
        status, path = record[:2], record[3:]
        previous = None
        if "R" in status or "C" in status:
            if index < len(records):
                previous = os.fsdecode(records[index])
                index += 1
        yield status, path, previous


def dirty_files(repo: Path) -> list[DirtyFile]:
    result = _run(["git", "-C", str(repo), "status", "--porcelain=v1", "-z", "--untracked-files=all"], text=False)
    files = []
    for status, path, previous in _status_records(result.stdout):
        full = repo / path
        try:
            stat = full.lstat()
            mtime, size = stat.st_mtime, stat.st_size
        except OSError:
            mtime = size = 0
        history_paths = [path] + ([previous] if previous else [])
        additions, deletions = _numstat(repo, history_paths)
        files.append(DirtyFile(path, status, mtime, _last_commit(repo, history_paths),
                               previous, size, additions, deletions))
    return files


def _timestamp(value) -> float:
    if isinstance(value, (int, float)):
        return float(value / 1000 if value > 10_000_000_000 else value)
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _compact(value: str, limit: int = 300) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _block_text(content, kinds: set[str]) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    values = []
    for block in content:
        if isinstance(block, dict) and block.get("type") in kinds and isinstance(block.get("text"), str):
            values.append(block["text"])
    return "\n".join(values)


def _strings(value) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


def _parse_json_lines(raw: bytes, markers: tuple[bytes, ...] = (), hints: list[bytes] | None = None) -> Iterator[dict]:
    for line in raw.splitlines():
        if markers and not any(marker in line for marker in markers):
            continue
        if hints and not any(hint in line for hint in hints):
            continue
        try:
            event = json.loads(line)
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(event, dict):
            yield event


def _claude_transcript(raw: bytes, hints: list[bytes] | None = None) -> tuple[dict, list[tuple[float, str, object]]]:
    details = {"cwd": "", "title": "", "last_event": 0.0, "user": "", "assistant": ""}
    calls = []
    markers = (b'"tool_use"',) if hints else ()
    for event in _parse_json_lines(raw, markers, hints):
        when = _timestamp(event.get("timestamp"))
        details["last_event"] = max(details["last_event"], when)
        if not details["cwd"] and isinstance(event.get("cwd"), str):
            details["cwd"] = event["cwd"]
        event_type = event.get("type")
        if event_type == "custom-title" and event.get("customTitle"):
            details["title"] = _compact(str(event["customTitle"]), 160)
        elif event_type == "ai-title" and event.get("aiTitle"):
            details["title"] = _compact(str(event["aiTitle"]), 160)
        elif event_type == "summary":
            title = event.get("summary") or event.get("title")
            if isinstance(title, str) and title:
                details["title"] = _compact(title, 160)
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        if event_type == "user" and not event.get("isMeta"):
            text = _block_text(message.get("content"), {"text"})
            if text:
                details["user"] = _compact(text)
        if event_type != "assistant":
            continue
        text = _block_text(message.get("content"), {"text"})
        if text:
            details["assistant"] = _compact(text)
        for block in message.get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append((when, str(block.get("name") or ""), block.get("input", {})))
    return details, calls


def _codex_transcript(raw: bytes, hints: list[bytes] | None = None) -> tuple[dict, list[tuple[float, str, object]]]:
    details = {"cwd": "", "title": "", "last_event": 0.0, "user": "", "assistant": ""}
    calls = []
    markers = (b'"function_call"', b'"custom_tool_call"') if hints else ()
    for event in _parse_json_lines(raw, markers, hints):
        when = _timestamp(event.get("timestamp"))
        details["last_event"] = max(details["last_event"], when)
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event.get("type") == "session_meta":
            if isinstance(payload.get("cwd"), str):
                details["cwd"] = payload["cwd"]
            continue
        if event.get("type") != "response_item":
            continue
        item_type = payload.get("type")
        if item_type in ("function_call", "custom_tool_call"):
            call_input = payload.get("input") if "input" in payload else payload.get("arguments", "")
            calls.append((when, str(payload.get("name") or ""), call_input))
        elif item_type == "message" and payload.get("role") in ("user", "assistant"):
            text = _block_text(payload.get("content"), {"input_text", "output_text"})
            if text:
                details["user" if payload["role"] == "user" else "assistant"] = _compact(text)
    return details, calls


def _codex_meta(raw: bytes) -> dict:
    for event in _parse_json_lines(raw):
        if event.get("type") == "session_meta" and isinstance(event.get("payload"), dict):
            return event["payload"]
    return {}


def _claude_meta(raw: bytes) -> dict:
    for event in _parse_json_lines(raw):
        if isinstance(event.get("cwd"), str):
            return {"cwd": event["cwd"]}
    return {}


def _source_parent(source) -> str | None:
    if isinstance(source, str):
        try:
            source = json.loads(source)
        except ValueError:
            return None
    if not isinstance(source, dict):
        return None
    subagent = source.get("subagent")
    spawn = subagent.get("thread_spawn", {}) if isinstance(subagent, dict) else {}
    return spawn.get("parent_thread_id") if isinstance(spawn, dict) else None


def _codex_parent(meta: dict) -> str | None:
    parent = meta.get("parent_thread_id") or _source_parent(meta.get("source"))
    sid, own = meta.get("session_id"), meta.get("id")
    if not parent and meta.get("thread_source") == "subagent" and sid and sid != own:
        parent = sid
    return parent if isinstance(parent, str) else None


def _codex_threads() -> dict[str, dict]:
    dbs = sorted(CODEX_HOME.glob("state_*.sqlite"), key=lambda p: int(re.sub(r"\D", "", p.name) or 0))
    if not dbs:
        return {}
    for uri in (f"file:{dbs[-1]}?mode=ro", f"file:{dbs[-1]}?immutable=1"):
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
                columns = {row[1] for row in db.execute("pragma table_info(threads)")}
                title = "coalesce(name, title, '')" if "name" in columns else "coalesce(title, '')"
                selected = ["id", "cwd", f"{title} as title", "updated_at", "source", "thread_source"]
                selected.append("rollout_path" if "rollout_path" in columns else "'' as rollout_path")
                rows = db.execute("select " + ", ".join(selected) + " from threads").fetchall()
            keys = ("id", "cwd", "title", "updated", "source", "thread_source", "rollout_path")
            return {row[0]: dict(zip(keys, row)) for row in rows}
        except sqlite3.Error:
            continue
    return {}


def _inside(path: str, root: Path) -> bool:
    try:
        Path(path).expanduser().resolve().relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def _call_hints(_cwd: str, _repo: Path, relative_names: list[str]) -> list[bytes]:
    return [os.fsencode(name) for name in sorted({Path(value).name for value in relative_names})]


def _call_path(value: str, cwd: str) -> Path:
    if value == "~" or value.startswith("~/"):
        value = str(HOME) + value[1:]
    path = Path(value)
    return (path if path.is_absolute() else Path(cwd) / path).resolve()


def _path_aliases(dirty: DirtyFile, repo: Path) -> tuple[set[Path], list[str], re.Pattern]:
    names = [dirty.path] + ([dirty.previous_path] if dirty.previous_path else [])
    resolved = {(repo / name).resolve() for name in names}
    mentions = names + [str(repo / name) for name in names]
    basenames = sorted({Path(name).name for name in names})
    token = re.compile(r"(?<![\w.-])(?:" + "|".join(re.escape(name) for name in basenames) + r")(?![\w.-])")
    return resolved, mentions, token


def _call_evidence(call: tuple[float, str, object], tool: str,
                   cwd: str) -> tuple[float, set[Path], str]:
    when, name, call_input = call
    direct = []
    if tool == "claude" and name.lower() in EDIT_TOOLS and isinstance(call_input, dict):
        for key in ("file_path", "notebook_path"):
            if isinstance(call_input.get(key), str):
                direct.append(call_input[key])
    text = "\n".join(_strings(call_input))
    direct += [match.strip().strip("'\"") for match in PATCH_RE.findall(text)]
    resolved = set()
    for value in direct:
        try:
            resolved.add(_call_path(value, cwd))
        except (OSError, ValueError):
            continue
    return when, resolved, text


def _strength(evidence: list[tuple[float, set[Path], str]], dirty: DirtyFile,
              aliases: tuple[set[Path], list[str], re.Pattern]) -> str | None:
    resolved, mentions, name_token = aliases
    best = None
    for when, edited, text in evidence:
        if when <= dirty.last_commit:
            continue
        if edited & resolved:
            return "edit"
        if any(value and value in text for value in mentions):
            best = "mention"
        elif best is None and name_token.search(text):
            best = "name"
    return best


def _root_id(session_id: str, parents: dict[str, str]) -> str:
    seen = set()
    while session_id in parents and session_id not in seen:
        seen.add(session_id)
        session_id = parents[session_id]
    return session_id


def _recent_paths(paths: list[Path], cutoff: float) -> dict[Path, float]:
    recent = {}
    for path in paths:
        try:
            modified = path.stat().st_mtime
            if modified >= cutoff:
                recent[path] = modified
        except OSError:
            pass
    return recent


def _select_candidates(paths: list[Path], local_paths: list[Path], repo: Path) -> tuple[set[Path], dict[Path, bytes]]:
    root = os.fsencode(str(repo))
    selected, cache = set(local_paths), {}
    for path in paths:
        if path in selected:
            continue
        raw = _read_bytes(path)
        if raw is not None and root in raw:
            selected.add(path)
            cache[path] = raw
    return selected, cache


def _file_cwd(path: Path, tool: str) -> str:
    try:
        with path.open("rb") as stream:
            for _, line in zip(range(30), stream):
                try:
                    event = json.loads(line)
                except (UnicodeDecodeError, ValueError):
                    continue
                if tool == "claude" and isinstance(event.get("cwd"), str):
                    return event["cwd"]
                payload = event.get("payload")
                if tool == "codex" and event.get("type") == "session_meta" and isinstance(payload, dict):
                    return payload.get("cwd") or ""
    except OSError:
        pass
    return ""


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _custom_claude_title(parent_path: Path | None, session_id: str) -> str:
    if parent_path is None:
        return ""
    title_path = parent_path.parent / session_id / "custom-title.json"
    try:
        value = json.loads(title_path.read_text()).get("customTitle")
        return _compact(value, 160) if isinstance(value, str) else ""
    except (OSError, ValueError, AttributeError):
        return ""


def _resume_command(tool: str, cwd: str, session_id: str) -> str:
    # Kept identical to llm_restore's shell_command shape; this tool cannot import
    # its sibling when deployed flat and run with python3 -I on the Pi.
    words = (["claude", "--permission-mode", "bypassPermissions", "--resume", session_id]
             if tool == "claude" else ["codex", "resume", session_id])
    return f"cd -- {shlex.quote(cwd)} && command " + " ".join(shlex.quote(word) for word in words)


def _owner(tool: str, session_id: str, details: dict, title: str, files: list[DirtyFile],
           fallback_cwd: str, last_event: float) -> SessionOwner:
    cwd = details.get("cwd") or fallback_cwd
    return SessionOwner(tool, session_id, title or details.get("title", ""), cwd,
                        max(details.get("last_event", 0), last_event), details.get("user", ""),
                        details.get("assistant", ""), sorted(files, key=lambda item: item.path),
                        resume_command=_resume_command(tool, cwd, session_id) if cwd else "")


def _transcript_records(repo: Path, files: list[DirtyFile], cutoff: float, codex_threads: dict[str, dict],
                        parents: dict[str, str]) -> tuple[list, dict]:
    records, parent_paths = [], {}
    claude_paths = list((CLAUDE_HOME / "projects").glob("*/*.jsonl"))
    claude_paths += list((CLAUDE_HOME / "projects").glob("*/*/subagents/*.jsonl"))
    codex_paths = list((CODEX_HOME / "sessions").glob("**/rollout-*.jsonl"))
    recent = _recent_paths(claude_paths + codex_paths, cutoff)
    codex_by_id = {match.group(1): path for path in codex_paths if path in recent
                   if (match := ROLLOUT_RE.search(path.name))}
    local_paths = []
    claude_set = set(claude_paths)
    for path in recent:
        if path in claude_set:
            cwd = _file_cwd(path, "claude")
        else:
            match = ROLLOUT_RE.search(path.name)
            cwd = codex_threads.get(match.group(1) if match else "", {}).get("cwd") or _file_cwd(path, "codex")
        if cwd and _inside(cwd, repo):
            local_paths.append(path)
    selected, raw_cache = _select_candidates(list(recent), local_paths, repo)
    for path in claude_paths:
        if path not in recent:
            continue
        is_subagent = path.parent.name == "subagents"
        session_id = path.parent.parent.name if is_subagent else path.stem
        if not is_subagent:
            parent_paths[("claude", session_id)] = path
        if path not in selected:
            continue
        raw = raw_cache.get(path) or _read_bytes(path)
        if raw is not None:
            records.append(("claude", session_id, session_id, path, raw,
                            _claude_meta(raw), recent[path]))
    for path in codex_paths:
        if path not in recent:
            continue
        match = ROLLOUT_RE.search(path.name)
        if path not in selected:
            continue
        raw = raw_cache.get(path) or _read_bytes(path)
        if raw is None:
            continue
        meta = _codex_meta(raw)
        session_id = meta.get("id") or meta.get("session_id") or (match.group(1) if match else path.stem)
        parent = _codex_parent(meta)
        if parent:
            parents[session_id] = parent
        root_id = _root_id(session_id, parents)
        parent_paths[("codex", root_id)] = codex_by_id.get(root_id, path if not parent else None)
        records.append(("codex", session_id, root_id, path, raw, meta, recent[path]))
    return records, parent_paths


def find_owners(repo: Path, files: list[DirtyFile], days: int) -> list[SessionOwner]:
    cutoff = time.time() - days * 86400
    oldest_relevant = min((dirty.last_commit or dirty.mtime) for dirty in files)
    cutoff = max(cutoff, oldest_relevant)
    codex_threads = _codex_threads()
    parents = {tid: parent for tid, info in codex_threads.items()
               if (parent := _source_parent(info.get("source")))}
    records, parent_paths = _transcript_records(repo, files, cutoff, codex_threads, parents)
    details_cache = {}
    hits: dict[tuple[str, str], dict[str, str]] = {}
    context: dict[tuple[str, str], dict] = {}
    aliases = {dirty.path: _path_aliases(dirty, repo) for dirty in files}
    relative_names = [name for dirty in files for name in (dirty.path, dirty.previous_path) if name]
    hints = _call_hints("", repo, relative_names)
    for tool, session_id, initial_root, path, raw, meta, modified in records:
        root_id = _root_id(session_id, parents) if tool == "codex" else initial_root
        cwd = meta.get("cwd", "")
        parser = _claude_transcript if tool == "claude" else _codex_transcript
        details, calls = parser(raw, hints)
        if not calls:
            continue
        key = (tool, root_id)
        event_time = max((call[0] for call in calls), default=modified)
        info = context.setdefault(key, {"cwd": cwd, "last_event": event_time})
        info["last_event"] = max(info["last_event"], event_time)
        evidence = [_call_evidence(call, tool, details.get("cwd") or cwd) for call in calls]
        for dirty in files:
            best = _strength(evidence, dirty, aliases[dirty.path])
            if best:
                current = hits.setdefault(key, {}).get(dirty.path)
                if STRENGTH_RANK[best] > STRENGTH_RANK[current]:
                    hits[key][dirty.path] = best
    strong = {name for strengths in hits.values() for name, strength in strengths.items()
              if strength in ("edit", "mention")}
    name_winners = {}
    for dirty in files:
        if dirty.path in strong:
            continue
        candidates = [key for key, strengths in hits.items() if strengths.get(dirty.path) == "name"]
        if not candidates:
            continue
        parent = Path(dirty.path).parent
        def score(key):
            sibling_evidence = sum(Path(name).parent == parent and strength in ("edit", "mention")
                                   for name, strength in hits[key].items())
            return (_inside(context[key]["cwd"], repo), sibling_evidence,
                    context[key]["last_event"], key)
        name_winners[dirty.path] = max(candidates, key=score)
    for key, strengths in list(hits.items()):
        hits[key] = {name: strength for name, strength in strengths.items()
                     if strength != "name" or name_winners.get(name) == key}
        if not hits[key]:
            del hits[key]
    by_path = {dirty.path: dirty for dirty in files}
    owners = []
    for (tool, session_id), strengths in hits.items():
        info = context[(tool, session_id)]
        path = parent_paths.get((tool, session_id))
        if path is None and tool == "codex":
            candidate = Path(codex_threads.get(session_id, {}).get("rollout_path") or "")
            path = candidate if candidate.is_file() else None
        if path and path not in details_cache:
            try:
                raw = path.read_bytes()
                details_cache[path] = (_claude_transcript(raw) if tool == "claude" else _codex_transcript(raw))[0]
            except OSError:
                pass
        details = details_cache.get(path, {"cwd": "", "title": "", "last_event": 0,
                                           "user": "", "assistant": ""})
        title = ""
        fallback_cwd = info["cwd"]
        if tool == "claude":
            title = _custom_claude_title(path, session_id)
        else:
            thread = codex_threads.get(session_id, {})
            title, fallback_cwd = thread.get("title") or "", thread.get("cwd") or fallback_cwd
        owned = [replace(by_path[name], strength=strength) for name, strength in strengths.items()]
        owners.append(_owner(tool, session_id, details, title, owned,
                             fallback_cwd, info["last_event"]))
    return owners


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False


def _claude_open() -> dict[str, dict]:
    found = {}
    for path in (CLAUDE_HOME / "sessions").glob("*.json"):
        try:
            pid = int(path.stem)
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        session_id = data.get("sessionId")
        if session_id and _pid_alive(pid):
            found[session_id] = {"pid": pid, "status": data.get("status"), "name": data.get("name") or ""}
    return found


def _codex_open() -> tuple[dict[str, dict], bool]:
    try:
        result = subprocess.run([sys.executable, "-I", "-B", str(LLM_RESTORE), "--list", "--json"],
                                capture_output=True, text=True, timeout=10)
        if result.returncode:
            return {}, False
        data = json.loads(result.stdout)
        records = data.get("sessions", []) if isinstance(data, dict) else data
        return {record["id"]: record for record in records if record.get("tool") == "codex"}, True
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}, False


def _tty(pid: int) -> str | None:
    try:
        text = _run(["/bin/ps", "-o", "tty=", "-p", str(pid)]).stdout.strip()
        return text if text and text != "??" else None
    except (ToolError, OSError):
        return None


def _iterm_locations() -> dict[str, str]:
    script = '''set resultText to ""
tell application "iTerm2"
repeat with w in windows
repeat with t in tabs of w
repeat with s in sessions of t
set resultText to resultText & (tty of s) & (ASCII character 9) & (name of s) & linefeed
end repeat
end repeat
end repeat
end tell
return resultText'''
    try:
        result = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True,
                                text=True, timeout=5)
        if result.returncode:
            return {}
        locations = {}
        for line in result.stdout.splitlines():
            tty, separator, name = line.partition("\t")
            if separator:
                locations[tty.removeprefix("/dev/")] = JOB_SUFFIX_RE.sub("", name)
        return locations
    except (OSError, subprocess.TimeoutExpired):
        return {}


def _tmux_locations() -> dict[str, str]:
    if not shutil.which("tmux"):
        return {}
    try:
        result = _run(["tmux", "list-panes", "-a", "-F",
                       "#{pane_tty} #{session_name}:#{window_index} #{window_name}"], check=False)
        locations = {}
        for line in result.stdout.splitlines():
            tty, separator, name = line.partition(" ")
            if separator:
                locations[tty.removeprefix("/dev/")] = name
        return locations
    except OSError:
        return {}


def _reassign_codex_pids(codex: dict[str, dict], pid_locations: dict[int, str]) -> None:
    """Apply only title-proven pid permutations, leaving incomplete chains alone."""
    original = {int(record["pid"]): session_id for session_id, record in codex.items()}
    claims = {}
    for session_id, record in codex.items():
        title = record.get("title") or ""
        matches = [pid for pid in original if title and title in (pid_locations.get(pid) or "")]
        if len(matches) == 1:
            claims[session_id] = matches[0]
    duplicated = {pid for pid in claims.values() if list(claims.values()).count(pid) > 1}
    claims = {session_id: pid for session_id, pid in claims.items() if pid not in duplicated}
    for start in claims:
        chain, current = [], start
        while current in claims and current not in chain:
            chain.append(current)
            current = original.get(claims[current], "")
        if current == start:
            for session_id in chain:
                codex[session_id]["pid"] = claims[session_id]


def enrich_open_state(owners: list[SessionOwner]) -> None:
    claude, (codex, codex_known) = _claude_open(), _codex_open()
    records = [*claude.values(), *codex.values()]
    ttys = {int(record["pid"]): _tty(int(record["pid"])) for record in records}
    locations = _iterm_locations() if sys.platform == "darwin" else _tmux_locations()
    pid_locations = {pid: locations.get((tty or "").removeprefix("/dev/")) or tty
                     for pid, tty in ttys.items()}
    _reassign_codex_pids(codex, pid_locations)
    for owner in owners:
        record = claude.get(owner.session_id) if owner.tool == "claude" else codex.get(owner.session_id)
        owner.is_open = bool(record) if owner.tool == "claude" or codex_known else None
        if record:
            owner.pid = int(record["pid"])
            owner.status = record.get("status") or "open"
            owner.location = pid_locations.get(owner.pid)
            if not owner.title:
                owner.title = record.get("name") or record.get("title") or ""


def _file_dict(dirty: DirtyFile) -> dict:
    return asdict(dirty)


def _owner_dict(owner: SessionOwner) -> dict:
    data = asdict(owner)
    data["open"] = data.pop("is_open")
    data["files"] = [_file_dict(item) for item in owner.files]
    return data


def _clock(value: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(value)) if value else "never"


def _print_file(dirty: DirtyFile, indent: str = "  ") -> None:
    change = (f"+{dirty.additions}/-{dirty.deletions}" if dirty.additions is not None
              else f"{dirty.size} bytes")
    strength = f"{dirty.strength:7} " if dirty.strength else ""
    print(f"{indent}{strength}{dirty.status} {dirty.path}  "
          f"mtime {_clock(dirty.mtime)}; commit {_clock(dirty.last_commit)}; {change}")


def print_text(repo: Path, owners: list[SessionOwner], unattributed: list[DirtyFile]) -> None:
    print(f"Repository: {repo}")
    groups = ((True, "Open sessions"), (None, "Open status unknown"), (False, "Closed sessions"))
    for state, heading in groups:
        sessions = [owner for owner in owners if owner.is_open is state]
        if not sessions:
            continue
        print(f"\n{heading}")
        for owner in sessions:
            print(f"{owner.tool} {owner.title or owner.session_id}")
            print(f"  id {owner.session_id} | cwd {owner.cwd} | last {_clock(owner.last_event)}")
            if state is True:
                where = f" | {owner.location}" if owner.location else ""
                print(f"  {owner.status or 'open'} | pid {owner.pid}{where}")
            elif owner.resume_command:
                label = "resume if closed" if state is None else "resume"
                print(f"  {label}: {owner.resume_command}")
            if owner.last_user_prompt:
                print(f"  user: {owner.last_user_prompt}")
            if owner.last_assistant_text:
                print(f"  assistant: {owner.last_assistant_text}")
            for dirty in owner.files:
                _print_file(dirty, "    ")
    print("\nUnattributed files")
    if unattributed:
        for dirty in unattributed:
            _print_file(dirty)
    else:
        print("  (none)")


def _days(value: str) -> int:
    try:
        days = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a whole number") from exc
    if days < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return days


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("repo", nargs="?", metavar="REPO",
                        help="Git checkout (default: checkout containing the current directory)")
    parser.add_argument("--days", type=_days, default=DEFAULT_DAYS,
                        help=f"consider transcripts modified this many days ago (default {DEFAULT_DAYS})")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    args = parser.parse_args()
    try:
        repo = _repo_root(args.repo)
        files = dirty_files(repo)
        owners = find_owners(repo, files, args.days) if files else []
        enrich_open_state(owners)
    except ToolError as exc:
        parser.error(str(exc))
    rank = {True: 0, None: 1, False: 2}
    owners.sort(key=lambda owner: (rank[owner.is_open], -owner.last_event, owner.tool, owner.session_id))
    attributed = {dirty.path for owner in owners for dirty in owner.files}
    unattributed = [dirty for dirty in files if dirty.path not in attributed]
    if args.json:
        print(json.dumps({"repo": str(repo), "sessions": [_owner_dict(owner) for owner in owners],
                          "unattributed": [_file_dict(dirty) for dirty in unattributed]},
                         indent=2, sort_keys=True))
    else:
        print_text(repo, owners, unattributed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
