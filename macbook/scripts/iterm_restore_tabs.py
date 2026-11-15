#!/usr/bin/python3
"""Mac-only iTerm transport for llm_restore.py; standalone Python 3.9+."""

import subprocess
import sys


ITERM_SCRIPT = """
on run argv
  tell application "iTerm"
    activate
    set w to (create window with default profile)
    set isFirst to true
    repeat with c in argv
      if isFirst then
        set isFirst to false
        set s to current session of w
      else
        tell w to set t to (create tab with default profile)
        set s to current session of t
      end if
      tell s to write text (c as text)
    end repeat
  end tell
end run
"""


def main(argv=None):
    commands = sys.argv[1:] if argv is None else argv
    subprocess.run(["/usr/bin/osascript", "-e", ITERM_SCRIPT, *commands],
                   capture_output=True, text=True, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
