from dataclasses import replace
from pi.apps.iptorrents_watch import config, state, wantlist
from pi.apps.iptorrents_watch.records import SOURCE_LIBRARY, SOURCE_MANUAL
from pi.tests.media.iptorrents_watch_support import WatcherTest, wanted


class WantlistTests(WatcherTest):
    def test_library_derivation_and_latest_disk_episode(self):
        now = 2000000000
        payload = {'shows': [
            {'id': 'recent', 'name': 'Recent', 'kind': 'tv', 'last_watched': now - 1},
            {'id': 'next', 'name': 'Up Next', 'kind': 'tv', 'last_watched': 1},
            {'id': 'old', 'name': 'Old', 'kind': 'tv', 'last_watched': 1},
            {'id': 'movie', 'name': 'Movie', 'kind': 'movie', 'last_watched': now},
            {'id': 'never', 'name': 'Never', 'kind': 'tv', 'last_watched': 0},
        ], 'up_next': [{'id': 'next'}]}
        called = []
        def details(key):
            called.append(key)
            return {'episodes': [{'season': 1, 'episode': 99}, {'season': 2, 'episode': 5},
                                 {'season': 2, 'episode': 3}, {'season': None, 'episode': None}]}
        rows = wantlist.derive(payload, details, now)
        self.assertEqual([row.name for row in rows], ['Recent', 'Up Next'])
        self.assertEqual(called, ['recent', 'next'])
        self.assertTrue(all((row.have_season, row.have_episode) == (2, 5) for row in rows))
        self.assertTrue(all(row.source == SOURCE_LIBRARY for row in rows))

    def test_manual_newer_only_merge_and_pause_survive_refresh(self):
        library = replace(wanted(season=2, episode=4), source=SOURCE_LIBRARY, library_show_id='silo')
        state.write_json(config.WATCHLIST_PATH, {'manual': [], 'library': [library.to_dict()], 'paused': [], 'removed': []})
        row = wantlist.edit('add', 'Silo', 1, 99)[0]
        self.assertEqual((row.have_season, row.have_episode), (2, 4))
        self.assertEqual(row.source, SOURCE_MANUAL)
        row = wantlist.edit('add', 'Silo', 2, 6)[0]
        self.assertEqual((row.have_season, row.have_episode), (2, 6))
        self.assertTrue(wantlist.edit('pause', 'SILO')[0].paused)
        with state.operation():
            wantlist.refresh(self.library)
        self.assertTrue(wantlist.merged()[0].paused)
        self.assertFalse(wantlist.edit('resume', 'Silo')[0].paused)
        self.assertEqual(wantlist.edit('remove', 'Silo'), [])

    def test_removed_library_show_stays_removed(self):
        library = replace(wanted(), source=SOURCE_LIBRARY)
        data = {'manual': [], 'library': [library.to_dict()], 'paused': [], 'removed': []}
        state.write_json(config.WATCHLIST_PATH, data)
        wantlist.edit('remove', 'Silo')
        self.assertEqual(wantlist.merged(), [])
        self.assertEqual(len(wantlist.edit('add', 'Silo')), 1)

    def test_bad_input_rejected(self):
        for args in [('add', '', None, None), ('add', 'Bad\nName', None, None),
                     ('add', 'Silo', -1, None), ('add', 'Silo', None, 2), ('pause', 'Missing', None, None)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                wantlist.edit(*args)

    def test_unavailable_library_preserves_records(self):
        wantlist.edit('add', 'Silo', 1, 1)
        before = config.WATCHLIST_PATH.read_bytes()
        with state.operation(), self.assertRaisesRegex(RuntimeError, 'library'):
            wantlist.refresh(lambda url: {})
        self.assertEqual(config.WATCHLIST_PATH.read_bytes(), before)
        self.assertFalse(state.metadata()['library_available'])
