"""Offline unit tests for issuelens_v2_offline_check.py; synthetic public catalogs only.
No API calls, GT access, or project-file modifications.
"""
import copy
import json
import unittest

from issuelens_v2_offline_check import resolve, java_parameter_types

PREFIX = 'source-occurrence-compat-v1:sha256:'
FILE = 'src/main/java/org/apache/commons/lang3/RandomStringUtils.java'


def record(tag, line, signature, *, file=FILE, identity='AVAILABLE', binding='SOURCE_BOUND'):
    return {
        'occurrence_id': PREFIX + tag * 64,
        'features': {'file': file, 'signature': signature},
        'index_record': {'start_line': line},
        'identity_status': identity,
        'binding_status': binding,
    }


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.a = record('a', 796, 'nextAlphanumeric(int)')
        self.b = record('b', 812, 'nextAlphanumeric(int,int)')
        self.catalog = {'records': [self.a, self.b]}

    def accepted(self, ranking, catalog=None):
        catalog = catalog or self.catalog
        if not isinstance(ranking, list) or not 1 <= len(ranking) <= 5:
            return False
        matches = []
        for item in ranking:
            found, _ = resolve(item, catalog)
            if len(found) != 1 or found[0].get('identity_status') != 'AVAILABLE' or found[0].get('binding_status') != 'SOURCE_BOUND':
                return False
            matches.append(found[0]['occurrence_id'])
        return len(matches) == len(set(matches))

    def test_accept_short_ranking(self):
        self.assertTrue(self.accepted([self.a['occurrence_id']]))
        self.assertTrue(self.accepted([self.a['occurrence_id'], self.b['occurrence_id']]))

    def test_reject_empty(self):
        self.assertFalse(self.accepted([]))

    def test_reject_six(self):
        self.assertFalse(self.accepted([self.a['occurrence_id']] * 6))

    def test_reject_nonsensical_ranking(self):
        self.assertFalse(self.accepted('not a list'))
        self.assertFalse(self.accepted([None]))

    def test_reject_duplicate(self):
        self.assertFalse(self.accepted([self.a['occurrence_id']] * 2))
        self.assertFalse(self.accepted([
            self.a['occurrence_id'],
            f'{FILE}:796:public String nextAlphanumeric(final int count)',
        ]))

    def test_reject_unknown_id(self):
        self.assertFalse(self.accepted([PREFIX + 'f' * 64]))

    def test_accept_unique_path_line_signature(self):
        self.assertTrue(self.accepted([f'{FILE}:796:public String nextAlphanumeric(final int count)']))

    def test_reject_path_line_or_signature_mismatch(self):
        for text in (
            f'{FILE}:797:public String nextAlphanumeric(final int count)',
            f'{FILE}:796:public String nextAlphanumeric(final int min, final int max)',
            f'src/main/java/wrong/RandomStringUtils.java:796:public String nextAlphanumeric(final int count)',
            f'{FILE}:796:public String nextAlphabetic(final int count)',
        ):
            with self.subTest(text=text):
                self.assertFalse(self.accepted([text]))

    def test_reject_ambiguous_occurrence(self):
        duplicate = copy.deepcopy(self.a)
        duplicate['occurrence_id'] = PREFIX + 'c' * 64
        self.assertFalse(self.accepted([f'{FILE}:796:public String nextAlphanumeric(final int count)'],
                                       {'records': [self.a, duplicate]}))

    def test_overloads_disambiguated_by_signature_and_line(self):
        self.assertTrue(self.accepted([
            f'{FILE}:796:public String nextAlphanumeric(final int count)',
            f'{FILE}:812:public String nextAlphanumeric(final int min, final int max)',
        ]))
        # A method name alone must never resolve an overloaded method.
        self.assertFalse(self.accepted(['nextAlphanumeric']))

    def test_reject_non_source_bound(self):
        invalid = copy.deepcopy(self.a)
        invalid['binding_status'] = 'RECORD_ONLY'
        self.assertFalse(self.accepted([invalid['occurrence_id']], {'records': [invalid]}))
        invalid['binding_status'] = 'SOURCE_BOUND'
        invalid['identity_status'] = 'UNAVAILABLE'
        self.assertFalse(self.accepted([invalid['occurrence_id']], {'records': [invalid]}))

    def test_reject_unparsable_method_declaration(self):
        self.assertFalse(self.accepted([f'{FILE}:796:not a method']))
        self.assertIsNone(java_parameter_types('broken text'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
