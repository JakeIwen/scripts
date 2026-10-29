"""Frozen real-library oracle captured from the original Bash/Perl pipeline."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from pi.apps.media_organizer.alias_names import clean_title


class AliasNamesTests(unittest.TestCase):
    def test_real_library_names_match_original_pipeline(self):
        fixture = Path(__file__).with_name("alias_names_fixture.json")
        rows = json.loads(fixture.read_text(encoding="utf-8"))["names"]
        self.assertGreaterEqual(len(rows), 80)
        for path, expected in rows:
            with self.subTest(path=path):
                ext = path.rsplit(".", 1)[-1]
                self.assertEqual(clean_title(path, ext), expected)
                self.assertEqual(clean_title(path, "." + ext), expected)

    def test_substitution_order_and_boundaries_are_not_cosmetic(self):
        cases = [
            ("Film & Friends (2020) [1080p] x264-Unknown.mkv", "Film_and_Friends_2020"),
            ("Show/Show's.S01E01.1080p.WEB-DL.DDP5.1.H.264-GROUP.mkv", "Shows_S01E01"),
            ("Film.HEVC.mkv", "Film"),
            ("Film.X_265_Any_Group.mkv", "Film"),
            ("Film.720p.ION10.mkv", "Film"),
            ("Film.ION10.720p.mkv", "Film"),
            ("Film.HEVC.Some-Nonword!Group.mkv", "Film_HEVC_Some_Nonword!Group"),
            ("Film___1080p.mkv", "Film__"),
            ("Film.1080pXYZ.mkv", "Film_1080pXYZ"),
            ("Film.1080p.mkv.extra.mkv", "Film_extra"),
            ("Film.2160p.mkv", "Film_2160p"),
            ("Film.(Not a Tag).mkv", "Film_Not_a_Tag"),
            ("Film-Name,+More.mkv", "Film_Name_More"),
            ("Film___mkv.mkv", "Film___mkv"),
            ("Léon.1080p.x264-Grüp.mkv", "Léon_x264_Grüp"),
        ]
        for path, expected in cases:
            with self.subTest(path=path):
                self.assertEqual(clean_title(path, "mkv"), expected)


if __name__ == "__main__":
    unittest.main()
