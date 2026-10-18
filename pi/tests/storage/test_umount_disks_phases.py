from __future__ import annotations

import unittest

from ._umount_phase_support import (
    SOURCE,
    ParentCheck,
    Result,
    Resolution,
    Scenario,
    _run_scenario,
    _select_bash,
)


def attached(
    *,
    device: str = "/dev/sdz1",
    parent: str = "/dev/sdz",
    mounts: str = "",
) -> Resolution:
    return Resolution(device=device, parent=parent, mounts=mounts)


MOUNTED = attached(mounts="/mnt/movingparts")
UNMOUNTED = attached()


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "dry_run_mounted",
        args=("--dry-run", "movingparts"),
        resolutions={"movingparts": (MOUNTED,)},
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
        "samba_close_failure_generic",
        args=("movingparts",),
        resolutions={"movingparts": (MOUNTED,)},
        samba_close=Result(4, "close failed without structured detail"),
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
        "hd_idle_zero_with_diagnostic",
        args=("--spindown", "movingparts"),
        resolutions={"movingparts": (UNMOUNTED, UNMOUNTED)},
        hd_idle={"/dev/sdz": Result(0, "unexpected diagnostic")},
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
)


class PhaseSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bash, _version = _select_bash()

def _make_case_test(scenario: Scenario):
    def test(self: PhaseSafetyTests) -> None:
        actual = _run_scenario(SOURCE, self.bash, scenario)
        successful = {"dry_run_mounted", "qbit_failure_continues", "spindown_success"}
        self.assertEqual(actual["returncode"], 0 if scenario.name in successful else 1)
        trace = actual["trace"]
        events = trace.splitlines()
        if scenario.name == "spindown_success":
            self.assertIn("hd-idle:/dev/sdz:status=0:output=none", events)
            self.assertLess(events.index("abort-backup:normal"),
                            events.index("hd-idle:/dev/sdz:status=0:output=none"))
        elif scenario.name == "hd_idle_zero_with_diagnostic":
            self.assertIn("hd-idle:/dev/sdz:status=0:output=unexpected diagnostic", events)
            self.assertNotIn("write-state:", trace)
        else:
            self.assertNotIn("hd-idle:", trace)
        if scenario.name == "qbit_failure_continues":
            required = ("samba:drain:movingparts", "abort-backup:normal",
                        "samba:close:movingparts", "sync:/mnt/movingparts",
                        "unmount:/mnt/movingparts:1:status=0")
            positions = [events.index(event) for event in required]
            self.assertEqual(positions, sorted(positions))
        elif scenario.name == "unmount_failure_continues_independent_disk":
            self.assertIn("unmount:/mnt/bigboi:1:status=0", events)
        elif not scenario.name.startswith("post_unmount_"):
            self.assertNotIn("unmount:", trace)

    test.__name__ = f"test_{scenario.name}"
    test.__doc__ = f"Preserve the disk-safety outcome for {scenario.name}."
    return test


for _index, _scenario in enumerate(SCENARIOS, 1):
    setattr(
        PhaseSafetyTests,
        f"test_{_index:02d}_{_scenario.name}",
        _make_case_test(_scenario),
    )


if __name__ == "__main__":
    unittest.main()
