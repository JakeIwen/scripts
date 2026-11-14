#!/usr/bin/python3
"""Send a reply longer than one terminal screen back to be moved to a page.

The global "Long replies" rule puts any reply past about 50 rows or 450 words
in a local page. Agents misjudge their own length while writing, so this
Claude Code and Codex Stop hook measures the finished reply and, past either
limit with no link to a page this turn saved, blocks once with
instructions. Claude blocks are claimed per prompt in
~/.agent/long-reply-guard/ so each prompt is blocked at most once. Only replies
Jacob reads in a terminal count: headless runs (`claude -p`, `codex exec`) and
subagents pass. Failures never block. Standard library only; supports Mac
/usr/bin/python3 3.9 and Pi Python 3.11.
"""

from __future__ import annotations

import bisect
from datetime import datetime
import fcntl
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
from urllib.parse import unquote

WORD_LIMIT = 450
ROW_LIMIT = 50
# The narrower of Jacob's two iTerm tab widths (104 and 114 columns, 2026-11-02).
COLUMNS = 104
# Both agents draw reply text two columns in, under the message marker.
GUTTER = 2
ART_SCRIPT = "~/.codex/skills/codex-artifact/scripts/agent_art.py"
ARTIFACTS = os.path.join(".agent", "artifacts")
# Pi pages are linked through the Mac's mount; on the Pi that is its own store.
PI_ARTIFACTS = "/Volumes/pihome/.agent/artifacts/"
URL_END = re.compile(r"""[)\]<>"'`]""")
# The turn's start is searched for backwards through the transcript in
# CHUNK_BYTES reads, stopping at the first match or after SEARCH_SECONDS.
# The hook has 5 s; a substring scan covered a 31 MB transcript in 0.16 s,
# so one second reaches far past any transcript seen while leaving room for
# the rest of the hook. Without a start there is no exemption at all.
CHUNK_BYTES = 4 * 1024 * 1024
SEARCH_SECONDS = 1.0
CLOCK_SKEW_SECONDS = 60
LEDGER_DIR = ".agent/long-reply-guard"
LEDGER_PROMPTS = 16
LEDGER_MAX_AGE_SECONDS = 30 * 86400
# A fixed set of lock files keyed by session hash, never pruned, so a lock's
# inode never changes under a process that holds or is about to take it.
LOCK_STRIPES = 64
LOCK_ATTEMPTS = 20
LOCK_WAIT_SECONDS = 0.025
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
MARKUP = re.compile(r"\*\*|__|`|^\s*#{1,6}\s+")
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


def prose(message: str) -> list[str]:
    """Lines outside fenced blocks: the rule exempts commands and scripts
    Jacob must act on, and replies carry those in fences. A fence never
    closed is not a block, so its lines count."""
    lines, fence, fenced = [], "", []
    for line in message.splitlines():
        marker = FENCE.match(line)
        if marker:
            token = marker.group(1)
            if not fence:
                fence, fenced = token, [line]
                continue
            if token[0] == fence[0] and len(token) >= len(fence):
                fence, fenced = "", []
                continue
        if fence:
            fenced.append(line)
        else:
            lines.append(line)
    return lines + fenced


def word_count(lines: list[str]) -> int:
    # Whitespace-separated tokens holding a letter or digit, counted in one
    # pass: a regex over the same tokens backtracked quadratically on long
    # runs of punctuation (40k box-drawing characters took 10 s).
    return sum(1 for line in lines for token in line.split()
               if any(char.isalnum() for char in token))


def after(positions: list[int], index: int) -> int | None:
    found = bisect.bisect_right(positions, index)
    return positions[found] if found < len(positions) else None


