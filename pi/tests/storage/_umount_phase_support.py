from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE = REPO_ROOT / "pi" / "scripts" / "umount_disks.sh"
GOLDEN = Path(__file__).with_name("fixtures") / "umount_disks_phases_golden.json"
ORACLE_SHA256 = "be5f82d1b44097c47ec31332bf911eeb0d348680a0ae8eb28985ba7bc1d6cfe6"


@dataclass(frozen=True)
class Result:
    status: int = 0
    output: str = ""


@dataclass(frozen=True)
class Resolution:
    status: int = 0
    device: str = "/dev/sdz1"
    parent: str = "/dev/sdz"
    mounts: str = ""
    reason: str = ""
    error: str = ""
    vanished_device: str = ""


@dataclass(frozen=True)
class ParentCheck:
    status: int = 0
    error: str = ""


@dataclass(frozen=True)
class Scenario:
    name: str
    args: tuple[str, ...] = ()
    hdd_labels: tuple[str, ...] = ("movingparts", "bigboi")
    mount_labels: tuple[str, ...] = ("movingparts",)
    manual_mount_labels: tuple[str, ...] = ("bigboi",)
    resolutions: Mapping[str, tuple[Resolution, ...]] = field(default_factory=dict)
    current_markers: Mapping[str, str] = field(default_factory=dict)
    state_path_kind: str = "absent"
    prepare_state: Result = Result()
    clear_state: Result = Result()
    hd_idle_available: bool = True
    accept_vanished: Result = Result()
    samba_drain: Result = Result()
    samba_close: Result = Result()
    abort_backup: Result = Result()
    qbit_stop: Result = Result()
    emergency_samba: Result = Result()
    sync_mount: Mapping[str, Result] = field(default_factory=dict)
    unmounts: Mapping[str, tuple[Result, ...]] = field(default_factory=dict)
    findmnt_sources: Mapping[str, tuple[Result, ...]] = field(default_factory=dict)
    holder_summaries: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    evict_mount: Mapping[str, Result] = field(default_factory=dict)
    parent_checks: Mapping[str, ParentCheck] = field(default_factory=dict)
    hd_idle: Mapping[str, Result] = field(default_factory=dict)
    write_state: Mapping[str, Result] = field(default_factory=dict)
    all_mounts: Result = Result()
    report_mounts: Result = Result()


def _quote(value: object) -> str:
    return shlex.quote(str(value))


def _array(name: str, values: Sequence[str]) -> str:
    return f"{name}=({' '.join(_quote(value) for value in values)})"


def _assoc(name: str, values: Mapping[str, object]) -> str:
    body = "\n".join(
        f"  [{_quote(key)}]={_quote(value)}" for key, value in sorted(values.items())
    )
    return f"declare -A {name}=(\n{body}\n)"


def _sequence_fields(
    mapping: Mapping[str, Sequence[object]], fields: Mapping[str, str]
) -> dict[str, dict[str, object]]:
    output = {array_name: {} for array_name in fields.values()}
    for subject, sequence in mapping.items():
        for index, item in enumerate(sequence, 1):
            for attribute, array_name in fields.items():
                output[array_name][f"{subject}#{index}"] = getattr(item, attribute)
        if sequence:
            for attribute, array_name in fields.items():
                output[array_name][f"{subject}#last"] = getattr(sequence[-1], attribute)
    return output


