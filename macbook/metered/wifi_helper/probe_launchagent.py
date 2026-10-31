"""Live verification worker, launched only by verify.zsh's temporary GUI LaunchAgent."""
import json
import os
from pathlib import Path
import subprocess
import sys

from .reader import APP_NAME, OPEN, read_wifi


def probe(output: Path) -> int:
    app = Path.home() / "Applications" / APP_NAME
    evidence = {"parent_pid": os.getppid(),
                "launch_agent": os.environ.get("XPC_SERVICE_NAME"), "readings": {}}
    commands = {
        "direct_executable": [str(app / "Contents/MacOS/MeteredWiFi")],
        "launchservices": [OPEN, "-g", "-j", "-n", "-W", str(app), "--args"],
    }
    for model, command in commands.items():
        destination = output.parent / (model + ".json")
        try:
            subprocess.run(command + ["--output", str(destination)], check=True,
                           capture_output=True, timeout=10)
            evidence["readings"][model] = json.loads(destination.read_text())
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            evidence["readings"][model] = {"status": "error", "detail": type(error).__name__}
    evidence["readings"]["python_reader"] = read_wifi()
    # Only the chosen invocation is a gate; the direct executable is a diagnostic comparison.
    evidence["success"] = evidence["readings"]["python_reader"]["status"] == "ok"
    temporary = output.with_suffix(".pending")
    temporary.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    temporary.chmod(0o600)
    temporary.replace(output)
    return 0 if evidence["success"] else 1


if __name__ == "__main__":
    raise SystemExit(probe(Path(sys.argv[1])))
