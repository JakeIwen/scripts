"""Discover hosted projects and report them to the van dashboard."""

import argparse
import glob
import ipaddress
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import urllib.error
import urllib.request


DENIED_PI_PORTS = {22, 53, 139, 445, 1883, 5900}
DEFAULT_EDGE_CONFIG = "/Users/jacobr/dev/lionfeather-edge/edge.json"
DEFAULT_TAILSCALE_IP = "100.82.91.76"
HOST_ORDER = ("pi", "mac", "web")


class ScanError(RuntimeError):
    """A host could not be scanned completely."""


class PostError(RuntimeError):
    """The dashboard refused or could not receive a report."""


def clean_text(value: Any, limit: int) -> str:
    """Return bounded printable text with control characters removed."""
    if not isinstance(value, str):
        return ""
    printable = "".join(character if character.isprintable() else " " for character in value)
    return " ".join(printable.split())[:limit].strip()


def title_name(value: str) -> str:
    return clean_text(value.replace("-", " ").title(), 80)


def parse_systemctl_units(output: str) -> List[str]:
    units = []
    for line in output.splitlines():
        fields = line.split()
        if fields and fields[0].endswith(".service"):
            units.append(fields[0])
    return sorted(set(units))


def parse_systemctl_show(output: str) -> Dict[str, Dict[str, Any]]:
    services: Dict[str, Dict[str, Any]] = {}
    for block in re.split(r"\n\s*\n", output.strip()):
        values: Dict[str, str] = {}
        for line in block.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
        unit = values.get("Id", "")
        if not unit:
            continue
        try:
            pid = int(values.get("MainPID", "0"))
        except ValueError:
            pid = 0
        services[unit] = {
            "pid": pid,
            "description": clean_text(values.get("Description", ""), 200),
        }
    return services


def _split_socket_address(endpoint: str) -> Optional[Tuple[str, int]]:
    endpoint = endpoint.strip()
    if endpoint.endswith(" (LISTEN)"):
        endpoint = endpoint[:-9].rstrip()
    if endpoint.startswith("n"):
        endpoint = endpoint[1:]
    if ":" not in endpoint:
        return None
    address, port_text = endpoint.rsplit(":", 1)
    try:
        port = int(port_text)
    except ValueError:
        return None
    address = address.strip("[]")
    return address, port


def parse_ss_listeners(output: str) -> List[Dict[str, Any]]:
    listeners = []
    for line in output.splitlines():
        fields = line.split(None, 5)
        if len(fields) < 4:
            continue
        endpoint = _split_socket_address(fields[3])
        if endpoint is None:
            continue
        address, port = endpoint
        process_field = fields[5] if len(fields) > 5 else ""
        pids = sorted({int(value) for value in re.findall(r"pid=(\d+)", process_field)})
        listeners.append({"address": address, "port": port, "pids": pids})
    return listeners


def _is_wildcard_bind(address: str) -> bool:
    return address.split("%", 1)[0] in {"*", "0.0.0.0", "::"}


def _is_public_bind(address: str) -> bool:
    plain_address = address.split("%", 1)[0]
    if _is_wildcard_bind(address):
        return True
    try:
        parsed = ipaddress.ip_address(plain_address)
    except ValueError:
        return plain_address not in {"localhost", "ip6-localhost"}
    if parsed.version == 6 and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    return not parsed.is_loopback


def parse_tailscale_ip(output: str) -> str:
    for value in output.split():
        try:
            return str(ipaddress.IPv4Address(value))
        except ipaddress.AddressValueError:
            continue
    return DEFAULT_TAILSCALE_IP


