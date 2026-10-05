from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
POLICY = REPO_ROOT / "pi" / "scripts" / "disk_policy.sh"
FIXTURE = Path(__file__).with_name("fixtures") / "disk_policy_discovery_golden.json"
BASH = "/bin/bash"


@dataclass(frozen=True)
class Response:
    output: str = ""
    status: int = 0
    stderr: str = ""


@dataclass
class AdapterCase:
    name: str
    adapter: str
    resolve_status: int = 0
    resolve_reason: str = ""
    resolve_error: str = ""
    device: str = "/dev/sdz1"
    ancestry: dict[str, Response] = field(default_factory=dict)
    transport: dict[str, Response] = field(default_factory=dict)
    root_source: Response = Response("/dev/mmcblk0p2")
    root_readlink: dict[str, Response] = field(
        default_factory=lambda: {"/dev/mmcblk0p2": Response("/dev/mmcblk0p2")}
    )
    mounts: Response = Response("", 1)
    filesystem: Response = Response("exfat")


def response(output: str = "", status: int = 0, stderr: str = "") -> Response:
    return Response(output, status, stderr)


def normal_umount(name: str, **overrides: Any) -> AdapterCase:
    case = AdapterCase(
        name=name,
        adapter="umount",
        ancestry={
            "/dev/sdz1": response(
                "/dev/sdz1 part extra\n/dev/sdz disk extra\n"
            ),
            "/dev/mmcblk0p2": response(
                "/dev/mmcblk0p2 part\n/dev/mmcblk0 disk\n"
            ),
        },
        transport={"/dev/sdz": response("usb\n")},
    )
    for key, value in overrides.items():
        setattr(case, key, value)
    return case


def normal_diskctl(name: str, **overrides: Any) -> AdapterCase:
    case = AdapterCase(
        name=name,
        adapter="diskctl",
        ancestry={
            "/dev/sdz1": response(
                "/dev/sdz1 part\n/dev/sdz disk\n"
            )
        },
        transport={"/dev/sdz": response("usb\n")},
    )
    for key, value in overrides.items():
        setattr(case, key, value)
    return case


