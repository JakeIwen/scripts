import unittest
from dataclasses import replace
from pi.apps.iptorrents_watch import filters
from pi.tests.media.iptorrents_watch_support import candidate, wanted


class FilterTests(unittest.TestCase):
    def test_allowed_codecs(self):
        for codec in ('x264', 'X264', 'h264', 'h.264', 'h 264', 'avc'):
            with self.subTest(codec=codec):
                self.assertIsNotNone(filters.select(candidate(f'Silo.S01E02.1080p.{codec}-TEST'), wanted()))

    def test_excluded_codecs_and_resolutions(self):
        for marker in ('x265', 'h265', 'h.265', 'hevc', 'av1', '2160p', '720p', '480p', 'remux', '3D'):
            with self.subTest(marker=marker):
                self.assertIsNone(filters.select(candidate(f'Silo S01E02 1080p x264 {marker}'), wanted()))
        for name in ('Silo S01E02 720p x264', 'Silo S01E02 1080p', 'Silo S01E02 1080p x2640'):
            self.assertIsNone(filters.select(candidate(name), wanted()))

    def test_title_year_punctuation_and_prefix(self):
        for title, show in [("Marvels.Agents.of.S.H.I.E.L.D.", "Marvel's Agents of S.H.I.E.L.D."),
                            ('Law.and.Order', 'Law & Order'), ('Silo.2023', 'Silo'),
                            ('Silo', 'Silo (2023)'), ('Doctor.Who.2005', 'Doctor Who (2005)')]:
            self.assertIsNotNone(filters.select(candidate(title + '.S02E01.1080p.x264'), wanted(show)))
        for title, show in [('Silo Aftershow', 'Silo'), ('Other Silo', 'Silo'), ('Doctor Who 1963', 'Doctor Who 2005')]:
            self.assertIsNone(filters.select(candidate(title + ' S02E01 1080p x264'), wanted(show)))

    def test_category(self):
        self.assertIsNone(filters.select(replace(candidate(), category='Movie/HD/Bluray'), wanted()))

    def test_newer_and_pack_rules(self):
        for label, expected in [('S01E01', False), ('S01E02', True), ('S00E99', False),
                                ('S02E01', True), ('S01', False), ('S02', True)]:
            row = filters.select(candidate(f'Silo {label} 1080p x264'), wanted())
            self.assertEqual(row is not None, expected, label)
            if row and label == 'S02':
                self.assertIsNone(row.episode)
        self.assertIsNotNone(filters.select(candidate('Silo S01 1080p x264'), wanted(season=None, episode=None)))

    def test_existing_names_normalized_watch_suffix(self):
        existing = {filters.release_name('Silo.S01E02.1080p.WEB.H264-TEST [IPT].torrent')}
        self.assertIsNone(filters.select(candidate(), wanted(), existing))
        self.assertEqual(filters.release_name(candidate().name + '.mkv'), filters.release_name(candidate().name))
