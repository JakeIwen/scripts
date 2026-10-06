from __future__ import annotations

import unittest

from ._umount_phase_support import (
    ORACLE_SHA256,
    SOURCE,
    ParentCheck,
    Result,
    Resolution,
    Scenario,
    _run_scenario,
    _source_sha256,
    load_golden,
)


def attached(
    *,
    device: str = "/dev/sdz1",
    parent: str = "/dev/sdz",
    mounts: str = "",
) -> Resolution:
    return Resolution(device=device, parent=parent, mounts=mounts)


def discovery_error(message: str = "synthetic discovery failure") -> Resolution:
    return Resolution(status=2, error=message)


def vanished(device: str = "/dev/vanished-test") -> Resolution:
    return Resolution(
        status=2,
        reason="vanished-udev-mapping",
        error=f"udev mapping points to vanished device {device}",
        vanished_device=device,
    )


MOUNTED = attached(mounts="/mnt/movingparts")
UNMOUNTED = attached()


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("help_long", args=("--help",)),
    Scenario("invalid_option", args=("--not-an-option",)),
    Scenario("multiple_positional_labels", args=("movingparts", "bigboi")),
    Scenario("reject_all_with_spindown", args=("--all", "--spindown")),
    Scenario("reject_emergency_without_spindown", args=("--emergency",)),
    Scenario("reject_emergency_with_label", args=("--spindown", "--emergency", "movingparts")),
    Scenario("clear_state_empty", args=("--clear-spindown-state",)),
    Scenario(
        "clear_state_existing",
        args=("--clear-spindown-state",),
        state_path_kind="dir",
    ),
    Scenario(
        "clear_state_prepare_failure",
        args=("--clear-spindown-state",),
        state_path_kind="dir",
        prepare_state=Result(1, "state metadata rejected"),
    ),
    Scenario("clear_state_conflict", args=("--clear-spindown-state", "--dry-run")),
    Scenario(
        "dry_run_mounted",
        args=("--dry-run", "movingparts"),
        resolutions={"movingparts": (MOUNTED,)},
    ),
    Scenario("dry_run_absent", args=("--dry-run", "movingparts")),
    Scenario(
        "dry_run_unmounted",
        args=("--dry-run", "movingparts"),
        resolutions={"movingparts": (UNMOUNTED,)},
    ),
    Scenario(
        "preflight_discovery_failure",
        args=("movingparts",),
        resolutions={"movingparts": (discovery_error(),)},
    ),
    Scenario(
        "preflight_unexpected_mount",
        args=("movingparts",),
        resolutions={"movingparts": (attached(mounts="/mnt/wrong"),)},
    ),
    Scenario(
        "preflight_multi_label_failure_prevents_mutation",
        args=("--all",),
        mount_labels=("movingparts", "bigboi"),
        manual_mount_labels=(),
        resolutions={
            "movingparts": (MOUNTED,),
            "bigboi": (attached(device="/dev/sdy1", parent="/dev/sdy", mounts="/mnt/wrong"),),
        },
    ),
    Scenario(
        "spindown_reject_non_hdd",
        args=("--spindown", "EXFAT512"),
    ),
    Scenario(
        "spindown_state_prepare_failure",
        args=("--spindown", "movingparts"),
        prepare_state=Result(1, "cannot prepare shared state"),
    ),
    Scenario(
        "spindown_hd_idle_unavailable",
        args=("--spindown", "movingparts"),
        resolutions={"movingparts": (UNMOUNTED,)},
        hd_idle_available=False,
    ),
    Scenario(
        "marker_skip",
        args=("--spindown", "movingparts"),
        current_markers={"movingparts": "/dev/sdz"},
    ),
    Scenario(
        "all_vanished_accepted",
        args=("--all",),
        mount_labels=("movingparts",),
        manual_mount_labels=(),
        resolutions={"movingparts": (vanished(),)},
    ),
    Scenario(
        "all_vanished_rejected",
        args=("--all",),
        mount_labels=("movingparts",),
        manual_mount_labels=(),
        resolutions={"movingparts": (vanished(),)},
        accept_vanished=Result(1, "vanished mapping still mounted"),
    ),
    Scenario(
        "samba_drain_failure",
        args=("movingparts",),
        resolutions={"movingparts": (MOUNTED,)},
        samba_drain=Result(3, "drain refused"),
    ),
    Scenario(
        "backup_abort_failure",
        args=("movingparts",),
        resolutions={"movingparts": (MOUNTED,)},
        abort_backup=Result(1, "abort refused"),
    ),
    Scenario(
        "samba_close_failure_detail",
        args=("movingparts",),
        resolutions={"movingparts": (MOUNTED,)},
        samba_close=Result(4, "ERROR: share MovingParts still has clients\nERROR: close denied"),
    ),
    Scenario(
        "samba_close_failure_generic",
        args=("movingparts",),
        resolutions={"movingparts": (MOUNTED,)},
        samba_close=Result(4, "close failed without structured detail"),
    ),
    Scenario(
        "emergency_samba_fallback_success",
        args=("--spindown", "--emergency"),
        hdd_labels=("movingparts",),
        resolutions={"movingparts": (MOUNTED, UNMOUNTED)},
        samba_close=Result(4, "scoped close failed"),
        unmounts={"/mnt/movingparts": (Result(),)},
        findmnt_sources={"/dev/sdz1": (Result(1),)},
    ),
    Scenario(
        "emergency_samba_fallback_failure",
        args=("--spindown", "--emergency"),
        hdd_labels=("movingparts",),
        resolutions={"movingparts": (MOUNTED,)},
        samba_close=Result(4, "scoped close failed"),
        emergency_samba=Result(1, "global stop failed"),
    ),
    Scenario(
        "qbit_failure_continues",
        args=("movingparts",),
        resolutions={"movingparts": (MOUNTED,)},
        qbit_stop=Result(1),
        unmounts={"/mnt/movingparts": (Result(),)},
        findmnt_sources={"/dev/sdz1": (Result(1),)},
    ),
    Scenario(
        "unmount_success",
        args=("bigboi",),
        resolutions={"bigboi": (attached(device="/dev/sdy1", parent="/dev/sdy", mounts="/mnt/bigboi"),)},
        unmounts={"/mnt/bigboi": (Result(),)},
        findmnt_sources={"/dev/sdy1": (Result(1),)},
    ),
    Scenario(
        "unmount_retry_success",
        args=("movingparts",),
        resolutions={"movingparts": (MOUNTED,)},
        unmounts={"/mnt/movingparts": (Result(32, "busy"), Result())},
        findmnt_sources={"/dev/sdz1": (Result(0, "/mnt/movingparts"), Result(1))},
        holder_summaries={"/mnt/movingparts": ("pi 111 f.... worker",)},
    ),
    Scenario(
        "unmount_reconciled_first_attempt",
        args=("movingparts",),
        resolutions={"movingparts": (MOUNTED,)},
        unmounts={"/mnt/movingparts": (Result(124, "timed out"),)},
        findmnt_sources={"/dev/sdz1": (Result(1), Result(1))},
    ),
    Scenario(
        "unmount_reconciled_retry",
        args=("movingparts",),
        resolutions={"movingparts": (MOUNTED,)},
        unmounts={"/mnt/movingparts": (Result(32, "busy"), Result(124, "timed out"))},
        findmnt_sources={
            "/dev/sdz1": (Result(0, "/mnt/movingparts"), Result(1), Result(1)),
        },
        holder_summaries={"/mnt/movingparts": ("pi 222 f.... worker",)},
    ),
    Scenario(
        "unmount_failure_continues_independent_disk",
        args=("--all",),
        mount_labels=("movingparts", "bigboi"),
        manual_mount_labels=(),
        resolutions={
            "movingparts": (MOUNTED,),
            "bigboi": (attached(device="/dev/sdy1", parent="/dev/sdy", mounts="/mnt/bigboi"),),
        },
        unmounts={
            "/mnt/movingparts": (Result(32, "busy one"), Result(32, "busy two")),
            "/mnt/bigboi": (Result(),),
        },
        findmnt_sources={
            "/dev/sdz1": (Result(0, "/mnt/movingparts"), Result(0, "/mnt/movingparts")),
            "/dev/sdy1": (Result(1),),
        },
        holder_summaries={
            "/mnt/movingparts": ("pi 333 f.... first", "pi 444 f.... final"),
        },
    ),
    Scenario(
        "post_unmount_still_mounted",
        args=("movingparts",),
        resolutions={"movingparts": (MOUNTED,)},
        unmounts={"/mnt/movingparts": (Result(),)},
        findmnt_sources={"/dev/sdz1": (Result(0, "/mnt/movingparts"),)},
    ),
    Scenario(
        "post_unmount_discovery_error",
        args=("movingparts",),
        resolutions={"movingparts": (MOUNTED,)},
        unmounts={"/mnt/movingparts": (Result(),)},
        findmnt_sources={"/dev/sdz1": (Result(2, "findmnt unavailable"),)},
    ),
    Scenario(
        "spindown_success",
        args=("--spindown", "movingparts"),
        resolutions={"movingparts": (UNMOUNTED, UNMOUNTED)},
    ),
    Scenario(
        "spindown_reresolve_absent",
        args=("--spindown", "movingparts"),
        resolutions={"movingparts": (UNMOUNTED, Resolution(status=1))},
    ),
    Scenario(
        "spindown_reresolve_vanished",
        args=("--spindown", "movingparts"),
        resolutions={"movingparts": (UNMOUNTED, vanished())},
    ),
    Scenario(
        "spindown_mapping_changed",
        args=("--spindown", "movingparts"),
        resolutions={
            "movingparts": (
                UNMOUNTED,
                attached(device="/dev/sdy1", parent="/dev/sdy"),
            ),
        },
    ),
    Scenario(
        "spindown_failure_continues_independent_disk",
        args=("--spindown",),
        hdd_labels=("movingparts", "bigboi"),
        resolutions={
            "movingparts": (
                UNMOUNTED,
                attached(device="/dev/sdx1", parent="/dev/sdx"),
            ),
            "bigboi": (
                attached(device="/dev/sdy1", parent="/dev/sdy"),
                attached(device="/dev/sdy1", parent="/dev/sdy"),
            ),
        },
    ),
    Scenario(
        "spindown_remounted",
        args=("--spindown", "movingparts"),
        resolutions={"movingparts": (UNMOUNTED, MOUNTED)},
    ),
    Scenario(
        "spindown_other_partition_mounted",
        args=("--spindown", "movingparts"),
        resolutions={"movingparts": (UNMOUNTED, UNMOUNTED)},
        parent_checks={
            "/dev/sdz": ParentCheck(
                1,
                "refusing to spin down /dev/sdz: /dev/sdz2 is still mounted at /mnt/other",
            ),
        },
    ),
    Scenario(
        "spindown_duplicate_parent_once",
        args=("--spindown",),
        hdd_labels=("movingparts", "bigboi"),
        resolutions={
            "movingparts": (UNMOUNTED, UNMOUNTED),
            "bigboi": (
                attached(device="/dev/sdz2"),
                attached(device="/dev/sdz2"),
            ),
        },
    ),
    Scenario(
        "hd_idle_nonzero_diagnostic",
        args=("--spindown", "movingparts"),
        resolutions={"movingparts": (UNMOUNTED, UNMOUNTED)},
        hd_idle={"/dev/sdz": Result(1, "device open failed")},
    ),
    Scenario(
        "hd_idle_zero_with_diagnostic",
        args=("--spindown", "movingparts"),
        resolutions={"movingparts": (UNMOUNTED, UNMOUNTED)},
        hd_idle={"/dev/sdz": Result(0, "unexpected diagnostic")},
    ),
    Scenario(
        "spindown_state_write_failure",
        args=("--spindown", "movingparts"),
        resolutions={"movingparts": (UNMOUNTED, UNMOUNTED)},
        write_state={"movingparts": Result(1, "cannot record spindown state")},
    ),
    Scenario(
        "all_unknown_mount",
        args=("--all",),
        mount_labels=("movingparts",),
        manual_mount_labels=(),
        all_mounts=Result(0, "/dev/sdx1 /mnt/unknown"),
        report_mounts=Result(0, "/dev/sdx1 /mnt/unknown"),
    ),
    Scenario(
        "all_final_discovery_failure",
        args=("--all",),
        mount_labels=("movingparts",),
        manual_mount_labels=(),
        all_mounts=Result(2, "findmnt final failure"),
    ),
    Scenario(
        "all_clean",
        args=("--all",),
        mount_labels=("movingparts",),
        manual_mount_labels=(),
    ),
)


class PhaseGoldenRegressionTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.bash, cls.bash_version, cls.golden = load_golden()

    def test_frozen_fixture_covers_exact_named_cases(self) -> None:
        expected_names = {scenario.name for scenario in SCENARIOS}
        self.assertEqual(1, self.golden["schema"])
        self.assertEqual(len(SCENARIOS), self.golden["case_count"])
        self.assertEqual(expected_names, set(self.golden["cases"]))
        self.assertEqual(50, len(SCENARIOS))
        self.assertEqual(ORACLE_SHA256, self.golden["oracle"]["sha256"])
        self.assertEqual("pre-extraction umount_disks.sh", self.golden["oracle"]["name"])

        remount_trace = self.golden["cases"]["spindown_remounted"]["trace"]
        self.assertIn("resolve:movingparts:1:status=0:device=/dev/sdz1:parent=/dev/sdz:mounts=none\n", remount_trace)
        self.assertIn("resolve:movingparts:2:status=0:device=/dev/sdz1:parent=/dev/sdz:mounts=/mnt/movingparts\n", remount_trace)
        self.assertNotIn("samba:drain", remount_trace)


def _make_case_test(scenario: Scenario):
    def test(self: PhaseGoldenRegressionTests) -> None:
        actual = _run_scenario(SOURCE, self.bash, scenario)
        self.assertEqual(
            self.golden["cases"][scenario.name],
            actual,
            f"Bash {self.bash_version}; current source {_source_sha256(SOURCE)}",
        )

    test.__name__ = f"test_{scenario.name}"
    test.__doc__ = f"Match the frozen pre-extraction oracle for {scenario.name}."
    return test


for _index, _scenario in enumerate(SCENARIOS, 1):
    setattr(
        PhaseGoldenRegressionTests,
        f"test_{_index:02d}_{_scenario.name}",
        _make_case_test(_scenario),
    )


if __name__ == "__main__":
    unittest.main()
