"""Stand-alone synthetic tests for V2; no GT, API, or project access."""
import copy
import unittest
from output_contract_v2 import normalize, resolve, java_parameter_types

PREFIX = 'source-occurrence-compat-v1:sha256:'
PATH = 'src/main/java/org/apache/commons/lang3/RandomStringUtils.java'


def record(ch, line, signature):
    return {'occurrence_id': PREFIX + ch * 64,
            'features': {'file': PATH, 'signature': signature},
            'index_record': {'start_line': line},
            'identity_status': 'AVAILABLE', 'binding_status': 'SOURCE_BOUND'}


class ContractV2Tests(unittest.TestCase):
    def setUp(self):
        self.a = record('a', 796, 'nextAlphanumeric(int)')
        self.b = record('b', 812, 'nextAlphanumeric(int,int)')
        self.catalog = {'records': [self.a, self.b]}

    def check(self, ranking, *, catalog=None, reason='evidence'):
        return normalize({'ranking': ranking, 'reason': reason}, self.catalog if catalog is None else catalog)

    def test_short_ranking(self):
        self.assertEqual(self.check([self.a['occurrence_id']]).status, 'ACCEPTED')
        self.assertEqual(len(self.check([self.a['occurrence_id'], self.b['occurrence_id']]).ranking), 2)

    def test_location_signature(self):
        x = self.check([f'{PATH}:796:public String nextAlphanumeric(final int count)'])
        self.assertEqual(x.status, 'ACCEPTED')
        self.assertEqual(x.mapping[0]['mode'], 'PATH_LINE_SIGNATURE')

    def test_overload(self):
        x = self.check([f'{PATH}:796:public String nextAlphanumeric(final int count)',
                        f'{PATH}:812:public String nextAlphanumeric(final int min, final int max)'])
        self.assertEqual(x.status, 'ACCEPTED')

    def test_zero_or_six(self):
        for x in ([], [self.a['occurrence_id']] * 6):
            self.assertEqual(self.check(x).status, 'REJECTED')

    def test_nonlist(self):
        for x in (None, 'x', [None], [123]):
            self.assertEqual(self.check(x).status, 'REJECTED')

    def test_duplicate_and_atomic(self):
        x = self.check([self.a['occurrence_id'], f'{PATH}:796:public String nextAlphanumeric(final int count)'])
        self.assertEqual(x.status, 'REJECTED')
        self.assertEqual(x.ranking, ())

    def test_unknown_id(self):
        self.assertEqual(self.check([PREFIX + 'f' * 64]).status, 'REJECTED')

    def test_path_line_signature_conflicts(self):
        for x in (f'{PATH}:797:public String nextAlphanumeric(final int count)',
                  f'{PATH}:796:public String nextAlphanumeric(final int min,final int max)',
                  f'{PATH}:796:public String nextAlphabetic(final int count)',
                  'src/main/java/wrong/X.java:796:public String nextAlphanumeric(final int count)'):
            with self.subTest(x=x):
                self.assertEqual(self.check([x]).status, 'REJECTED')

    def test_ambiguous(self):
        other = copy.deepcopy(self.a)
        other['occurrence_id'] = PREFIX + 'c' * 64
        self.assertEqual(self.check([f'{PATH}:796:public String nextAlphanumeric(final int count)'],
                                    catalog={'records': [self.a, other]}).status, 'REJECTED')

    def test_source_unavailable(self):
        for key, value in [('identity_status','UNAVAILABLE'), ('binding_status','RECORD_ONLY')]:
            r = copy.deepcopy(self.a)
            r[key] = value
            self.assertEqual(self.check([r['occurrence_id']], catalog={'records':[r]}).status, 'REJECTED')

    def test_unsupported_declaration(self):
        self.assertIsNone(java_parameter_types('not a method'))
        self.assertEqual(self.check([f'{PATH}:796:not a method']).status, 'REJECTED')

    def test_required_fields(self):
        self.assertEqual(normalize({}, self.catalog).status, 'REJECTED')
        self.assertEqual(self.check([self.a['occurrence_id']], reason=' ').status, 'REJECTED')
        self.assertEqual(normalize({'ranking':[self.a['occurrence_id']], 'reason':'ok','extra':1}, self.catalog).status, 'REJECTED')

    def test_invalid_catalog(self):
        self.assertEqual(self.check([self.a['occurrence_id']], catalog={'records':None}).status, 'REJECTED')


if __name__ == '__main__':
    unittest.main()