ADAPTER_CASES: tuple[AdapterCase, ...] = (
    normal_umount("umount_success_repeated_parent"),
    normal_umount(
        "umount_transport_stderr_on_success",
        transport={"/dev/sdz": response("usb\n", stderr="transport-warning")},
    ),
    normal_umount(
        "umount_parent_command_error",
        ancestry={"/dev/sdz1": response(status=7, stderr="ancestry-failed")},
    ),
    normal_umount(
        "umount_transport_command_error",
        transport={"/dev/sdz": response(status=7, stderr="transport-failed")},
    ),
    normal_umount(
        "umount_non_usb_stops_before_root",
        transport={"/dev/sdz": response("ata\n")},
    ),
    normal_umount(
        "umount_root_source_command_error",
        root_source=response(status=7, stderr="root-source-failed"),
    ),
    normal_umount(
        "umount_root_source_readlink_error",
        root_readlink={"/dev/mmcblk0p2": response(status=7, stderr="root-readlink-failed")},
    ),
    normal_umount(
        "umount_root_parent_command_error",
        ancestry={
            "/dev/sdz1": response("/dev/sdz1 part\n/dev/sdz disk\n"),
            "/dev/mmcblk0p2": response(status=7, stderr="root-ancestry-failed"),
        },
    ),
    normal_umount(
        "umount_mount_command_error",
        mounts=response(status=7, stderr="mount-discovery-failed"),
    ),
    normal_umount(
        "umount_root_guard_same_parent",
        ancestry={
            "/dev/sdz1": response("/dev/sdz1 part\n/dev/sdz disk\n"),
            "/dev/mmcblk0p2": response("/dev/mmcblk0p2 part\n/dev/sdz disk\n"),
        },
    ),
    normal_umount(
        "umount_parent_count_error",
        ancestry={
            "/dev/sdz1": response(
                "/dev/sdz1 part\n/dev/sdz disk\n/dev/sdy disk\n"
            )
        },
    ),
    normal_umount(
        "umount_label_absent_rc1",
        resolve_status=1,
        resolve_error="label not present",
    ),
    normal_umount(
        "umount_label_vanished_rc2",
        resolve_status=2,
        resolve_reason="vanished-udev-mapping",
        resolve_error="udev mapping points to vanished device /dev/vanished",
        device="/dev/vanished",
    ),
    normal_diskctl("diskctl_repair_success"),
    normal_diskctl(
        "diskctl_type_command_error",
        filesystem=response(status=7, stderr="type-check-failed"),
    ),
    normal_diskctl("diskctl_unsupported_filesystem", filesystem=response("ntfs\n")),
    normal_diskctl(
        "diskctl_parent_command_error",
        ancestry={"/dev/sdz1": response(status=7, stderr="parent-failed")},
    ),
    normal_diskctl(
        "diskctl_parent_count_error",
        ancestry={
            "/dev/sdz1": response(
                "/dev/sdz1 part\n/dev/sdz disk\n/dev/sdy disk\n"
            )
        },
    ),
    normal_diskctl(
        "diskctl_non_usb_stops_after_transport",
        transport={"/dev/sdz": response("ata\n")},
    ),
    normal_diskctl(
        "diskctl_transport_command_error",
        transport={"/dev/sdz": response(status=7, stderr="transport-failed")},
    ),
    normal_diskctl(
        "diskctl_strict_extra_non_disk",
        ancestry={"/dev/sdz1": response("/dev/sdz1 part extra\n/dev/sdz disk\n")},
    ),
    normal_diskctl(
        "diskctl_transport_stderr_on_success",
        transport={"/dev/sdz": response("usb\n", stderr="transport-warning")},
    ),
    normal_diskctl(
        "diskctl_label_rc1",
        resolve_status=1,
        resolve_error="label not present",
    ),
    normal_diskctl(
        "diskctl_label_rc2_vanished",
        resolve_status=2,
        resolve_reason="vanished-udev-mapping",
        resolve_error="udev mapping points to vanished device /dev/vanished",
        device="/dev/vanished",
    ),
)


PURE_CASES = (
    ("empty", "", "allow-extra", 1, "", 0),
    ("blank", "\n\n", "allow-extra", 1, "", 0),
    ("partition_only", "/dev/sda1 part", "allow-extra", 1, "", 0),
    ("one_disk", "/dev/sda1 part\n/dev/sda disk", "allow-extra", 0, "/dev/sda", 1),
    (
        "duplicate_disk_rows",
        "/dev/sda disk\n/dev/sda disk",
        "allow-extra",
        1,
        "",
        2,
    ),
    (
        "multiple_disks",
        "/dev/sda disk\n/dev/sdb disk",
        "allow-extra",
        1,
        "",
        2,
    ),
    (
        "extra_disk_allow",
        "/dev/sda disk note",
        "allow-extra",
        0,
        "/dev/sda",
        1,
    ),
    (
        "extra_non_disk_allow",
        "/dev/sda1 part note\n/dev/sda disk",
        "allow-extra",
        0,
        "/dev/sda",
        1,
    ),
    (
        "extra_disk_strict",
        "/dev/sda disk note",
        "strict",
        2,
        "",
        0,
    ),
    (
        "extra_non_disk_strict",
        "/dev/sda1 part note\n/dev/sda disk",
        "strict",
        2,
        "",
        0,
    ),
    (
        "leading_trailing_whitespace",
        "  /dev/sda1   part   \n\t/dev/sda\tdisk\t",
        "allow-extra",
        0,
        "/dev/sda",
        1,
    ),
    ("blank_between_rows", "/dev/sda1 part\n\n/dev/sda disk", "allow-extra", 0, "/dev/sda", 1),
    ("non_disk_type", "/dev/sda1 crypt", "allow-extra", 1, "", 0),
    ("malformed_row", "garbage", "allow-extra", 1, "", 0),
)