def build_pi_listeners(
    units_output: str,
    show_output: str,
    ss_output: str,
    tailscale_output: str,
) -> List[Dict[str, Any]]:
    units = parse_systemctl_units(units_output)
    services = parse_systemctl_show(show_output)
    sockets = parse_ss_listeners(ss_output)
    tailscale_ip = parse_tailscale_ip(tailscale_output)
    listeners = []
    for unit in units:
        service = services.get(unit)
        if not service or service["pid"] <= 0:
            continue
        service_sockets = [
            socket for socket in sockets
            if service["pid"] in socket["pids"]
            and _is_public_bind(socket["address"])
            and socket["port"] not in DENIED_PI_PORTS
        ]
        # Interface-specific callback sockets should not outrank a public wildcard listener.
        wildcard_ports = {
            socket["port"] for socket in service_sockets
            if _is_wildcard_bind(socket["address"])
        }
        ports = wildcard_ports or {socket["port"] for socket in service_sockets}
        if not ports:
            continue
        port = min(ports)
        listeners.append({
            "key": "pi:" + unit,
            "host": "pi",
            "name": title_name(unit[:-8]),
            "description": service["description"],
            "links": [
                {"kind": "lan", "url": "http://vanpi.lan:{}/".format(port)},
                {"kind": "ts", "url": "http://{}:{}/".format(tailscale_ip, port)},
            ],
        })
    return sorted(listeners, key=lambda listener: listener["key"])


def parse_launchctl_pid(output: str) -> Optional[int]:
    match = re.search(r"^\s*pid = (\d+)\s*$", output, re.MULTILINE)
    return int(match.group(1)) if match else None


def parse_lsof_ports(output: str) -> List[int]:
    ports = set()
    for line in output.splitlines():
        candidate = line.strip()
        if candidate.startswith("n"):
            endpoint = _split_socket_address(candidate)
        elif " TCP " in " {} ".format(candidate) and candidate.endswith("(LISTEN)"):
            endpoint = _split_socket_address(candidate.split()[-2])
        else:
            endpoint = None
        if endpoint is not None and 0 < endpoint[1] <= 65535:
            ports.add(endpoint[1])
    return sorted(ports)


def read_package_description(working_directory: Any) -> str:
    if not isinstance(working_directory, str) or not working_directory:
        return ""
    try:
        with open(os.path.join(working_directory, "package.json"), encoding="utf-8") as handle:
            package = json.load(handle)
    except (OSError, ValueError):
        return ""
    return clean_text(package.get("description", ""), 200) if isinstance(package, dict) else ""


def read_launchd_jobs(
    daemon_pattern: str = "/Library/LaunchDaemons/com.jacobr.*.plist",
    agent_pattern: Optional[str] = None,
    uid: Optional[int] = None,
) -> List[Dict[str, Any]]:
    user_id = os.getuid() if uid is None else uid
    if agent_pattern is None:
        agent_pattern = str(Path.home() / "Library/LaunchAgents/com.jacobr.*.plist")
    candidates = [(path, "system") for path in sorted(glob.glob(daemon_pattern))]
    candidates += [(path, "gui/{}".format(user_id)) for path in sorted(glob.glob(agent_pattern))]
    jobs = []
    seen = set()
    for path, domain in candidates:
        try:
            with open(path, "rb") as handle:
                plist = plistlib.load(handle)
        except (OSError, plistlib.InvalidFileException) as exc:
            raise ScanError("cannot read {}: {}".format(path, exc)) from exc
        label = plist.get("Label", "") if isinstance(plist, dict) else ""
        if not isinstance(label, str) or not label.startswith("com.jacobr.") or label in seen:
            continue
        seen.add(label)
        directory = plist.get("WorkingDirectory", "")
        jobs.append({
            "label": label,
            "domain": domain,
            "description": read_package_description(directory),
        })
    return jobs


def build_mac_listeners(
    jobs: Iterable[Mapping[str, Any]],
    launchctl_outputs: Mapping[str, str],
    lsof_outputs: Mapping[int, str],
) -> List[Dict[str, Any]]:
    listeners = []
    for job in sorted(jobs, key=lambda item: str(item["label"])):
        label = str(job["label"])
        pid = parse_launchctl_pid(launchctl_outputs.get(label, ""))
        if pid is None:
            continue
        ports = parse_lsof_ports(lsof_outputs.get(pid, ""))
        if not ports:
            continue
        port = ports[0]
        listeners.append({
            "key": "mac:" + label,
            "host": "mac",
            "name": title_name(label[len("com.jacobr."):]),
            "description": clean_text(job.get("description", ""), 200),
            "links": [{"kind": "local", "url": "http://127.0.0.1:{}/".format(port)}],
        })
    return listeners


