import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from retrieval_reranking_bottleneck import analyze


def ids(n):
    return ['java-source-v2:example.C#m' + str(i) + '(java.lang.String)' for i in range(n)]


class BottleneckTests(unittest.TestCase):
    def test_multi_GT_and_retrieval_omission(self):
        g = ids(4); r = analyze(g, g[:3], g[:2], 'VALID')
        self.assertEqual((r['pool_GT'], r['final_hits'], r['retrieval_missed'], r['gap']), (3, 2, 1, 1))

    def test_capacity_is_not_model_error(self):
        g = ids(8); r = analyze(g, g, g[:5], 'VALID')
        self.assertEqual((r['pool_GT_not_in_top5'], r['capacity_limit'], r['gap']), (3, 3, 0))
        self.assertNotIn('RERANKING_AVOIDABLE_GAP', r['labels'])

    def test_capacity_and_reranking_coexist(self):
        g = ids(9); r = analyze(g, g[:7], g[:2], 'VALID')
        self.assertEqual((r['retrieval_missed'], r['capacity_limit'], r['gap']), (2, 2, 3))
        self.assertEqual(r['pool_GT_not_in_top5'], 5)

    def test_missing_GT_stays_na(self):
        r = analyze(None, ids(2), ids(1), 'VALID')
        self.assertIsNone(r['final_hits']); self.assertIsNone(r['GT_count'])

    def test_identity_conflict_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'METHOD_IDENTITY_CONFLICT'):
            analyze(ids(1), ids(1) * 2, [], 'VALID')

    def test_overloads_and_owner_are_not_normalized(self):
        gt = ['java-source-v2:example.C#run(java.lang.String)']
        pool = ['java-source-v2:example.C#run(int)', 'java-source-v2:other.C#run(java.lang.String)']
        r = analyze(gt, pool, pool, 'VALID')
        self.assertEqual((r['pool_GT'], r['final_hits']), (0, 0))

    def test_protocol_failure_no_ranking_no_invented_model_loss(self):
        r = analyze(ids(3), ids(2), None, 'OUTPUT_PROTOCOL_FAILED')
        self.assertEqual(r['pool_GT'], 2)
        self.assertIsNone(r['final_hits']); self.assertIsNone(r['gap'])

    def test_blocked_preview_is_not_actual_LLM_pool(self):
        r = analyze(ids(3), ids(2), None, 'BLOCKED')
        self.assertIsNone(r['pool_GT']); self.assertIsNone(r['theoretical_top5'])

    def test_alien_or_duplicate_output_rejected(self):
        with self.assertRaisesRegex(ValueError, 'OUTPUT_OUTSIDE_CANDIDATE_POOL'):
            analyze(ids(2), ids(1), [ids(2)[1]], 'VALID')
        with self.assertRaisesRegex(ValueError, 'METHOD_IDENTITY_CONFLICT'):
            analyze(ids(2), ids(2), ids(1) * 2, 'VALID')


if __name__ == '__main__':
    unittest.main()
