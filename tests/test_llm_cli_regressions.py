"""Actual CLI reranker boundary regressions; synthetic candidates, no API/GT."""
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import llm_reranker as reranker
import evidence_replay as replay


class LLMCLIRegressionTests(unittest.TestCase):
    def setUp(self):
        self.candidates = [{'method': {'name': name, 'class': 'Fruit', 'file': 'Fruit.java',
                            'signature': name + '()', 'annotations': [], 'strings': [], 'calls': []},
                            'score': 2 - i, 'detail': {}}
                           for i, name in enumerate(['first', 'second'])]

    def check_invalid(self, ranking):
        content = json.dumps({'ranking': ranking, 'reason': 'fixture'})
        with patch.object(reranker, '_call_llm', return_value=content) as call:
            result, info = reranker.rerank_candidates('Original issue', self.candidates)
        self.assertIs(result, self.candidates)
        self.assertFalse(info['reordered'])
        self.assertIn('ranking 不合法', info['reason'])
        self.assertEqual(call.call_count, 1)

    def test_float_indices_fall_back_instead_of_list_index_crash(self):
        self.check_invalid([2.0, 1.0])

    def test_boolean_is_not_a_candidate_number(self):
        self.check_invalid([True, 2])

    def test_mixed_string_and_integer_fall_back_instead_of_sort_crash(self):
        self.check_invalid(['2', 1])

    def test_null_and_object_indices_fail_without_crashing(self):
        for ranking in [[None, 2], [{'id': 1}, 2]]:
            with self.subTest(ranking=ranking):
                self.check_invalid(ranking)

    def test_duplicate_and_out_of_range_are_not_repaired(self):
        for ranking in [[1, 1], [0, 2], [1, 3], [1]]:
            with self.subTest(ranking=ranking):
                self.check_invalid(ranking)

    def test_valid_integer_permutation_keeps_order_and_objects(self):
        with patch.object(reranker, '_call_llm', return_value='{"ranking":[2,1],"reason":"fixture"}'):
            result, info = reranker.rerank_candidates('Original issue', self.candidates)
        self.assertTrue(info['reordered'])
        self.assertIs(result[0], self.candidates[1])
        self.assertIs(result[1], self.candidates[0])

    def test_replay_reports_invalid_ranking_as_rule_fallback(self):
        with patch.object(reranker, '_call_llm', return_value='{"ranking":[1.0,2.0]}'), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            result, info = replay.step_4_rerank({'issue_text': 'Original issue'}, self.candidates,
                                              'deepseek', False, False)
        self.assertIs(result, self.candidates)
        self.assertFalse(info['reordered'])
        self.assertIn('[fallback: rule-only]', output.getvalue())

    def test_provider_failure_remains_visible_and_not_retried(self):
        with patch.object(reranker, '_call_llm', side_effect=TimeoutError('offline fixture')) as call, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            result, info = replay.step_4_rerank({'issue_text': 'Original issue'}, self.candidates,
                                              'deepseek', False, False)
        self.assertIs(result, self.candidates)
        self.assertIn('LLM 调用失败', output.getvalue())
        self.assertEqual(call.call_count, 1)


if __name__ == '__main__':
    unittest.main()
