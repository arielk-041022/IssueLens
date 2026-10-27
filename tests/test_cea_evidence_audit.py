"""Synthetic tests: no GT, network, Java repo, or third-party dependency."""
from __future__ import annotations

from copy import deepcopy
import math
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

here = Path(__file__).resolve().parent
source_dir = here.parent / "src"
if not (source_dir / "cea_evidence_audit.py").is_file():
    source_dir = here
sys.path.insert(0, str(source_dir))
from cea_evidence_audit import AuditError, audit


def keywords(issue):
    return issue.lower().split()


def scorer(features, tokens, issue):
    # Deterministic stand-in: exact name, string, and call fields have different weights.
    terms = set(tokens)
    score = 10 * (features['name'].lower() in terms if features['name'] else False)
    score += 7 * sum(value.lower() in terms for value in (features['strings'] or []))
    score += 5 * sum(value.lower() in terms for value in (features['calls'] or []))
    return float(score), {"synthetic": True}


def catalog():
    return {"algorithm": "bm25", "ranking": [
        {"method_id": "A", "ordinal": 0, "rank": 3,
         "features": {"name": "parser", "strings": [], "calls": []}},
        {"method_id": "B", "ordinal": 1, "rank": 1,
         "features": {"name": "helper", "strings": ["parser"], "calls": ["parser"]}},
        {"method_id": "C", "ordinal": 2, "rank": 2,
         "features": {"name": "other", "strings": [], "calls": []}},
    ]}


class CeaTests(unittest.TestCase):
    def test_name_mask_changes_leader(self):
        data = catalog()
        data['ranking'][1]['features']['calls'] = []
        output = audit(data, "parser", keywords, scorer)
        self.assertEqual(output['base_top1_method_id'], 'A')
        name = output['interventions'][0]
        self.assertTrue(name['top1_changed'])
        self.assertEqual(name['original_top1_new_rank'], 2)
        self.assertEqual(name['new_top1_method_id'], 'B')

    def test_masked_strings_can_change_leader(self):
        data = catalog()
        data['ranking'][1]['features']['calls'] = []
        data['ranking'][0]['features']['name'] = 'missing'
        output = audit(data, "parser", keywords, scorer)
        self.assertEqual(output['base_top1_method_id'], 'B')
        self.assertTrue(output['interventions'][1]['top1_changed'])

    def test_masked_calls_can_change_leader(self):
        data = catalog()
        data['ranking'][1]['features']['strings'] = []
        data['ranking'][0]['features']['name'] = 'missing'
        output = audit(data, "parser", keywords, scorer)
        self.assertEqual(output['base_top1_method_id'], 'B')
        self.assertTrue(output['interventions'][2]['top1_changed'])

    def test_input_unchanged(self):
        data = catalog()
        before = deepcopy(data)
        audit(data, 'parser', keywords, scorer)
        self.assertEqual(data, before)

    def test_ties_use_original_ordinal_not_bm25_rank(self):
        data = catalog()
        output = audit(data, 'unrelated', keywords, scorer)
        self.assertEqual(output['base_top1_method_id'], 'A')
        self.assertFalse(any(it['top1_changed'] for it in output['interventions']))

    def test_ranking_has_no_gt_dependencies(self):
        data = catalog()
        output = audit(data, 'parser', keywords, scorer)
        self.assertNotIn('gt', str(output).lower().replace('no_gt', ''))
        self.assertEqual(output['source_algorithm'], 'bm25')

    def test_duplicate_method_identity_rejected(self):
        data = catalog()
        data['ranking'][1]['method_id'] = 'A'
        with self.assertRaisesRegex(AuditError, 'DUPLICATE_METHOD_ID'):
            audit(data, 'parser', keywords, scorer)

    def test_missing_channel_rejected(self):
        data = catalog()
        del data['ranking'][0]['features']['calls']
        with self.assertRaisesRegex(AuditError, 'MISSING_CHANNEL_CALLS'):
            audit(data, 'parser', keywords, scorer)

    def test_response_reports_effect_counts(self):
        result = audit(catalog(), 'parser', keywords, scorer)
        for record in result['interventions']:
            self.assertGreaterEqual(record['candidates_with_score_change'], 1)
            self.assertGreaterEqual(record['nonempty_channel_candidates'], 1)

    def test_cli_end_to_end_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / 'src'
            src.mkdir()
            shutil.copyfile(source_dir / 'cea_evidence_audit.py', src / 'cea_evidence_audit.py')
            (src / 'retriever.py').write_text("""def extract_keywords(s):
    return s.lower().split()
def score_method(m, keywords, issue_text, disable=None):
    parts = set(keywords)
    return (10 * (m.get('name', '').lower() in parts) +
            7 * sum(x.lower() in parts for x in (m.get('strings') or [])) +
            5 * sum(x.lower() in parts for x in (m.get('calls') or []))), {}
""", encoding='utf-8')
            inputs = root / 'inputs'
            inputs.mkdir()
            (inputs / 'full-ranking.json').write_text(json.dumps(catalog()), encoding='utf-8')
            (inputs / 'issue.txt').write_text('parser', encoding='utf-8')
            args = [sys.executable, '-B', str(src / 'cea_evidence_audit.py'),
                    '--ranking', str(inputs / 'full-ranking.json'),
                    '--issue-file', str(inputs / 'issue.txt'),
                    '--output', str(root / 'out' / 'audit.json')]
            first = subprocess.run(args, text=True, capture_output=True, check=False)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn('CEA_AUDIT_OK', first.stdout)
            report = json.loads((root / 'out' / 'audit.json').read_text(encoding='utf-8'))
            self.assertEqual(len(report['interventions']), 3)
            self.assertEqual(report['input_provenance']['retriever_py_sha256'].__len__(), 64)
            second = subprocess.run(args, text=True, capture_output=True, check=False)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn('OUTPUT_ALREADY_EXISTS', second.stderr)

    def test_nonfinite_score_rejected(self):
        def nan_scorer(method, terms, issue):
            return math.nan, {}
        with self.assertRaisesRegex(AuditError, 'INVALID_RULE_SCORE'):
            audit(catalog(), 'parser', keywords, nan_scorer)

    def test_deterministic_report(self):
        a = audit(catalog(), 'parser', keywords, scorer)
        b = audit(catalog(), 'parser', keywords, scorer)
        self.assertEqual(a, b)

    def test_bad_topk_rejected(self):
        with self.assertRaisesRegex(AuditError, 'INVALID_TOPK'):
            audit(catalog(), 'parser', keywords, scorer, topk=0)


if __name__ == '__main__':
    unittest.main()
