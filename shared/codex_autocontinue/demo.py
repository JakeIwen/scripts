"""Watch the real Codex TUI recover using a temporary, offline custom provider."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from .demo_environment import create_environment
from .fake_server import ResponsesServer, Scenario


LAUNCHER = Path(__file__).with_name("run")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="codex-autocontinue --demo", description=__doc__)
    parser.add_argument("--codex", default=shutil.which("codex"))
    parser.add_argument("--scenario", choices=[item.value for item in Scenario],
                        default=Scenario.FAIL_THEN_SUCCEED.value)
    parser.add_argument("--failures", type=int, default=1)
    args = parser.parse_args(argv)
    if not args.codex:
        parser.error("Codex is not on PATH; pass --codex /path/to/codex")
    if args.failures < 0:
        parser.error("--failures must be nonnegative")
    codex = str(Path(args.codex).resolve())
    with tempfile.TemporaryDirectory(prefix="codex-capacity-demo-") as temporary:
        log_path = Path(temporary) / "requests.jsonl"
        server = ResponsesServer(args.scenario, args.failures, log_path=log_path).start()
        try:
            fixture = create_environment(temporary, server.base_url, {
                "enabled": True, "base_seconds": 2, "factor": 2,
                "max_seconds": 8, "jitter": 0, "max_attempts": 3})
            print("Offline provider: {}\nRequest log: {}\n"
                  "Type a prompt; Ctrl+C twice exits. No real Codex home or account is used."
                  .format(server.base_url, log_path), flush=True)
            result = subprocess.run(
                [str(LAUNCHER), "--"] + fixture.command(codex),
                cwd=fixture.project, env=fixture.environ)
            return result.returncode if result.returncode >= 0 else 128 - result.returncode
        finally:
            server.stop()


if __name__ == "__main__":
    sys.exit(main())