def strip_links(line: str) -> str:
    """[text](destination) -> text, as the regex `\\[([^\\[\\]]+)\\]\\([^)]*\\)`
    would, in O(n log n): a regex rescanned an unclosed destination on every
    attempt, so "[a](" repeated 80k times ran past the hook's timeout."""
    if "](" not in line:
        return line
    opens, closes, parens = [], [], []
    for index, char in enumerate(line):
        if char == "[":
            opens.append(index)
        elif char == "]":
            closes.append(index)
        elif char == ")":
            parens.append(index)
    parts, done = [], 0
    for start in opens:
        if start < done:
            continue
        close = after(closes, start)
        if close is None:
            break
        inner = after(opens, start)
        if inner is not None and inner < close:
            continue  # link text holds no "["; try again from the inner one
        if close == start + 1 or line[close + 1:close + 2] != "(":
            continue
        end = after(parens, close + 1)
        if end is None:
            break
        parts += [line[done:start], line[start + 1:close]]
        done = end + 1
    return "".join(parts) + line[done:]


def wrapped_rows(text: str, width: int, indent: int) -> int:
    if len(text) <= width:
        return 1
    # Wrapped lines of a list item hang under its text, not its marker.
    return 1 + math.ceil((len(text) - width) / max(width - indent, 20))


def row_count(lines: list[str], columns: int = COLUMNS) -> int:
    """Rendered rows, estimated: markdown markers dropped, long lines wrapped,
    tables given their top and bottom borders."""
    width, total, in_table = columns - GUTTER, 0, False
    for line in lines:
        table_row = line.lstrip().startswith("|")
        if table_row != in_table:
            total += 1
            in_table = table_row
        text = MARKUP.sub("", strip_links(line)).rstrip()
        item = LIST_ITEM.match(text)
        indent = len(item.group(0)) if item else len(text) - len(text.lstrip())
        total += wrapped_rows(text, width, indent)
    return total + (1 if in_table else 0)


def codex_source(transcript: object) -> object:
    """The session_meta source Codex writes first in its rollout: "cli" for
    the TUI, "exec" for headless runs, a dict for subagents."""
    if not isinstance(transcript, str) or not transcript:
        return None
    try:
        with open(transcript, encoding="utf-8") as handle:
            first = json.loads(handle.readline())
    except (OSError, ValueError):
        return None
    payload = first.get("payload") if isinstance(first, dict) else None
    return payload.get("source") if isinstance(payload, dict) else None


def is_codex(event: dict) -> bool:
    # Payload, not environment: a codex exec started from a Claude shell
    # inherits Claude's variables.
    return "turn_id" in event


def attended(event: dict, env: dict) -> bool:
    if is_codex(event):
        return codex_source(event.get("transcript_path")) == "cli"
    state = env.get("CLAUDE_CODE_SESSION_ATTENDED")
    if state is not None:
        return state == "1"
    return env.get("CLAUDE_CODE_ENTRYPOINT") == "cli"


def reason(event: dict, words: int, rows: int) -> str:
    claude = not is_codex(event)
    agent_flag = " --agent claude" if claude else ""
    text = (
        f"Long replies rule: this reply measures about {words} words and {rows} rows, "
        f"past one terminal screen ({WORD_LIMIT} words or {ROW_LIMIT} rows; fenced blocks "
        f"don't count). Save the reply itself as a local page with "
        f"`python3 {ART_SCRIPT} save{agent_flag} --title \"<name>\"` (body on stdin; "
        f"no --hosted, no Artifact publish). Then send a short final message: anything "
        f"Jacob must act on, in full; a summary of up to five lines; the page's file:// "
        f"URL; and the Terms block, if any. Leave the reply in the terminal only if Jacob "
        f"explicitly asked for the answer there, or a script or parent agent reads it; "
        f"then reply in one line saying which."
    )
    if claude:
        armed = "+x or +ho" if sys.platform == "darwin" else "+ho"
        text += f" If you armed {armed} this turn, arm it again as your last tool call."
    return text


