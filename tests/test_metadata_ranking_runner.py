"""Synthetic-only tests using original scorer modules and installed Jieba.

Original Jieba is explicitly warmed in class setup using a fresh temporary cache;
the runner is tested separately with all data I/O/environment/network denied.
No real issue, benchmark, GT, EXP001 artifact, or legacy predictor is loaded.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SRC = Path(__file__).resolve().parents[1] / 'src'
sys.path.insert(0, str(SRC))
import bm25_baseline as bm25
import metadata_experiment as protocol
import metadata_ranking_runner as runner
import retriever as rule

_SPEC = importlib.util.spec_from_file_location(
    'data08_synthetic_fixture', Path(__file__).resolve().parent / 'test_metadata_experiment.py')
_FIXTURE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_FIXTURE)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def fixture(size=12, text='Inspect reader failure 原始描述 重复输入'):
    case, catalog, bindings = _FIXTURE.fixture(size)
    case['issue_text'] = text
    bindings['case_metadata_sha256'] = digest(case)
    request = protocol.build_metadata_request(case, catalog, bindings)
    return request, bindings


def direct_scores(request, algorithm):
    rows = request['catalog']
    text = request['case_metadata']['issue_text']
    if algorithm == 'rule':
        keywords = rule.extract_keywords(text)
        return [rule.score_method(copy.deepcopy(r['features']), keywords, text)[0] for r in rows]
    docs = [bm25.tokenize(bm25.document(r['features'])) for r in rows]
    return bm25.score_tokens(docs, bm25.tokenize(text))[0]


class MetadataRankingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Explicit dependency preparation outside rank_metadata. Never replace
        # the tokenizer or copy its dictionary. A cold dependency failure must
        # fail tests, not silently skip original Rule consistency checks.
        cls._cache = tempfile.TemporaryDirectory(prefix='issuelens-data09-jieba-')
        cls.addClassCleanup(cls._cache.cleanup)
        previous = rule.jieba.dt.tmp_dir
        try:
            rule.jieba.dt.tmp_dir = cls._cache.name
            rule.jieba.initialize()
        finally:
            rule.jieba.dt.tmp_dir = previous
        if rule.jieba.dt.initialized is not True:
            raise AssertionError('Original Jieba failed initialization')

    def setUp(self):
        self.request, self.bindings = fixture()

    def rank(self, algorithm='bm25'):
        return runner.rank_metadata(self.request, self.bindings, algorithm=algorithm)

    def test_rule_normal(self):
        r = self.rank('rule')
        self.assertEqual((r['status'], r['scored_count'], r['operation']), ('SUCCESS', 12, 'METADATA_RANKING_ONLY'))

    def test_bm25_normal(self):
        r = self.rank()
        self.assertEqual((r['status'], r['scored_count']), ('SUCCESS', 12))

    def test_rule_exact_direct_scorer_scores_and_order(self):
        for text in ('Inspect reader failure', '重复输入 查找文件', 'inspect read 重复', 'Exception not found'):
            with self.subTest(text=text):
                self.request, self.bindings = fixture(text=text)
                scores = direct_scores(self.request, 'rule')
                order = sorted(range(len(scores)), key=lambda i: (-scores[i], self.request['catalog'][i]['ordinal']))
                result = self.rank('rule')
                self.assertEqual([r['ordinal'] for r in result['ranking']], order)
                self.assertEqual([r['score'] for r in result['ranking']], [scores[i] for i in order])

    def test_bm25_exact_direct_scores_and_order(self):
        scores = direct_scores(self.request, 'bm25')
        order = sorted(range(len(scores)), key=lambda i: (-scores[i], self.request['catalog'][i]['ordinal']))
        result = self.rank()
        self.assertEqual([r['ordinal'] for r in result['ranking']], order)
        self.assertEqual([r['score'] for r in result['ranking']], [scores[i] for i in order])

    def test_rule_calls_original_functions_once_per_method(self):
        with patch.object(rule, 'extract_keywords', wraps=rule.extract_keywords) as kw, \
             patch.object(rule, 'score_method', wraps=rule.score_method) as score:
            self.rank('rule')
        self.assertEqual(kw.call_count, 1)
        self.assertEqual(score.call_count, 12)
        for call in score.call_args_list:
            self.assertEqual(call.kwargs, {})  # no disable flags/weight overrides

    def test_bm25_calls_frozen_functions(self):
        with patch.object(bm25, 'document', wraps=bm25.document) as doc, \
             patch.object(bm25, 'tokenize', wraps=bm25.tokenize) as tok, \
             patch.object(bm25, 'score_tokens', wraps=bm25.score_tokens) as score:
            self.rank()
        self.assertEqual((doc.call_count, tok.call_count, score.call_count), (12, 13, 1))
        self.assertEqual((bm25.K1, bm25.B), (1.2, 0.75))

    def test_frozen_bm25_parameters_not_silently_changed(self):
        with patch.object(bm25, 'K1', 2.0), patch.object(bm25, 'score_tokens') as score:
            with self.assertRaisesRegex(runner.MetadataRankingError, 'FROZEN_BM25_PARAMETERS_CHANGED'):
                self.rank()
            score.assert_not_called()

    def test_rule_tie_stability(self):
        with patch.object(rule, 'score_method', return_value=(3.0, {})):
            r = self.rank('rule')
        self.assertEqual([x['ordinal'] for x in r['ranking']], list(range(12)))

    def test_bm25_zero_match_tie_stability(self):
        self.request, self.bindings = fixture(text='zzzzuniquequerynomatch')
        r = self.rank()
        self.assertEqual([x['score'] for x in r['ranking']], [0.0] * 12)
        self.assertEqual([x['ordinal'] for x in r['ranking']], list(range(12)))

    def test_descending_score_then_ordinal(self):
        scores = [4.0, 2.0, 4.0, 9.0, 2.0, 0.0, 1.0, 1.0, 4.0, 0.0, 9.0, 3.0]
        with patch.object(bm25, 'score_tokens', return_value=(scores, {})):
            r = self.rank()
        self.assertEqual([x['ordinal'] for x in r['ranking']], [3, 10, 0, 2, 8, 11, 1, 4, 6, 7, 5, 9])

    def test_ranks_one_based_complete(self):
        for alg in ('rule', 'bm25'):
            r = self.rank(alg)
            self.assertEqual([x['rank'] for x in r['ranking']], list(range(1, 13)))
            self.assertEqual({x['ordinal'] for x in r['ranking']}, set(range(12)))

    def test_catalog_rows_restored_exactly_by_ordinal(self):
        for alg in ('rule', 'bm25'):
            r = self.rank(alg)
            restored = [{k: v for k, v in row.items() if k not in ('rank', 'score')}
                        for row in sorted(r['ranking'], key=lambda row: row['ordinal'])]
            self.assertEqual(restored, self.request['catalog'])

    def test_mapping_unavailable_methods_remain(self):
        for alg in ('rule', 'bm25'):
            r = self.rank(alg)
            self.assertEqual(sum(x['location_status'] == 'UNAVAILABLE_MAPPING' for x in r['ranking']), 3)
            self.assertEqual(r['location_status_counts'], {'AVAILABLE': 9, 'UNAVAILABLE_MAPPING': 3})

    def test_bodyless_methods_remain(self):
        self.assertEqual(sum(not x['has_body'] for x in self.rank()['ranking']), 6)

    def test_overloads_nested_and_cross_file_names_preserved(self):
        result = self.rank()
        ids = [tuple(r[k] for k in ('repo_id', 'source_snapshot_id', 'file_path', 'method_id')) for r in result['ranking']]
        expected = {tuple(r[k] for k in ('repo_id', 'source_snapshot_id', 'file_path', 'method_id')) for r in self.request['catalog']}
        self.assertEqual(set(ids), expected)
        self.assertEqual(len(ids), len(set(ids)))

    def test_invalid_request_before_any_scoring(self):
        self.request['catalog'][0]['features']['strings'].append('tampered')
        with patch.object(bm25, 'score_tokens') as bm, patch.object(rule, 'extract_keywords') as kw:
            for alg in ('rule', 'bm25'):
                with self.assertRaises(protocol.MetadataRequestError):
                    self.rank(alg)
            bm.assert_not_called()
            kw.assert_not_called()

    def test_data08_public_validator_called_first(self):
        with patch.object(runner, 'validate_metadata_request', side_effect=protocol.MetadataRequestError('SYNTHETIC_REJECTION')) as val, \
             patch.object(rule, 'extract_keywords') as kw, patch.object(bm25, 'score_tokens') as bm:
            with self.assertRaises(protocol.MetadataRequestError):
                self.rank()
            val.assert_called_once()
            kw.assert_not_called()
            bm.assert_not_called()

    def test_cross_snapshot_rejected(self):
        self.request['catalog'][0]['source_snapshot_id'] = 'sha256:' + '0' * 64
        with self.assertRaisesRegex(protocol.MetadataRequestError, 'CATALOG_REPO_OR_SNAPSHOT_MISMATCH'):
            self.rank()

    def test_cross_repo_rejected(self):
        self.request['catalog'][0]['repo_id'] = 'fiction/other'
        with self.assertRaises(protocol.MetadataRequestError):
            self.rank()

    def test_missing_or_duplicate_identity_rejected(self):
        self.request['catalog'][1]['method_id'] = None
        with self.assertRaises(protocol.MetadataRequestError):
            self.rank()

    def test_unknown_fields_rejected(self):
        for key in ('gt', 'sidecar', 'patch', 'answers', 'ranking', 'score'):
            with self.subTest(key=key):
                req = copy.deepcopy(self.request)
                req[key] = []
                with self.assertRaises(protocol.MetadataRequestError):
                    runner.rank_metadata(req, self.bindings, algorithm='bm25')

    def test_unknown_algorithm(self):
        for alg in ('Rule', 'BM25', 'llm', '', None, True, [], 'rule '):
            with self.subTest(algorithm=alg):
                with self.assertRaisesRegex(runner.MetadataRankingError, 'UNKNOWN_ALGORITHM'):
                    self.rank(alg)

    def test_rule_nonfinite_and_boolean_scores_rejected(self):
        for value in (float('nan'), float('inf'), -float('inf'), True, '3', None, 10 ** 400):
            with self.subTest(value_type=type(value).__name__):
                with patch.object(rule, 'score_method', return_value=(value, {})):
                    with self.assertRaisesRegex(runner.MetadataRankingError, 'NONFINITE_OR_INVALID_SCORE'):
                        self.rank('rule')

    def test_bm25_nonfinite_scores_rejected(self):
        with patch.object(bm25, 'score_tokens', return_value=([float('nan')] * 12, {})):
            with self.assertRaisesRegex(runner.MetadataRankingError, 'NONFINITE_OR_INVALID_SCORE'):
                self.rank()

    def test_score_count_mismatch_rejected(self):
        with patch.object(bm25, 'score_tokens', return_value=([1.0] * 11, {})):
            with self.assertRaisesRegex(runner.MetadataRankingError, 'SCORE_CATALOG_COVERAGE_MISMATCH'):
                self.rank()

    def test_negative_bm25_rejected(self):
        with patch.object(bm25, 'score_tokens', return_value=([-1.0] * 12, {})):
            with self.assertRaisesRegex(runner.MetadataRankingError, 'NEGATIVE_BM25_SCORE'):
                self.rank()

    def test_original_rule_negative_penalty_preserved(self):
        case, rows, b = _FIXTURE.fixture()
        case['issue_text'] = 'unrelatedzzzz'
        rows[0]['features'].update(name='process', signature='process(int)')
        rows[0]['method_id'] = 'java-source-v1:fiction.Outer.Inner#process(int)'
        b.update(case_metadata_sha256=digest(case), catalog_sha256=digest(rows))
        request = protocol.build_metadata_request(case, rows, b)
        expected = direct_scores(request, 'rule')[0]
        self.assertEqual(expected, -5.0)
        result = runner.rank_metadata(request, b, algorithm='rule')
        self.assertEqual(next(x['score'] for x in result['ranking'] if x['ordinal'] == 0), expected)

    def test_empty_catalog_no_scorer_call(self):
        self.request, self.bindings = fixture(0)
        with patch.object(rule, 'extract_keywords') as kw, patch.object(bm25, 'score_tokens') as score:
            for alg in ('rule', 'bm25'):
                result = self.rank(alg)
                self.assertEqual((result['status'], result['ranking'], result['scored_count']), ('EMPTY', [], 0))
                self.assertFalse(result['assurance']['prediction_performed'])
            kw.assert_not_called()
            score.assert_not_called()

    def test_error_is_not_empty_or_fallback(self):
        with patch.object(rule, 'score_method', side_effect=RuntimeError('synthetic private detail')):
            with self.assertRaisesRegex(runner.MetadataRankingError, '^SCORER_EXECUTION_ERROR$'):
                self.rank('rule')

    def test_no_retry_or_algorithm_fallback(self):
        with patch.object(rule, 'extract_keywords', side_effect=RuntimeError('failure')) as kw, \
             patch.object(bm25, 'score_tokens') as other:
            with self.assertRaises(runner.MetadataRankingError):
                self.rank('rule')
            self.assertEqual(kw.call_count, 1)
            other.assert_not_called()

    def test_cold_rule_rejected_without_initializing(self):
        with patch.object(rule.jieba.dt, 'initialized', False), \
             patch.object(rule.jieba.dt, 'initialize') as init, patch.object(rule, 'extract_keywords') as kw:
            with self.assertRaisesRegex(runner.MetadataRankingError, 'RULE_TOKENIZER_NOT_INITIALIZED'):
                self.rank('rule')
            init.assert_not_called()
            kw.assert_not_called()

    def test_missing_rule_not_auto_imported(self):
        with patch.dict(sys.modules, {'retriever': None}):
            with self.assertRaisesRegex(runner.MetadataRankingError, 'RULE_DEPENDENCY_NOT_PRELOADED'):
                self.rank('rule')

    def test_bm25_does_not_require_rule(self):
        with patch.dict(sys.modules, {'retriever': None}):
            self.assertEqual(self.rank()['status'], 'SUCCESS')

    def test_validator_failure_and_evidence_states_preserved(self):
        for alg in ('rule', 'bm25'):
            r = self.rank(alg)
            self.assertEqual(r['validator_summary'], self.bindings['validator_summary'])
            self.assertEqual(r['assurance']['evidence_validation_status'], 'FAIL')
            self.assertEqual(r['assurance']['source_evidence_status'], 'UNVERIFIABLE')
            self.assertEqual(r['assurance']['feature_verification_status'], 'UNVERIFIED')
            self.assertEqual(r['assurance']['issue_relevance_status'], 'NOT_ASSESSED')
            self.assertFalse(r['assurance']['independent_ast_parse'])

    def test_no_experiment_authorization_created(self):
        r = self.rank()
        self.assertFalse(r['assurance']['execution_authorized'])
        self.assertTrue(r['assurance']['prediction_performed'])
        self.assertEqual(r['assurance']['binding_assurance'], 'CALLER_SUPPLIED_REFERENCE_ONLY')
        self.assertEqual(r['scorer_binding_status'], 'LOCAL_DEPENDENCY_NOT_ATTESTED')

    def test_original_request_assurance_not_changed(self):
        self.rank()
        self.assertFalse(self.request['assurance']['prediction_performed'])

    def test_inputs_immutable_results_detached(self):
        before = copy.deepcopy((self.request, self.bindings))
        result = self.rank('rule')
        self.assertEqual((self.request, self.bindings), before)
        result['ranking'][0]['features']['strings'].append('mutation')
        result['validator_summary']['summary']['verified'] = 1
        self.assertEqual((self.request, self.bindings), before)

    def test_mutating_scorer_cannot_change_original_input(self):
        original = rule.score_method
        def mutate(features, keywords, issue_text):
            features['strings'].append('synthetic mutation')
            return original(features, keywords, issue_text)
        before = copy.deepcopy(self.request)
        with patch.object(rule, 'score_method', side_effect=mutate):
            self.rank('rule')
        self.assertEqual(self.request, before)

    def test_deterministic_results(self):
        for alg in ('rule', 'bm25'):
            self.assertEqual(canonical(self.rank(alg)), canonical(self.rank(alg)))

    def test_json_roundtrip_and_result_hash(self):
        r = self.rank()
        self.assertEqual(json.loads(canonical(r)), r)
        self.assertEqual(r['result_sha256'], digest({k: v for k, v in r.items() if k != 'result_sha256'}))

    def test_no_invented_cost_usage_or_accuracy(self):
        r = self.rank()
        self.assertFalse(set(r) & {'usage', 'cost', 'accuracy', 'confidence', 'top1', 'mrr'})
        self.assertFalse(r['fallback_applied'])

    def test_no_data_io_environment_network_process_or_legacy_entry(self):
        def deny(*args, **kwargs):
            raise AssertionError('Forbidden external operation')
        with patch('builtins.open', deny), patch('pathlib.Path.open', deny), \
             patch('pathlib.Path.read_bytes', deny), patch('pathlib.Path.write_bytes', deny), \
             patch('socket.socket', deny), patch('socket.create_connection', deny), \
             patch('subprocess.Popen', deny), patch('os.getenv', deny), patch('os._Environ.__getitem__', deny), \
             patch.object(rule, 'main', deny), patch.object(rule, 'load_json', deny), \
             patch.object(bm25, 'predict', deny), \
             patch.dict(sys.modules, {'experiment_adapter': None, 'baseline_producers': None, 'controlled_authorization': None}):
            for alg in ('rule', 'bm25'):
                self.assertEqual(self.rank(alg)['status'], 'SUCCESS')


if __name__ == '__main__':
    unittest.main()
