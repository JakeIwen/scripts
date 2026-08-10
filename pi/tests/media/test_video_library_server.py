import ast
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from pi.apps.video_library import video_library_server as video


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
APP_DIR = REPOSITORY_ROOT / "pi" / "apps" / "video_library"


class FakeClock:
    def __init__(self, value=1_700_000_000.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FakePlayer:
    """In-memory VLC stand-in; no D-Bus, subprocess, or display calls escape."""

    def __init__(self):
        self.snapshot_value = {
            "available": False,
            "state": "OFFLINE",
            "position": 0.0,
            "duration": 0.0,
        }
        self.launch_calls = []
        self.action_calls = []
        self.seek_calls = []
        self.position_calls = []
        self.volume_calls = []
        self.rate_calls = []
        self.prepare_room_calls = []
        self.prepare_room_error = None
        self.prepare_room_callback = None
        self.quit_calls = 0
        self.fullscreen = False

    def snapshot(self):
        return dict(self.snapshot_value)

    def launch(self, paths, *, position=0, subtitles="auto"):
        if not paths:
            raise ValueError("no media paths supplied")
        if subtitles not in ("auto", "off"):
            raise ValueError("subtitles must be auto or off")
        self.launch_calls.append(
            {
                "paths": list(paths),
                "position": float(position),
                "subtitles": subtitles,
            }
        )
        self.snapshot_value = {
            "available": True,
            "state": "PLAYING",
            "path": paths[0],
            "url": Path(paths[0]).as_uri(),
            "title": Path(paths[0]).name,
            "position": float(position),
            "duration": 1_800.0,
            "track_id": "/fake/track/1",
            "volume": 1.0,
            "rate": 1.0,
            "fullscreen": False,
            "can_fullscreen": True,
            "can_seek": True,
        }
        return dict(self.snapshot_value)

    def action(self, name):
        if name not in ("toggle", "play", "pause", "next", "previous", "stop"):
            raise ValueError(f"unknown player action '{name}'")
        self.action_calls.append(name)

    def seek(self, seconds):
        self.seek_calls.append(float(seconds))

    def set_position(self, track_id, seconds):
        self.position_calls.append((track_id, float(seconds)))

    def set_volume(self, value):
        self.volume_calls.append(float(value))
        return max(0.0, min(1.25, float(value)))

    def set_rate(self, value):
        self.rate_calls.append(float(value))
        return max(0.5, min(2.0, float(value)))

    def prepare_room(self, *, wait=False):
        self.prepare_room_calls.append({"wait": bool(wait)})
        if self.prepare_room_callback is not None:
            self.prepare_room_callback()
        if self.prepare_room_error is not None:
            raise self.prepare_room_error

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        return self.fullscreen

    def quit(self):
        self.quit_calls += 1


class FakeSonosVolume:
    """In-memory rear-Sonos stand-in; discovery and UPnP never escape."""

    def __init__(self, *, available=True, volume=47, muted=False, device="vonRear"):
        self.snapshot_value = {
            "available": available,
            "volume": volume if available else None,
            "muted": muted,
            "device": device if available else None,
        }
        self.volume_calls = []
        self.snapshot_calls = 0
        self.invalidate_calls = 0
        self.snapshot_error = None

    def snapshot(self):
        self.snapshot_calls += 1
        if self.snapshot_error is not None:
            raise self.snapshot_error
        return dict(self.snapshot_value)

    def invalidate(self):
        self.invalidate_calls += 1

    def set_volume(self, value):
        value = int(value)
        self.volume_calls.append(value)
        self.snapshot_value.update(available=True, volume=value)
        return value


class FakeSonosGroup:
    def __init__(self, coordinator, *, volume=73):
        self.coordinator = coordinator
        self.volume = volume


class FakeSonosZone:
    def __init__(self, uid, name, volume, *, visible=True, muted=False):
        self.uid = uid
        self.player_name = name
        self.volume = volume
        self.mute = muted
        self.is_visible = visible
        self.group = FakeSonosGroup(self)


class MediaFixture:
    def __init__(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.mount = self.root / "movingparts"
        self.index = self.mount / "links"
        self.media = self.mount / "torrent"
        self.index.mkdir(parents=True)
        self.media.mkdir(parents=True)
        self._serial = 0

    def cleanup(self):
        self.tempdir.cleanup()

    def target(self, name, *, outside=False):
        self._serial += 1
        parent = self.root / "outside" if outside else self.media
        path = parent / f"{self._serial:02d}-{name}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake video")
        return path

    def link(self, category, relative, *, target=None, outside=False):
        target = target or self.target(Path(relative).name, outside=outside)
        link = self.index / category / relative
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
        return link, target

    def library(self, *, mounted=True):
        return video.MediaLibrary(
            [video.LibrarySource("movingparts", str(self.mount), str(self.index))],
            mount_check=lambda path: mounted and path == str(self.mount),
        )


def add_sample_library(fixture):
    episode_specs = (
        ("Galaxy.Quest.S01E01.Pilot.mkv", "Pilot"),
        ("Galaxy.Quest.S01E02.The.Mutiny.mkv", "The Mutiny"),
        ("Galaxy.Quest.S01E03.Home.mkv", "Home"),
    )
    for filename, _title in episode_specs:
        fixture.link("TV", f"Galaxy Quest/{filename}")
    movie_target = fixture.target("Dune.Part.Two.2024.mkv")
    fixture.link("Movies", "Dune.Part.Two.2024.mkv", target=movie_target)
    fixture.link("New", "Dune.Part.Two.2024.mkv", target=movie_target)
    library = fixture.library()
    if not library.scan():
        raise AssertionError(library.error)
    return library


class MediaParsingAndIndexTests(unittest.TestCase):
    def setUp(self):
        self.fixture = MediaFixture()
        self.addCleanup(self.fixture.cleanup)

    def test_parse_candidate_understands_episode_movie_year_and_fallback_code(self):
        episode_path = self.fixture.target("Galaxy.Quest.S02E03.The.Mutiny.mkv")
        episode = video.parse_candidate(
            "TV",
            "TV/Galaxy Quest/Galaxy.Quest.S02E03.The.Mutiny.mkv",
            str(self.fixture.index / "TV/Galaxy Quest/Galaxy.Quest.S02E03.The.Mutiny.mkv"),
            str(episode_path),
            "movingparts",
        )
        self.assertEqual(episode.media_type, "episode")
        self.assertEqual(episode.series, "Galaxy Quest")
        self.assertEqual((episode.season, episode.episode), (2, 3))
        self.assertEqual(episode.episode_code, "S02E03")
        self.assertEqual(episode.episode_title, "The Mutiny")
        self.assertEqual(
            episode.rel_path,
            "/TV/Galaxy Quest/Galaxy.Quest.S02E03.The.Mutiny.mkv",
        )

        fallback_path = self.fixture.target("Galaxy.Quest.304.mkv")
        fallback = video.parse_candidate(
            "TV",
            "TV/Galaxy Quest/Galaxy.Quest.304.mkv",
            str(self.fixture.index / "TV/Galaxy Quest/Galaxy.Quest.304.mkv"),
            str(fallback_path),
            "movingparts",
        )
        self.assertEqual(fallback.series, "Galaxy Quest")
        self.assertEqual((fallback.season, fallback.episode), (3, 4))

        movie_path = self.fixture.target("Dune.Part.Two.2024.mkv")
        movie = video.parse_candidate(
            "Movies",
            "Movies/Dune.Part.Two.2024.mkv",
            str(self.fixture.index / "Movies/Dune.Part.Two.2024.mkv"),
            str(movie_path),
            "movingparts",
        )
        self.assertEqual(movie.media_type, "movie")
        self.assertEqual(movie.title, "Dune Part Two")
        self.assertEqual(movie.year, 2024)
        self.assertRegex(movie.id, r"^[0-9a-f]{16}$")

    def test_part_number_is_episode_only_in_episode_contexts(self):
        cases = (
            ("Movies", "Movies/Foo.Part.2.2024.mkv", "movie", None, 2024),
            ("New", "New/Foo.Part.2.2024.mkv", "movie", None, 2024),
            ("TV", "TV/Foo/Foo.Part.2.mkv", "episode", 2, None),
            (
                "Documentaries",
                "Documentaries/Foo.Part.2.mkv",
                "episode",
                2,
                None,
            ),
        )
        for category, relative, media_type, episode, year in cases:
            with self.subTest(category=category):
                target = self.fixture.target(Path(relative).name)
                item = video.parse_candidate(
                    category,
                    relative,
                    str(self.fixture.index / relative),
                    str(target),
                    "movingparts",
                )
                self.assertEqual(item.media_type, media_type)
                self.assertEqual(item.episode, episode)
                self.assertEqual(item.year, year)
                if media_type == "movie":
                    self.assertEqual(item.title, "Foo Part 2")
                    self.assertIsNone(item.series)
                else:
                    self.assertEqual((item.season, item.episode), (1, 2))

    def test_e_only_tv_alias_uses_real_target_season_and_first_episode(self):
        target = (
            self.fixture.media
            / "Example Show"
            / "S02"
            / "Example.Show.E07E08.Double.Feature.mkv"
        )
        target.parent.mkdir(parents=True)
        target.write_bytes(b"fake video")
        item = video.parse_candidate(
            "TV",
            "TV/Example Show/Example.Show.E07E08.Double.Feature.mkv",
            str(
                self.fixture.index
                / "TV/Example Show/Example.Show.E07E08.Double.Feature.mkv"
            ),
            str(target),
            "movingparts",
        )
        self.assertEqual(item.media_type, "episode")
        self.assertEqual(item.series, "Example Show")
        self.assertEqual((item.season, item.episode), (2, 7))
        self.assertEqual(item.episode_code, "S02E07")
        self.assertEqual(
            item.id,
            video.stable_id("episode:tv:example show:s2:e7"),
        )

    def test_hash_tv_alias_prefers_verified_real_parent_episode_code(self):
        hash_name = "deadbeef-304-cafe-720-feed.mkv"
        target = (
            self.fixture.media
            / "Example Show"
            / "S08E06"
            / "Example.Show.S08E06.Release.mkv"
        )
        target.parent.mkdir(parents=True)
        target.write_bytes(b"fake video")
        item = video.parse_candidate(
            "TV",
            f"TV/Example Show/{hash_name}",
            str(self.fixture.index / "TV" / "Example Show" / hash_name),
            str(target),
            "movingparts",
        )
        self.assertEqual(item.media_type, "episode")
        self.assertEqual(item.series, "Example Show")
        self.assertEqual((item.season, item.episode), (8, 6))
        self.assertEqual(item.episode_code, "S08E06")
        self.assertEqual(
            item.id,
            video.stable_id("episode:tv:example show:s8:e6"),
        )

    def test_anchored_three_digit_fallback_does_not_decode_hash_internals(self):
        numbered_target = (
            self.fixture.media / "SeaLab_2021" / "407_Butchslap.mkv"
        )
        numbered_target.parent.mkdir(parents=True)
        numbered_target.write_bytes(b"fake video")
        numbered = video.parse_candidate(
            "TV",
            "TV/SeaLab_2021/407_Butchslap.mkv",
            str(self.fixture.index / "TV/SeaLab_2021/407_Butchslap.mkv"),
            str(numbered_target),
            "movingparts",
        )
        self.assertEqual(numbered.series, "SeaLab 2021")
        self.assertEqual((numbered.season, numbered.episode), (4, 7))
        self.assertEqual(numbered.episode_code, "S04E07")

        hash_name = "deadbeef-304-cafe-720-feed.mkv"
        hash_target = self.fixture.media / "SeaLab_2021" / "unsorted" / hash_name
        hash_target.parent.mkdir(parents=True)
        hash_target.write_bytes(b"fake video")
        unnumbered = video.parse_candidate(
            "TV",
            f"TV/SeaLab_2021/{hash_name}",
            str(self.fixture.index / "TV" / "SeaLab_2021" / hash_name),
            str(hash_target),
            "movingparts",
        )
        self.assertEqual(unnumbered.series, "SeaLab 2021")
        self.assertIsNone(unnumbered.season)
        self.assertIsNone(unnumbered.episode)
        self.assertIsNone(unnumbered.episode_code)
        self.assertNotEqual(
            unnumbered.id,
            video.stable_id("episode:tv:sealab:s3:e4"),
        )

    def test_tv_episode_alias_precedence_beats_part_suffix(self):
        target = (
            self.fixture.media
            / "Example Show"
            / "S02"
            / "Example.Show.S02E07.Release.mkv"
        )
        target.parent.mkdir(parents=True)
        target.write_bytes(b"fake video")
        cases = (
            ("E07_Title_Part_1.mkv", 2, 7),
            ("Example_Show_03_Title_Part_1.mkv", 1, 3),
        )
        for basename, season, episode in cases:
            with self.subTest(basename=basename):
                item = video.parse_candidate(
                    "TV",
                    f"TV/Example_Show/{basename}",
                    str(self.fixture.index / "TV" / "Example_Show" / basename),
                    str(target),
                    "movingparts",
                )
                self.assertEqual(item.media_type, "episode")
                self.assertEqual((item.season, item.episode), (season, episode))
                self.assertEqual(item.series_kind, "tv")

    def test_tv_and_documentary_series_with_same_name_remain_distinct(self):
        self.fixture.link(
            "TV", "Yellowstone/Yellowstone.S01E01.Daybreak.mkv"
        )
        self.fixture.link(
            "Documentaries", "Yellowstone.S01E01.National.Park.mkv"
        )
        library = self.fixture.library()
        self.assertTrue(library.scan())
        items, shows = library.snapshot()
        self.assertEqual(len(items), 2)
        self.assertEqual(len(shows), 2)
        self.assertEqual(
            {item.id for item in items},
            {
                video.stable_id("episode:tv:yellowstone:s1:e1"),
                video.stable_id("episode:documentary:yellowstone:s1:e1"),
            },
        )
        self.assertEqual(
            {show.id for show in shows},
            {
                video.stable_id("show:tv:yellowstone"),
                video.stable_id("show:documentary:yellowstone"),
            },
        )
        self.assertEqual(
            {item.series_kind for item in items}, {"tv", "documentary"}
        )

    def test_feature_year_can_precede_edition_text_without_eating_year_title(self):
        cases = (
            (
                "Movies/Blade.Runner.1982.Final.Cut.mkv",
                "Blade Runner Final Cut",
                1982,
            ),
            ("Movies/1917.mkv", "1917", None),
            (
                "Movies/1917.2019.Awards.Edition.mkv",
                "1917 Awards Edition",
                2019,
            ),
        )
        for relative, title, year in cases:
            with self.subTest(relative=relative):
                target = self.fixture.target(Path(relative).name)
                item = video.parse_candidate(
                    "Movies",
                    relative,
                    str(self.fixture.index / relative),
                    str(target),
                    "movingparts",
                )
                self.assertEqual(item.media_type, "movie")
                self.assertEqual(item.title, title)
                self.assertEqual(item.year, year)

    def test_scan_deduplicates_new_aliases_and_builds_natural_show_order(self):
        episode_two = self.fixture.target("Galaxy.Quest.S01E02.The.Mutiny.mkv")
        self.fixture.link(
            "TV",
            "Galaxy Quest/Galaxy.Quest.S01E02.The.Mutiny.mkv",
            target=episode_two,
        )
        self.fixture.link(
            "New",
            "Galaxy.Quest.S01E02.The.Mutiny.mkv",
            target=episode_two,
        )
        self.fixture.link("TV", "Galaxy Quest/Galaxy.Quest.S01E10.Finale.mkv")
        self.fixture.link("TV", "Galaxy Quest/Galaxy.Quest.S01E01.Pilot.mkv")

        movie_target = self.fixture.target("Dune.Part.Two.2024.mkv")
        movie_link, _ = self.fixture.link(
            "Movies", "Dune.Part.Two.2024.mkv", target=movie_target
        )
        self.fixture.link("New", "Dune.Part.Two.2024.mkv", target=movie_target)
        self.fixture.link("Documentaries", "Free.Solo.2018.mkv")

        library = self.fixture.library()
        self.assertTrue(library.scan())
        items, shows = library.snapshot()
        self.assertEqual(len(items), 5)
        self.assertEqual(len(shows), 1)

        show = shows[0]
        self.assertEqual(show.name, "Galaxy Quest")
        self.assertRegex(show.id, r"^[0-9a-f]{16}$")
        self.assertEqual(
            [(item.season, item.episode) for item in show.episodes],
            [(1, 1), (1, 2), (1, 10)],
        )
        episode = next(item for item in items if item.episode == 2)
        self.assertEqual(episode.categories, {"TV", "New"})
        self.assertEqual(len(episode.aliases), 2)
        self.assertTrue(episode.path.startswith(str(self.fixture.index / "TV")))

        movie = next(item for item in items if item.title == "Dune Part Two")
        self.assertEqual(movie.categories, {"Movies", "New"})
        self.assertEqual(movie.path, str(movie_link))
        self.assertTrue(movie.new)
        documentary = next(item for item in items if item.title == "Free Solo")
        self.assertEqual(documentary.media_type, "documentary")

    def test_deduplicated_episode_maps_every_distinct_real_copy(self):
        primary_target = self.fixture.target("primary-episode.mkv")
        alternate_target = self.fixture.target("alternate-episode.mkv")
        primary_link, _ = self.fixture.link(
            "TV",
            "Example Show/Example.Show.S01E01.Pilot.mkv",
            target=primary_target,
        )
        alternate_link, _ = self.fixture.link(
            "New",
            "Example.Show.S01E01.Pilot.Alternate.mkv",
            target=alternate_target,
        )

        library = self.fixture.library()
        self.assertTrue(library.scan())
        items, shows = library.snapshot()
        self.assertEqual(len(items), 1)
        self.assertEqual(len(shows), 1)
        item = items[0]
        self.assertEqual((item.season, item.episode), (1, 1))
        self.assertEqual(item.categories, {"TV", "New"})
        self.assertEqual(len(item.aliases), 2)
        for path in (
            primary_link,
            alternate_link,
            primary_target,
            alternate_target,
        ):
            with self.subTest(path=path):
                self.assertIs(library.item_for_path(str(path)), item)

    def test_canonical_series_merges_article_and_year_folder_variants(self):
        self.assertEqual(video.canonical_series("The_Last_of_Us"), "last of us")
        self.assertEqual(video.canonical_series("The_Last_of_Us_2023"), "last of us")
        self.assertEqual(video.canonical_series("Last_of_Us"), "last of us")

        first_target = self.fixture.target(
            "The_Last_of_Us_S01E01_When_Youre_Lost_in_the_Darkness.mkv"
        )
        self.fixture.link(
            "TV",
            "The_Last_of_Us/The_Last_of_Us_S01E01_When_Youre_Lost_in_the_Darkness.mkv",
            target=first_target,
        )
        self.fixture.link(
            "TV",
            "The_Last_of_Us_2023/The_Last_of_Us_2023_S01E01_When_Youre_Lost_in_the_Darkness.mkv",
            target=first_target,
        )
        self.fixture.link(
            "TV", "Last_of_Us/Last_of_Us_S01E02_Infected.mkv"
        )

        library = self.fixture.library()
        self.assertTrue(library.scan())
        items, shows = library.snapshot()
        self.assertEqual(len(items), 2)
        self.assertEqual(len(shows), 1)
        show = shows[0]
        self.assertEqual(show.id, video.stable_id("show:tv:last of us"))
        self.assertEqual(
            [(item.season, item.episode) for item in show.episodes], [(1, 1), (1, 2)]
        )
        first = show.episodes[0]
        self.assertEqual(
            first.id, video.stable_id("episode:tv:last of us:s1:e1")
        )
        self.assertEqual(len(first.aliases), 2)
        store = video.ProgressStore(":memory:")
        self.addCleanup(store.connection.close)
        service = video.VideoService(
            library, store, FakePlayer(), sonos=FakeSonosVolume()
        )
        for episode in show.episodes:
            self.assertIs(service.show_for_item(episode), show)

    def test_scan_accepts_only_safe_video_symlinks_inside_the_selected_mount(self):
        good, _ = self.fixture.link("Movies", "Good.Movie.2020.mkv")
        self.fixture.link("Movies", "Outside.Movie.2021.mkv", outside=True)
        self.fixture.link("Movies", "Notes.txt")
        broken = self.fixture.index / "Movies" / "Broken.Movie.2022.mkv"
        broken.symlink_to(self.fixture.media / "missing.mkv")
        regular = self.fixture.index / "Movies" / "Regular.Movie.2023.mkv"
        regular.write_bytes(b"not a link")
        hidden_target = self.fixture.target("Hidden.Movie.2024.mkv")
        (self.fixture.index / "Movies" / ".Hidden.Movie.2024.mkv").symlink_to(
            hidden_target
        )

        library = self.fixture.library()
        self.assertTrue(library.scan())
        items, _shows = library.snapshot()
        self.assertEqual([item.title for item in items], ["Good Movie"])
        self.assertIs(library.item_for_path(str(good)), items[0])
        self.assertIs(library.item_for_path(os.path.realpath(good)), items[0])

    def test_mount_check_fails_closed_before_reading_an_index(self):
        source = video.LibrarySource(
            "movingparts", str(self.fixture.mount), str(self.fixture.index)
        )
        checked = []
        library = video.MediaLibrary(
            [source], mount_check=lambda path: checked.append(path) or False
        )
        with mock.patch.object(
            video.os.path,
            "isdir",
            side_effect=AssertionError("index must not be probed on an unmounted source"),
        ):
            self.assertFalse(library.scan())
        self.assertEqual(checked, [str(self.fixture.mount)])
        self.assertFalse(library.available)
        self.assertIsNone(library.source)
        self.assertIn("not mounted", library.error)

    def test_unavailable_primary_source_can_fall_back_only_to_a_verified_mount(self):
        backup_mount = self.fixture.root / "bigboi"
        backup_index = backup_mount / "mp_backup" / "links"
        backup_media = backup_mount / "mp_backup" / "torrent"
        backup_media.mkdir(parents=True)
        (backup_index / "Movies").mkdir(parents=True)
        target = backup_media / "Backup.Movie.2019.mkv"
        target.write_bytes(b"fake video")
        (backup_index / "Movies" / target.name).symlink_to(target)
        sources = (
            video.LibrarySource(
                "movingparts", str(self.fixture.mount), str(self.fixture.index)
            ),
            video.LibrarySource("bigboi", str(backup_mount), str(backup_index)),
        )
        library = video.MediaLibrary(
            sources, mount_check=lambda path: path == str(backup_mount)
        )
        self.assertTrue(library.scan())
        self.assertEqual(library.source.name, "bigboi")
        self.assertEqual([item.title for item in library.items.values()], ["Backup Movie"])

    def test_failed_rescan_after_mount_loss_blocks_stale_index_playback(self):
        self.fixture.link("Movies", "Primer.2004.mkv")
        mounted = {"value": True}
        library = video.MediaLibrary(
            [
                video.LibrarySource(
                    "movingparts", str(self.fixture.mount), str(self.fixture.index)
                )
            ],
            mount_check=lambda _path: mounted["value"],
        )
        self.assertTrue(library.scan())
        item = next(iter(library.items.values()))
        mounted["value"] = False
        self.assertFalse(library.scan())
        player = FakePlayer()
        store = video.ProgressStore(":memory:")
        self.addCleanup(store.connection.close)
        service = video.VideoService(
            library,
            store,
            player,
            sonos=FakeSonosVolume(),
            legacy_positions=str(self.fixture.root / "missing-legacy.txt"),
        )
        with self.assertRaisesRegex(RuntimeError, "not mounted"):
            service.play(item_id=item.id)
        self.assertEqual(player.launch_calls, [])


class ProgressAndLegacyImportTests(unittest.TestCase):
    def setUp(self):
        self.store = video.ProgressStore(":memory:")
        self.addCleanup(self.store.connection.close)

    def test_progress_store_merges_fields_marks_and_clears(self):
        first = self.store.record(
            "feature:test:2024",
            position=125.5,
            duration=1_000,
            title="Test",
            rel_path="/Movies/Test.2024.mkv",
            updated=100,
            increment_play=True,
        )
        self.assertEqual(first["play_count"], 1)
        merged = self.store.record(
            "feature:test:2024", position=150, updated=200, increment_play=True
        )
        self.assertEqual(merged["duration"], 1_000)
        self.assertEqual(merged["title"], "Test")
        self.assertEqual(merged["play_count"], 2)
        untouched = self.store.record(
            "feature:test:2024", position=999, only_if_absent=True
        )
        self.assertEqual(untouched["position"], 150)

        watched = self.store.mark("feature:test:2024", True)
        self.assertEqual(watched["position"], 1_000)
        self.assertEqual(watched["finished"], 1)
        public = video.public_progress(watched)
        self.assertEqual(public["position_text"], "16:40")
        self.assertEqual(public["duration_text"], "16:40")
        self.assertEqual(public["fraction"], 1.0)
        self.assertTrue(public["finished"])
        self.assertTrue(self.store.clear("feature:test:2024"))
        self.assertFalse(self.store.clear("feature:test:2024"))

    def test_legacy_import_uses_latest_matching_line_and_is_idempotent(self):
        fixture = MediaFixture()
        self.addCleanup(fixture.cleanup)
        fixture.link("Movies", "Primer.2004.mkv")
        library = fixture.library()
        self.assertTrue(library.scan())
        item = next(iter(library.items.values()))
        legacy = fixture.root / "vlc-positions.txt"
        legacy.write_text(
            "bad line\n"
            f"{item.rel_path} 60000000 0:01:00\n"
            "/Movies/Unknown.2000.mkv 90000000 0:01:30\n"
            f"{item.rel_path} 245000000 0:04:05\n",
            encoding="utf-8",
        )
        os.utime(legacy, (1_700_000_123, 1_700_000_123))
        service = video.VideoService(
            library,
            self.store,
            FakePlayer(),
            sonos=FakeSonosVolume(),
            legacy_positions=str(legacy),
        )
        self.assertGreaterEqual(service.import_legacy_positions(), 1)
        progress = self.store.get(item.key)
        self.assertEqual(progress["position"], 245.0)
        self.assertEqual(progress["title"], "Primer")
        self.assertEqual(progress["rel_path"], "/Movies/Primer.2004.mkv")
        self.assertEqual(service.import_legacy_positions(), 0)

    def test_legacy_import_does_not_replace_existing_database_progress(self):
        fixture = MediaFixture()
        self.addCleanup(fixture.cleanup)
        fixture.link("Movies", "Primer.2004.mkv")
        library = fixture.library()
        self.assertTrue(library.scan())
        item = next(iter(library.items.values()))
        self.store.record(item.key, position=500, updated=2_000, title=item.title)
        legacy = fixture.root / "vlc-positions.txt"
        legacy.write_text(
            f"{item.rel_path} 100000000 0:01:40\n", encoding="utf-8"
        )
        service = video.VideoService(
            library,
            self.store,
            FakePlayer(),
            sonos=FakeSonosVolume(),
            legacy_positions=str(legacy),
        )
        service.import_legacy_positions()
        self.assertEqual(self.store.get(item.key)["position"], 500)


class SonosVolumeControllerTests(unittest.TestCase):
    REAR_LEFT_UID = "RINCON_7828CA20F21A01400"
    REAR_RIGHT_UID = "RINCON_7828CA20F1DA01400"

    def test_snapshot_and_volume_target_the_visible_physical_rear_zone(self):
        front = FakeSonosZone("RINCON_FRONT", "vonFront", 19)
        rear = FakeSonosZone(self.REAR_LEFT_UID, "vonRear", 47, muted=True)
        bonded_partner = FakeSonosZone(
            self.REAR_RIGHT_UID, "vonRear Right", 12, visible=False
        )
        rear.group = FakeSonosGroup(front, volume=73)
        bonded_partner.group = rear.group
        discovery_calls = []

        def discover(timeout, include_invisible=False):
            discovery_calls.append((timeout, include_invisible))
            return {front, rear, bonded_partner}

        controller = video.SonosVolumeController(
            discover_func=discover
        )

        self.assertEqual(
            controller.snapshot(),
            {
                "available": True,
                "volume": 47,
                "muted": True,
                "device": "vonRear",
            },
        )
        self.assertEqual(controller.set_volume(31), 31)
        self.assertEqual(rear.volume, 31)
        self.assertTrue(rear.mute, "changing volume must preserve Sonos mute")
        self.assertEqual(rear.group.volume, 73, "must not change party-group volume")
        self.assertEqual(front.volume, 19, "must not target the current coordinator")
        self.assertTrue(discovery_calls)
        self.assertTrue(
            discovery_calls[0][1], "resolver must discover the hidden stereo partner"
        )

    def test_rear_resolver_fails_closed_for_missing_or_split_stereo_pair(self):
        front = FakeSonosZone("RINCON_FRONT", "vonFront", 19)
        left = FakeSonosZone(self.REAR_LEFT_UID, "Rear Left", 47)
        right = FakeSonosZone(self.REAR_RIGHT_UID, "Rear Right", 47)

        cases = (
            {front},
            {front, left},
            {front, left, right},
        )
        for zones in cases:
            with self.subTest(zones={zone.player_name for zone in zones}):
                def discover(timeout, include_invisible=False, zones=zones):
                    return zones

                controller = video.SonosVolumeController(
                    discover_func=discover
                )
                snapshot = controller.snapshot()
                self.assertFalse(snapshot["available"])
                self.assertIsNone(snapshot["volume"])
                with self.assertRaises(RuntimeError):
                    controller.set_volume(35)
        self.assertEqual(front.volume, 19)
        self.assertEqual(left.volume, 47)
        self.assertEqual(right.volume, 47)


class SonosTaskTests(unittest.TestCase):
    def test_rear_movie_resume_preserves_current_volume_including_zero(self):
        source_path = REPOSITORY_ROOT / "shared" / "python" / "sonos_tasks.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "rear_movie_resume"
        )
        module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))

        for current_volume in (0, 25):
            with self.subTest(current_volume=current_volume):
                device = mock.Mock(volume=current_volume)
                calls = []

                def get_rear_stereo_master():
                    calls.append(("get",))
                    return device

                def audio_source_device(selected, source, volume):
                    calls.append(("audio", selected, source, volume))
                    selected.volume = 47
                    return selected

                namespace = {
                    "get_rear_stereo_master": get_rear_stereo_master,
                    "audio_source_device": audio_source_device,
                }
                exec(compile(module, str(source_path), "exec"), namespace)

                result = namespace["rear_movie_resume"]()

                self.assertIs(result, device)
                self.assertEqual(
                    calls,
                    [("get",), ("audio", device, "optical", current_volume)],
                )


