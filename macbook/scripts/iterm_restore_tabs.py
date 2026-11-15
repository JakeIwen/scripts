#!/usr/bin/python3
"""Mac-only iTerm transport for llm_restore.py; standalone Python 3.9+.

Creation uses system Python and AppleScript. Optional tab titles run separately
in the font follower's existing iTerm API venv so its absence cannot lose tabs.
Input is JSON pairs: [command index, title], or [session id, title] for --titles.
"""

import json
from pathlib import Path
import re
import subprocess
import sys


TITLE_PYTHON = Path.home() / ".local/share/iterm-screen-fonts/venv/bin/python"
TITLE_TIMEOUT = 10
TITLE_LIMIT = 60
TITLE_WARNING = "warning: restored tabs opened, but iTerm conversation titles could not be set."

ITERM_SCRIPT = """
on run argv
  tell application "iTerm"
    activate
    set w to (create window with default profile)
    set isFirst to true
    set sessionIDs to {}
    repeat with c in argv
      if isFirst then
        set isFirst to false
        set s to current session of w
      else
        tell w to set t to (create tab with default profile)
        set s to current session of t
      end if
      set end of sessionIDs to (id of s as text)
      tell s to write text (c as text)
    end repeat
    set AppleScript's text item delimiters to linefeed
    return sessionIDs as text
  end tell
end run
"""


def literal_title(title):
    """Bound visible text, then escape iTerm's interpolated-string syntax."""
    visible = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", title).strip()[:TITLE_LIMIT]
    return visible.replace("\\", "\\\\")


def title_requests(raw, session_ids):
    """Validate all optional titles before targeting any created session."""
    pairs = json.loads(raw)
    if not isinstance(pairs, list):
        raise ValueError("expected title pairs")
    requests = []
    for pair in pairs:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError("expected a title pair")
        index, title = pair
        if type(index) is not int or not 0 <= index < len(session_ids) or not isinstance(title, str):
            raise ValueError("invalid title pair")
        escaped = literal_title(title)
        if escaped:
            requests.append([session_ids[index], escaped])
    return requests


def set_titles(session_ids, raw):
    """Best effort after creation: a failed optional API never fails restore."""
    try:
        requests = title_requests(raw, session_ids)
        if requests:
            subprocess.run([str(TITLE_PYTHON), "-I", "-B", str(Path(__file__).resolve()), "--titles"],
                           input=json.dumps(requests), capture_output=True, text=True,
                           check=True, timeout=TITLE_TIMEOUT)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"{TITLE_WARNING} ({type(error).__name__})", file=sys.stderr)


async def apply_titles(connection, requests):
    import iterm2

    app = await iterm2.async_get_app(connection)
    for session_id, title in requests:
        session = app.get_session_by_id(session_id)
        window, tab = app.get_window_and_tab_for_session(session) if session else (None, None)
        if window is None or tab is None:
            raise ValueError("created iTerm session no longer exists")
        # The workgroup owns session.name; a tab override outranks it and remains
        # editable through iTerm's Edit Tab Title, without changing any profile.
        await tab.async_set_title(title)


def main(argv=None):
    commands = sys.argv[1:] if argv is None else argv
    if commands == ["--titles"]:
        import iterm2

        requests = json.load(sys.stdin)
        iterm2.run_until_complete(lambda connection: apply_titles(connection, requests))
        return 0
    if not commands:
        return 0
    result = subprocess.run(["/usr/bin/osascript", "-e", ITERM_SCRIPT, *commands],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True)
    session_ids = result.stdout.splitlines()
    raw = sys.stdin.read()
    if raw.strip():
        if len(session_ids) != len(commands) or any(not sid.strip() for sid in session_ids):
            print(f"{TITLE_WARNING} (ValueError)", file=sys.stderr)
        else:
            set_titles(session_ids, raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
