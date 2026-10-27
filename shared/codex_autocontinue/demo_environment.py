"""Isolated Codex configuration used by the manual demo and real-TUI tests."""
from dataclasses import dataclass
import json
import os
from pathlib import Path


@dataclass(frozen=True)
class DemoEnvironment:
    home: Path
    project: Path
    environ: dict

    def command(self, codex, *, alternate_screen=False):
        # -c forces embedded mode: no managed-daemon download or orphan after the demo.
        command = [codex, "-c", 'model_provider="mock"', "--ask-for-approval", "never"]
        if not alternate_screen:
            command.append("--no-alt-screen")
        return command


def create_environment(directory, base_url, overrides):
    """Write only beneath directory; custom-provider requests stay on loopback."""
    root = Path(directory).resolve()
    home = root / "codex-home"
    project = root / "project"
    values = root / "settings"
    for path in (home, project, values):
        path.mkdir(parents=True, exist_ok=True)
    config = '''model = "mock-model"
model_provider = "mock"
check_for_update_on_startup = false
model_reasoning_effort = "low"
[model_providers.mock]
name = "Offline fixture"
base_url = %s
wire_api = "responses"
request_max_retries = 0
stream_max_retries = 0
[projects.%s]
trust_level = "trusted"
[analytics]
enabled = false
[feedback]
enabled = false
''' % (json.dumps(base_url), json.dumps(str(project)))
    (home / "config.toml").write_text(config, encoding="utf-8")
    (values / "codex-autocontinue.json").write_text(json.dumps(overrides), encoding="utf-8")
    environ = {key: value for key, value in os.environ.items()
               if not key.startswith(("CODEX_", "OPENAI_", "JACOBR_"))
               and key.lower() not in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")}
    environ.update(CODEX_HOME=str(home), HOME=str(root), XDG_CONFIG_HOME=str(root / "config"),
                   JACOBR_CONFIG_DIR=str(values), TERM="xterm-256color", NO_PROXY="127.0.0.1,localhost")
    return DemoEnvironment(home, project, environ)