def _scenario_config(scenario: Scenario) -> str:
    chunks = [
        _array("ARGS", scenario.args),
        _array("HDD_LABELS", scenario.hdd_labels),
        _array("MOUNT_LABELS", scenario.mount_labels),
        _array("MANUAL_MOUNT_LABELS", scenario.manual_mount_labels),
        f"STATE_PATH_KIND={_quote(scenario.state_path_kind)}",
        f"PREPARE_STATE_STATUS={scenario.prepare_state.status}",
        f"PREPARE_STATE_OUTPUT={_quote(scenario.prepare_state.output)}",
        f"CLEAR_STATE_STATUS={scenario.clear_state.status}",
        f"CLEAR_STATE_OUTPUT={_quote(scenario.clear_state.output)}",
        f"HD_IDLE_AVAILABLE={int(scenario.hd_idle_available)}",
        f"ACCEPT_VANISHED_STATUS={scenario.accept_vanished.status}",
        f"ACCEPT_VANISHED_OUTPUT={_quote(scenario.accept_vanished.output)}",
        f"SAMBA_DRAIN_STATUS={scenario.samba_drain.status}",
        f"SAMBA_DRAIN_OUTPUT={_quote(scenario.samba_drain.output)}",
        f"SAMBA_CLOSE_STATUS={scenario.samba_close.status}",
        f"SAMBA_CLOSE_OUTPUT={_quote(scenario.samba_close.output)}",
        f"ABORT_STATUS={scenario.abort_backup.status}",
        f"ABORT_OUTPUT={_quote(scenario.abort_backup.output)}",
        f"QBIT_STATUS={scenario.qbit_stop.status}",
        f"QBIT_OUTPUT={_quote(scenario.qbit_stop.output)}",
        f"EMERGENCY_SAMBA_STATUS={scenario.emergency_samba.status}",
        f"EMERGENCY_SAMBA_OUTPUT={_quote(scenario.emergency_samba.output)}",
        f"ALL_MOUNTS_STATUS={scenario.all_mounts.status}",
        f"ALL_MOUNTS_OUTPUT={_quote(scenario.all_mounts.output)}",
        f"REPORT_MOUNTS_STATUS={scenario.report_mounts.status}",
        f"REPORT_MOUNTS_OUTPUT={_quote(scenario.report_mounts.output)}",
        _assoc("STATE_CURRENT", scenario.current_markers),
    ]

    field_groups = (
        _sequence_fields(
            scenario.resolutions,
            {
                "status": "RESOLVE_STATUS",
                "device": "RESOLVE_DEVICE",
                "parent": "RESOLVE_PARENT",
                "mounts": "RESOLVE_MOUNTS",
                "reason": "RESOLVE_REASON",
                "error": "RESOLVE_ERROR",
                "vanished_device": "RESOLVE_VANISHED",
            },
        ),
        _sequence_fields(
            scenario.unmounts,
            {"status": "UNMOUNT_STATUS", "output": "UNMOUNT_OUTPUT"},
        ),
        _sequence_fields(
            scenario.findmnt_sources,
            {"status": "FINDMNT_STATUS", "output": "FINDMNT_OUTPUT"},
        ),
    )
    for group in field_groups:
        chunks.extend(_assoc(name, values) for name, values in group.items())

    holder_values: dict[str, str] = {}
    for mountpoint, summaries in scenario.holder_summaries.items():
        for index, summary in enumerate(summaries, 1):
            holder_values[f"{mountpoint}#{index}"] = summary
        if summaries:
            holder_values[f"{mountpoint}#last"] = summaries[-1]
    chunks.append(_assoc("HOLDER_TEXT", holder_values))

    for name, mapping, attributes in (
        ("SYNC", scenario.sync_mount, ("status", "output")),
        ("EVICT", scenario.evict_mount, ("status", "output")),
        ("HD_IDLE", scenario.hd_idle, ("status", "output")),
        ("WRITE_STATE", scenario.write_state, ("status", "output")),
        ("PARENT", scenario.parent_checks, ("status", "error")),
    ):
        for attribute in attributes:
            chunks.append(
                _assoc(
                    f"{name}_{attribute.upper()}",
                    {key: getattr(value, attribute) for key, value in mapping.items()},
                )
            )
    return "\n\n".join(chunks) + "\n"


def _transform_source(source_text: str) -> str:
    replacements = (
        (
            "[[ ! -x /usr/sbin/hd-idle ]]",
            "! ud_fake_hd_idle_available",
            1,
        ),
        (
            '/usr/bin/sudo /home/pi/scripts/backup/abort_backup.sh "${abort_args[@]}"',
            'ud_fake_abort_backup "${abort_args[@]}"',
            1,
        ),
        (
            '/usr/bin/sudo /usr/sbin/hd-idle -t "$parent_name"',
            'ud_fake_hd_idle "$parent_name"',
            1,
        ),
        (
            "/usr/bin/findmnt -rn -o SOURCE,TARGET",
            "ud_fake_all_mounts",
            1,
        ),
        (
            "/usr/bin/findmnt -rn -t ext4,exfat,hfsplus -o SOURCE,TARGET",
            "ud_fake_report_mounts",
            1,
        ),
        (
            "/usr/bin/grep '^/dev/sd'",
            "ud_fake_grep_scsi",
            2,
        ),
    )
    transformed = source_text
    for old, new, expected_count in replacements:
        actual_count = transformed.count(old)
        if actual_count != expected_count:
            raise AssertionError(
                f"safe-copy replacement count for {old!r}: "
                f"expected {expected_count}, got {actual_count}"
            )
        transformed = transformed.replace(old, new)
    return transformed


