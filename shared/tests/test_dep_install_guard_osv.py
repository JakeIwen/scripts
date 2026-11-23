"""Exercise the OSV adapter and its decisions through offline wire fixtures."""
import json
import unittest
from collections import Counter

from dep_guard_osv import lookup_advisories

A = ('npm', 'alpha')
B = ('PyPI', 'beta-tool')
M1, M2, M3 = ('MAL-2025-1001', 'MAL-2025-1002', 'MAL-2025-1003')


def page(*ids, token=None):
    data = {'vulns': [{'id': i} for i in ids]}
    if token:
        data['next_page_token'] = token
    return data


def entry(key=A, **fields):
    return dict(package={'ecosystem': key[0], 'name': key[1]}, **fields)


def detail(aid, *entries):
    return {'id': aid, 'affected': list(entries)}


def span(*events, kind='ECOSYSTEM'):
    return {'type': kind, 'events': [{key: value} for key, value in events]}


class Cache(dict):
    def put(self, key, value, ttl_seconds):
        self[key] = json.loads(json.dumps(value))


class Wire:
    def __init__(self):
        self.calls = Counter()
        self.pages = {}
        self.details = {}
        self.exact = {}
        self.expired = False
        self.batch_reply = None
        self.in_map = False

    def remaining(self):
        return 0 if self.expired else 1

    def map(self, jobs):
        self.in_map = True
        try:
            return {k: f() for k, f in jobs.items()}
        finally:
            self.in_map = False

    def get_json(self, url, *, method='GET', body=None, **_):
        assert self.in_map
        base = 'https://api.osv.dev/v1/'
        assert url.startswith(base)
        endpoint = url[len(base):]
        self.calls[endpoint] += 1
        if endpoint.startswith('vulns/'):
            assert method == 'GET' and body is None
            return 200, self.details[endpoint[6:]]
        assert method == 'POST'
        if endpoint == 'querybatch':
            if self.batch_reply is not None:
                return self.batch_reply
            assert set(body) == {'queries'}
            rows = []
            for q in body['queries']:
                assert set(q) in ({'package'}, {'package', 'page_token'})
                p = q['package']
                assert set(p) == {'ecosystem', 'name'}
                rows.append(self.pages[(p['ecosystem'], p['name']), q.get('page_token')])
            return 200, {'results': rows}
        assert endpoint == 'query'
        p = body['package']
        assert set(body) in ({'package', 'version'}, {'package', 'version', 'page_token'})
        return self.exact[(p['ecosystem'], p['name']), body['version'], body.get('page_token')]