def build_web_listeners(config_output: str) -> List[Dict[str, Any]]:
    try:
        config = json.loads(config_output)
    except (TypeError, ValueError) as exc:
        raise ScanError("invalid edge config: {}".format(exc)) from exc
    apps = config.get("apps") if isinstance(config, dict) else None
    if not isinstance(apps, list):
        raise ScanError("edge config apps must be a list")
    listeners = []
    for app in apps:
        if not isinstance(app, dict) or not isinstance(app.get("name"), str) or not app["name"].strip():
            raise ScanError("edge config contains an invalid app")
        app_name = clean_text(app["name"], 253)
        listeners.append({
            "key": "web:" + app_name,
            "host": "web",
            "name": title_name(app_name),
            "description": clean_text(app.get("note", ""), 200),
            "links": [{"kind": "web", "url": "https://{}.lionfeather.dev/".format(app_name)}],
        })
    return sorted(listeners, key=lambda listener: listener["key"])


def run_command(arguments: Sequence[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(arguments),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )


def _require_success(result: subprocess.CompletedProcess, description: str) -> str:
    if result.returncode != 0:
        detail = clean_text(result.stderr, 200) or "exit {}".format(result.returncode)
        raise ScanError("{} failed: {}".format(description, detail))
    return result.stdout


def collect_pi(timeout: float) -> List[Dict[str, Any]]:
    unit_result = run_command(
        ["systemctl", "list-units", "--type=service", "--state=running", "--no-legend", "--plain"],
        timeout,
    )
    units_output = _require_success(unit_result, "systemctl list-units")
    units = parse_systemctl_units(units_output)
    if not units:
        return []
    show_result = run_command(
        ["systemctl", "show", "--property=Id", "--property=MainPID", "--property=Description"] + units,
        timeout,
    )
    show_output = _require_success(show_result, "systemctl show")
    ss_result = run_command(["sudo", "-n", "ss", "-ltnpH"], timeout)
    if ss_result.returncode != 0:
        ss_result = run_command(["ss", "-ltnpH"], timeout)
    ss_output = _require_success(ss_result, "ss")
    tailscale_result = run_command(["tailscale", "ip", "-4"], timeout)
    tailscale_output = tailscale_result.stdout if tailscale_result.returncode == 0 else ""
    return build_pi_listeners(units_output, show_output, ss_output, tailscale_output)


def collect_mac(timeout: float) -> List[Dict[str, Any]]:
    jobs = read_launchd_jobs()
    launchctl_outputs: Dict[str, str] = {}
    lsof_outputs: Dict[int, str] = {}
    for job in jobs:
        label = job["label"]
        target = "{}/{}".format(job["domain"], label)
        launch_result = run_command(["launchctl", "print", target], timeout)
        if launch_result.returncode != 0:
            continue
        launchctl_outputs[label] = launch_result.stdout
        pid = parse_launchctl_pid(launch_result.stdout)
        if pid is None:
            continue
        lsof_result = run_command(
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-p", str(pid), "-F", "n"],
            timeout,
        )
        if lsof_result.returncode not in {0, 1}:
            _require_success(lsof_result, "lsof for {}".format(label))
        lsof_outputs[pid] = lsof_result.stdout
    return build_mac_listeners(jobs, launchctl_outputs, lsof_outputs)


def collect_web(edge_config: str) -> List[Dict[str, Any]]:
    try:
        with open(edge_config, encoding="utf-8") as handle:
            return build_web_listeners(handle.read())
    except OSError as exc:
        raise ScanError("cannot read edge config: {}".format(exc)) from exc


def utc_timestamp() -> str:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return now.isoformat().replace("+00:00", "Z")


def normalized_hosts(hosts: Iterable[str]) -> List[str]:
    requested = set(hosts)
    return [host for host in HOST_ORDER if host in requested]


def build_report(
    hosts: Iterable[str],
    listeners: Iterable[Mapping[str, Any]],
    scanned_at: Optional[str] = None,
) -> Dict[str, Any]:
    host_list = normalized_hosts(hosts)
    covered = set(host_list)
    ordered_listeners = sorted(
        (dict(listener) for listener in listeners if listener.get("host") in covered),
        key=lambda listener: (HOST_ORDER.index(listener["host"]), listener["key"]),
    )
    return {
        "hosts": host_list,
        "scanned_at": scanned_at or utc_timestamp(),
        "listeners": ordered_listeners,
    }


def scan_hosts(hosts: Iterable[str], edge_config: str, timeout: float) -> List[Dict[str, Any]]:
    listeners = []
    for host in normalized_hosts(hosts):
        try:
            if host == "pi":
                listeners.extend(collect_pi(timeout))
            elif host == "mac":
                listeners.extend(collect_mac(timeout))
            else:
                listeners.extend(collect_web(edge_config))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise ScanError("{} scan failed: {}".format(host, exc)) from exc
    return listeners


def encode_report(report: Mapping[str, Any]) -> bytes:
    return json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")


def post_report(
    base_url: str,
    report: Mapping[str, Any],
    timeout: float = 10.0,
    opener: Optional[Any] = None,
) -> Mapping[str, Any]:
    url = base_url.rstrip("/") + "/api/hosted-projects/scan"
    request = urllib.request.Request(
        url,
        data=encode_report(report),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if opener is None:
        open_url = urllib.request.urlopen
    elif callable(opener):
        open_url = opener
    else:
        open_url = opener.open
    response = None
    try:
        response = open_url(request, timeout=timeout)
        status = getattr(response, "status", None)
        if status is None and hasattr(response, "getcode"):
            status = response.getcode()
        body = response.read()
        if status is not None and not 200 <= int(status) < 300:
            raise PostError("dashboard returned HTTP {}".format(status))
        result = json.loads(body.decode("utf-8"))
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise PostError("dashboard refused the scan")
        return result
    except PostError:
        raise
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise PostError(str(exc)) from exc
    finally:
        if response is not None and hasattr(response, "close"):
            response.close()


def positive_timeout(value: str) -> float:
    timeout = float(value)
    if timeout <= 0:
        raise argparse.ArgumentTypeError("timeout must be greater than zero")
    return timeout


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", action="append", choices=HOST_ORDER, required=True)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--post", metavar="BASE_URL")
    destination.add_argument("--print", action="store_true", dest="print_report")
    parser.add_argument("--edge-config", default=DEFAULT_EDGE_CONFIG)
    parser.add_argument("--timeout", type=positive_timeout, default=10.0)
    return parser


def _log_summary(hosts: Iterable[str], count: int, result: str, detail: str = "") -> None:
    suffix = " detail={}".format(clean_text(detail, 200)) if detail else ""
    print(
        "hosted-project-scan hosts={} listeners={} result={}{}".format(
            ",".join(normalized_hosts(hosts)), count, result, suffix
        ),
        file=sys.stderr,
    )


def main(argv: Optional[Sequence[str]] = None, opener: Optional[Any] = None) -> int:
    args = argument_parser().parse_args(argv)
    listeners: List[Dict[str, Any]] = []
    try:
        listeners = scan_hosts(args.host, args.edge_config, args.timeout)
        report = build_report(args.host, listeners)
    except (ScanError, OSError, ValueError, subprocess.SubprocessError) as exc:
        _log_summary(args.host, len(listeners), "scan-failed", str(exc))
        return 1
    if args.print_report:
        print(encode_report(report).decode("utf-8"))
        _log_summary(args.host, len(listeners), "printed")
        return 0
    try:
        post_report(args.post, report, args.timeout, opener=opener)
    except PostError as exc:
        _log_summary(args.host, len(listeners), "post-failed", str(exc))
        return 2
    _log_summary(args.host, len(listeners), "posted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