DISK_POLICY_STUB = """\
MOUNT_LABELS=()
MANUAL_MOUNT_LABELS=()
HDD_LABELS=()
DISK_POLICY_RESOLVE_REASON=
DISK_POLICY_RESOLVE_ERROR=
DISK_POLICY_VANISHED_DEVICE=
disk_policy_resolve_exact_label() { return 1; }
disk_policy_samba_share_name() { return 1; }
"""


DRIVER = r"""#!/usr/bin/env bash

export UMOUNT_DISKS_LIBRARY_ONLY=1
source "$HARNESS_DIR/umount_disks.sh"
source "$HARNESS_DIR/scenario.sh"
samba_share_control=fake_samba_share_control

trace() {
  printf '%s\n' "$*" >> "$TRACE_FILE"
}

bump() {
  local key=$1 safe path value=0
  safe=${key//\//_}
  safe=${safe//:/_}
  safe=${safe// /_}
  path="$COUNTER_DIR/$safe"
  if [[ -f "$path" ]]; then
    IFS= read -r value < "$path"
  fi
  ((value += 1))
  printf '%s\n' "$value" > "$path"
  REPLY=$value
}

emit_output() {
  [[ -z "$1" ]] || printf '%s\n' "$1"
}

ud_record_failure() {
  local message=$1
  printf 'ERROR: %s\n' "$message" >&2
  UD_FAILURES+=("$message")
  trace "failure:$message"
}

ud_notify_failures() {
  trace "notify-failures:$1"
}

ud_notify_recovery() {
  trace "notify-recovery:$1"
}

ud_prepare_spindown_state_dir() {
  trace "prepare-state"
  if (( PREPARE_STATE_STATUS != 0 )); then
    ud_record_failure "${PREPARE_STATE_OUTPUT:-spindown state preparation failed}"
  fi
  return "$PREPARE_STATE_STATUS"
}

ud_clear_spindown_state() {
  trace "clear-state"
  if (( CLEAR_STATE_STATUS != 0 )) && [[ -n "$CLEAR_STATE_OUTPUT" ]]; then
    ud_record_failure "$CLEAR_STATE_OUTPUT"
  fi
  return "$CLEAR_STATE_STATUS"
}

ud_spindown_state_is_current() {
  local label=$1
  trace "marker-check:$label"
  if [[ -n "${STATE_CURRENT[$label]+present}" ]]; then
    UD_FAST_PARENT=${STATE_CURRENT[$label]}
    return 0
  fi
  return 1
}

ud_resolve_label() {
  local label=$1 key last status
  bump "resolve:$label"
  key="$label#$REPLY"
  last="$label#last"
  status=${RESOLVE_STATUS[$key]-${RESOLVE_STATUS[$last]-1}}
  UD_DEVICE=${RESOLVE_DEVICE[$key]-${RESOLVE_DEVICE[$last]-}}
  UD_PARENT=${RESOLVE_PARENT[$key]-${RESOLVE_PARENT[$last]-}}
  UD_MOUNTS=${RESOLVE_MOUNTS[$key]-${RESOLVE_MOUNTS[$last]-}}
  DISK_POLICY_RESOLVE_REASON=${RESOLVE_REASON[$key]-${RESOLVE_REASON[$last]-}}
  DISK_POLICY_RESOLVE_ERROR=${RESOLVE_ERROR[$key]-${RESOLVE_ERROR[$last]-}}
  DISK_POLICY_VANISHED_DEVICE=${RESOLVE_VANISHED[$key]-${RESOLVE_VANISHED[$last]-}}
  trace "resolve:$label:$REPLY:status=$status:device=${UD_DEVICE:-none}:parent=${UD_PARENT:-none}:mounts=${UD_MOUNTS:-none}"
  if (( status != 0 && status != 1 )) &&
      [[ "$DISK_POLICY_RESOLVE_REASON" != vanished-udev-mapping ]]; then
    ud_record_failure "${DISK_POLICY_RESOLVE_ERROR:-synthetic discovery failure}"
  fi
  return "$status"
}

ud_accept_vanished_label_for_all() {
  local label=$1 device=$2
  trace "accept-vanished:$label:$device"
  if (( ACCEPT_VANISHED_STATUS != 0 )); then
    ud_record_failure "${ACCEPT_VANISHED_OUTPUT:-vanished mapping rejected}"
    return "$ACCEPT_VANISHED_STATUS"
  fi
  printf '%s\n' "$label: accepted vanished mapping $device"
  return 0
}

disk_policy_samba_share_name() {
  trace "share-name:$1"
  case "$1" in
    movingparts) printf '%s\n' MovingParts ;;
    bigboi) printf '%s\n' BigBoi ;;
    mbp2tbkup) printf '%s\n' mbp2tbkup ;;
    EXFAT512) printf '%s\n' EXFAT512 ;;
    *) return 1 ;;
  esac
}

fake_samba_share_control() {
  local action=$1
  shift
  trace "samba:$action:$*"
  case "$action" in
    drain)
      emit_output "$SAMBA_DRAIN_OUTPUT"
      return "$SAMBA_DRAIN_STATUS"
      ;;
    close)
      emit_output "$SAMBA_CLOSE_OUTPUT"
      return "$SAMBA_CLOSE_STATUS"
      ;;
    *) return 2 ;;
  esac
}

ud_fake_abort_backup() {
  trace "abort-backup:${*:-normal}"
  emit_output "$ABORT_OUTPUT"
  return "$ABORT_STATUS"
}

ud_kill_torrent_client() {
  trace "qbit-stop:$1"
  emit_output "$QBIT_OUTPUT"
  return "$QBIT_STATUS"
}

ud_emergency_stop_samba() {
  trace "emergency-samba"
  emit_output "$EMERGENCY_SAMBA_OUTPUT"
  return "$EMERGENCY_SAMBA_STATUS"
}

ud_sync_mount() {
  local mountpoint=$1 status=${SYNC_STATUS[$1]:-0} output=${SYNC_OUTPUT[$1]:-}
  trace "sync:$mountpoint"
  emit_output "$output"
  return "$status"
}

ud_normal_unmount() {
  local mountpoint=$1 key last status output
  bump "unmount:$mountpoint"
  key="$mountpoint#$REPLY"
  last="$mountpoint#last"
  status=${UNMOUNT_STATUS[$key]-${UNMOUNT_STATUS[$last]-0}}
  output=${UNMOUNT_OUTPUT[$key]-${UNMOUNT_OUTPUT[$last]-}}
  trace "unmount:$mountpoint:$REPLY:status=$status"
  emit_output "$output"
  return "$status"
}

ud_findmnt_source_targets() {
  local device=$1 key last status output
  bump "findmnt:$device"
  key="$device#$REPLY"
  last="$device#last"
  status=${FINDMNT_STATUS[$key]-${FINDMNT_STATUS[$last]-1}}
  output=${FINDMNT_OUTPUT[$key]-${FINDMNT_OUTPUT[$last]-}}
  trace "findmnt-source:$device:$REPLY:status=$status:output=${output:-none}"
  emit_output "$output"
  return "$status"
}

ud_mount_holder_summary() {
  local mountpoint=$1 key last output
  bump "holders:$mountpoint"
  key="$mountpoint#$REPLY"
  last="$mountpoint#last"
  output=${HOLDER_TEXT[$key]-${HOLDER_TEXT[$last]-no userspace mount holder identified}}
  trace "holder-summary:$mountpoint:$REPLY:$output"
  printf '%s\n' "$output"
}

ud_evict_mount_holders() {
  local mountpoint=$1 status=${EVICT_STATUS[$1]:-0} output=${EVICT_OUTPUT[$1]:-}
  trace "evict:$mountpoint"
  emit_output "$output"
  return "$status"
}

ud_parent_is_unmounted() {
  local parent=$1 status=${PARENT_STATUS[$1]:-0} error=${PARENT_ERROR[$1]:-}
  trace "parent-check:$parent:status=$status"
  if (( status != 0 )) && [[ -n "$error" ]]; then
    ud_record_failure "$error"
  fi
  return "$status"
}

ud_fake_hd_idle_available() {
  trace "hd-idle-available:$HD_IDLE_AVAILABLE"
  (( HD_IDLE_AVAILABLE == 1 ))
}

ud_fake_hd_idle() {
  local parent="/dev/$1" status=${HD_IDLE_STATUS["/dev/$1"]:-0}
  local output=${HD_IDLE_OUTPUT["/dev/$1"]:-}
  trace "hd-idle:$parent:status=$status:output=${output:-none}"
  emit_output "$output"
  return "$status"
}

ud_write_spindown_state() {
  local label=$1 device=$2 parent=$3
  local status=${WRITE_STATE_STATUS[$label]:-0} output=${WRITE_STATE_OUTPUT[$label]:-}
  trace "write-state:$label:$device:$parent:status=$status"
  if (( status != 0 )); then
    ud_record_failure "${output:-spindown state write failed}"
  fi
  return "$status"
}

ud_fake_all_mounts() {
  trace "all-mounts:status=$ALL_MOUNTS_STATUS"
  emit_output "$ALL_MOUNTS_OUTPUT"
  return "$ALL_MOUNTS_STATUS"
}

ud_fake_report_mounts() {
  trace "report-mounts:status=$REPORT_MOUNTS_STATUS"
  emit_output "$REPORT_MOUNTS_OUTPUT"
  return "$REPORT_MOUNTS_STATUS"
}

ud_fake_grep_scsi() {
  local line matched=1
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" == /dev/sd* ]]; then
      printf '%s\n' "$line"
      matched=0
    fi
  done
  return "$matched"
}

umount_disks_main "${ARGS[@]}"
exit $?
"""