TRANSPORT_CASES = (
    ("usb", "usb", True),
    ("empty", "", False),
    ("space", " ", False),
    ("trailing_space", "usb ", False),
    ("leading_space", " usb", False),
    ("newline", "usb\n", False),
    ("embedded_newline", "usb\nwarning", False),
    ("uppercase", "USB", False),
    ("mixed_case", "Usb", False),
)


SENTINELS = {
    "DISK_POLICY_RESOLVED_DEVICE": "sentinel-device",
    "DISK_POLICY_RESOLVED_LABEL_KEY": "sentinel-key",
    "DISK_POLICY_RESOLVE_ERROR": "sentinel-error",
    "DISK_POLICY_RESOLVE_REASON": "sentinel-reason",
    "DISK_POLICY_VANISHED_DEVICE": "sentinel-vanished",
}


def run_bash(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    process_env = os.environ.copy()
    process_env.pop("BASH_ENV", None)
    process_env.pop("ENV", None)
    process_env["CDPATH"] = ""
    process_env.update(env)
    result = subprocess.run(
        [BASH, "-c", script],
        env=process_env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"isolated Bash harness failed ({result.returncode}): "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    return result


def shell_literal(value: str) -> str:
    return shlex.quote(value)


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def response_script(response_map: dict[str, Response], key_expression: str) -> str:
    arms: list[str] = []
    for key, result in response_map.items():
        arms.append(
            "  "
            + shell_literal(key)
            + ")\n"
            + f"    printf '%s' {shell_literal(result.stderr)} >&2\n"
            + f"    printf '%s' {shell_literal(result.output)}\n"
            + f"    exit {result.status}\n"
            + "    ;;"
        )
    arms.append("  *) exit 98 ;;")
    return (
        f"key={key_expression}\n"
        "case \"$key\" in\n"
        + "\n".join(arms)
        + "\nesac\n"
    )


def fake_command(path: Path, name: str, calls: Path, body: str) -> None:
    script = f"""#!/bin/bash
printf '%s' {shell_literal(name)} >> {shell_literal(str(calls))}
for arg in \"$@\"; do printf '\\t%s' \"$arg\" >> {shell_literal(str(calls))}; done
printf '\\n' >> {shell_literal(str(calls))}
{body}
"""
    write_executable(path, script)


def make_fakes(root: Path, case: AdapterCase) -> dict[str, Path]:
    calls = root / "calls"
    calls.touch()
    fakes: dict[str, Path] = {}

    lsblk = root / "lsblk"
    ancestry_key = "${4:-}"
    ancestry_key_with_dash = "${5:-}"
    transport_key = "${3:-}"
    transport_key_with_dash = "${4:-}"
    lsblk_body = f"""if [[ \"${{1:-}}\" == -s ]]; then
  if [[ \"${{4:-}}\" == -- ]]; then key=ancestry:{ancestry_key_with_dash}; else key=ancestry:{ancestry_key}; fi
elif [[ \"${{1:-}}\" == -dnro ]]; then
  if [[ \"${{3:-}}\" == -- ]]; then key=transport:{transport_key_with_dash}; else key=transport:{transport_key}; fi
else
  exit 97
fi
"""
    lsblk_body += response_script(
        {**{f"ancestry:{k}": v for k, v in case.ancestry.items()}, **{f"transport:{k}": v for k, v in case.transport.items()}},
        "$key",
    )
    fake_command(lsblk, "lsblk", calls, lsblk_body)
    fakes["lsblk"] = lsblk

    findmnt = root / "findmnt"
    findmnt_body = response_script(
        {
            "-nro SOURCE /": case.root_source,
            f"-rn -S {case.device} -o TARGET": case.mounts,
        },
        '"$*"',
    )
    fake_command(findmnt, "findmnt", calls, findmnt_body)
    fakes["findmnt"] = findmnt

    readlink = root / "readlink"
    readlink_body = response_script(case.root_readlink, '"${3:-}"')
    fake_command(readlink, "readlink", calls, readlink_body)
    fakes["readlink"] = readlink

    sudo = root / "sudo"
    fake_command(
        sudo, "sudo", calls,
        f'[[ "${{1:-}}" == {shell_literal(str(root / "blkid"))} ]] || exit 97\nexec "$@"',
    )
    fakes["sudo"] = sudo

    blkid = root / "blkid"
    fake_command(blkid, "blkid", calls, response_script({"type": case.filesystem}, "'type'"))
    fakes["blkid"] = blkid
    return fakes


def parse_markers(stdout: str) -> tuple[str, dict[str, str], list[str], int | None]:
    visible: list[str] = []
    globals_seen: dict[str, str] = {}
    failures: list[str] = []
    status: int | None = None
    for line in stdout.splitlines():
        if line.startswith("__RESULT__ "):
            status = int(line.split(" ", 1)[1])
        elif line.startswith("__GLOBAL__ "):
            _, name, encoded = line.split(" ", 2)
            values = shlex.split(encoded)
            globals_seen[name] = values[0] if values else ""
        elif line.startswith("__FAILURE__ "):
            values = shlex.split(line.split(" ", 1)[1])
            failures.append(values[0] if values else "")
        else:
            visible.append(line)
    if status is None:
        raise AssertionError(f"adapter harness did not report a status: {stdout!r}")
    return "\n".join(visible) + ("\n" if visible else ""), globals_seen, failures, status


def normalized(value: str, root: Path) -> str:
    return value.replace(str(root), "<TMP>")


def command_log(path: Path, root: Path) -> list[list[str]]:
    if not path.exists():
        return []
    commands: list[list[str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        commands.append([normalized(part, root) for part in line.split("\t")])
    return commands


def extract_diskctl_resolver(source: Path) -> str:
    text = source.read_text(encoding="utf-8")
    start = text.index("resolve_exact_filesystem() {")
    end = text.index('\ncase "$action" in', start)
    return text[start:end]


def run_umount_adapter(source_dir: Path, case: AdapterCase) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="disk_policy_discovery_") as temp_name:
        root = Path(temp_name)
        (root / "by-label").mkdir()
        calls = root / "calls"
        copied_policy = root / "disk_policy.sh"
        copied_umount = root / "umount_disks.sh"
        copied_policy.write_text((source_dir / "disk_policy.sh").read_text(encoding="utf-8"), encoding="utf-8")
        script = (source_dir / "umount_disks.sh").read_text(encoding="utf-8")
        fakes = make_fakes(root, case)
        for original, replacement in {
            "/usr/bin/lsblk": fakes["lsblk"],
            "/usr/bin/findmnt": fakes["findmnt"],
            "/usr/bin/readlink": fakes["readlink"],
        }.items():
            script = script.replace(original, shell_literal(str(replacement)))
        copied_umount.write_text(script, encoding="utf-8")

        sentinel_assignments = "\n".join(
            f"{name}={shell_literal(value)}" for name, value in SENTINELS.items()
        )
        harness = f"""export UMOUNT_DISKS_LIBRARY_ONLY=1
export DISK_POLICY_BY_LABEL_DIR={shell_literal(str(root / 'by-label'))}
source {shell_literal(str(copied_umount))}
UD_FAILURES=()
{sentinel_assignments}
DISK_POLICY_RESOLVED_DEVICE={shell_literal(case.device)}
DISK_POLICY_RESOLVED_LABEL_KEY=LABEL
DISK_POLICY_RESOLVE_ERROR={shell_literal(case.resolve_error)}
DISK_POLICY_RESOLVE_REASON={shell_literal(case.resolve_reason)}
DISK_POLICY_VANISHED_DEVICE={shell_literal(case.device if case.resolve_reason else '')}
disk_policy_resolve_exact_label() {{
  DISK_POLICY_RESOLVED_DEVICE={shell_literal(case.device)}
  DISK_POLICY_RESOLVED_LABEL_KEY=LABEL
  DISK_POLICY_RESOLVE_ERROR={shell_literal(case.resolve_error)}
  DISK_POLICY_RESOLVE_REASON={shell_literal(case.resolve_reason)}
  DISK_POLICY_VANISHED_DEVICE={shell_literal(case.device if case.resolve_reason else '')}
  return {case.resolve_status}
}}
ud_resolve_label movingparts
rc=$?
printf '__RESULT__ %s\\n' "$rc"
printf '__GLOBAL__ UD_DEVICE %q\\n' "$UD_DEVICE"
printf '__GLOBAL__ UD_PARENT %q\\n' "$UD_PARENT"
printf '__GLOBAL__ UD_MOUNTS %q\\n' "$UD_MOUNTS"
for key in DISK_POLICY_RESOLVED_DEVICE DISK_POLICY_RESOLVED_LABEL_KEY DISK_POLICY_RESOLVE_ERROR DISK_POLICY_RESOLVE_REASON DISK_POLICY_VANISHED_DEVICE; do
  printf '__GLOBAL__ %s %q\\n' "$key" "${{!key}}"
done
for failure in "${{UD_FAILURES[@]}}"; do printf '__FAILURE__ %q\\n' "$failure"; done
"""
        result = run_bash(harness, {})
        stdout, globals_seen, failures, status = parse_markers(result.stdout)
        return {
            "status": status,
            "stdout": normalized(stdout, root),
            "stderr": normalized(result.stderr, root),
            "commands": command_log(calls, root),
            "failures": [normalized(value, root) for value in failures],
            "globals": {key: normalized(value, root) for key, value in globals_seen.items()},
        }


def run_diskctl_adapter(source_dir: Path, case: AdapterCase) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="disk_policy_discovery_") as temp_name:
        root = Path(temp_name)
        calls = root / "calls"
        copied_policy = root / "disk_policy.sh"
        copied_policy.write_text((source_dir / "disk_policy.sh").read_text(encoding="utf-8"), encoding="utf-8")
        fakes = make_fakes(root, case)
        resolver = extract_diskctl_resolver(source_dir / "diskctl")
        harness = f"""set -u
source {shell_literal(str(copied_policy))}
{resolver}
sudo_command={shell_literal(str(fakes['sudo']))}
blkid_command={shell_literal(str(fakes['blkid']))}
lsblk_command={shell_literal(str(fakes['lsblk']))}
label=movingparts
device=
filesystem=
parent=
DISK_POLICY_RESOLVED_DEVICE={shell_literal(case.device)}
DISK_POLICY_RESOLVED_LABEL_KEY=LABEL
DISK_POLICY_RESOLVE_ERROR={shell_literal(case.resolve_error)}
DISK_POLICY_RESOLVE_REASON={shell_literal(case.resolve_reason)}
DISK_POLICY_VANISHED_DEVICE={shell_literal(case.device if case.resolve_reason else '')}
disk_policy_resolve_exact_label() {{
  DISK_POLICY_RESOLVED_DEVICE={shell_literal(case.device)}
  DISK_POLICY_RESOLVED_LABEL_KEY=LABEL
  DISK_POLICY_RESOLVE_ERROR={shell_literal(case.resolve_error)}
  DISK_POLICY_RESOLVE_REASON={shell_literal(case.resolve_reason)}
  DISK_POLICY_VANISHED_DEVICE={shell_literal(case.device if case.resolve_reason else '')}
  return {case.resolve_status}
}}
resolve_exact_filesystem
rc=$?
printf '__RESULT__ %s\\n' "$rc"
printf '__GLOBAL__ device %q\\n' "$device"
printf '__GLOBAL__ filesystem %q\\n' "$filesystem"
printf '__GLOBAL__ parent %q\\n' "$parent"
for key in DISK_POLICY_RESOLVED_DEVICE DISK_POLICY_RESOLVED_LABEL_KEY DISK_POLICY_RESOLVE_ERROR DISK_POLICY_RESOLVE_REASON DISK_POLICY_VANISHED_DEVICE; do
  printf '__GLOBAL__ %s %q\\n' "$key" "${{!key}}"
done
"""
        result = run_bash(harness, {})
        stdout, globals_seen, failures, status = parse_markers(result.stdout)
        return {
            "status": status,
            "stdout": normalized(stdout, root),
            "stderr": normalized(result.stderr, root),
            "commands": command_log(calls, root),
            "failures": [normalized(value, root) for value in failures],
            "globals": {key: normalized(value, root) for key, value in globals_seen.items()},
        }


def run_adapter(source_dir: Path, case: AdapterCase) -> dict[str, Any]:
    if case.adapter == "umount":
        return run_umount_adapter(source_dir, case)
    return run_diskctl_adapter(source_dir, case)


def fixture_record(source_dir: Path, case: AdapterCase) -> dict[str, Any]:
    return {"name": case.name, "adapter": case.adapter, "result": run_adapter(source_dir, case)}


class DiskPolicyTests(unittest.TestCase):
    def test_parent_parser_cases_and_sentinel_globals(self) -> None:
        self.assertTrue(POLICY.is_file())
        for name, rows, mode, expected_status, expected_parent, expected_count in PURE_CASES:
            with self.subTest(name=name):
                script = """source "$POLICY"
DISK_POLICY_RESOLVED_DEVICE=sentinel-device
DISK_POLICY_RESOLVED_LABEL_KEY=sentinel-key
DISK_POLICY_RESOLVE_ERROR=sentinel-error
DISK_POLICY_RESOLVE_REASON=sentinel-reason
DISK_POLICY_VANISHED_DEVICE=sentinel-vanished
disk_policy_parent_from_rows "$ROWS" "$MODE"
rc=$?
printf 'rc=%s\\nparent=%s\\ncount=%s\\n' "$rc" "$DISK_POLICY_PARENT_DISK" "$DISK_POLICY_PARENT_COUNT"
printf 'sentinels=%s|%s|%s|%s|%s\\n' \\
  "$DISK_POLICY_RESOLVED_DEVICE" "$DISK_POLICY_RESOLVED_LABEL_KEY" \\
  "$DISK_POLICY_RESOLVE_ERROR" "$DISK_POLICY_RESOLVE_REASON" \\
  "$DISK_POLICY_VANISHED_DEVICE"
"""
                result = run_bash(
                    script,
                    {"POLICY": str(POLICY), "ROWS": rows, "MODE": mode},
                )
                self.assertEqual(result.stderr, "")
                values = dict(line.split("=", 1) for line in result.stdout.splitlines())
                self.assertEqual(int(values["rc"]), expected_status)
                self.assertEqual(values["parent"], expected_parent)
                self.assertEqual(int(values["count"]), expected_count)
                self.assertEqual(
                    values["sentinels"],
                    "sentinel-device|sentinel-key|sentinel-error|sentinel-reason|sentinel-vanished",
                )

    def test_parent_parser_resets_after_success_then_failure(self) -> None:
        script = """source "$POLICY"
disk_policy_parent_from_rows '/dev/sda1 part
/dev/sda disk' allow-extra
first_rc=$?
printf 'first=%s,%s,%s\\n' "$first_rc" "$DISK_POLICY_PARENT_DISK" "$DISK_POLICY_PARENT_COUNT"
disk_policy_parent_from_rows '/dev/sda1 part' allow-extra
second_rc=$?
printf 'second=%s,%s,%s\\n' "$second_rc" "$DISK_POLICY_PARENT_DISK" "$DISK_POLICY_PARENT_COUNT"
"""
        result = run_bash(script, {"POLICY": str(POLICY)})
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.splitlines(), ["first=0,/dev/sda,1", "second=1,,0"])

    def test_transport_is_exact_literal(self) -> None:
        script = """source "$POLICY"
DISK_POLICY_RESOLVED_DEVICE=sentinel-device
DISK_POLICY_RESOLVED_LABEL_KEY=sentinel-key
DISK_POLICY_RESOLVE_ERROR=sentinel-error
DISK_POLICY_RESOLVE_REASON=sentinel-reason
DISK_POLICY_VANISHED_DEVICE=sentinel-vanished
disk_policy_transport_is_usb "$VALUE"
rc=$?
printf '%s\\n' "$rc"
printf '%s|%s|%s|%s|%s\\n' \\
  "$DISK_POLICY_RESOLVED_DEVICE" "$DISK_POLICY_RESOLVED_LABEL_KEY" \\
  "$DISK_POLICY_RESOLVE_ERROR" "$DISK_POLICY_RESOLVE_REASON" \\
  "$DISK_POLICY_VANISHED_DEVICE"
"""
        for name, value, expected in TRANSPORT_CASES:
            with self.subTest(name=name):
                result = run_bash(script, {"POLICY": str(POLICY), "VALUE": value})
                self.assertEqual(result.stderr, "")
                self.assertEqual(result.stdout.splitlines(), [
                    f"{0 if expected else 1}",
                    "sentinel-device|sentinel-key|sentinel-error|sentinel-reason|sentinel-vanished",
                ])

    def test_current_adapters_match_frozen_old_results(self) -> None:
        frozen = json.loads(FIXTURE.read_text(encoding="utf-8"))
        by_name = {(item["adapter"], item["name"]): item["result"] for item in frozen["cases"]}
        self.assertEqual(frozen["schema"], 1)
        self.assertEqual(len(frozen["cases"]), 24)
        self.assertEqual(set(by_name), {(case.adapter, case.name) for case in ADAPTER_CASES})
        self.assertEqual(len(by_name), len(ADAPTER_CASES))
        for case in ADAPTER_CASES:
            with self.subTest(adapter=case.adapter, name=case.name):
                old_result = by_name[(case.adapter, case.name)]
                current_result = run_adapter(REPO_ROOT / "pi" / "scripts", case)
                # Compare argv verbatim, including each caller's original -- placement.
                self.assertEqual(current_result, old_result)

    def test_adapter_success_command_shapes_and_order(self) -> None:
        umount_case = ADAPTER_CASES[0]
        umount_result = run_adapter(REPO_ROOT / "pi" / "scripts", umount_case)
        self.assertEqual(umount_result["status"], 0)
        self.assertEqual(
            umount_result["commands"][:2],
            [
                ["lsblk", "-s", "-nrpo", "NAME,TYPE", "/dev/sdz1"],
                ["lsblk", "-dnro", "TRAN", "/dev/sdz"],
            ],
        )

        diskctl_case = ADAPTER_CASES[13]
        diskctl_result = run_adapter(REPO_ROOT / "pi" / "scripts", diskctl_case)
        self.assertEqual(diskctl_result["status"], 0)
        self.assertEqual(
            diskctl_result["commands"][:4],
            [
                ["sudo", "<TMP>/blkid", "-s", "TYPE", "-o", "value", "--", "/dev/sdz1"],
                ["blkid", "-s", "TYPE", "-o", "value", "--", "/dev/sdz1"],
                ["lsblk", "-s", "-nrpo", "NAME,TYPE", "--", "/dev/sdz1"],
                ["lsblk", "-dnro", "TRAN", "--", "/dev/sdz"],
            ],
        )


if __name__ == "__main__":
    unittest.main()
