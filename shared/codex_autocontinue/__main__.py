"""Wrap a command's interactive tty; use --demo for an offline Codex session."""
import argparse
import sys

from shared.python.tool_settings import SettingsError
from .pty_proxy import run


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--demo"]:
        from .demo import main as demo
        return demo(argv[1:])
    parser = argparse.ArgumentParser(prog="codex-autocontinue", description=__doc__,
                                     usage="%(prog)s [--] command [args ...]")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a command is required (or use --demo)")
    try:
        status = run(command)
        return status if status >= 0 else 128 - status
    except (SettingsError, OSError) as error:
        print("codex-autocontinue: {}".format(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
