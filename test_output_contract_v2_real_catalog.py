"""Read-only V2 regression tests against the frozen PUBLIC Commons Lang catalogs.

Run from IssueLens project root:
    python -B -m unittest -v test_output_contract_v2_real_catalog.py
No API, GT, score calculation, or writes to experiment artifacts.
"""
import copy
import json
import unittest
from pathlib import Path

from output_contract_v2 import normalize

ROOT = Path(__file__).resolve().parent
EXEC = ROOT / 'experiments' / 'fair-comparison-commons-lang-pilot-v1' / 'executor'


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


class RealCatalogV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (EXEC / 'binding.json').is_file():
            raise RuntimeError(f'Expected public Pilot binding at {EXEC / "binding.json"}. Place script in project root.')
        binding = read_json(EXEC / 'binding.json')
        rows = {r['case_id']: r for r in read_json(EXEC / 'cases.json')['cases']}
        cls.catalogs = {}
        for case in ('LANG-1745', 'LANG-1750'):
            cls.catalogs[case] = read_json(Path(binding['parent_public_root']) / rows[case]['catalog'])
        cls.predictions = {}
        for case, batch, response in (
            ('LANG-1745', 'formal-recovery-v1', 's7.response.bin'),
            ('LANG-1750', 'formal-supplement-v1', 's12.response.bin'),
        ):
            envelope = read_json(EXEC / 'output' / batch / case / 'B_DIRECT' / response)
            cls.predictions[case] = json.loads(envelope['choices'][0]['message']['content'])

    def test_1745_real_response_two_candidates(self):
        r = normalize(self.predictions['LANG-1745'], self.catalogs['LANG-1745'])
        self.assertEqual(r.status, 'ACCEPTED', r.failure_reason)
        self.assertEqual(len(r.ranking), 2)
        self.assertTrue(all(m['mode'] == 'CANONICAL_ID' for m in r.mapping))

    def test_1750_real_response_five_locations(self):
        r = normalize(self.predictions['LANG-1750'], self.catalogs['LANG-1750'])
        self.assertEqual(r.status, 'ACCEPTED', r.failure_reason)
        self.assertEqual(len(r.ranking), 5)
        self.assertTrue(all(m['mode'] == 'PATH_LINE_SIGNATURE' for m in r.mapping))
        self.assertEqual(len(set(r.ranking)), 5)

    def test_real_overloads_remain_distinct(self):
        r = normalize(self.predictions['LANG-1750'], self.catalogs['LANG-1750'])
        self.assertEqual(r.status, 'ACCEPTED')
        self.assertNotEqual(r.ranking[0], r.ranking[3])  # nextAlphanumeric(int) vs (int,int)
        self.assertNotEqual(r.ranking[1], r.ranking[2])  # next(int,boolean,boolean) vs next(int)

    def test_changed_line_rejected(self):
        pred = copy.deepcopy(self.predictions['LANG-1750'])
        pred['ranking'][0] = pred['ranking'][0].replace(':796:', ':797:', 1)
        r = normalize(pred, self.catalogs['LANG-1750'])
        self.assertEqual((r.status, r.ranking), ('REJECTED', ()))

    def test_changed_signature_rejected(self):
        pred = copy.deepcopy(self.predictions['LANG-1750'])
        pred['ranking'][0] = pred['ranking'][0].replace('nextAlphanumeric(', 'nextAlphabetic(', 1)
        r = normalize(pred, self.catalogs['LANG-1750'])
        self.assertEqual((r.status, r.ranking), ('REJECTED', ()))

    def test_duplicate_real_record_must_be_ambiguous(self):
        # Small subset cloned from a genuine catalog record; no historical files modified.
        source = self.catalogs['LANG-1750']['records']
        example = next(rec for rec in source if rec.get('features', {}).get('signature') == 'nextAlphanumeric(int)'
                       and rec.get('index_record', {}).get('start_line') == 796)
        clone = copy.deepcopy(example)
        clone['occurrence_id'] = 'source-occurrence-compat-v1:sha256:' + 'f' * 64
        pred = {'ranking': [self.predictions['LANG-1750']['ranking'][0]], 'reason': 'diagnostic'}
        r = normalize(pred, {'records': [example, clone]})
        self.assertEqual((r.status, r.ranking), ('REJECTED', ()))

    def test_source_unavailable_fail_closed(self):
        example = next(rec for rec in self.catalogs['LANG-1750']['records']
                       if rec.get('features', {}).get('signature') == 'nextAlphanumeric(int)'
                       and rec.get('index_record', {}).get('start_line') == 796)
        clone = copy.deepcopy(example)
        clone['binding_status'] = 'RECORD_ONLY'
        r = normalize({'ranking': [clone['occurrence_id']], 'reason': 'diagnostic'}, {'records': [clone]})
        self.assertEqual((r.status, r.ranking), ('REJECTED', ()))

    def test_no_partial_accept_after_invalid_second_candidate(self):
        pred = copy.deepcopy(self.predictions['LANG-1745'])
        pred['ranking'][1] = 'source-occurrence-compat-v1:sha256:' + 'f' * 64
        r = normalize(pred, self.catalogs['LANG-1745'])
        self.assertEqual((r.status, r.ranking, r.mapping), ('REJECTED', (), ()))


if __name__ == '__main__':
    unittest.main()
