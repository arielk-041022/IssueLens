"""Offline tests of the isolated V2-to-pilot adaptation boundary."""
import unittest
from pilot_output_contract_v2_adapter import adapt_final_v2


def rec(hexchar, name, sig, line, status='SOURCE_BOUND'):
    oid = 'source-occurrence-compat-v1:sha256:' + hexchar * 64
    return {'occurrence_id': oid, 'features': {'file': 'src/main/java/a/A.java', 'signature': sig},
            'index_record': {'start_line': line}, 'identity_status': 'AVAILABLE',
            'binding_status': status}


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.a = rec('a', 'f', 'f(int)', 10)
        self.b = rec('b', 'g', 'g(int)', 12)
        self.catalog = {'records': [self.a, self.b]}
        self.loc = 'src/main/java/a/A.java:12:public void g(final int n)'

    def check_reject(self, obj, allowed=None, direct=True):
        r = adapt_final_v2(obj, self.catalog, allowed if allowed is not None else {self.a['occurrence_id'], self.b['occurrence_id']}, direct=direct)
        self.assertEqual(r.status, 'REJECTED')
        self.assertIsNone(r.result)
        return r

    def test_partial_two_canonical(self):
        obj = {'ranking': [self.a['occurrence_id'], self.b['occurrence_id']], 'reason': 'why'}
        r = adapt_final_v2(obj, self.catalog, {self.a['occurrence_id'], self.b['occurrence_id']})
        self.assertEqual(r.status, 'ACCEPTED')
        self.assertEqual(len(r.result['ranking']), 2)
        self.assertEqual(r.result['original_ranking'], obj['ranking'])

    def test_direct_location(self):
        r = adapt_final_v2({'ranking': [self.loc], 'reason': 'why'}, self.catalog, {self.b['occurrence_id']}, direct=True)
        self.assertEqual(r.status, 'ACCEPTED')
        self.assertEqual(r.result['ranking'], [self.b['occurrence_id']])

    def test_no_non_direct_locators(self):
        self.assertEqual(self.check_reject({'ranking': [self.loc], 'reason': 'why'}, direct=False).failure_reason, 'LOCATORS_DIRECT_ONLY')

    def test_outside_top20_rejected_even_with_valid_id(self):
        r = self.check_reject({'ranking': [self.b['occurrence_id']], 'reason': 'why'}, allowed={self.a['occurrence_id']}, direct=False)
        self.assertEqual(r.failure_reason, 'OUTSIDE_FROZEN_ALLOWED_POOL')

    def test_direct_also_requires_allowed_pool(self):
        self.check_reject({'ranking': [self.loc], 'reason': 'why'}, allowed={self.a['occurrence_id']})

    def test_empty_or_overlong_reason(self):
        self.assertEqual(self.check_reject({'ranking': [self.a['occurrence_id']], 'reason': ' '}).failure_reason, 'REASON')
        self.assertEqual(self.check_reject({'ranking': [self.a['occurrence_id']], 'reason': 'x'*2049}).failure_reason, 'REASON')

    def test_duplicate_and_unresolved_are_atomic(self):
        self.check_reject({'ranking': [self.a['occurrence_id'], self.a['occurrence_id']], 'reason': 'why'})
        self.check_reject({'ranking': [self.a['occurrence_id'], 'source-occurrence-compat-v1:sha256:'+'c'*64], 'reason': 'why'})

    def test_ambiguous_source_and_line_conflict(self):
        self.catalog['records'].append(dict(self.b))
        self.check_reject({'ranking': [self.loc], 'reason': 'why'})
        self.catalog['records'].pop()
        self.check_reject({'ranking': ['src/main/java/a/A.java:13:public void g(final int n)'], 'reason': 'why'})

    def test_fields_and_length(self):
        self.check_reject({'ranking': [], 'reason': 'why'})
        self.check_reject({'ranking': [self.a['occurrence_id']], 'reason': 'why', 'extra': True})
        self.check_reject({'ranking': [self.a['occurrence_id']]*6, 'reason': 'why'})

    def test_invalid_allowed(self):
        self.assertEqual(adapt_final_v2({'ranking': [self.a['occurrence_id']], 'reason': 'why'}, self.catalog, None).failure_reason, 'ALLOWED_POOL_INVALID')


if __name__ == '__main__':
    unittest.main()
