"""Only synthetic HTTP. No credentials or GT inputs."""
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from test_brd_rerank_strategy import fixture, ROOT

spec = importlib.util.spec_from_file_location('brd_live_test', ROOT / 'experiments/brd_v1/live_adapter.py')
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.source = self.path / 'c.json'
        self.source.write_bytes(live.canonical_json(fixture()))
        self.profile = json.loads((ROOT / 'experiments/live_bridge01/verified-api-profile.json').read_bytes())
        self.profile['verified_at_utc'] = datetime.now(timezone.utc).isoformat()
        self.profile_path = self.path / 'profile.json'
        self.profile_path.write_bytes(live.canonical_json(self.profile))
        self.out = self.path / 'pair'
        self.block = patch.object(socket.socket, 'connect', side_effect=AssertionError('NETWORK_FORBIDDEN'))
        self.block.start()
        self.addCleanup(self.block.stop)

    def prepare(self):
        return live.prepare(self.source, live.sha(self.source.read_bytes()), self.profile_path, self.out, 'SYNTHETIC')

    def run_arm(self, transport, arm='BRD', auth=True):
        token = live.sha((self.out / arm / 'authorization.json').read_bytes()) if auth else None
        return live.execute(self.out, arm, token, '0.65', transport=transport)

    def response(self, extra=False):
        content = {'ranking': list(range(1, 21)), 'reason': 'Synthetic comparison.'}
        if extra:
            content['extra'] = 'rejected'
        return 200, live.canonical_json({'object': 'chat.completion', 'model': 'deepseek-flash',
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120,
                      'prompt_cache_hit_tokens': 0, 'prompt_cache_miss_tokens': 100},
            'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(content)}}]})

    def test_dry_run_pair_and_invariance(self):
        result = self.prepare()
        self.assertEqual(result['real_API_calls'], 0)
        for arm in ('C', 'BRD'):
            live.verify_seal(self.out / arm)
        c = json.loads((self.out / 'C/http-payload.json').read_bytes())
        b = json.loads((self.out / 'BRD/http-payload.json').read_bytes())
        self.assertEqual(c['messages'][1], b['messages'][1])
        with self.assertRaises(ValueError): self.prepare()

    def test_success_pair_independent_and_duplicate_blocked(self):
        self.prepare()
        calls = []
        def transport(wire):
            calls.append(wire)
            return self.response()
        for arm in ('BRD', 'C'):
            result = self.run_arm(transport, arm)
            self.assertEqual(result['status'], 'SUCCESS')
            self.assertEqual(result['mode'], 'MOCK_HTTP')
            live.verify_seal(self.out / arm / 'attempt')
            with self.assertRaises(ValueError): self.run_arm(transport, arm)
        self.assertEqual(len(calls), 2)

    def test_no_authorization(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, 'AUTHORIZATION'):
            self.run_arm(lambda _: self.fail('transport'), auth=False)
        self.assertFalse((self.out / 'BRD/single-call.lock.json').exists())

    def test_failure_branches_never_retry(self):
        for error, expected in [(TimeoutError(), 'TIMEOUT_EXECUTION_UNCERTAIN'),
                                (ConnectionError(), 'CONNECTION_ERROR_EXECUTION_UNCERTAIN')]:
            with self.subTest(expected=expected):
                self.out = self.path / expected
                self.prepare()
                calls = []
                def fail(wire):
                    calls.append(1)
                    raise error
                self.assertEqual(self.run_arm(fail)['status'], expected)
                with self.assertRaises(ValueError): self.run_arm(fail)
                self.assertEqual(len(calls), 1)

    def test_http_error_and_protocol_failure(self):
        self.prepare()
        self.assertEqual(self.run_arm(lambda _: (503, b'error'))['status'], 'HTTP_ERROR')
        result = self.run_arm(lambda _: self.response(extra=True), 'C')
        self.assertEqual(result['status'], 'PROTOCOL_FAILED')
        self.assertFalse((self.out / 'C/attempt/reranking.json').exists())
        self.assertTrue((self.out / 'C/attempt/http-response.body.raw').exists())

    def test_budget_and_stale_price_rejected(self):
        for change in ({'single_call_cap_usd': '0.01'}, {'verified_at_utc': '2020-01-01T00:00:00+00:00'}):
            p = {**self.profile, **change}
            self.profile_path.write_bytes(live.canonical_json(p))
            with self.assertRaises(live.bridge.Rejected): self.prepare()
            self.assertFalse(self.out.exists())

    def test_existing_uncertain_lock_blocks(self):
        self.prepare()
        (self.out / 'BRD/single-call.lock.json').write_text('{}')
        with self.assertRaises(FileExistsError):
            self.run_arm(lambda _: self.fail('transport'))

    def test_parameters_and_tamper_rejected(self):
        payload = fixture()
        payload['temperature'] = 1
        self.source.write_bytes(live.canonical_json(payload))
        with self.assertRaisesRegex(ValueError, 'PARAMETERS_CHANGED'): self.prepare()
        self.source.write_bytes(live.canonical_json(fixture()))
        self.prepare()
        (self.out / 'BRD/http-payload.json').write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'SEALED_FILE_CHANGED'):
            self.run_arm(lambda _: self.fail('transport'))


if __name__ == '__main__':
    unittest.main()
