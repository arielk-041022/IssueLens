"""Synthetic offline BRD tests; no GT, historical responses or live transport."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import brd_rerank_strategy as brd

spec = importlib.util.spec_from_file_location("brd_offline_adapter", ROOT / "experiments/brd_v1/prepare.py")
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
spec = importlib.util.spec_from_file_location("brd_existing_c_runner", adapter.CHECKER_PATH)
c_runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c_runner)


def fixture():
    ranking = []
    sources = []
    for i in range(1, 21):
        path = "src/main/java/fixture/Example.java"
        signature = f"method{i}(String)"
        ranking.append({"repo_id": "synthetic/example", "source_snapshot_id": "sha256:" + "0" * 64,
            "file_path": path, "method_id": "java-source-v1:fixture.Example#" + signature,
            "rank": i, "score": 100 - i / 10, "ordinal": i + 20, "has_body": True,
            "features": {"name": f"method{i}", "class": "Example", "signature": signature, "file": path,
                         "annotations": [], "strings": ["a", "😀"], "calls": ["trim"]}})
        sources.append({"method_id": ranking[-1]["method_id"], "bm25_rank": i,
            "source_status": "FULL_METHOD_LINES", "source_excerpt":
            f'    String method{i}(String value) {{\r\n\t// 😀 comment\r\n        return value.trim();\r\n    }}\r\n'})
    return c_runner.build_payload("Synthetic behavior issue\n原始标题", ranking, sources, "C", {"model": "deepseek-flash"})


class BRDStrategyTests(unittest.TestCase):
    def setUp(self):
        self.baseline = fixture()
        self.request = brd.build_brd_request(self.baseline)

    def check(self, content):
        return brd.validate_brd_response(self.request, content, protocol_validator=c_runner.check_response)

    def response(self, ranking=None, reason="Synthetic relative judgment.", **extra):
        return json.dumps({"ranking": list(range(1, 21)) if ranking is None else ranking,
                           "reason": reason, **extra}, ensure_ascii=False)

    def mutate_user(self, callback):
        payload = deepcopy(self.baseline)
        user = json.loads(payload["messages"][1]["content"])
        callback(user)
        payload["messages"][1]["content"] = json.dumps(user, ensure_ascii=False)
        return payload

    def test_legal_request_and_fixed_version(self):
        self.assertEqual(self.request["strategy_version"], "brd-v1")
        self.assertEqual(self.request["http_payload"]["messages"][0]["content"], brd.BRD_SYSTEM_INSTRUCTIONS)
        self.assertEqual(self.request["http_payload"]["response_format"], {"type": "json_object"})
        self.assertEqual([m["role"] for m in self.request["http_payload"]["messages"]], ["system", "user"])

    def test_only_system_message_changes(self):
        restored = deepcopy(self.request["http_payload"])
        restored["messages"][0] = deepcopy(self.baseline["messages"][0])
        self.assertEqual(restored, self.baseline)

    def test_candidates_source_signatures_scores_and_user_bytes_unchanged(self):
        before = self.baseline["messages"][1]["content"]
        self.assertEqual(before.encode(), self.request["http_payload"]["messages"][1]["content"].encode())
        self.assertEqual(json.loads(before), json.loads(self.request["http_payload"]["messages"][1]["content"]))

    def test_unavailable_source_and_availability_states_preserved(self):
        def unavailable(user):
            c = user["candidates"][0]["candidate"]
            c["location_status"] = "UNAVAILABLE_MAPPING"
            c["identity_status"] = "AVAILABLE"
            c["source_implementation"].update(extraction_status="UNAVAILABLE", method_declaration_and_body=None)
        payload = self.mutate_user(unavailable)
        result = brd.build_brd_request(payload)
        self.assertEqual(result["http_payload"]["messages"][1], payload["messages"][1])

    def test_deterministic_payload_and_request_hashes(self):
        repeat = brd.build_brd_request(deepcopy(self.baseline))
        self.assertEqual(self.request, repeat)
        self.assertEqual(self.request["http_payload_sha256"], hashlib.sha256(brd.canonical_json(repeat["http_payload"])).hexdigest())
        unhashed = deepcopy(repeat); del unhashed["request_sha256"]
        self.assertEqual(repeat["request_sha256"], hashlib.sha256(brd.canonical_json(unhashed)).hexdigest())

    def test_input_is_not_mutated_and_returned_data_is_detached(self):
        before = deepcopy(self.baseline)
        brd.build_brd_request(self.baseline)
        self.assertEqual(self.baseline, before)
        self.request["baseline_http_payload"]["messages"][1]["content"] = "changed"
        self.assertEqual(self.baseline, before)

    def test_full_twenty_permutation_reuses_existing_checker(self):
        content = self.response(list(range(20, 0, -1)))
        self.assertEqual(self.check(content), c_runner.check_response(content))
        self.assertEqual(self.check(content)["status"], "SUCCESS")

    def test_duplicate_missing_outside_boolean_and_noninteger_rejected(self):
        bad = [[1] * 20, list(range(1, 20)), list(range(2, 22)), [True] + list(range(2, 21)),
               [1.0] + list(range(2, 21)), ["1"] + list(range(2, 21))]
        for order in bad:
            with self.subTest(order=order):
                content = self.response(order)
                self.assertEqual(self.check(content), c_runner.check_response(content))
                self.assertEqual(self.check(content)["status"], "INVALID_RESPONSE")

    def test_extra_fields_and_invalid_reasons_rejected(self):
        bad = [self.response(extra="not allowed"), self.response(reason="x" * 2001),
               self.response(reason=" "), self.response(reason=None), '{"ranking":[1]}']
        for content in bad:
            with self.subTest(content=content[:80]):
                self.assertEqual(self.check(content)["status"], "INVALID_RESPONSE")

    def test_reason_unicode_length_boundary(self):
        self.assertEqual(self.check(self.response(reason="字" * 2000))["status"], "SUCCESS")
        self.assertEqual(self.check(self.response(reason="字" * 2001))["status"], "INVALID_RESPONSE")

    def test_strict_json_markdown_nonfinite_and_duplicate_keys_rejected(self):
        for text in ('```json\n' + self.response() + '\n```', '{"ranking":[],"ranking":[],"reason":"x"}',
                     '{"ranking":[NaN],"reason":"x"}', self.response(reason="\ud800"), "{", "x" * 16001):
            with self.subTest(text=text[:60]):
                self.assertEqual(self.check(text)["status"], "INVALID_RESPONSE")

    def test_request_prompt_hash_and_source_tampering_rejected(self):
        for field in ("http_payload_sha256", "strategy_version", "request_sha256"):
            modified = deepcopy(self.request); modified[field] = "tampered"
            self.assertEqual(brd.validate_brd_response(modified, self.response(), protocol_validator=c_runner.check_response)["status"], "INVALID_REQUEST")
        modified = deepcopy(self.request)
        modified["http_payload"]["messages"][1]["content"] += " "
        self.assertEqual(brd.validate_brd_response(modified, self.response(), protocol_validator=c_runner.check_response)["status"], "INVALID_REQUEST")

    def test_bad_candidate_inputs_fail_closed(self):
        mutations = [lambda u: u["candidates"].pop(),
                     lambda u: u["candidates"][0].update(candidate_id=2),
                     lambda u: u["candidates"][0]["candidate"].update(bm25_score=True),
                     lambda u: u["candidates"][0]["candidate"]["features"].update(signature="wrong()"),
                     lambda u: u["candidates"][0]["candidate"]["source_implementation"].update(source_path="other.java"),
                     lambda u: u["candidates"][0]["candidate"].update(ground_truth=True)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises(brd.BRDRequestError):
                    brd.build_brd_request(self.mutate_user(mutation))

    def test_permissive_callback_cannot_disable_permutation_checks(self):
        callback = lambda *a, **k: self.fail("Invalid permutation must not reach callback")
        result = brd.validate_brd_response(self.request, self.response([1] * 20), protocol_validator=callback)
        self.assertEqual(result["status"], "INVALID_RESPONSE")

    def test_unknown_payload_fields_are_not_propagated(self):
        payload = deepcopy(self.baseline)
        payload["ground_truth"] = "synthetic forbidden field"
        with self.assertRaisesRegex(brd.BRDRequestError, "UNKNOWN_OR_MISSING_PAYLOAD_FIELDS"):
            brd.build_brd_request(payload)

    def test_offline_adapter_and_hash_rejection_without_network(self):
        with tempfile.TemporaryDirectory(prefix="issuelens-brd-test-") as tmp:
            p = Path(tmp); source = p / "sealed-c.json"
            source.write_bytes(brd.canonical_json(self.baseline))
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            with patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")), \
                 patch.object(socket, "getaddrinfo", side_effect=AssertionError("Network forbidden")):
                with self.assertRaisesRegex(ValueError, "C_REQUEST_HASH_MISMATCH"):
                    adapter.prepare(source, "0" * 64, p / "wrong")
                self.assertFalse((p / "wrong").exists())
                summary = adapter.prepare(source, digest, p / "output")
                self.assertEqual(summary["real_API_calls"], 0)
                for name, expected in json.loads((p / "output/artifacts.sha256.json").read_bytes()).items():
                    self.assertEqual(hashlib.sha256((p / "output" / name).read_bytes()).hexdigest(), expected)
                with self.assertRaisesRegex(ValueError, "OUTPUT_ALREADY_EXISTS"):
                    adapter.prepare(source, digest, p / "output")
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