class VlcControllerTests(unittest.TestCase):
    def test_room_prep_wakes_display_and_runs_readable_sonos_script_with_bash(self):
        run = mock.Mock()
        popen = mock.Mock()
        controller = video.VlcController(run=run, popen=popen)
        with (
            mock.patch.object(video.os.path, "isfile", return_value=True),
            mock.patch.object(video.os, "access", return_value=True) as access,
        ):
            controller._prepare_room()

        run.assert_called_once_with(
            [video.XSET, "-display", video.DISPLAY, "dpms", "force", "on"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        access.assert_called_once_with(video.SNS, os.R_OK)
        popen.assert_called_once_with(
            ["/bin/bash", video.SNS, "rear_movie"],
            stdin=video.subprocess.DEVNULL,
            stdout=video.subprocess.DEVNULL,
            stderr=video.subprocess.DEVNULL,
            start_new_session=True,
        )

    def test_waiting_resume_prep_runs_volume_preserving_sonos_task(self):
        run = mock.Mock()
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        popen = mock.Mock(return_value=process)
        controller = video.VlcController(run=run, popen=popen)

        with (
            mock.patch.object(video.os.path, "isfile", return_value=True),
            mock.patch.object(video.os, "access", return_value=True),
        ):
            controller.prepare_room(wait=True)

        popen.assert_called_once_with(
            ["/bin/bash", video.SNS, "rear_movie_resume"],
            stdin=video.subprocess.DEVNULL,
            stdout=video.subprocess.DEVNULL,
            stderr=video.subprocess.DEVNULL,
            start_new_session=True,
        )
        process.wait.assert_called_once()
        self.assertIsNone(controller.room_process)

    def test_room_prep_reuses_running_sonos_topology_job(self):
        run = mock.Mock()
        running = mock.Mock()
        running.poll.return_value = None
        replacement = mock.Mock()
        replacement.poll.return_value = None
        popen = mock.Mock(side_effect=(running, replacement))
        controller = video.VlcController(run=run, popen=popen)

        with (
            mock.patch.object(video.os.path, "isfile", return_value=True),
            mock.patch.object(video.os, "access", return_value=True),
        ):
            controller._prepare_room()
            controller._prepare_room()
            self.assertIs(controller.room_process, running)
            self.assertEqual(popen.call_count, 1)

            running.poll.return_value = 0
            controller._prepare_room()

        self.assertEqual(popen.call_count, 2)
        self.assertIs(controller.room_process, replacement)

    def test_waiting_room_prep_reuses_running_job_and_requires_success(self):
        run = mock.Mock()
        popen = mock.Mock()
        running = mock.Mock()
        running.poll.return_value = None
        running.wait.return_value = 0
        controller = video.VlcController(run=run, popen=popen)
        controller.room_process = running

        controller.prepare_room(wait=True)

        popen.assert_not_called()
        running.wait.assert_called_once()
        timeout = running.wait.call_args.kwargs.get("timeout")
        self.assertIsInstance(timeout, (int, float))
        self.assertGreater(timeout, 0)
        self.assertIsNone(controller.room_process)

    def test_waiting_room_prep_reports_timeout_nonzero_and_missing_setup(self):
        outcomes = (
            video.subprocess.TimeoutExpired(cmd="rear_movie", timeout=1),
            9,
        )
        for outcome in outcomes:
            with self.subTest(outcome=outcome):
                process = mock.Mock()
                process.poll.return_value = None
                if isinstance(outcome, BaseException):
                    process.wait.side_effect = outcome
                else:
                    process.wait.return_value = outcome
                controller = video.VlcController(run=mock.Mock())
                controller.room_process = process

                with self.assertRaises(video.RoomPreparationError):
                    controller.prepare_room(wait=True)
                self.assertIsNone(controller.room_process)
                if isinstance(outcome, BaseException):
                    self.assertTrue(process.terminate.called or process.kill.called)

        controller = video.VlcController(run=mock.Mock())
        with mock.patch.object(video.os.path, "isfile", return_value=False):
            with self.assertRaises(video.RoomPreparationError):
                controller.prepare_room(wait=True)

    def test_waiting_room_prep_timeout_terminates_process_group_and_clears_owner(self):
        process = mock.Mock(pid=4321)
        process.poll.return_value = None
        process.wait.side_effect = (
            video.subprocess.TimeoutExpired(cmd="rear_movie_resume", timeout=1),
            0,
        )
        killpg = mock.Mock()
        controller = video.VlcController(run=mock.Mock(), killpg=killpg)
        controller.room_process = process

        with self.assertRaisesRegex(video.RoomPreparationError, "did not finish"):
            controller.prepare_room(wait=True)

        killpg.assert_called_once_with(4321, video.signal.SIGTERM)
        self.assertEqual(process.wait.call_count, 2)
        self.assertIsNone(controller.room_process)

    def test_launch_waits_for_first_track_before_setting_resume_position(self):
        first_path = "/mnt/movingparts/torrent/My Movie.mkv"
        run = mock.Mock(
            return_value=mock.Mock(returncode=0, stdout="", stderr="")
        )
        sleep = mock.Mock()
        controller = video.VlcController(run=run, sleep=sleep)
        early_registration = {
            "available": True,
            "state": "STOPPED",
            "path": None,
            "track_id": None,
            "position": 0,
            "duration": 0,
        }
        stale_track = {
            "available": True,
            "state": "STOPPED",
            "path": "/mnt/movingparts/torrent/Previous Movie.mkv",
            "track_id": "/fake/track/old",
            "position": 0,
            "duration": 0,
        }
        matching_track = {
            "available": True,
            "state": "PLAYING",
            "path": first_path,
            "track_id": "/fake/track/new",
            "position": 0,
            "duration": 1_800,
            "volume": 0.42,
        }
        resumed = dict(matching_track, position=321)
        snapshots = iter(
            [early_registration, stale_track, matching_track, resumed]
        )

        def next_snapshot():
            return next(snapshots, resumed)

        with (
            mock.patch.object(controller, "_stop_existing"),
            mock.patch.object(controller, "_prepare_room"),
            mock.patch.object(controller, "_subtitle_index", return_value=None),
            mock.patch.object(
                controller,
                "snapshot",
                side_effect=next_snapshot,
            ) as snapshot,
            mock.patch.object(controller, "set_position") as set_position,
            mock.patch.object(
                controller, "set_volume", return_value=video.VLC_FIXED_VOLUME
            ) as set_volume,
        ):
            result = controller.launch([first_path], position=321)

        launch_command = next(
            call.args[0]
            for call in run.call_args_list
            if call.args and video.SYSTEMD_RUN in call.args[0]
        )
        self.assertIn("--volume=256", launch_command)
        self.assertIn("--no-volume-save", launch_command)
        self.assertIn("--gain=1.0", launch_command)
        self.assertGreaterEqual(snapshot.call_count, 4)
        self.assertGreaterEqual(sleep.call_count, 2)
        self.assertEqual(sleep.call_args_list[:2], [mock.call(0.25), mock.call(0.25)])
        set_volume.assert_called_once_with(video.VLC_FIXED_VOLUME)
        set_position.assert_called_once_with("/fake/track/new", 321)
        self.assertEqual(result, resumed)

    def test_snapshot_reasserts_fixed_vlc_volume_only_when_it_drifted(self):
        module = mock.Mock()
        module.Double.side_effect = float
        props = mock.Mock()
        player_values = {
            "PlaybackStatus": "Playing",
            "Metadata": {
                "xesam:url": "file:///mnt/movingparts/torrent/Test.mkv",
                "mpris:length": 1_800_000_000,
                "mpris:trackid": "/fake/track/current",
            },
            "Position": 20_000_000,
            "Volume": 0.42,
            "Rate": 1.0,
            "CanSeek": True,
        }

        def get_all(interface):
            return player_values if interface == video.MPRIS_PLAYER else {}

        props.GetAll.side_effect = get_all
        controller = video.VlcController(dbus_module=module)
        with mock.patch.object(
            controller,
            "_interfaces",
            return_value=(module, props, mock.Mock(), mock.Mock()),
        ):
            drifted = controller.snapshot()
            props.Set.assert_called_once_with(
                video.MPRIS_PLAYER,
                "Volume",
                video.VLC_FIXED_VOLUME,
            )
            self.assertEqual(drifted["volume"], video.VLC_FIXED_VOLUME)

            props.Set.reset_mock()
            player_values["Volume"] = video.VLC_FIXED_VOLUME
            fixed = controller.snapshot()
            props.Set.assert_not_called()
            self.assertEqual(fixed["volume"], video.VLC_FIXED_VOLUME)


class VideoServiceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = MediaFixture()
        self.addCleanup(self.fixture.cleanup)
        self.library = add_sample_library(self.fixture)
        self.store = video.ProgressStore(":memory:")
        self.addCleanup(self.store.connection.close)
        self.player = FakePlayer()
        self.sonos = FakeSonosVolume()
        self.clock = FakeClock()
        self.service = video.VideoService(
            self.library,
            self.store,
            self.player,
            sonos=self.sonos,
            legacy_positions=str(self.fixture.root / "missing-legacy.txt"),
            clock=self.clock,
        )
        self.show = next(iter(self.library.shows.values()))
        self.episodes = self.show.episodes
        self.movie = next(
            item for item in self.library.items.values() if item.media_type == "movie"
        )

    def test_next_episode_prefers_most_recent_resume_then_first_unwatched(self):
        one, two, three = self.episodes
        self.store.record(
            one.key, position=1_000, duration=1_000, finished=True, updated=100
        )
        self.store.record(
            two.key, position=200, duration=1_000, finished=False, updated=200
        )
        self.store.record(
            three.key, position=100, duration=1_000, finished=False, updated=300
        )
        self.assertIs(self.service._next_episode(self.show, self.store.all()), three)

        self.store.record(three.key, finished=True, updated=400)
        self.assertIs(self.service._next_episode(self.show, self.store.all()), two)
        self.store.record(two.key, finished=True, updated=500)
        self.assertIs(self.service._next_episode(self.show, self.store.all()), one)

    def test_playing_show_resumes_with_rewind_and_queues_remaining_episodes(self):
        one, two, three = self.episodes
        self.store.record(
            one.key, position=1_000, duration=1_000, finished=True, updated=100
        )
        self.store.record(
            two.key, position=160, duration=1_000, finished=False, updated=200
        )
        result = self.service.play(show_id=self.show.id, subtitles="off")
        self.assertEqual(result["queued"], 2)
        self.assertIn("Resuming Galaxy Quest S01E02", result["message"])
        call = self.player.launch_calls[-1]
        self.assertEqual(
            call["paths"], [os.path.realpath(two.path), os.path.realpath(three.path)]
        )
        self.assertEqual(call["position"], 160 - video.RESUME_REWIND)
        self.assertEqual(call["subtitles"], "off")
        self.assertEqual(self.store.get(two.key)["play_count"], 1)

        restarted = self.service.play(item_id=two.id, restart=True)
        self.assertIn("Playing Galaxy Quest S01E02", restarted["message"])
        self.assertEqual(self.player.launch_calls[-1]["position"], 0)
        self.assertEqual(self.store.get(two.key)["play_count"], 2)

    def test_shuffled_show_launches_only_the_selected_episode(self):
        selected = self.episodes[1]
        self.service.random = mock.Mock()
        self.service.random.choice.return_value = selected

        result = self.service.play(show_id=self.show.id, shuffle=True)
        self.service.random.choice.assert_called_once_with(self.show.episodes)
        self.assertEqual(result["queued"], 1)
        self.assertEqual(
            self.player.launch_calls[-1]["paths"],
            [os.path.realpath(selected.path)],
        )

    def test_sonos_volume_serializes_with_play_room_preparation(self):
        launch_entered = threading.Event()
        allow_launch = threading.Event()
        volume_attempted_lock = threading.Event()
        volume_finished = threading.Event()
        errors = []

        class BlockingPlayer(FakePlayer):
            def launch(self, paths, *, position=0, subtitles="auto"):
                launch_entered.set()
                if not allow_launch.wait(2):
                    raise RuntimeError("test timed out waiting to release launch")
                return super().launch(
                    paths, position=position, subtitles=subtitles
                )

        class ObservedRLock:
            def __init__(self):
                self.lock = threading.RLock()

            def __enter__(self):
                if threading.current_thread().name == "sonos-volume-test":
                    volume_attempted_lock.set()
                self.lock.acquire()
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                self.lock.release()
                return False

        self.player = BlockingPlayer()
        self.service.player = self.player
        self.service.control_lock = ObservedRLock()

        def play():
            try:
                self.service.play(item_id=self.movie.id, restart=True)
            except Exception as exc:
                errors.append(exc)

        def set_volume():
            try:
                self.service.set_audio_volume(39)
            except Exception as exc:
                errors.append(exc)
            finally:
                volume_finished.set()

        play_thread = threading.Thread(target=play, name="video-play-test", daemon=True)
        volume_thread = threading.Thread(
            target=set_volume, name="sonos-volume-test", daemon=True
        )
        play_thread.start()
        try:
            self.assertTrue(launch_entered.wait(1), "play never entered launch")
            volume_thread.start()
            self.assertTrue(
                volume_attempted_lock.wait(1),
                "volume change did not attempt to acquire the playback lock",
            )
            self.assertFalse(volume_finished.is_set())
            self.assertEqual(self.sonos.volume_calls, [])
        finally:
            allow_launch.set()
            play_thread.join(2)
            if volume_thread.ident is not None:
                volume_thread.join(2)

        self.assertFalse(play_thread.is_alive())
        self.assertFalse(volume_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.sonos.volume_calls, [39])

    def test_resume_controls_wait_for_room_prep_and_preserve_sonos_volume(self):
        for state, action, volume, expected_action in (
            ("PAUSED", "toggle", 0, "play"),
            ("PAUSED_PLAYBACK", "play", 25, "play"),
            ("STOPPED", "toggle", 63, "play"),
            ("STOPPED", "play", 71, "play"),
        ):
            with self.subTest(state=state, action=action, volume=volume):
                player = FakePlayer()
                player.snapshot_value.update(available=True, state=state)
                sonos = FakeSonosVolume(volume=volume)
                service = video.VideoService(
                    self.library,
                    self.store,
                    player,
                    sonos=sonos,
                    legacy_positions=str(self.fixture.root / "missing-legacy.txt"),
                    clock=self.clock,
                )
                events = []
                player.prepare_room_callback = lambda: events.append("prepare")
                original_action = player.action
                original_set_volume = sonos.set_volume

                def record_action(name):
                    events.append(f"action:{name}")
                    original_action(name)

                def record_volume(value):
                    events.append(f"volume:{value}")
                    return original_set_volume(value)

                player.action = record_action
                sonos.set_volume = record_volume

                result = service.control_player(action)

                self.assertTrue(result["ok"])
                self.assertEqual(player.prepare_room_calls, [{"wait": True}])
                self.assertEqual(sonos.volume_calls, [volume])
                self.assertEqual(player.action_calls, [expected_action])
                self.assertEqual(
                    events,
                    ["prepare", f"volume:{volume}", f"action:{expected_action}"],
                )

    def test_pause_and_redundant_play_do_not_run_room_prep(self):
        for state, action in (
            ("PLAYING", "toggle"),
            ("PLAYING", "play"),
            ("PLAYING", "pause"),
            ("PAUSED", "pause"),
        ):
            with self.subTest(state=state, action=action):
                player = FakePlayer()
                player.snapshot_value.update(available=True, state=state)
                service = video.VideoService(
                    self.library,
                    self.store,
                    player,
                    sonos=FakeSonosVolume(volume=63),
                    legacy_positions=str(self.fixture.root / "missing-legacy.txt"),
                    clock=self.clock,
                )

                service.control_player(action)

                self.assertEqual(player.prepare_room_calls, [])
                self.assertEqual(player.action_calls, [action])

    def test_offline_or_unknown_player_state_does_not_start_sonos_work(self):
        for available, state in (
            (False, "OFFLINE"),
            (True, "BUFFERING"),
            (True, ""),
        ):
            with self.subTest(available=available, state=state):
                player = FakePlayer()
                player.snapshot_value.update(available=available, state=state)
                player.action = mock.Mock(side_effect=RuntimeError("VLC unavailable"))
                service = video.VideoService(
                    self.library,
                    self.store,
                    player,
                    sonos=FakeSonosVolume(),
                    legacy_positions=str(self.fixture.root / "missing-legacy.txt"),
                    clock=self.clock,
                )

                with self.assertRaisesRegex(RuntimeError, "VLC unavailable"):
                    service.control_player("play")

                self.assertEqual(player.prepare_room_calls, [])
                player.action.assert_called_once_with("play")

    def test_failed_room_prep_does_not_resume_or_restore_volume(self):
        self.player.snapshot_value.update(available=True, state="PAUSED")
        self.player.prepare_room_error = video.RoomPreparationError(
            "rear_movie timed out"
        )
        self.sonos.snapshot_value["volume"] = 61

        with self.assertRaisesRegex(video.RoomPreparationError, "timed out"):
            self.service.control_player("toggle")

        self.assertEqual(self.player.prepare_room_calls, [{"wait": True}])
        self.assertEqual(self.player.action_calls, [])
        self.assertEqual(self.sonos.volume_calls, [])

    def test_resume_uses_last_known_volume_if_fresh_sonos_read_fails(self):
        self.player.snapshot_value.update(available=True, state="PAUSED")
        self.sonos.snapshot_value["volume"] = 58
        self.assertEqual(self.service.audio_status()["volume"], 58)
        restored = dict(self.sonos.snapshot_value)
        self.sonos.snapshot = mock.Mock(
            side_effect=(RuntimeError("transient discovery failure"), restored)
        )

        self.service.control_player("play")

        self.assertEqual(self.player.prepare_room_calls, [{"wait": True}])
        self.assertEqual(self.sonos.volume_calls, [58])
        self.assertEqual(self.player.action_calls, ["play"])

    def test_user_volume_change_waits_for_resume_and_wins_after_restore(self):
        prep_entered = threading.Event()
        allow_prep = threading.Event()
        volume_attempted_lock = threading.Event()
        volume_finished = threading.Event()
        errors = []
        events = []

        class BlockingPlayer(FakePlayer):
            def prepare_room(self, *, wait=False):
                self.prepare_room_calls.append({"wait": bool(wait)})
                events.append("prepare")
                prep_entered.set()
                if not allow_prep.wait(2):
                    raise RuntimeError("test timed out waiting to release room prep")

            def action(self, name):
                events.append(f"action:{name}")
                super().action(name)

        class ObservedRLock:
            def __init__(self):
                self.lock = threading.RLock()

            def __enter__(self):
                if threading.current_thread().name == "resume-volume-test":
                    volume_attempted_lock.set()
                self.lock.acquire()
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                self.lock.release()
                return False

        player = BlockingPlayer()
        player.snapshot_value.update(available=True, state="PAUSED")
        sonos = FakeSonosVolume(volume=61)
        original_set_volume = sonos.set_volume

        def record_volume(value):
            events.append(f"volume:{value}")
            return original_set_volume(value)

        sonos.set_volume = record_volume
        service = video.VideoService(
            self.library,
            self.store,
            player,
            sonos=sonos,
            legacy_positions=str(self.fixture.root / "missing-legacy.txt"),
            clock=self.clock,
        )
        service.control_lock = ObservedRLock()

        def resume():
            try:
                service.control_player("play")
            except Exception as exc:
                errors.append(exc)

        def set_volume():
            try:
                service.set_audio_volume(39)
            except Exception as exc:
                errors.append(exc)
            finally:
                volume_finished.set()

        resume_thread = threading.Thread(
            target=resume, name="video-resume-test", daemon=True
        )
        volume_thread = threading.Thread(
            target=set_volume, name="resume-volume-test", daemon=True
        )
        resume_thread.start()
        try:
            self.assertTrue(prep_entered.wait(1), "resume never entered room prep")
            volume_thread.start()
            self.assertTrue(
                volume_attempted_lock.wait(1),
                "volume change did not attempt to acquire the playback lock",
            )
            self.assertFalse(volume_finished.is_set())
            self.assertEqual(sonos.volume_calls, [])
        finally:
            allow_prep.set()
            resume_thread.join(2)
            if volume_thread.ident is not None:
                volume_thread.join(2)

        self.assertFalse(resume_thread.is_alive())
        self.assertFalse(volume_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(sonos.volume_calls, [61, 39])
        self.assertEqual(sonos.snapshot_value["volume"], 39)
        self.assertEqual(
            events,
            ["prepare", "volume:61", "action:play", "volume:39"],
        )

    def test_sleep_timer_expiry_waits_for_blocked_resume_then_pauses(self):
        prep_entered = threading.Event()
        allow_prep = threading.Event()
        expiry_attempted_lock = threading.Event()
        errors = []
        events = []

        class BlockingPlayer(FakePlayer):
            def prepare_room(self, *, wait=False):
                self.prepare_room_calls.append({"wait": bool(wait)})
                events.append("prepare")
                prep_entered.set()
                if not allow_prep.wait(2):
                    raise RuntimeError("test timed out waiting to release room prep")

            def action(self, name):
                events.append(f"action:{name}")
                super().action(name)

        class ObservedRLock:
            def __init__(self):
                self.lock = threading.RLock()

            def __enter__(self):
                if threading.current_thread().name == "sleep-expiry-test":
                    expiry_attempted_lock.set()
                self.lock.acquire()
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                self.lock.release()
                return False

        class OneIterationStopEvent:
            def __init__(self):
                self.calls = 0

            def wait(self, _timeout):
                self.calls += 1
                return self.calls > 1

        player = BlockingPlayer()
        player.snapshot_value.update(available=True, state="PAUSED")
        sonos = FakeSonosVolume(volume=61)
        original_set_volume = sonos.set_volume

        def record_volume(value):
            events.append(f"volume:{value}")
            return original_set_volume(value)

        sonos.set_volume = record_volume
        service = video.VideoService(
            self.library,
            self.store,
            player,
            sonos=sonos,
            legacy_positions=str(self.fixture.root / "missing-legacy.txt"),
            clock=self.clock,
        )
        service.control_lock = ObservedRLock()
        service.stop_event = OneIterationStopEvent()
        service.sleep_deadline = self.clock() - 1

        def resume():
            try:
                service.control_player("play")
            except Exception as exc:
                errors.append(exc)

        resume_thread = threading.Thread(
            target=resume, name="video-resume-test", daemon=True
        )
        expiry_thread = threading.Thread(
            target=service._loop, name="sleep-expiry-test", daemon=True
        )
        resume_thread.start()
        try:
            self.assertTrue(prep_entered.wait(1), "resume never entered room prep")
            expiry_thread.start()
            self.assertTrue(
                expiry_attempted_lock.wait(1),
                "sleep expiry did not attempt to acquire the playback lock",
            )
            self.assertNotIn("pause", player.action_calls)
            self.assertIsNotNone(service.sleep_deadline)
        finally:
            allow_prep.set()
            resume_thread.join(2)
            if expiry_thread.ident is not None:
                expiry_thread.join(2)

        self.assertFalse(resume_thread.is_alive())
        self.assertFalse(expiry_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(player.action_calls, ["play", "pause"])
        self.assertIsNone(service.sleep_deadline)
        self.assertEqual(
            events,
            ["prepare", "volume:61", "action:play", "action:pause"],
        )

    def test_manual_progress_override_survives_polling_until_explicit_play(self):
        self.player.snapshot_value = {
            "available": True,
            "state": "PAUSED",
            "path": self.movie.real_path,
            "position": 100.0,
            "duration": 1_000.0,
            "track_id": "/fake/track/movie",
            "can_seek": True,
        }
        self.store.record(
            self.movie.key,
            position=100,
            duration=1_000,
            finished=False,
            updated=100,
        )
        self.store.mark(self.movie.key, True)
        self.clock.advance(20)
        self.service.status()
        self.assertTrue(self.store.get(self.movie.key)["finished"])

        self.store.mark(self.movie.key, False)
        self.player.snapshot_value.update(position=960.0)
        self.clock.advance(20)
        self.service.status()
        self.assertFalse(self.store.get(self.movie.key)["finished"])

        self.service.play(item_id=self.movie.id, restart=True)
        self.player.snapshot_value.update(
            path=self.movie.real_path,
            position=980.0,
            duration=1_000.0,
            state="PLAYING",
        )
        self.clock.advance(20)
        self.service.status()
        self.assertTrue(self.store.get(self.movie.key)["finished"])

    def test_identical_paused_poll_does_not_refresh_database_updated_time(self):
        self.player.snapshot_value = {
            "available": True,
            "state": "PAUSED",
            "path": self.movie.real_path,
            "position": 300.0,
            "duration": 1_000.0,
            "track_id": "/fake/track/movie",
            "can_seek": True,
        }
        with mock.patch.object(
            self.store, "record", wraps=self.store.record
        ) as record:
            self.service.status()
            first_call_count = record.call_count
            first_updated = self.store.get(self.movie.key)["updated"]
            self.clock.advance(60)
            self.service.status()
            self.assertEqual(record.call_count, first_call_count)
        self.assertEqual(self.store.get(self.movie.key)["updated"], first_updated)

    def test_sleep_timer_stays_armed_when_pause_fails(self):
        class TwoPassStopEvent:
            def __init__(self):
                self.calls = 0

            def wait(self, _timeout):
                self.calls += 1
                return self.calls > 1

        deadline = self.clock() - 1
        self.service.sleep_deadline = deadline
        self.service.stop_event = TwoPassStopEvent()
        with mock.patch.object(
            self.player, "action", side_effect=RuntimeError("pause unavailable")
        ) as action:
            self.service._loop()
        action.assert_called_once_with("pause")
        self.assertEqual(self.service.sleep_deadline, deadline)
        self.assertEqual(self.service.last_error, "pause unavailable")

    def test_play_re_resolves_queue_and_passes_verified_real_paths(self):
        first = self.episodes[0]
        replacement = self.fixture.target("replacement-first-episode.mkv")
        Path(first.path).unlink()
        Path(first.path).symlink_to(replacement)

        result = self.service.play(item_id=first.id, restart=True)
        self.assertEqual(result["queued"], len(self.episodes))
        expected_paths = [os.path.realpath(item.path) for item in self.episodes]
        self.assertEqual(self.player.launch_calls[-1]["paths"], expected_paths)
        self.assertEqual(expected_paths[0], os.path.realpath(replacement))
        self.assertNotIn(first.path, expected_paths)

    def test_play_rejects_symlink_replaced_with_target_outside_selected_mount(self):
        outside = self.fixture.target("outside-replacement.mkv", outside=True)
        Path(self.movie.path).unlink()
        Path(self.movie.path).symlink_to(outside)

        with self.assertRaises(RuntimeError):
            self.service.play(item_id=self.movie.id, restart=True)
        self.assertEqual(self.player.launch_calls, [])

    def test_status_records_current_item_and_marks_near_credits_finished(self):
        episode = self.episodes[0]
        self.player.snapshot_value = {
            "available": True,
            "state": "PAUSED",
            "path": episode.real_path,
            "position": 950.0,
            "duration": 1_000.0,
            "track_id": "/fake/private/track",
            "volume": 0.75,
            "rate": 1.0,
            "can_fullscreen": True,
        }
        status = self.service.status()
        self.assertEqual(status["player"]["item"]["id"], episode.id)
        self.assertEqual(status["player"]["show_id"], self.show.id)
        self.assertNotIn("track_id", status["player"])
        self.assertNotIn("volume", status["player"])
        self.assertEqual(
            status["audio"],
            {
                "available": True,
                "volume": 47,
                "muted": False,
                "device": "vonRear",
                "vlc_fixed": 100,
            },
        )
        self.assertEqual(status["player"]["remaining_text"], "-0:50")
        self.assertTrue(self.store.get(episode.key)["finished"])

    def test_status_reports_sonos_failure_without_exposing_vlc_volume(self):
        self.player.snapshot_value.update(available=True, volume=0.33)
        self.sonos.snapshot_value = {
            "available": False,
            "volume": None,
            "muted": None,
            "device": None,
            "error": "rear Sonos pair unavailable",
        }

        status = self.service.status()

        self.assertFalse(status["audio"]["available"])
        self.assertIsNone(status["audio"]["volume"])
        self.assertEqual(status["audio"]["vlc_fixed"], 100)
        self.assertIn("rear Sonos pair unavailable", status["audio"]["error"])
        self.assertNotIn("volume", status["player"])

    def test_library_payload_exposes_continue_up_next_and_no_filesystem_paths(self):
        one, two, _three = self.episodes
        self.store.record(
            one.key, position=1_000, duration=1_000, finished=True, updated=100
        )
        self.store.record(
            two.key, position=220, duration=1_000, finished=False, updated=200
        )
        self.store.record(
            self.movie.key,
            position=400,
            duration=2_000,
            finished=False,
            updated=300,
        )
        payload = self.service.library_payload()
        self.assertEqual(
            [item["id"] for item in payload["continue"][:2]],
            [self.movie.id, two.id],
        )
        self.assertEqual(payload["up_next"][0]["id"], self.show.id)
        self.assertEqual(payload["up_next"][0]["next"]["id"], two.id)
        self.assertEqual(payload["up_next"][0]["watched"], 1)
        serialized = json.dumps(payload)
        self.assertNotIn(str(self.fixture.mount), serialized)
        self.assertNotIn("real_path", serialized)
        self.assertNotIn("rel_path", serialized)

    def test_search_finds_fuzzy_show_exact_episode_and_movie(self):
        fuzzy = self.service.search("galaxi qust")
        self.assertIn(self.show.id, {result["id"] for result in fuzzy})
        episode_matches = self.service.search("mutiny")
        self.assertIn(self.episodes[1].id, {result["id"] for result in episode_matches})
        movie_matches = self.service.search("dune part two")
        self.assertEqual(movie_matches[0]["id"], self.movie.id)
        self.assertEqual(self.service.search("   "), [])

    def test_sleep_timer_has_bounds_and_reports_remaining_time(self):
        with self.assertRaisesRegex(ValueError, "0 to 480"):
            self.service.set_sleep_timer(-1)
        with self.assertRaisesRegex(ValueError, "0 to 480"):
            self.service.set_sleep_timer(481)
        self.service.set_sleep_timer(30)
        self.clock.advance(125)
        status = self.service.status()["sleep_timer"]
        self.assertTrue(status["active"])
        self.assertEqual(status["remaining"], 1_675)
        self.assertEqual(status["remaining_text"], "27:55")
        self.service.set_sleep_timer(0)
        self.assertFalse(self.service.status()["sleep_timer"]["active"])


class ApiRouteTests(unittest.TestCase):
    def setUp(self):
        self.fixture = MediaFixture()
        self.addCleanup(self.fixture.cleanup)
        self.library = add_sample_library(self.fixture)
        self.store = video.ProgressStore(":memory:")
        self.addCleanup(self.store.connection.close)
        self.player = FakePlayer()
        self.sonos = FakeSonosVolume()
        self.service = video.VideoService(
            self.library,
            self.store,
            self.player,
            sonos=self.sonos,
            legacy_positions=str(self.fixture.root / "missing-legacy.txt"),
        )
        self.active_service_patch = mock.patch.object(
            video, "active_service", return_value=self.service
        )
        self.active_service_patch.start()
        self.addCleanup(self.active_service_patch.stop)
        video.app.config.update(TESTING=True)
        self.client = video.app.test_client()
        self.item = next(
            item for item in self.library.items.values() if item.media_type == "movie"
        )

    def post(self, endpoint, data=None, **kwargs):
        return self.client.post(f"/api/{endpoint}", data=data or {}, **kwargs)

    def test_play_routes_accept_opaque_ids_but_never_client_paths(self):
        forged_path = "/etc/passwd"
        missing_id = self.post("play", {"path": forged_path})
        self.assertEqual(missing_id.status_code, 400)
        self.assertEqual(self.player.launch_calls, [])

        path_as_id = self.post("play", {"item": self.item.path})
        self.assertEqual(path_as_id.status_code, 404)
        self.assertEqual(self.player.launch_calls, [])

        traversal = self.post("play", {"item": "../../etc/passwd"})
        self.assertEqual(traversal.status_code, 404)
        self.assertEqual(self.player.launch_calls, [])

        valid = self.post(
            "play", {"item": self.item.id, "path": forged_path, "subtitles": "auto"}
        )
        self.assertEqual(valid.status_code, 200)
        self.assertEqual(
            self.player.launch_calls[-1]["paths"], [os.path.realpath(self.item.path)]
        )
        valid_payload = valid.get_json()
        serialized = json.dumps(valid_payload)
        self.assertNotIn(forged_path, serialized)
        self.assertNotIn(self.item.real_path, serialized)
        self.assertNotIn(Path(self.item.real_path).as_uri(), serialized)
        response_player = valid_payload.get("player") or {}
        for private_field in ("path", "url", "track_id", "volume"):
            self.assertNotIn(private_field, response_player)

        unknown_progress = self.post(
            "progress", {"item": forged_path, "action": "watched"}
        )
        self.assertEqual(unknown_progress.status_code, 404)
        encoded_show_path = self.client.get("/api/shows/%2Fetc%2Fpasswd")
        self.assertEqual(encoded_show_path.status_code, 404)

    def test_surprise_response_does_not_expose_raw_player_fields(self):
        self.service.random = mock.Mock()
        self.service.random.choice.return_value = self.item

        response = self.post("surprise", {"type": "movie", "subtitles": "auto"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        serialized = json.dumps(payload)
        self.assertNotIn(self.item.real_path, serialized)
        self.assertNotIn(Path(self.item.real_path).as_uri(), serialized)
        response_player = payload.get("player") or {}
        for private_field in ("path", "url", "track_id", "volume"):
            self.assertNotIn(private_field, response_player)

    def test_csrf_rejects_cross_origin_and_requires_header_for_browser_origin(self):
        base_url = "http://vanpi.lan:8789"
        cross_origin = self.post(
            "control",
            {"action": "toggle"},
            base_url=base_url,
            headers={"Origin": "https://evil.example", "X-Van-Video": "1"},
        )
        self.assertEqual(cross_origin.status_code, 403)
        self.assertEqual(self.player.action_calls, [])

        missing_header = self.post(
            "control",
            {"action": "toggle"},
            base_url=base_url,
            headers={"Origin": base_url},
        )
        self.assertEqual(missing_header.status_code, 403)
        self.assertEqual(self.player.action_calls, [])

        bad_referer = self.post(
            "control",
            {"action": "toggle"},
            base_url=base_url,
            headers={"Referer": "https://evil.example/player"},
        )
        self.assertEqual(bad_referer.status_code, 403)
        self.assertEqual(self.player.action_calls, [])

        same_origin = self.post(
            "control",
            {"action": "toggle"},
            base_url=base_url,
            headers={"Origin": base_url, "X-Van-Video": "1"},
        )
        self.assertEqual(same_origin.status_code, 200)
        cli_style = self.post("control", {"action": "pause"}, base_url=base_url)
        self.assertEqual(cli_style.status_code, 200)
        self.assertEqual(self.player.action_calls, ["toggle", "pause"])

    def test_control_route_waits_for_room_setup_before_resuming(self):
        events = []
        self.player.snapshot_value.update(available=True, state="PAUSED")
        self.player.prepare_room_callback = lambda: events.append("prepare")
        self.sonos.snapshot_value["volume"] = 25
        original_action = self.player.action
        original_set_volume = self.sonos.set_volume

        def record_action(name):
            events.append(f"action:{name}")
            original_action(name)

        def record_volume(value):
            events.append(f"volume:{value}")
            return original_set_volume(value)

        self.player.action = record_action
        self.sonos.set_volume = record_volume

        response = self.post("control", {"action": "toggle"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        self.assertEqual(self.player.prepare_room_calls, [{"wait": True}])
        self.assertEqual(self.sonos.volume_calls, [25])
        self.assertEqual(self.player.action_calls, ["play"])
        self.assertEqual(events, ["prepare", "volume:25", "action:play"])

    def test_control_route_fails_closed_when_room_setup_fails(self):
        self.player.snapshot_value.update(available=True, state="PAUSED")
        self.player.prepare_room_error = video.RoomPreparationError(
            "rear_movie exited 7"
        )

        response = self.post("control", {"action": "play"})

        self.assertEqual(response.status_code, 503)
        self.assertIn("rear_movie exited 7", response.get_json()["message"])
        self.assertEqual(self.player.prepare_room_calls, [{"wait": True}])
        self.assertEqual(self.player.action_calls, [])
        self.assertEqual(self.sonos.volume_calls, [])

    def test_control_action_allowlist_bookmarks_navigation_and_quits_on_stop(self):
        invalid = self.post("control", {"action": "Quit; rm -rf /"})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(self.player.action_calls, [])

        with mock.patch.object(self.service, "bookmark") as bookmark:
            for action in ("next", "previous", "stop"):
                response = self.post("control", {"action": action})
                self.assertEqual(response.status_code, 200)
            self.assertEqual(bookmark.call_count, 3)
        self.assertEqual(self.player.action_calls, ["next", "previous", "stop"])
        self.assertEqual(self.player.quit_calls, 1)

    def test_seek_rejects_invalid_nonfinite_and_out_of_range_values(self):
        for value in ("", "words", "nan", "inf", "-inf", "3600.01", "-3600.01"):
            with self.subTest(value=value):
                before = list(self.player.seek_calls)
                response = self.post("seek", {"seconds": value})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.player.seek_calls, before)
        for value in ("-3600", "-20", "0", "20", "3600"):
            with self.subTest(value=value):
                response = self.post("seek", {"seconds": value})
                self.assertEqual(response.status_code, 200)
        self.assertEqual(self.player.seek_calls, [-3600.0, -20.0, 0.0, 20.0, 3600.0])

    def test_absolute_position_is_bounded_by_current_seekable_track_duration(self):
        self.player.snapshot_value = {
            "available": True,
            "state": "PAUSED",
            "path": self.item.real_path,
            "position": 20.0,
            "duration": 100.0,
            "track_id": "/fake/track/current",
            "can_seek": True,
        }
        for position in ("0", "42.5", "100"):
            with self.subTest(position=position):
                response = self.post("position", {"position": position})
                self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.player.position_calls,
            [
                ("/fake/track/current", 0.0),
                ("/fake/track/current", 42.5),
                ("/fake/track/current", 100.0),
            ],
        )

        for position in ("", "words", "nan", "inf", "-inf", "-0.01", "100.01"):
            with self.subTest(position=position):
                before = list(self.player.position_calls)
                response = self.post("position", {"position": position})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.player.position_calls, before)

    def test_absolute_position_rejects_missing_or_unseekable_current_track(self):
        unavailable_states = (
            {
                "available": False,
                "state": "OFFLINE",
                "position": 0,
                "duration": 0,
            },
            {
                "available": True,
                "state": "PAUSED",
                "track_id": None,
                "position": 10,
                "duration": 100,
                "can_seek": True,
            },
            {
                "available": True,
                "state": "PAUSED",
                "track_id": "/fake/track/current",
                "position": 10,
                "duration": 100,
                "can_seek": False,
            },
            {
                "available": True,
                "state": "PAUSED",
                "track_id": "/fake/track/current",
                "position": 0,
                "duration": 0,
                "can_seek": True,
            },
            {
                "available": True,
                "state": "PAUSED",
                "track_id": "/fake/track/current",
                "position": 0,
                "duration": float("nan"),
                "can_seek": True,
            },
            {
                "available": True,
                "state": "PAUSED",
                "track_id": "/fake/track/current",
                "position": 0,
                "duration": float("inf"),
                "can_seek": True,
            },
        )
        for snapshot in unavailable_states:
            with self.subTest(snapshot=snapshot):
                self.player.snapshot_value = snapshot
                response = self.post("position", {"position": "20"})
                self.assertIn(response.status_code, (400, 409))
        self.assertEqual(self.player.position_calls, [])

    def test_volume_route_controls_sonos_only_and_requires_integer_0_to_100(self):
        for value in (
            "",
            "words",
            "nan",
            "inf",
            "-inf",
            "-1",
            "100.01",
            "101",
            "47.5",
        ):
            with self.subTest(value=value):
                response = self.post("volume", {"value": value})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.sonos.volume_calls, [])
        self.assertEqual(self.player.volume_calls, [])

        low_volume = self.post("volume", {"value": "0"})
        high_volume = self.post("volume", {"value": "100"})
        self.assertEqual(low_volume.status_code, 200)
        self.assertEqual(high_volume.status_code, 200)
        self.assertEqual(low_volume.get_json()["volume"], 0)
        self.assertEqual(high_volume.get_json()["volume"], 100)
        self.assertIn("Sonos", high_volume.get_json()["message"])
        self.assertEqual(self.sonos.volume_calls, [0, 100])
        self.assertEqual(self.player.volume_calls, [])

    def test_sonos_volume_failure_never_falls_back_to_vlc(self):
        self.sonos.set_volume = mock.Mock(side_effect=RuntimeError("UPnP unavailable"))
        response = self.post("volume", {"value": "47"})
        self.assertEqual(response.status_code, 502)
        self.assertIn("UPnP unavailable", response.get_json()["message"])
        self.assertEqual(self.player.volume_calls, [])

    def test_sonos_volume_waits_for_rear_movie_room_preparation(self):
        process = mock.Mock()
        process.poll.return_value = None
        player = video.VlcController()
        player.room_process = process
        self.service.player = player

        preparing = self.service.audio_status()
        blocked = self.post("volume", {"value": "51"})
        self.assertEqual(
            preparing,
            {
                "available": False,
                "preparing": True,
                "device": None,
                "volume": None,
                "muted": None,
                "vlc_fixed": 100,
                "error": None,
            },
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(self.sonos.volume_calls, [])

        process.poll.return_value = 0
        accepted = self.post("volume", {"value": "51"})
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.get_json()["volume"], 51)
        self.assertEqual(self.sonos.volume_calls, [51])
        self.assertEqual(self.sonos.invalidate_calls, 1)

    def test_rate_rejects_nonfinite_and_out_of_range_values(self):
        for value in ("", "words", "nan", "inf", "-inf", "0.49", "2.01"):
            with self.subTest(value=value):
                before = list(self.player.rate_calls)
                response = self.post("rate", {"value": value})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.player.rate_calls, before)

        low_rate = self.post("rate", {"value": "0.5"})
        high_rate = self.post("rate", {"value": "2"})
        self.assertEqual(low_rate.get_json()["rate"], 0.5)
        self.assertEqual(high_rate.get_json()["rate"], 2.0)

    def test_play_progress_surprise_and_sleep_inputs_are_validated(self):
        invalid_boolean = self.post(
            "play", {"item": self.item.id, "restart": "sometimes"}
        )
        self.assertEqual(invalid_boolean.status_code, 400)
        invalid_subtitles = self.post(
            "play", {"item": self.item.id, "subtitles": "$(touch /tmp/nope)"}
        )
        self.assertEqual(invalid_subtitles.status_code, 400)
        self.assertEqual(self.player.launch_calls, [])

        invalid_surprise = self.post("surprise", {"type": "everything"})
        self.assertEqual(invalid_surprise.status_code, 400)
        invalid_progress = self.post(
            "progress", {"item": self.item.id, "action": "delete"}
        )
        self.assertEqual(invalid_progress.status_code, 400)
        for minutes in ("-1", "481", "1.5", "later"):
            with self.subTest(minutes=minutes):
                response = self.post("sleep", {"minutes": minutes})
                self.assertEqual(response.status_code, 400)
        valid = self.post("sleep", {"minutes": "30"})
        self.assertEqual(valid.status_code, 200)

    def test_web_assets_manifest_and_api_cache_policy_are_served(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'id="search"', page.data)
        self.assertIn(b'id="player"', page.data)
        self.assertIn(b'data-control="toggle"', page.data)
        self.assertIn(b'data-seek="-300"', page.data)
        with self.client.get("/static/video_library.js") as javascript:
            self.assertEqual(javascript.status_code, 200)
        with self.client.get("/static/video_library.css") as stylesheet:
            self.assertEqual(stylesheet.status_code, 200)
        manifest = self.client.get("/manifest.webmanifest")
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest.get_json()["short_name"], "Movies & TV")
        status = self.client.get("/api/status")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.headers["Cache-Control"], "no-store")

    def test_status_maps_opaque_item_without_exposing_raw_vlc_identifiers(self):
        self.player.snapshot_value = {
            "available": True,
            "state": "PLAYING",
            "path": self.item.real_path,
            "url": Path(self.item.real_path).as_uri(),
            "title": "Dune.Part.Two.2024.mkv",
            "position": 120.0,
            "duration": 2_000.0,
            "track_id": "/org/videolan/vlc/playlist/42",
            "volume": 1.0,
            "rate": 1.0,
            "can_seek": True,
            "can_control": True,
        }
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        player = response.get_json()["player"]
        self.assertEqual(player["item"]["id"], self.item.id)
        self.assertEqual(player["item"]["title"], "Dune Part Two")
        self.assertEqual(response.get_json()["audio"]["volume"], 47)
        self.assertEqual(response.get_json()["audio"]["vlc_fixed"], 100)
        self.assertNotIn("volume", player)
        for private_field in ("path", "url", "track_id"):
            self.assertNotIn(private_field, player)
            self.assertNotIn(private_field, player["item"])
        serialized = json.dumps(response.get_json())
        self.assertNotIn(str(self.fixture.mount), serialized)
        self.assertNotIn(self.item.real_path, serialized)


class DeploymentWiringTests(unittest.TestCase):
    def test_service_declares_every_runtime_asset_and_session_dependency(self):
        unit = (REPOSITORY_ROOT / "pi" / "services" / "video-library.service").read_text(
            encoding="utf-8"
        )
        expected_paths = (
            "/home/pi/scripts/python-automation/video_library_server.py",
            "/home/pi/scripts/python-automation/templates/video_library.html",
            "/home/pi/scripts/python-automation/static/video_library.js",
            "/home/pi/scripts/python-automation/static/video_library.css",
            "/home/pi/scripts/python-automation/sonos_tasks.py",
            "/home/pi/sns.sh",
        )
        for path in expected_paths:
            with self.subTest(path=path):
                self.assertIn(f"ExecStartPre=/usr/bin/test -r {path}", unit)
        self.assertIn(
            "ExecStart=/usr/bin/python3 /home/pi/scripts/python-automation/video_library_server.py",
            unit,
        )
        self.assertIn("Environment=DISPLAY=:0", unit)
        self.assertIn("Environment=XDG_RUNTIME_DIR=/run/user/1000", unit)
        self.assertIn(
            "Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus", unit
        )
        self.assertIn(
            "Environment=PYTHONPATH=/home/pi/scripts/python-automation", unit
        )
        self.assertIn("User=pi", unit)
        self.assertIn("WantedBy=multi-user.target", unit)

    def test_sync_stages_python_template_and_static_assets_for_unit_restart_tracking(self):
        sync = (REPOSITORY_ROOT / "pi" / "sync_scripts.sh").read_text(
            encoding="utf-8"
        )
        updater = (REPOSITORY_ROOT / "pi" / "scripts" / "update_services.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'cp "$pi_apps/video_library/templates/video_library.html" "$python_stage/templates/"',
            sync,
        )
        for asset in ("video_library.js", "video_library.css"):
            self.assertIn(
                f'cp "$pi_apps/video_library/static/{asset}" "$python_stage/static/"',
                sync,
            )
        self.assertIn('"$pi_apps" "$pi_python" "$shared_python" -type f -name "*.py"', sync)
        self.assertIn('"$dsc/pi/sns.sh"', sync)
        self.assertIn("ExecStartPre", updater)
        self.assertIn("/home/pi/scripts/", updater)
        self.assertIn("changed_units+=(\"$unit\")", updater)

    def test_template_static_assets_dashboard_and_cli_use_the_same_api(self):
        template = (APP_DIR / "templates" / "video_library.html").read_text(
            encoding="utf-8"
        )
        javascript = (APP_DIR / "static" / "video_library.js").read_text(
            encoding="utf-8"
        )
        css = (APP_DIR / "static" / "video_library.css").read_text(encoding="utf-8")
        dashboard_template = (
            REPOSITORY_ROOT / "pi" / "apps" / "van_dashboard" / "templates" / "van_dashboard.html"
        ).read_text(encoding="utf-8")
        dashboard_javascript = (
            REPOSITORY_ROOT / "pi" / "apps" / "van_dashboard" / "static" / "van_dashboard.js"
        ).read_text(encoding="utf-8")
        bashrc = (REPOSITORY_ROOT / "pi" / ".bashrc").read_text(encoding="utf-8")

        self.assertIn("video_library.css", template)
        self.assertIn("video_library.js", template)
        self.assertRegex(
            template,
            r'id="volume"[^>]*max="100"[^>]*aria-label="[^"]*Sonos[^"]*"',
        )
        self.assertIn('"X-Van-Video": "1"', javascript)
        for endpoint in (
            'post("play"',
            'post("control"',
            'post("seek"',
            'post("position"',
            'post("progress"',
            'post("sleep"',
        ):
            self.assertIn(endpoint, javascript)
        self.assertIn(".player-shell", css)
        self.assertIn("const audio = payload.audio || {};", javascript)
        self.assertIn('$("volume").disabled = !audio.available;', javascript)
        self.assertIn("Number.isFinite(audio.volume)", javascript)
        self.assertNotIn("Number.isFinite(player.volume)", javascript)
        self.assertIn('id="video-library"', dashboard_template)
        self.assertIn("$('video-library').href = siblingServiceUrl(8789)", dashboard_javascript)
        self.assertIn('VIDEOAPI="http://localhost:8789/api"', bashrc)
        self.assertIn("vid()", bashrc)
        vlcmd = bashrc[
            bashrc.index("vlcmd() {") : bashrc.index("### AUDIOBOOKS ###")
        ]
        api_branch = vlcmd[: vlcmd.index("dbus-send")]
        for command, action in (
            ("PlayPause", "toggle"),
            ("Play", "play"),
            ("Pause", "pause"),
            ("Next", "next"),
            ("Previous", "previous"),
            ("Stop", "stop"),
        ):
            with self.subTest(vlcmd=command, action=action):
                self.assertIn(
                    f'{command}) video_action="{action}"',
                    api_branch,
                )
        self.assertIn("/control", api_branch)

    def test_frontend_captures_search_generation_and_honors_player_capabilities(self):
        javascript = (APP_DIR / "static" / "video_library.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("async function searchLibrary(query, serial)", javascript)
        self.assertIn("const serial = ++searchSerial", javascript)
        self.assertIn(
            "setTimeout(() => searchLibrary(query, serial), 220)", javascript
        )
        self.assertIn(
            "button.disabled = !player.available || !player.can_seek", javascript
        )
        self.assertIn(
            'if (control === "next") enabled = enabled && Boolean(player.can_next)',
            javascript,
        )
        self.assertIn(
            'else if (control === "previous") enabled = enabled && Boolean(player.can_previous)',
            javascript,
        )
        self.assertIn("if (!player || !player.can_seek) return", javascript)

    def test_legacy_position_logger_skips_manager_real_target_playback_privately(self):
        logger = (
            REPOSITORY_ROOT / "pi" / "scripts" / "log_position.sh"
        ).read_text(encoding="utf-8")
        guard = 'if [[ "$decoded" != */links/* ]]; then'
        trim = 'trimmed="`echo $decoded | sed -E \'s|.*\\/links||g\'`"'
        self.assertIn(guard, logger)
        self.assertIn(trim, logger)
        self.assertLess(logger.index(guard), logger.index(trim))
        guard_block = logger[
            logger.index(guard) : logger.index("  fi", logger.index(guard))
        ]
        self.assertIn("exit 0", guard_block)
        self.assertNotIn("echo ", guard_block)
        for line in logger.splitlines():
            if line.strip().startswith("echo "):
                self.assertNotIn("$decoded", line)


if __name__ == "__main__":
    unittest.main()
