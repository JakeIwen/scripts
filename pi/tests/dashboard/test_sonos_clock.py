import unittest

from pi.apps.van_dashboard.sonos_clock import sonos_time_zone


class SonosClockTests(unittest.TestCase):
    def clock(self, local="2026-10-15 21:00:00", utc="2026-10-16 03:00:00",
              rule="01a40b000102000003000202ffc4"):
        return {"CurrentLocalTime": local, "CurrentUTCTime": utc, "CurrentTimeZone": rule}

    def test_mountain_seasons_follow_the_speaker_offset(self):
        self.assertEqual(sonos_time_zone(self.clock()), "MDT")
        self.assertEqual(sonos_time_zone(self.clock(local="2026-01-15 20:00:00",
                         utc="2026-01-16 03:00:00")), "MST")

    def test_same_offset_does_not_imply_same_zone(self):
        self.assertEqual(sonos_time_zone(self.clock(rule="unknown")), "UTC-06:00")
        self.assertEqual(sonos_time_zone(self.clock(rule=None)), "UTC-06:00")
        self.assertEqual(sonos_time_zone(self.clock(local="2026-10-15 22:00:00",
                         rule="01680b000102000003000202ffc4")), "CDT")

    def test_arizona_does_not_pick_up_daylight_saving(self):
        self.assertEqual(sonos_time_zone(self.clock(local="2026-10-15 20:00:00",
                         rule="01a400000000000000000000ffc4")), "MST")

    def test_fractional_offset_fallback_and_invalid_clock(self):
        self.assertEqual(sonos_time_zone(self.clock(local="2026-10-16 08:30:00",
                         rule="unknown")), "UTC+05:30")
        self.assertIsNone(sonos_time_zone({}))
        self.assertIsNone(sonos_time_zone(self.clock(local="bad")))
        self.assertIsNone(sonos_time_zone(self.clock(local="2026-10-15 21:00:01")))


if __name__ == "__main__":
    unittest.main()