def page_paths(message: str, home: str) -> list[str]:
    """Pages in this machine's own artifacts store that a reply links to.
    Only that store is stat-ed, never a network mount: a Pi page's link names
    the Mac's /Volumes/pihome mount, which on the Pi is its own store."""
    if not os.path.isabs(home):
        return []
    store = os.path.join(home, ARTIFACTS) + os.sep
    paths = []
    for token in message.split():
        start = token.find("file://")
        if start < 0:
            continue
        path = unquote(URL_END.split(token[start + len("file://"):], 1)[0].rstrip(".,;:"))
        if path.startswith(PI_ARTIFACTS):
            path = store + path[len(PI_ARTIFACTS):]
        path = os.path.normpath(path)
        if path.startswith(store):
            paths.append(path)
    return paths


def parse_time(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    return moment.timestamp() if moment.tzinfo else None


def is_turn_start(row: object, marker: str, codex: bool) -> bool:
    if not isinstance(row, dict):
        return False
    if codex:
        payload = row.get("payload")
        return (isinstance(payload, dict) and payload.get("type") == "task_started"
                and payload.get("turn_id") == marker)
    return row.get("type") == "user" and row.get("promptId") == marker and "turnPosition" in row


def lines_backwards(handle, deadline: float):
    """The file's lines, last first, read in CHUNK_BYTES pieces until the
    deadline passes."""
    position, carry = handle.seek(0, os.SEEK_END), b""
    while position > 0 and time.monotonic() < deadline:
        start = max(0, position - CHUNK_BYTES)
        handle.seek(start)
        lines = (handle.read(position - start) + carry).split(b"\n")
        position = start
        # The first piece may be the tail of a line the next read finishes.
        carry = lines.pop(0) if position > 0 else b""
        yield from reversed(lines)


def turn_start(event: dict) -> float | None:
    """When this turn began: Claude's turn-start row (a user row with
    `turnPosition`, typed prompt or task notification) for the payload's
    prompt_id, or Codex's `task_started` event for its turn_id, searched for
    backwards through the transcript. None when it isn't found in time."""
    path = event.get("transcript_path")
    codex = is_codex(event)
    marker = event.get("turn_id" if codex else "prompt_id")
    if not (isinstance(path, str) and path and isinstance(marker, str) and marker):
        return None
    kind, needle = (b'"task_started"' if codex else b'"turnPosition"'), marker.encode("utf-8")
    try:
        with open(path, "rb") as handle:
            for raw in lines_backwards(handle, time.monotonic() + SEARCH_SECONDS):
                if kind not in raw or needle not in raw:
                    continue
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if is_turn_start(row, marker, codex):
                    return parse_time(row.get("timestamp"))
    except OSError:
        return None
    return None


def links_this_turns_page(message: str, event: dict, env: dict, now: float) -> bool:
    """A linked page in the local store saved during this turn (and not
    dated in the future), so a reply that cites an older page still counts.
    When the turn's start can't be found there is no exemption: the reply
    is measured as it stands (a short follow-up with a page link passes
    anyway, and the ledger still bounds the guard to one block a prompt)."""
    paths = page_paths(message, env.get("HOME", ""))
    if not paths:
        return False
    started = turn_start(event)
    if started is None:
        return False
    # One second covers filesystem timestamp rounding against the row's time.
    earliest = started - 1
    for path in paths:
        try:
            saved = os.stat(path).st_mtime
        except OSError:
            continue
        if earliest <= saved <= now + CLOCK_SKEW_SECONDS:
            return True
    return False


def chain(event: dict) -> tuple[str, str] | None:
    """Claude's session and prompt ids. The prompt id stays the same from a
    prompt until the next one, so it names the chain of Stops that blocks
    and their continuations make. Codex sends neither."""
    session, prompt = event.get("session_id"), event.get("prompt_id")
    if is_codex(event) or not all(isinstance(value, str) and value for value in (session, prompt)):
        return None
    return session, prompt


def ledger_path(env: dict, session: str) -> str:
    """'' (unusable) without an absolute HOME, never a path under the cwd."""
    home = env.get("HOME", "")
    if not os.path.isabs(home):
        return ""
    # Session ids are external input; hashing keeps every file inside its folder.
    name = hashlib.sha256(session.encode("utf-8")).hexdigest()[:24] + ".json"
    return os.path.join(home, LEDGER_DIR, name)


def blocked_prompts(path: str) -> list | None:
    """Prompt ids this guard blocked in the session; None when unreadable."""
    try:
        with open(path, encoding="utf-8") as handle:
            prompts = json.load(handle)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        return None
    return prompts if isinstance(prompts, list) else None


def prune(folder: str, now: float) -> None:
    # Only blocks write here, so idle sessions' ledgers are cleared on the
    # next one. Lock stripes are never removed (see LOCK_STRIPES).
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.name.endswith(".lock"):
                continue
            try:
                if entry.stat().st_mtime < now - LEDGER_MAX_AGE_SECONDS:
                    os.unlink(entry.path)
            except OSError:
                pass


def lock_path(ledger: str, session: str) -> str:
    stripe = int(hashlib.sha256(session.encode("utf-8")).hexdigest(), 16) % LOCK_STRIPES
    return os.path.join(os.path.dirname(ledger), f".stripe-{stripe:02d}.lock")


def write_ledger(path: str, prompts: list) -> None:
    fd, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".ledger-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(prompts[-LEDGER_PROMPTS:], handle)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def claim_block(env: dict, key: tuple[str, str], now: float) -> bool:
    """Claim this prompt's one block under the session's lock stripe, re-reading
    the ledger there, so concurrent Stops of one prompt block at most once.
    False (don't block) when already claimed, unreadable, or the lock isn't
    free within LOCK_ATTEMPTS tries."""
    path = ledger_path(env, key[0])
    if not path:
        return False
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        lock_fd = os.open(lock_path(path, key[0]), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(lock_fd, "a") as lock:
            for attempt in range(LOCK_ATTEMPTS):
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if attempt == LOCK_ATTEMPTS - 1:
                        return False
                    time.sleep(LOCK_WAIT_SECONDS)
            prompts = blocked_prompts(path)
            if prompts is None or key[1] in prompts:
                return False
            prune(os.path.dirname(path), now)
            write_ledger(path, prompts + [key[1]])
    except OSError:
        return False
    return True


def verdict(event: dict, env: dict, now: float | None = None) -> str | None:
    """The block reason for an over-long attended reply, else None.

    At most one block per Claude prompt, ordinary Stop or continuation
    alike: a prompt with a block on record (or an unreadable record) is
    allowed before anything is measured, and a block is claimed under the
    session's lock before it is sent, so a reply written after another
    hook's block is measured too. Codex sends no prompt id: its
    continuations (stop_hook_active) are never checked, as before."""
    message = event.get("last_assistant_message")
    if event.get("hook_event_name") != "Stop" or not isinstance(message, str):
        return None
    now = time.time() if now is None else now
    key = chain(event)
    if key is None and event.get("stop_hook_active"):
        return None
    if key is not None:
        prompts = blocked_prompts(ledger_path(env, key[0]))
        if prompts is None or key[1] in prompts:
            return None
    lines = prose(message)
    words, rows = word_count(lines), row_count(lines)
    if words <= WORD_LIMIT and rows <= ROW_LIMIT:
        return None
    if not attended(event, env) or links_this_turns_page(message, event, env, now):
        return None
    if key is not None and not claim_block(env, key, now):
        return None
    return reason(event, words, rows)


def main() -> int:
    try:
        event = json.load(sys.stdin)
        if isinstance(event, dict):
            why = verdict(event, dict(os.environ))
            if why:
                json.dump({"decision": "block", "reason": why}, sys.stdout)
    except (OSError, ValueError, TypeError) as error:
        print(f"long_reply_guard: {str(error).splitlines()[0]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