DIRECT_PHASE_DRIVER = r"""#!/usr/bin/env bash

export UMOUNT_DISKS_LIBRARY_ONLY=1
source "$HARNESS_DIR/umount_disks.sh"

# A missing guard must never turn this regression test into a live alert.
ud_notify_failures() {
  printf '%s\n' notify-failures >> "$SIDE_EFFECT_FILE"
}
ud_notify_recovery() {
  printf '%s\n' notify-recovery >> "$SIDE_EFFECT_FILE"
}

case "$PHASE" in
  ud_preflight)
    spindown=1
    dry_run=0
    ud_prepare_spindown_state_dir() {
      printf '%s\n' prepare-state >> "$SIDE_EFFECT_FILE"
      return 1
    }
    ;;
  ud_unmount_all)
    spindown=0
    dry_run=0
    emergency=0
    mounted_labels=(movingparts)
    disk_policy_samba_share_name() {
      printf '%s\n' share-name >> "$SIDE_EFFECT_FILE"
      return 1
    }
    ud_direct_samba() {
      printf '%s\n' samba-control >> "$SIDE_EFFECT_FILE"
      return 1
    }
    samba_share_control=ud_direct_samba
    ;;
  ud_spindown)
    spindown=1
    attached_labels=(movingparts)
    ud_resolve_label() {
      printf '%s\n' resolve-label >> "$SIDE_EFFECT_FILE"
      DISK_POLICY_RESOLVE_REASON=
      return 1
    }
    ;;
  *)
    printf 'unknown phase: %s\n' "$PHASE" >&2
    exit 2
    ;;
esac

"$PHASE"
exit $?
"""


