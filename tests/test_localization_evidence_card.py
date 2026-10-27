"""Synthetic display checks and pinned LANG-1610 acceptance. No GT/network use."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from localization_evidence_card import render_card, read_bundle
from evidence_trace_manifest import trace, sha
from test_evidence_trace_manifest import fixture


def documents():
    ranking, index, metadata, report, snapshot, hashes = fixture()
    hashes = {**hashes, 'ranking': sha(json.dumps(ranking).encode()),
              'validator-report': sha(json.dumps(report).encode())}
    docs = dict(ranking=ranking, index=index, metadata=metadata, report=report,
                snapshot=snapshot, context={'issue_text': 'Synthetic issue'})
    docs['trace'] = trace(ranking, index, metadata, report, snapshot, hashes)
    return docs, hashes


def refresh(docs, hashes):
    docs['trace'] = trace(docs['ranking'], docs['index'], docs['metadata'],
                          docs['report'], docs['snapshot'], hashes)


class CardTests(unittest.TestCase):
    def test_exact_link_and_local_pass_repository_fail(self):
        d, h = documents(); result = render_card('SYNTHETIC', 'historical-bm25', d, h)
        self.assertIn('BOUND', result)
        self.assertIn('源码事实状态：` SOURCE_FACT_MATCH_RECORDED `', result)
        self.assertIn('整仓Validator结论：` FAIL `', result)
        self.assertIn('example.Sample#run()', result)
        self.assertIn('helper()', result)
        self.assertIn('非最终LLM结果', result)

    def test_unknown_is_not_inferred(self):
        d, h = documents(); text = render_card('SYNTHETIC', 'historical-bm25', d, h)
        self.assertIn('保存的测试命令：未提供', text)
        self.assertIn('保存的命令执行状态：未提供', text)
        self.assertIn('模型理由（原排序级推断，未核验，不逐候选重新分配）：未提供', text)

    def test_method_mismatch_stays_unbound(self):
        d, h = documents(); d['ranking'][0]['method_id'] = 'java-source-v2:example.Other#run()'
        refresh(d, h)
        text = render_card('SYNTHETIC', 'historical-bm25', d, h)
        self.assertIn('UNBOUND', text); self.assertIn('UNVERIFIABLE', text)

    def test_missing_evidence_never_invented(self):
        d, h = documents(); d['report']['evidence_results'] = []; refresh(d, h)
        text = render_card('SYNTHETIC', 'historical-bm25', d, h)
        self.assertNotIn('helper()', text)
        self.assertIn('MISSING_OR_DUPLICATE_VALIDATOR_EVIDENCE_RECORD', text)
        self.assertIn('原始片段：未提供', text)

    def test_contradiction_preserves_code(self):
        d, h = documents()
        d['report']['evidence_results'][0].update(status='STALE_SOURCE', reason_code='SOURCE_HASH_MISMATCH')
        refresh(d, h)
        text = render_card('SYNTHETIC', 'historical-bm25', d, h)
        self.assertIn('SOURCE_HASH_MISMATCH', text)
        self.assertIn('SOURCE_FACT_INCONSISTENCY_RECORDED', text)
        self.assertIn('不等于Issue相关性、AST语义或Bug根因认证', text)

    def test_saved_llm_reason_is_separate_data(self):
        d, h = documents()
        d['ranking'] = {'ranking': d['ranking'], 'status': 'VALID', 'reason': '<script>example</script>'}
        refresh(d, h)
        text = render_card('SYNTHETIC', 'saved-llm', d, h)
        self.assertIn('` <script>example</script> `', text)
        self.assertIn('模型理由（原排序级推断，未核验', text)

    def test_valid_model_does_not_verify_evidence(self):
        d, h = documents(); d['ranking'] = {'ranking': d['ranking'], 'status': 'VALID', 'reason': 'example'}
        d['report']['evidence_results'] = []; refresh(d, h)
        text = render_card('SYNTHETIC', 'saved-llm', d, h)
        self.assertIn('UNVERIFIABLE', text)
        self.assertNotIn('源码事实状态：` SOURCE_FACT_MATCH_RECORDED `', text)

    def test_saved_trace_tampering_fails_closed(self):
        d, h = documents(); d['trace']['validator_original_overall_verdict'] = 'PASS'
        with self.assertRaisesRegex(ValueError, 'SAVED_TRACE_DISAGREEMENT'):
            render_card('SYNTHETIC', 'historical-bm25', d, h)

    def test_range_disagreement_fails_closed(self):
        d, h = documents(); d['ranking'][0]['source_line_range'] = [99, 100]
        with self.assertRaisesRegex(ValueError, 'SAVED_CANDIDATE_RANGE_MISMATCH'):
            render_card('SYNTHETIC', 'historical-bm25', d, h)

    def test_snapshot_disagreement_not_guessed(self):
        d, h = documents(); d['snapshot']['source_snapshot_id'] = 'wrong'; refresh(d, h)
        text = render_card('SYNTHETIC', 'historical-bm25', d, h)
        self.assertIn('UNBOUND', text)
        self.assertIn('SOURCE_SNAPSHOT_BINDING_NOT_PROVEN', text)

    def test_private_path_rejected_before_read(self):
        with self.assertRaisesRegex(ValueError, 'PRIVATE_INPUT_FORBIDDEN'):
            read_bundle(Path('private-gt/not-opened.json'), '0' * 64)

    def test_saved_command_not_execution(self):
        d, h = documents(); d['context']['test_command'] = 'mvn test'
        text = render_card('SYNTHETIC', 'historical-bm25', d, h)
        self.assertIn('mvn test', text)
        self.assertIn('保存的命令执行状态：未提供', text)
        self.assertIn('打印命令不构成已执行证明', text)

    def test_deterministic_immutable_offline(self):
        d, h = documents(); before = deepcopy((d, h))
        with patch.object(socket, 'create_connection', side_effect=AssertionError('NETWORK_FORBIDDEN')):
            a = render_card('SYNTHETIC', 'historical-bm25', d, h)
            self.assertEqual(a, render_card('SYNTHETIC', 'historical-bm25', d, h))
        self.assertEqual((d, h), before)

    def test_real_saved_lang1610(self):
        folder = ROOT / 'experiments/localization-evidence-card-v1'
        bundle = folder / 'inputs.json'
        binding, d, h = read_bundle(bundle, sha(bundle.read_bytes()))
        with patch.object(socket, 'create_connection', side_effect=AssertionError('NETWORK_FORBIDDEN')):
            text = render_card(binding['issue_id'], binding['ranking_kind'], d, h)
        self.assertEqual(text.count('## 候选 '), 5)
        self.assertEqual(text.count('- evidence_id：'), 13)
        for row in d['ranking']:
            self.assertIn(row['method_id'], text)
            self.assertIn(row['file_path'], text)
            self.assertIn(json.dumps(row['source_line_range']), text)
        self.assertNotIn('gt_check', text)

    def test_cli_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'card.md'; output.write_text('unchanged', encoding='utf-8')
            p = subprocess.run([sys.executable, '-B', str(ROOT / 'src/localization_evidence_card.py'),
                                '--bundle', 'does-not-exist.json', '--bundle-sha256', '0' * 64,
                                '--output', str(output)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 2)
            self.assertIn('OUTPUT_EXISTS_NO_OVERWRITE', p.stderr)
            self.assertEqual(output.read_text(), 'unchanged')

    def test_bundle_hash_before_reading_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'bundle.json'; p.write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'BUNDLE_SHA256_MISMATCH'):
                read_bundle(p, '0' * 64)


if __name__ == '__main__':
    unittest.main()
