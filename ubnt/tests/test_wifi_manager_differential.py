#!/usr/bin/env python3
"""Byte-for-byte regression harness for the Wi-Fi manager split."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

STANDARD_FREQUENCIES = (
    "2412, 2417, 2422, 2427, 2432, 2437, 2442, 2447, 2452, 2457, 2462"
)


@dataclasses.dataclass(frozen=True)
class Case:
    name: str
    args: tuple[str, ...]
    expected_status: int
    stdin: bytes = b""
    setup: str = "base"
    environment: tuple[tuple[str, str], ...] = ()


def profile_bytes(ssid: str, security: str, bssid: str) -> bytes:
    lines = [
        "users.1.name=ubnt",
        "users.1.password=live-admin-test-hash",
        f"wireless.1.ssid={ssid}",
        f"wireless.1.ap={bssid}",
        "wireless.1.security.type=none",
        "wireless.1.scan_list.status=enabled",
        f"wireless.1.scan_list.channels={STANDARD_FREQUENCIES}",
    ]
    if security == "wpa":
        lines.extend(
            [
                "wpasupplicant.status=enabled",
                "wpasupplicant.device.1.status=enabled",
                f"aaa.1.wpa.psk={ssid}-test-password",
                f"wpasupplicant.profile.1.network.1.ssid={ssid}",
                f"wpasupplicant.profile.1.network.1.bssid={bssid}",
                f"wpasupplicant.profile.1.network.1.psk={ssid}-test-password",
            ]
        )
    else:
        lines.extend(
            [
                "wpasupplicant.status=disabled",
                "wpasupplicant.device.1.status=disabled",
            ]
        )
    lines.extend(
        [
            "radio.1.txpower=20",
            "radio.rate_module=atheros",
            "radio.1.rate.auto=enabled",
            "radio.1.rate.mcs=15",
        ]
    )
    return ("\n".join(lines) + "\n").encode()


def write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def set_system_profile(seed: Path, profile_name: str) -> None:
    system = seed / "profiles" / profile_name
    write_bytes(seed / "system.cfg", system.read_bytes())
    digest = hashlib.md5(system.read_bytes()).hexdigest().encode() + b"\n"
    write_bytes(seed / "state" / "observed-system-config.md5", digest)


def build_base_seed(seed: Path, tests_dir: Path) -> None:
    for directory in ("bin", "config", "profiles", "state"):
        (seed / directory).mkdir(parents=True, exist_ok=True)
    profiles = {
        "A Network With Spaces": profile_bytes(
            "A Network With Spaces", "none", "00:11:22:33:44:55"
        ),
        "WPA Profile": profile_bytes(
            "dendelion", "wpa", "D8:EC:5E:8D:6A:3A"
        ),
        "denlink": profile_bytes("denlink", "wpa", "4E:EA:85:26:34:F4"),
        "Broken Profile": b"users.1.name=ubnt\nusers.1.password=test-hash\n",
        "reset": profile_bytes("reset-network", "none", "02:00:00:00:00:01"),
    }
    for name, content in profiles.items():
        write_bytes(seed / "profiles" / name, content)
    set_system_profile(seed, "A Network With Spaces")
    write_bytes(seed / "associated", b"A Network With Spaces\n")
    write_bytes(seed / "authorized_keys", b"admin-key\n")
    write_bytes(seed / "persistent_keys", b"pi-rsa-key\npi-ed25519-key\n")
    write_bytes(seed / "uptime", b"1000.0 0.0\n")
    shutil.copyfile(tests_dir / "fixtures" / "iwlist-scan.txt", seed / "scan-fixture")
    write_bytes(seed / "iwlist-count", b"")
    write_bytes(seed / "reload-channels", b"")
    for command in ("iwlist", "iwgetid", "mca-status", "ip", "ping", "cfgmtd"):
        (seed / "bin" / command).symlink_to(tests_dir / "mock_command.sh")
    (seed / "bin" / "softrestart").symlink_to(tests_dir / "mock_softrestart.sh")
    (seed / "bin" / "sleep").symlink_to(tests_dir / "mock_sleep.sh")


def apply_setup(seed: Path, setup: str) -> None:
    if setup in {"disconnected", "empty-scan"}:
        write_bytes(seed / "associated", b"old-network\n")
    if setup == "empty-scan":
        write_bytes(seed / "empty-scan", b"")
    elif setup == "paused":
        write_bytes(seed / "state" / "paused", b"")
        write_bytes(seed / "state" / "manual-hold", b"900\nA Network With Spaces\n")
    elif setup == "stale-lock":
        write_bytes(seed / "state" / "lock" / "pid", b"not-a-pid\n")
    elif setup == "gui-grace":
        write_bytes(seed / "state" / "observed-system-config.md5", b"00000000000000000000000000000000\n")
        write_bytes(seed / "associated", b"old-network\n")
    elif setup == "captive-grace":
        write_bytes(seed / "state" / "manual-hold", b"1000\nA Network With Spaces\n")
    elif setup in {"denlink", "denlink-no-login"}:
        set_system_profile(seed, "denlink")
        write_bytes(seed / "associated", b"denlink\n")
        if setup == "denlink-no-login":
            lines = seed.joinpath("system.cfg").read_text().splitlines()
            content = "\n".join(line for line in lines if not line.startswith("users.1.")) + "\n"
            write_bytes(seed / "system.cfg", content.encode())
            digest = hashlib.md5(content.encode()).hexdigest().encode() + b"\n"
            write_bytes(seed / "state" / "observed-system-config.md5", digest)


def dispatch_cases() -> list[Case]:
    return [
        Case("usage-empty", (), 1),
        Case("usage-unknown", ("unknown",), 1),
        Case("auto-healthy", ("auto",), 0),
        Case("auto-extra-accepted", ("auto", "extra"), 0),
        Case("auto-empty-scan", ("auto",), 0, setup="empty-scan", environment=(("MOCK_FIXTURE", "@empty-scan"),)),
        Case("auto-active-lock", ("auto",), 0, environment=(("DIFF_ACTIVE_LOCK", "1"),)),
        Case("auto-stale-lock", ("auto",), 0, setup="stale-lock"),
        Case("auto-gui-grace", ("auto",), 0, setup="gui-grace"),
        Case("auto-captive-grace", ("auto",), 0, setup="captive-grace", environment=(("MOCK_PING_STATUS", "1"),)),
        Case("connect-switch", ("connect", "WPA Profile"), 0, setup="disconnected"),
        Case("connect-ready", ("connect", "A Network With Spaces"), 0),
        Case("connect-missing", ("connect", "Missing Profile"), 1),
        Case("connect-internet-error", ("connect", "WPA Profile"), 2, setup="disconnected", environment=(("MOCK_PING_STATUS", "1"),)),
        Case("connect-missing-arg", ("connect",), 1),
        Case("connect-extra-arg", ("connect", "WPA Profile", "extra"), 1),
        Case("status", ("status",), 0),
        Case("status-extra-accepted", ("status", "extra"), 0),
        Case("pause", ("pause",), 0),
        Case("pause-extra-accepted", ("pause", "extra"), 0),
        Case("resume", ("resume",), 0, setup="paused"),
        Case("resume-extra-accepted", ("resume", "extra"), 0, setup="paused"),
        Case("save-current", ("save-current", "Saved Copy"), 0),
        Case("save-current-invalid", ("save-current", "bad/name"), 1),
        Case("save-current-missing-arg", ("save-current",), 1),
        Case("save-current-extra-arg", ("save-current", "Saved Copy", "extra"), 1),
        Case("disable", ("disable", "WPA Profile"), 0),
        Case("disable-missing", ("disable", "Missing Profile"), 1),
        Case("disable-missing-arg", ("disable",), 1),
        Case("disable-extra-arg", ("disable", "WPA Profile", "extra"), 1),
        Case("dashboard-status", ("dashboard-status",), 0),
        Case("dashboard-status-extra-arg", ("dashboard-status", "extra"), 1),
        Case("dashboard-scan", ("dashboard-scan",), 0),
        Case("dashboard-scan-empty", ("dashboard-scan",), 0, setup="empty-scan", environment=(("MOCK_FIXTURE", "@empty-scan"),)),
        Case("dashboard-scan-extra-arg", ("dashboard-scan", "extra"), 1),
        Case("starlink-off-noop", ("starlink-off",), 0),
        Case("starlink-off-release", ("starlink-off",), 0, setup="denlink"),
        Case("starlink-off-error", ("starlink-off",), 1, setup="denlink-no-login"),
        Case("starlink-off-extra-arg", ("starlink-off", "extra"), 1),
    ]


def stdin_cases() -> list[Case]:
    update_ok = b"WPA Profile\nchange\nrotated-test-password\n02:11:22:33:44:55\n17\newma_ht\ndisabled\n4\nyes\n"
    update_bad = b"WPA Profile\nkeep\n\n\n99\natheros\nenabled\n15\nno\n"
    return [
        Case("manual-connect", ("manual-connect-stdin",), 0, b"A Network With Spaces\n"),
        Case("manual-connect-unknown", ("manual-connect-stdin",), 1, b"Missing Profile\n"),
        Case("manual-connect-empty", ("manual-connect-stdin",), 1),
        Case("manual-connect-extra-arg", ("manual-connect-stdin", "extra"), 1, b"A Network With Spaces\n"),
        Case("provision", ("provision-stdin",), 0, b"Fresh WPA\nwpa\n02:22:33:44:55:66\nfresh-test-password\n"),
        Case("provision-invalid", ("provision-stdin",), 1, b"Fresh WPA\ninvalid\n02:22:33:44:55:66\nfresh-test-password\n"),
        Case("provision-truncated", ("provision-stdin",), 1, b"Fresh WPA\nwpa\n"),
        Case("provision-extra-arg", ("provision-stdin", "extra"), 1, b"Fresh WPA\nwpa\n02:22:33:44:55:66\nfresh-test-password\n"),
        Case("update-profile", ("update-profile-stdin",), 0, update_ok, setup="disconnected"),
        Case("update-profile-invalid", ("update-profile-stdin",), 1, update_bad),
        Case("update-profile-truncated", ("update-profile-stdin",), 1, b"WPA Profile\nkeep\n"),
        Case("update-profile-extra-arg", ("update-profile-stdin", "extra"), 1, update_ok),
        Case("forget-disconnected", ("forget-stdin",), 0, b"WPA Profile\n"),
        Case("forget-active", ("forget-stdin",), 0, b"A Network With Spaces\n"),
        Case("forget-internal", ("forget-stdin",), 1, b"reset\n"),
        Case("forget-missing", ("forget-stdin",), 1, b"Missing Profile\n"),
        Case("forget-empty", ("forget-stdin",), 1),
        Case("forget-extra-arg", ("forget-stdin", "extra"), 1, b"WPA Profile\n"),
    ]


def resolve_value(value: str, runtime: Path) -> str:
    if value.startswith("@"):
        return str(runtime / value[1:])
    return value


def base_environment(runtime: Path, tests_dir: Path) -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("UBNT_", "MOCK_", "DIFF_"))
    }
    env.update(
        {
            "LC_ALL": "C",
            "PATH": f"{runtime / 'bin'}:{env.get('PATH', '/usr/bin:/bin')}",
            "UBNT_PROFILE_DIR": str(runtime / "profiles"),
            "UBNT_CONFIG_DIR": str(runtime / "config"),
            "UBNT_STATE_DIR": str(runtime / "state"),
            "UBNT_LOG_FILE": str(runtime / "wifi.log"),
            "UBNT_SYSTEM_CFG": str(runtime / "system.cfg"),
            "UBNT_SCAN_PARSER": str(runtime / "persistent/scripts/parse-iwlist.awk"),
            "UBNT_IWLIST": str(runtime / "bin/iwlist"),
            "UBNT_IWGETID": str(runtime / "bin/iwgetid"),
            "UBNT_MCA_STATUS": str(runtime / "bin/mca-status"),
            "UBNT_IP_CMD": str(runtime / "bin/ip"),
            "UBNT_PING": str(runtime / "bin/ping"),
            "UBNT_SOFTRESTART": str(runtime / "bin/softrestart"),
            "UBNT_CFGMTD": str(runtime / "bin/cfgmtd"),
            "UBNT_HEXDUMP": shutil.which("hexdump") or "/usr/bin/hexdump",
            "UBNT_MD5SUM": shutil.which("md5sum") or "/usr/bin/md5sum",
            "UBNT_UPTIME_FILE": str(runtime / "uptime"),
            "UBNT_SSH_KEY_INSTALLER": str(runtime / "persistent/scripts/ensure_ssh_keys.sh"),
            "UBNT_SSH_KEY_SOURCE": str(runtime / "persistent_keys"),
            "UBNT_AUTHORIZED_KEYS": str(runtime / "authorized_keys"),
            "UBNT_SCAN_PASSES": "1",
            "UBNT_SCAN_SETTLE_SECONDS": "0",
            "UBNT_ASSOCIATE_FAST_SECONDS": "0",
            "UBNT_ASSOCIATE_FALLBACK_SECONDS": "0",
            "UBNT_DHCP_SECONDS": "1",
            "UBNT_MANUAL_GRACE_SECONDS": "0",
            "UBNT_AUTO_SCAN_INTERVAL": "0",
            "MOCK_FIXTURE": str(runtime / "scan-fixture"),
            "MOCK_ASSOCIATED": str(runtime / "associated"),
            "MOCK_IWLIST_COUNT_FILE": str(runtime / "iwlist-count"),
            "MOCK_RELOAD_CHANNELS": str(runtime / "reload-channels"),
            "MOCK_COMMAND_SCRIPT": str(tests_dir / "mock_command.sh"),
        }
    )
    return env


def tree_entries(root: Path) -> dict[str, tuple[str, bytes]]:
    entries: dict[str, tuple[str, bytes]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries[relative] = ("symlink", os.readlink(path).encode())
        elif path.is_dir():
            entries[relative] = ("directory", b"")
        else:
            entries[relative] = ("file", path.read_bytes())
    return entries


def digest_entries(entries: dict[str, tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for path, (kind, content) in sorted(entries.items()):
        digest.update(path.encode() + b"\0" + kind.encode() + b"\0" + content + b"\0")
    return digest.hexdigest()


def compare_outputs(capture: Path, expected_status: int) -> tuple[list[str], dict[str, Any]]:
    old_dir = capture / "old"
    new_dir = capture / "new"
    problems: list[str] = []
    old_status = int((old_dir / "status").read_text().strip())
    new_status = int((new_dir / "status").read_text().strip())
    if old_status != expected_status:
        problems.append(f"old status {old_status}, expected {expected_status}")
    if new_status != expected_status:
        problems.append(f"new status {new_status}, expected {expected_status}")
    for stream in ("stdout", "stderr"):
        if old_dir.joinpath(stream).read_bytes() != new_dir.joinpath(stream).read_bytes():
            problems.append(f"{stream} bytes differ")
    old_tree = tree_entries(old_dir / "tree")
    new_tree = tree_entries(new_dir / "tree")
    if old_tree != new_tree:
        changed = sorted(set(old_tree) | set(new_tree))
        changed = [path for path in changed if old_tree.get(path) != new_tree.get(path)]
        problems.append("tree bytes differ: " + ", ".join(changed[:12]))
    evidence = {
        "status": old_status,
        "stdout_sha256": hashlib.sha256(old_dir.joinpath("stdout").read_bytes()).hexdigest(),
        "stderr_sha256": hashlib.sha256(old_dir.joinpath("stderr").read_bytes()).hexdigest(),
        "tree_sha256": digest_entries(old_tree),
    }
    return problems, evidence


def run_case(case: Case, context: dict[str, Path | str]) -> tuple[list[str], dict[str, Any]]:
    case_root = Path(context["workspace"]) / case.name
    seed = case_root / "seed"
    runtime = case_root / "runtime"
    capture = case_root / "capture"
    stdin_file = case_root / "stdin"
    build_base_seed(seed, Path(context["tests_dir"]))
    apply_setup(seed, case.setup)
    write_bytes(stdin_file, case.stdin)
    target = runtime / "persistent/scripts/wifi_manager.sh"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(context["driver"]), target)
    target.chmod(0o755)
    env = base_environment(runtime, Path(context["tests_dir"]))
    env.update({key: resolve_value(value, runtime) for key, value in case.environment})
    command = [
        str(context["shell"]), str(target), str(context["old_entrypoint"]),
        str(context["scripts_dir"]), str(seed), str(runtime), str(capture),
        str(stdin_file), *case.args,
    ]
    result = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        message = f"driver status {result.returncode}"
        if result.stderr:
            message += f" stderr_sha256={hashlib.sha256(result.stderr).hexdigest()}"
        return [message], {"driver_status": result.returncode}
    return compare_outputs(capture, case.expected_status)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shell", default="/bin/sh", help="POSIX shell used by the shared-parent driver")
    parser.add_argument("--old-ref", default="9901402", help="Git revision providing the old monolithic manager")
    parser.add_argument("--evidence", type=Path, help="write per-case hash evidence as JSON")
    parser.add_argument("--keep-artifacts", action="store_true", help="retain the temporary sandboxes and captures")
    return parser.parse_args()


def extract_old_entrypoint(repo: Path, old_ref: str, destination: Path) -> None:
    content = subprocess.check_output(
        ["git", "-C", str(repo), "show", f"{old_ref}:ubnt/persistent/scripts/wifi_manager.sh"]
    )
    write_bytes(destination, content)
    destination.chmod(0o755)


def main() -> int:
    args = parse_args()
    tests_dir = Path(__file__).resolve().parent
    repo = tests_dir.parents[1]
    scripts_dir = repo / "ubnt/persistent/scripts"
    shell = Path(args.shell).resolve()
    if not shell.is_file() or not os.access(shell, os.X_OK):
        raise SystemExit(f"shell is not executable: {args.shell}")
    workspace = Path(tempfile.mkdtemp(prefix="ubnt-wifi-differential-"))
    old_entrypoint = workspace / "old-wifi-manager.sh"
    extract_old_entrypoint(repo, args.old_ref, old_entrypoint)
    context: dict[str, Path | str] = {
        "workspace": workspace,
        "tests_dir": tests_dir,
        "driver": tests_dir / "wifi_manager_differential_driver.sh",
        "shell": shell,
        "old_entrypoint": old_entrypoint,
        "scripts_dir": scripts_dir,
    }
    failures: dict[str, list[str]] = {}
    evidence: dict[str, Any] = {}
    cases = dispatch_cases() + stdin_cases()
    for case in cases:
        problems, case_evidence = run_case(case, context)
        evidence[case.name] = case_evidence
        if problems:
            failures[case.name] = problems
    summary = {
        "shell": str(shell),
        "old_ref": args.old_ref,
        "cases": len(cases),
        "passed": len(cases) - len(failures),
        "failed": len(failures),
        "split_modules": len(list(scripts_dir.glob("wifi_manager_*.sh"))),
    }
    if args.evidence:
        args.evidence.write_text(json.dumps({"summary": summary, "cases": evidence}, indent=2) + "\n")
    if failures:
        summary["failures"] = failures
        summary["artifacts"] = str(workspace)
    print(json.dumps(summary, sort_keys=True))
    if not failures and not args.keep_artifacts:
        shutil.rmtree(workspace)
    elif args.keep_artifacts:
        print(json.dumps({"artifacts": str(workspace)}, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
