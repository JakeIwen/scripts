"""PreToolUse Bash hook. Ordinary commands need no registry modules or file I/O."""
import json
import os
import re
import sys

CANDIDATE = re.compile(r'npm|npx|yarn|bun|pip|poetry|\buv(?:x)?\b|corepack')


def context(message):
    return {'hookEventName': 'PreToolUse', 'additionalContext': message}


def candidate(command, env):
    return env.get('DEP_INSTALL_GUARD') != 'off' and CANDIDATE.search(re.sub(r'''[\\'"\n]''', '', command))


def evaluate(command, cwd, *, env, home, transport=None):
    if not candidate(command, env):
        return None
    return check_candidate(command, cwd, env, home, transport)


def check_candidate(command, cwd, env, home, transport=None):
    try:
        from dep_guard_run import check_command
        return check_command(command, cwd, env, home, transport)
    except ImportError:
        return context('Dependency install guard is broken (required hook module missing); check names manually.')
    except Exception:
        return context('Dependency install guard unavailable; check package names manually.')


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, TypeError, OSError):
        return 0
    if not isinstance(payload, dict) or payload.get('hook_event_name') != 'PreToolUse' or payload.get('tool_name') != 'Bash':
        return 0
    args = payload.get('tool_input')
    command = args.get('command') if isinstance(args, dict) else None
    if not isinstance(command, str) or not candidate(command, os.environ):
        return 0
    # The Pi deploys these modules flat onto its SD root. The bytecode cache is
    # unnecessary for a short-lived hook and is not part of its disk budget.
    sys.dont_write_bytecode = True
    from pathlib import Path
    cwd = payload.get('cwd')
    path = Path(cwd) if isinstance(cwd, str) and cwd.startswith('/') and '\x00' not in cwd else None
    result = check_candidate(command, path, os.environ, Path.home())
    if result:
        json.dump({'hookSpecificOutput': result}, sys.stdout, separators=(',', ':'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
