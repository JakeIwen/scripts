from dataclasses import replace
from unittest.mock import patch
from pi.apps.iptorrents_watch import check, config, state, status, wantlist
from pi.apps.iptorrents_watch.records import CANDIDATE_DISMISSED, CANDIDATE_DOWNLOADED, LOGIN_NEEDED, LOGIN_UNKNOWN
from pi.apps.iptorrents_watch.transport import LoginNeeded, SiteUnavailable
from pi.tests.media.iptorrents_watch_support import WatcherTest, candidate


class CheckTests(WatcherTest):
    def setUp(self):
        super().setUp()
        wantlist.edit('add', 'Silo', 1, 1)
        self.searches = []
        owner = self
        class FakeSite:
            def search(self, name):
                owner.assertTrue(status.status().checking)
                owner.searches.append(name)
                return [candidate()]
        self.site = FakeSite()

    def run_check(self, **kwargs):
        with patch.object(check.time, 'sleep') as sleep:
            result = check.run(site=self.site, get=self.library, **kwargs)
        return result, sleep

    def test_cache_force_spacing_and_busy_marker(self):
        wantlist.edit('add', 'Other', 1, 1)
        result, sleep = self.run_check()
        self.assertEqual(self.searches, ['Other', 'Silo'])
        self.assertEqual(sleep.call_count, 1)
        self.assertGreater(sleep.call_args.args[0], 0)
        self.assertFalse(result.checking)
        self.assertFalse((config.STATE_DIR / 'busy.json').exists())
        self.run_check()
        self.assertEqual(len(self.searches), 2)
        self.run_check(show='Silo')
        self.assertEqual(self.searches, ['Other', 'Silo', 'Silo'])
        self.assertTrue(result.library_available)
        self.assertEqual(result.new_candidate_count, 1)

    def test_merge_retains_history_and_discards_now_owned(self):
        known = replace(candidate(), first_seen='2020-01-01T00:00:00+00:00',
                        status=CANDIDATE_DOWNLOADED, downloaded_at='2021-01-01T00:00:00+00:00')
        state.save_candidates([known, candidate(torrent_id='456')])
        self.run_check()
        rows = state.candidates()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].first_seen, known.first_seen)
        self.assertEqual(rows[0].downloaded_at, known.downloaded_at)
        self.assertEqual(rows[0].status, CANDIDATE_DOWNLOADED)
        state.save_candidates([candidate()])
        wantlist.edit('add', 'Silo', 1, 2)
        self.run_check()
        self.assertEqual(state.candidates(), [])

    def test_known_new_first_seen_is_stable_and_dismissal_persists(self):
        self.run_check()
        initial = state.candidates()[0]
        initial.first_seen = '2020-01-01T00:00:00+00:00'
        state.save_candidates([initial])
        self.run_check(show='Silo')
        self.assertEqual(state.candidates()[0].first_seen, initial.first_seen)
        initial.status = CANDIDATE_DISMISSED
        state.save_candidates([initial])
        self.run_check()
        self.assertEqual(status.status().candidates, [])
        self.assertEqual(state.candidates()[0].status, CANDIDATE_DISMISSED)

    def test_pause_skip_and_new_prune(self):
        self.run_check()
        wantlist.edit('pause', 'Silo')
        result, _ = self.run_check()
        self.assertEqual(self.searches, ['Silo'])
        self.assertEqual(result.new_candidate_count, 0)

    def test_qbittorrent_and_watch_folder_exclusions(self):
        def local(url):
            return [{'name': candidate().name}] if url.endswith('/info') else self.library(url)
        check.run(site=self.site, get=local)
        self.assertEqual(state.candidates(), [])
        (config.TFILES_DIR / (candidate().name + ' [IPT].torrent')).write_bytes(b'fixture')
        self.run_check()
        self.assertEqual(state.candidates(), [])

    def test_failure_retains_candidates_and_releases_busy(self):
        state.save_candidates([candidate()])
        for error, login in [(LoginNeeded('login needed'), LOGIN_NEEDED),
                             (SiteUnavailable('site unreachable'), LOGIN_UNKNOWN)]:
            with patch.object(self.site, 'search', side_effect=error):
                with self.assertRaises(type(error)):
                    self.run_check()
            view = status.status()
            self.assertFalse(view.checking)
            self.assertEqual(view.login, login)
            self.assertEqual(len(view.candidates), 1)
            self.assertIsNone(view.last_check)
            self.assertEqual(view.last_error, str(error))

    def test_status_and_parallel_mutation(self):
        with state.operation(checking=True):
            self.assertTrue(status.status().checking)
            with self.assertRaisesRegex(RuntimeError, 'busy'):
                wantlist.edit('pause', 'Silo')
        self.assertFalse(status.status().checking)

    def test_show_selection_unknown_errors_without_site_calls(self):
        with self.assertRaisesRegex(ValueError, 'not wanted'):
            self.run_check(show='Missing')
        self.assertFalse(self.searches)