def _harness_environment(**updates: str) -> dict[str, str]:
    environment = os.environ.copy()
    for variable in tuple(environment):
        if variable.startswith("UMOUNT_DISKS_") or variable in {
            "BASH_ENV",
            "CDPATH",
            "ENV",
        }:
            environment.pop(variable)
    environment.update({"LC_ALL": "C", **updates})
    return environment


def _run_scenario(source: Path, bash: Path, scenario: Scenario) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="umount-phase-regression-") as temp_name:
        root = Path(temp_name)
        harness = root / "harness"
        counters = root / "counters"
        harness.mkdir()
        counters.mkdir()
        state_dir = root / "state"
        if scenario.state_path_kind == "dir":
            state_dir.mkdir()
        elif scenario.state_path_kind == "file":
            state_dir.write_text("not a directory\n", encoding="utf-8")
        elif scenario.state_path_kind != "absent":
            raise AssertionError(f"unknown state path kind: {scenario.state_path_kind}")

        source_text = source.read_text(encoding="utf-8")
        (harness / "umount_disks.sh").write_text(
            _transform_source(source_text), encoding="utf-8"
        )
        (harness / "disk_policy.sh").write_text(DISK_POLICY_STUB, encoding="utf-8")
        (harness / "scenario.sh").write_text(
            _scenario_config(scenario), encoding="utf-8"
        )
        driver = harness / "driver.sh"
        driver.write_text(DRIVER, encoding="utf-8")
        driver.chmod(0o700)
        trace_file = root / "trace"

        environment = _harness_environment(
            HARNESS_DIR=str(harness),
            TRACE_FILE=str(trace_file),
            COUNTER_DIR=str(counters),
            UMOUNT_DISKS_STATE_DIR=str(state_dir),
        )
        completed = subprocess.run(
            [str(bash), str(driver)],
            cwd=root,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        trace = trace_file.read_text(encoding="utf-8") if trace_file.exists() else ""
        marker = "$TMP"
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout.replace(temp_name, marker),
            "stderr": completed.stderr.replace(temp_name, marker),
            "trace": trace.replace(temp_name, marker),
        }


