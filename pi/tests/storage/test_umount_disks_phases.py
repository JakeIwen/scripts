from __future__ import annotations

import unittest

from ._umount_phase_support import SOURCE


class PhaseStructureTests(unittest.TestCase):
    def test_extracted_phases_are_wired_in_order(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        for function_name in ("ud_preflight", "ud_unmount_all", "ud_spindown"):
            self.assertIn(f"{function_name}() {{", source)

        main = source.split("umount_disks_main() {", 1)[1]
        calls = (
            main.index("ud_preflight || return 1"),
            main.index("ud_unmount_all || return 1"),
            main.index("ud_spindown"),
        )
        self.assertEqual(tuple(sorted(calls)), calls)


if __name__ == "__main__":
    unittest.main()
