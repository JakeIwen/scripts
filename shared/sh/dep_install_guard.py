"""PreToolUse Bash hook. Ordinary commands need no registry modules or file I/O."""
import json
import os
import sys


def context(message):
    return {'hookEventName': 'PreToolUse', 'additionalContext': message}


def candidate(command, env):
    if env.get('DEP_INSTALL_GUARD') == 'off':
        return False
    try:
        from dep_guard_forms import may_install
    except ImportError:
        # A broken deployment must be visible, not a silent pass on all calls.
        return True
    return may_install(command)


def evaluate(command, cwd, *, env, home, transport=None, seconds=None):
    if not candidate(command, env):
        return None
    return check_candidate(command, cwd, env, home, transport, seconds)


def check_candidate(command, cwd, env, home, transport=None, seconds=None):
    try:
        from dep_guard_deadline import Deadline
    except ImportError:
        return context('Dependency install guard is broken (deadline module missing); check names manually.')

    def check(budget):
        from dep_guard_run import check_command
        return check_command(command, cwd, env, home, transport, budget)

    return Deadline(seconds).run(check)


def main():
    # The Pi deploys these modules flat onto its SD root. The bytecode cache is
    # unnecessary for a short-lived hook and is not part of its disk budget.
    sys.dont_write_bytecode = True
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
    from pathlib import Path
    cwd = payload.get('cwd')
    path = Path(cwd) if isinstance(cwd, str) and cwd.startswith('/') and '\x00' not in cwd else None
    result = check_candidate(command, path, os.environ, Path.home())
    if result:
        json.dump({'hookSpecificOutput': result}, sys.stdout, separators=(',', ':'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
