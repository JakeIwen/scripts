"""Settings and phone-notification adapters for capacity-wrapper retirement."""
from pathlib import Path
import subprocess

from shared.python import tool_settings


NOTIFIER = Path(__file__).resolve().parents[2] / "macbook/scripts/claude_ntfy_notify.py"
PI_DISABLE_COMMAND = (
    "/usr/bin/python3 -I ~/.local/share/codex-autocontinue/shared/python/tool_settings.py "
    "set codex-autocontinue enabled=false"
)


def disable_mac():
    tool_settings.save("codex-autocontinue", {"enabled": False})


def disable_pi(runner=subprocess.run):
    result = runner(["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                     "vanpi.lan", PI_DISABLE_COMMAND], capture_output=True, text=True,
                    timeout=30, check=False)
    if result.returncode != 0:
        raise RuntimeError("Pi settings command exited {}".format(result.returncode))


def notify(message, runner=subprocess.run):
    result = runner(["/usr/bin/python3", str(NOTIFIER), "--send", "Codex autocontinue",
                     message, "default"], capture_output=True, text=True, timeout=50, check=False)
    if result.returncode != 0:
        raise RuntimeError("phone notification exited {}".format(result.returncode))
