import unittest
from pi.apps.media_organizer import plans, records, scan
from pi.tests.media._media_organizer_support import Fixture


class PlanTests(Fixture, unittest.TestCase):
    def test_store_supersession_decisions_and_auto_rule(self):
        self.show()
        self.payload('The.Example.S01')
        self.payload('Movie.2025')
        first = plans.create_plan(records.Settings(classifier='offline'))
        plan = plans.create_plan(records.Settings(classifier='offline'))
        self.assertEqual(plans.load(first.id).status, records.PLAN_DISCARDED)
        self.assertEqual(plans.list_plans()[0].id, plan.id)
        rows = {r.entry.name: r for r in plan.rows}
        self.assertTrue(rows['The.Example.S01'].auto_eligible)
        self.assertFalse(rows['Movie.2025'].auto_eligible)
        plan = plans.decide(plan, {'Movie.2025': {'category': 'Documentaries', 'show': 'Nature', 'accepted': True}})
        row = next(row for row in plan.rows if row.entry.name == 'Movie.2025')
        self.assertEqual(row.target_path, str(self.media / 'torrent/Documentaries/Nature/Movie.2025'))
        self.assertFalse(row.auto_eligible)
        proposal = records.Proposal('TV', 'Brand New', .99, 'new show', False, 'codex')
        self.assertFalse(plans.auto_eligible(proposal, records.Settings()))
        self.assertTrue(plans.auto_eligible(proposal, records.Settings(auto_apply_new_shows=True)))

    def test_path_rejections_leave_saved_plan_unchanged(self):
        self.payload('Movie.2025')
        plan = plans.create_plan(records.Settings(classifier='offline'))
        for show in ('', '../escape', '/tmp/escape', '.hidden', 'a/b', 'a\\b', ' trailing '):
            with self.subTest(show=show), self.assertRaises(ValueError):
                plans.decide(plan, {'Movie.2025': {'category': 'TV', 'show': show, 'accepted': True}})
        self.assertEqual(plans.load(plan.id).rows[0].decision.category, 'Movies')
        for payload in ({'category': 'TV', 'show': 'Example', 'accepted': 'yes'},
                        {'category': 'Other', 'show': None, 'accepted': True}):
            with self.assertRaises(ValueError):
                plans.decide(plan, {'Movie.2025': payload})
        (self.media / 'torrent/TV').symlink_to(self.root)
        with self.assertRaises(ValueError):
            plans.decide(plan, {'Movie.2025': {'category': 'TV', 'show': 'Example', 'accepted': True}})

    def test_reject_skipped_unknown_rows_and_traversal_ids(self):
        self.payload('Downloading', progress=.1)
        plan = plans.create_plan(records.Settings(classifier='offline'))
        for name in ('Downloading', 'Missing'):
            with self.assertRaises(ValueError):
                plans.decide(plan, {name: {'category': 'Movies', 'show': None, 'accepted': True}})
        with self.assertRaises(ValueError):
            plans.load('../settings')
