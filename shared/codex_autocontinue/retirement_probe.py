"""Offline installed-Codex behavior probe and informational upstream PR signal."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.request

from .fake_server import ResponsesServer, Scenario
from .retirement_state import PRStatus, ProbeOutcome, ProbeResult


PROBE_TIMEOUT = 8 * 60
PR_URL = "https://api.github.com/repos/openai/codex/pulls/31058"
CAPACITY_MESSAGE = "Selected model is at capacity"


def create_probe_environment(directory, base_url, environ):
    root = Path(directory).resolve()
    home, project = root / "codex-home", root / "project"
    home.mkdir()
    project.mkdir()
    # The retry defaults are the behavior under test, not fixture tuning knobs.
    config = '''model = "mock-model"
model_provider = "mock"
check_for_update_on_startup = false
model_reasoning_effort = "low"
[model_providers.mock]
name = "Offline capacity retirement probe"
base_url = %s
wire_api = "responses"
[projects.%s]
trust_level = "trusted"
[analytics]
enabled = false
[feedback]
enabled = false
''' % (json.dumps(base_url), json.dumps(str(project)))
    (home / "config.toml").write_text(config, encoding="utf-8")
    clean = {key: value for key, value in environ.items()
             if not key.upper().startswith(("OPENAI_", "CODEX_", "OTEL_"))
             and key.lower() not in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")}
    clean.update(HOME=str(root), CODEX_HOME=str(home),
                 XDG_CONFIG_HOME=str(root / "config"), XDG_CACHE_HOME=str(root / "cache"),
                 XDG_DATA_HOME=str(root / "data"), XDG_STATE_HOME=str(root / "state"),
                 NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    return project, clean


def classify_probe(version, requests, result):
    if requests >= 2 and result.returncode == 0:
        return ProbeResult(ProbeOutcome.SHIPPED, version, requests, result.returncode)
    output = (result.stdout or "") + (result.stderr or "")
    if requests == 1 and result.returncode != 0 and CAPACITY_MESSAGE in output:
        return ProbeResult(ProbeOutcome.NEGATIVE, version, requests, result.returncode)
    return ProbeResult(ProbeOutcome.ERROR, version, requests, result.returncode,
                       "exec did not produce a conclusive capacity-retry result")


def run_probe(codex=None, timeout=PROBE_TIMEOUT, scenario=Scenario.FAIL_THEN_SUCCEED,
              runner=subprocess.run, server_factory=ResponsesServer,
              temporary_factory=tempfile.TemporaryDirectory, environ=None):
    codex = str(codex or Path.home() / ".local/bin/codex")
    version, requests = "unknown", 0
    server = None
    try:
        with temporary_factory(prefix="codex-retirement-") as directory:
            server = server_factory(scenario=scenario).start()
            project, clean = create_probe_environment(
                directory, server.base_url, os.environ if environ is None else environ)
            reported = runner([codex, "--version"], cwd=str(project), env=clean,
                              capture_output=True, text=True, timeout=15, check=False)
            if reported.returncode != 0 or not reported.stdout.strip():
                raise RuntimeError("codex --version failed")
            version = reported.stdout.strip().removeprefix("codex-cli ")
            result = runner([codex, "-c", 'model_provider="mock"', "exec",
                             "--skip-git-repo-check", "Say hello from the offline fixture"],
                            cwd=str(project), env=clean, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=timeout, check=False)
            requests = len(server.turn_requests())
            return classify_probe(version, requests, result)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        requests = len(server.turn_requests()) if server is not None else requests
        detail = "probe timed out" if isinstance(error, subprocess.TimeoutExpired) else str(error)
        return ProbeResult(ProbeOutcome.ERROR, version, requests, detail=detail)
    finally:
        if server is not None:
            server.stop()


def fetch_pr_status(opener=urllib.request.urlopen):
    request = urllib.request.Request(PR_URL, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "codex-autocontinue-retirement"})
    try:
        with opener(request, timeout=15) as response:
            data = json.load(response)
        if (data.get("state") not in ("open", "closed")
                or not isinstance(data.get("merged"), bool)
                or data.get("merged_at") is not None and not isinstance(data["merged_at"], str)):
            raise ValueError("invalid PR status response")
        return PRStatus(data["state"], data["merged"], data.get("merged_at"))
    except (OSError, ValueError, AttributeError) as error:
        return PRStatus(error=str(error))