class AdvisoryTest(unittest.TestCase):
    def setUp(self):
        self.w = Wire()
        self.c = Cache()

    def evidence(self, *keys, pins=None):
        return lookup_advisories(keys, self.w, self.c, pins=pins)

    def assertIncomplete(self, decision):
        self.assertIn('incomplete', ' '.join(decision[1]))
        self.assertNotIn('outside', ' '.join(decision[1]))

    def test_clean_pages_include_no_versions_and_ignore_ordinary_cves(self):
        self.w.pages[A, None] = page('GHSA-xyz')
        self.w.pages[B, None] = {}
        first = self.evidence(A, B)
        self.assertEqual(first[A].decision('1.0.0'), ([], []))
        self.assertEqual(first[B].decision(None), ([], []))
        self.evidence(A, B)
        self.assertEqual(self.w.calls, {'querybatch': 1})

    def test_multi_query_pagination_and_all_matching_details(self):
        self.w.pages[A, None] = page(M1, token='a1')
        self.w.pages[A, 'a1'] = page(M2)
        self.w.pages[B, None] = page('GHSA-other', token='b1')
        self.w.pages[B, 'b1'] = page(M2, token='b2')
        self.w.pages[B, 'b2'] = page(M3)
        self.w.details[M1] = detail(M1, entry(versions=['1.0.0']))
        self.w.details[M2] = detail(M2, entry(versions=['2.0.0']), entry(('PyPI', 'Beta_Tool'), versions=['2.0']))
        self.w.details[M3] = detail(M3, entry(B, ranges=[span(('introduced', '0'))]))
        ev = self.evidence(A, B)
        self.assertIn(M1, ' '.join(ev[A].decision('1.0.0')[0]))
        self.assertIn(M2, ' '.join(ev[A].decision('2.0.0')[0]))
        self.assertIn(M3, ' '.join(ev[B].decision('999')[0]))
        self.assertNotIn(M3, ' '.join(ev[A].decision('3.0.0')[1]))
        self.assertEqual(ev[A].decision('3.0.0')[0], [])
        for nonpin in (None, '^3.0.0', 'latest'):
            reasons = ev[A].decision(nonpin)[0]
            self.assertEqual(len(reasons), 2)
            self.assertTrue(all('pin an exact version outside' in m for m in reasons))

    def test_mismatched_or_invalid_detail_is_not_a_confirmed_mal(self):
        for response in [detail(M1, entry(B, versions=['1'])), detail(M2, entry(versions=['1.0.0'])),
                         {'id': M1, 'affected': {}}, detail(M1, entry()),
                         detail(M1, entry(ranges=[span(('fixed', '1.0.0'))])), None]:
            with self.subTest(response=response):
                self.w.pages[A, None] = page(M1)
                self.w.details[M1] = response
                ev = self.evidence(A)[A]
                self.assertFalse(ev.decision(None)[0])
                self.assertIncomplete(ev.decision('1.0.0'))
                self.assertFalse(self.c)

    def test_bad_count_and_bad_shapes_are_not_empty_snapshots(self):
        for data in (None, [], {}, {'results': [{}]}, {'results': {}},
                     {'results': [{'error': True}, {'vulns': 'x'}]},
                     {'results': [{'vulns': [None]}, {'next_page_token': 12}]}):
            with self.subTest(data=data):
                self.w.batch_reply = 200, data
                ev = self.evidence(A, B)
                self.assertIncomplete(ev[A].decision(None))
                self.assertIncomplete(ev[B].decision(None))
                self.assertFalse(self.c)

    def test_partial_search_retains_only_confirmed_facts_on_current_and_later_runs(self):
        self.w.pages[A, None] = page(M1, M2, token='again')
        self.w.pages[A, 'again'] = page(token='again')
        self.w.pages[B, None] = {'error': 'unavailable'}
        self.w.details[M1] = detail(M1, entry(versions=['1.1.1', None]))
        self.w.details[M2] = None
        for expired in (False, True):
            self.w.expired = expired
            e = self.evidence(A, B)
            self.assertIn(M1, ' '.join(e[A].decision('1.1.1')[0]))
            self.assertIncomplete(e[A].decision('1.1.2'))
            self.assertIncomplete(e[B].decision(None))
        self.w.expired = False
        self.w.pages[A, None] = page(M1)
        self.w.details[M1] = detail(M1, entry(versions=['1.1.1']))
        self.assertIn('outside', ' '.join(self.evidence(A)[A].decision('2.0.0')[1]))

    def test_completed_advisory_survives_sibling_detail_deadline(self):
        self.w.pages[A, None] = page(M1, M2)
        self.w.details[M1] = detail(M1, entry(ranges=[span(('introduced', '0'))]))
        self.w.details[M2] = None
        self.assertTrue(self.evidence(A)[A].decision(None)[0])
        self.w.expired = True
        second = self.evidence(A)[A].decision('9.0.0')
        self.assertIn(M1, ' '.join(second[0]))
        self.assertIncomplete(second)

    def test_positive_discovered_after_cache_eviction_is_not_masked_by_old_snapshot(self):
        self.w.pages[A, None] = page(M1)
        self.w.details[M1] = detail(M1, entry(versions=['1.0.0']))
        self.evidence(A)
        # Simulate oldest-first eviction of one detail, leaving the manifest intact.
        self.c.pop(next(k for k in self.c if k.endswith(':' + M1)))
        self.w.pages[A, None] = page(M1, M2, M3)
        self.w.details[M2] = detail(M2, entry(versions=['2.0.0']))
        self.w.details[M3] = None
        self.assertTrue(self.evidence(A)[A].decision('2.0.0')[0])
        self.w.expired = True
        ev = self.evidence(A)[A]
        self.assertTrue(ev.decision('2.0.0')[0])
        self.assertIncomplete(ev.decision('3.0.0'))

    def test_commit_range_requires_an_exact_query_not_local_registry_ordering(self):
        self.w.pages[A, None] = page(M1)
        commits = dict(span(('introduced', '0'), ('fixed', 'a' * 40), kind='GIT'), repo='https://example.invalid/r')
        self.w.details[M1] = detail(M1, entry(ranges=[commits]))
        self.w.exact[A, '1.1.0', None] = 200, {'vulns': [self.w.details[M1]]}
        ev = self.evidence(A, pins={A: {'1.1.0'}})[A]
        self.assertTrue(ev.decision(None)[0])
        self.assertTrue(ev.decision('1.1.0')[0])
        self.assertIncomplete(ev.decision('1.2.0'))

    def test_failed_cache_write_does_not_turn_a_deny_into_allow(self):
        def broken(*args):
            raise OSError('not writable')
        self.c.put = broken
        self.w.pages[A, None] = page(M1)
        self.w.details[M1] = detail(M1, entry(versions=['1.0.0']))
        self.assertTrue(self.evidence(A)[A].decision('1.0.0')[0])

    def test_semver_and_pep440_have_distinct_release_ordering(self):
        self.w.pages[A, None] = self.w.pages[B, None] = page(M1)
        self.w.details[M1] = detail(M1, entry(ranges=[span(('introduced', '1.0.0-rc.2'), ('fixed', '1.0.0'),
                                                         ('introduced', '2.0.0'), ('last_affected', '2.1.0'), kind='SEMVER')]),
                                    entry(B, versions=['1!3.0rc0.post1'], ranges=[span(('introduced', '0'),
                                                                                      ('fixed', '1.0a2'))]))
        e = self.evidence(A, B)
        for pin in ('1.0.0-rc.10', '2.1.0', '2.0.0'):
            self.assertTrue(e[A].decision(pin)[0], pin)
        for pin in ('1.0.0-rc.1', '1.0.0', '2.1.1'):
            self.assertFalse(e[A].decision(pin)[0], pin)
        for pin in ('1.0.dev10', '1.0alpha1', '1!3.0.0c0-1'):
            self.assertTrue(e[B].decision(pin)[0], pin)
        self.assertFalse(e[B].decision('1.0b1')[0])

    def long_list(self):
        self.w.pages[A, None] = page(M1)
        self.w.details[M1] = detail(M1, entry(versions=[f'1.0.{i}' for i in range(201)]))

    def test_list_cap_is_not_a_whole_package_deny(self):
        self.long_list()
        self.w.exact[A, '2.0.0', None] = 200, page('GHSA-other', token='next')
        self.w.exact[A, '2.0.0', 'next'] = 200, {}
        e = self.evidence(A, pins={A: {'2.0.0'}})[A]
        self.assertTrue(e.decision(None)[0])
        self.assertFalse(e.decision('2.0.0')[0])
        self.assertIn('outside', ' '.join(e.decision('2.0.0')[1]))
        self.assertIncomplete(e.decision('1.0.200'))
        self.assertTrue(e.decision('1.0.0')[0])
        stored = [r['versions'] for r in self.c.values() if isinstance(r, dict) and 'versions' in r]
        self.assertEqual([len(v) for v in stored], [200])

    def test_contradictory_exact_reply_is_not_a_deny_or_a_clean_pin(self):
        self.long_list()
        pin = '3.0.0'
        self.w.exact[A, pin, None] = 200, {'vulns': [self.w.details[M1]]}
        decision = self.evidence(A, pins={A: {pin}})[A].decision(pin)
        self.assertFalse(decision[0])
        self.assertIncomplete(decision)

    def test_exact_page_timeout_warns_without_losing_confirmed_hits(self):
        self.long_list()
        pin = '1.0.200'
        self.w.exact[A, pin, None] = 200, {'vulns': [self.w.details[M1]], 'next_page_token': 'rest'}
        self.w.exact[A, pin, 'rest'] = 503, {}
        self.w.exact[A, '3.0.0', None] = 429, {}
        e = self.evidence(A, pins={A: {pin, '3.0.0'}})[A]
        self.assertIn(M1, ' '.join(e.decision(pin)[0]))
        self.assertIncomplete(e.decision('3.0.0'))
        self.w.expired = True
        self.assertTrue(self.evidence(A, pins={A: {pin}})[A].decision(pin)[0])

    def test_withdrawal_is_not_active_malware(self):
        self.w.pages[A, None] = page(M1)
        self.w.details[M1] = dict(detail(M1, entry(versions=['1.0.0'])), withdrawn='2025-03-01T02:03:04Z')
        self.assertEqual(self.evidence(A)[A].decision('1.0.0'), ([], []))


if __name__ == '__main__':
    unittest.main()