def _run_direct_phase(source: Path, bash: Path, phase: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="umount-phase-direct-") as temp_name:
        root = Path(temp_name)
        harness = root / "harness"
        harness.mkdir()
        (harness / "umount_disks.sh").write_text(
            source.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (harness / "disk_policy.sh").write_text(DISK_POLICY_STUB, encoding="utf-8")
        driver = harness / "driver.sh"
        driver.write_text(DIRECT_PHASE_DRIVER, encoding="utf-8")
        driver.chmod(0o700)
        side_effect_file = root / "side-effects"
        environment = _harness_environment(
            HARNESS_DIR=str(harness),
            PHASE=phase,
            SIDE_EFFECT_FILE=str(side_effect_file),
        )
        completed = subprocess.run(
            [str(bash), str(driver)],
            cwd=root,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        side_effects = (
            side_effect_file.read_text(encoding="utf-8")
            if side_effect_file.exists()
            else ""
        )
        marker = "$TMP"
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout.replace(temp_name, marker),
            "stderr": completed.stderr.replace(temp_name, marker),
            "side_effects": side_effects.replace(temp_name, marker),
        }


def _bash_major(path: Path) -> tuple[int, str]:
    completed = subprocess.run(
        [str(path), "-c", 'printf "%s|%s\\n" "${BASH_VERSINFO[0]}" "$BASH_VERSION"'],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0 or "|" not in completed.stdout:
        return 0, completed.stderr.strip() or "version probe failed"
    major, version = completed.stdout.strip().split("|", 1)
    return int(major), version


def _select_bash() -> tuple[Path, str]:
    override = os.environ.get("UMOUNT_PHASE_TEST_BASH")
    if override:
        candidates = [Path(override)]
    else:
        candidates = []
        path_bash = shutil.which("bash")
        if path_bash:
            candidates.append(Path(path_bash))
        candidates.extend((Path("/opt/homebrew/bin/bash"), Path("/usr/local/bin/bash")))

    seen: set[Path] = set()
    old_versions: list[str] = []
    for candidate in candidates:
        if candidate in seen or not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue
        seen.add(candidate)
        major, version = _bash_major(candidate)
        if major >= 4:
            return candidate, version
        old_versions.append(f"{candidate} ({version})")

    if override:
        detail = f"override {override!r} is unavailable or older than Bash 4"
    elif old_versions:
        detail = "only older Bash interpreters were found: " + ", ".join(old_versions)
    else:
        detail = "no Bash interpreter was found"
    message = f"phase regression needs Bash >=4; {detail}"
    if os.environ.get("UMOUNT_PHASE_TEST_REQUIRE") == "1":
        raise RuntimeError(
            f"{message}; UMOUNT_PHASE_TEST_REQUIRE=1 forbids skipping"
        )
    raise unittest.SkipTest(message)


def _source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()




def load_golden() -> tuple[Path, str, dict[str, object]]:
    bash, bash_version = _select_bash()
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return bash, bash_version, golden
