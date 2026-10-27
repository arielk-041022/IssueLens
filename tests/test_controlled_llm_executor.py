"""Synthetic synchronous callbacks only; no real model, issue, key or transport."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import unittest
from unittest import mock

from src.controlled_llm_executor import execute_llm_request
from src.llm_rerank_protocol import build_llm_request, validate_llm_response
from src.high_recall_candidates import build_candidate_pool
from src.candidate_evidence_adapter import attach_candidate_evidence
from src.candidate_shortlist import build_shortlist

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("executor_synthetic_fixture_helpers", ROOT / "tests/test_candidate_pipeline_integration.py")
helpers = importlib.util.module_from_spec(spec); spec.loader.exec_module(helpers)


class ControlledLLMExecutorTest(unittest.TestCase):
    def setUp(self):
        ranking, catalog, report = helpers.synthetic_inputs()
        pool = build_candidate_pool(ranking, catalog, 20)
        shortlist = build_shortlist(pool, attach_candidate_evidence(pool, report), shortlist_size=3)
        self.request = build_llm_request("Synthetic callback test: fictional serialization behavior. 中文😀", shortlist)
        self.response = json.dumps(dict(ranking=[2, 1, 3], reason="Synthetic relative judgment; no proven root cause."))

    def assert_failure(self, result, status, calls):
        self.assertEqual(result["status"], status)
        self.assertEqual(result["client_call_count"], calls)
        self.assertIs(result["client_call_attempted"], calls == 1)
        self.assertFalse(result["reranked"])
        self.assertFalse(result["fallback_applied"])
        self.assertIsInstance(result["error_category"], str)
        if result["rerank_result"] is not None:
            self.assertFalse(result["rerank_result"]["reranked"])
            self.assertFalse(result["rerank_result"]["fallback_applied"])
            self.assertEqual(result["rerank_result"]["ranking"], [])
            self.assertEqual(result["rerank_result"]["candidates"], [])

    def test_successful_synthetic_callback_once(self):
        client = mock.Mock(return_value=self.response)
        result = execute_llm_request(self.request, client)
        client.assert_called_once()
        self.assertEqual(result["schema_version"], "controlled-llm-execution-v1")
        self.assertEqual(result["execution_mode"], "INJECTED_OFFLINE_CLIENT")
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["client_call_count"], 1)
        self.assertTrue(result["client_call_attempted"])
        self.assertTrue(result["reranked"])
        self.assertFalse(result["fallback_applied"])
        self.assertIsNone(result["error_category"])
        self.assertEqual(result["request_sha256"], self.request["request_sha256"])

    def test_result_exactly_reuses_strict_protocol_validation(self):
        result = execute_llm_request(self.request, lambda _: self.response)
        self.assertEqual(result["rerank_result"], validate_llm_response(self.request, self.response))

    def test_full_identity_bm25_and_model_reason_preserved(self):
        result = execute_llm_request(self.request, lambda _: self.response)["rerank_result"]
        for cid, candidate in zip([2, 1, 3], result["candidates"]):
            original = self.request["shortlist"]["candidates"][cid - 1]
            self.assertEqual({k: candidate[k] for k in original}, original)
        self.assertEqual(result["model_reason"]["status"], "MODEL_GENERATED_UNVERIFIED")
        self.assertEqual(result["issue_relevance_status"], "NOT_ASSESSED")

    def test_unattested_references_are_not_upgraded(self):
        result = execute_llm_request(self.request, lambda _: self.response)["rerank_result"]
        for c in result["candidates"]:
            self.assertEqual(c["evidence_status"], "UNVERIFIED")
            self.assertEqual(c["evidence_association"]["status"], "NOT_ATTESTED")
            self.assertEqual(c["evidence_association"]["source_provenance_status"], "UNVERIFIABLE")
            self.assertEqual(c["evidence_association"]["issue_relevance_status"], "NOT_ASSESSED")

    def test_invalid_request_types_zero_calls(self):
        for request in (None, {}, [], "request.json", True):
            client = mock.Mock(return_value=self.response)
            with self.subTest(kind=type(request).__name__):
                self.assert_failure(execute_llm_request(request, client), "INVALID_REQUEST", 0)
                client.assert_not_called()

    def test_tampered_request_hash_zero_calls(self):
        self.request["request_sha256"] = "0" * 64
        client = mock.Mock(return_value=self.response)
        self.assert_failure(execute_llm_request(self.request, client), "INVALID_REQUEST", 0)
        client.assert_not_called()

    def test_tampered_messages_or_mapping_zero_calls(self):
        for field in ("messages", "candidate_map"):
            request = copy.deepcopy(self.request)
            if field == "messages": request[field][0]["content"] = "changed instructions"
            else: request[field][0]["candidate_id"] = 99
            client = mock.Mock(return_value=self.response)
            with self.subTest(field=field):
                self.assert_failure(execute_llm_request(request, client), "INVALID_REQUEST", 0)
                client.assert_not_called()

    def test_invalid_shortlist_zero_calls(self):
        self.request["shortlist"]["candidates"][0]["evidence_status"] = "VERIFIED"
        client = mock.Mock(return_value=self.response)
        self.assert_failure(execute_llm_request(self.request, client), "INVALID_REQUEST", 0)
        client.assert_not_called()

    def test_unknown_request_fields_and_gt_annotations_zero_calls(self):
        for key in ("authorization", "accepted_targets", "api_key"):
            request = copy.deepcopy(self.request); request[key] = "SYNTHETIC_SENTINEL_NOT_A_REAL_KEY"
            client = mock.Mock(return_value=self.response)
            with self.subTest(key=key):
                result = execute_llm_request(request, client)
                self.assert_failure(result, "INVALID_REQUEST", 0)
                self.assertNotIn("SYNTHETIC_SENTINEL", json.dumps(result))
                client.assert_not_called()

    def test_noncallable_client_is_explicit_zero_call_client_error(self):
        for client in (None, True, "client", {}, []):
            with self.subTest(kind=type(client).__name__):
                result = execute_llm_request(self.request, client)
                self.assert_failure(result, "CLIENT_ERROR", 0)
                self.assertEqual(result["error_category"], "CLIENT_NOT_CALLABLE")

    def test_invalid_request_precedes_client_check(self):
        self.assert_failure(execute_llm_request({}, None), "INVALID_REQUEST", 0)

    def test_simulated_timeout_one_attempt_no_retry(self):
        client = mock.Mock(side_effect=TimeoutError("SYNTHETIC_SECRET_TIMEOUT"))
        result = execute_llm_request(self.request, client)
        self.assert_failure(result, "TIMEOUT", 1)
        self.assertEqual(result["error_category"], "CLIENT_TIMEOUT")
        self.assertNotIn("SYNTHETIC_SECRET_TIMEOUT", json.dumps(result))
        client.assert_called_once()

    def test_simulated_ordinary_exceptions_one_attempt_no_retry(self):
        for exception in (RuntimeError("SYNTHETIC_SECRET"), ValueError("SYNTHETIC_SECRET"), OSError("SYNTHETIC_SECRET")):
            client = mock.Mock(side_effect=exception)
            with self.subTest(exception=type(exception).__name__):
                result = execute_llm_request(self.request, client)
                self.assert_failure(result, "CLIENT_ERROR", 1)
                self.assertEqual(result["error_category"], "CLIENT_EXCEPTION")
                self.assertNotIn("SYNTHETIC_SECRET", json.dumps(result))
                client.assert_called_once()

    def test_error_serialization_never_calls_exception_str(self):
        class SensitiveException(Exception):
            def __str__(self):
                raise AssertionError("Exception formatting must not occur")
        client = mock.Mock(side_effect=SensitiveException())
        result = execute_llm_request(self.request, client)
        self.assert_failure(result, "CLIENT_ERROR", 1)
        self.assertNotIn("SensitiveException", json.dumps(result))

    def test_exception_custom_class_name_message_and_object_are_not_recorded(self):
        secret_class = type("SYNTHETIC_SECRET_CLASS", (Exception,), {})
        result = execute_llm_request(self.request, mock.Mock(side_effect=secret_class("SYNTHETIC_SECRET_MESSAGE")))
        text = json.dumps(result, allow_nan=False)
        self.assertNotIn("SYNTHETIC_SECRET", text)
        self.assertNotIn("traceback", text)
        self.assertIsNone(result["rerank_result"])

    def test_invalid_json_response_no_retry_no_raw_error_echo(self):
        client = mock.Mock(return_value="SYNTHETIC_SECRET_RESPONSE: invalid JSON")
        result = execute_llm_request(self.request, client)
        self.assert_failure(result, "INVALID_RESPONSE", 1)
        self.assertNotIn("SYNTHETIC_SECRET_RESPONSE", json.dumps(result))
        client.assert_called_once()

    def test_duplicate_and_outside_rankings_rejected(self):
        for ranking in ([1, 1, 3], [1, 2, 4], [1, 2], [True, 2, 3], [1, 2, 3, 4]):
            client = mock.Mock(return_value=json.dumps(dict(ranking=ranking, reason="fixture reason")))
            with self.subTest(ranking=ranking):
                self.assert_failure(execute_llm_request(self.request, client), "INVALID_RESPONSE", 1)
                client.assert_called_once()

    def test_model_cannot_supply_new_candidate_or_verified_claim(self):
        for field in ("method_id", "evidence_id", "source_provenance_status", "root_cause_confirmed"):
            data = dict(ranking=[1, 2, 3], reason="fixture reason"); data[field] = "SYNTHETIC_FORGED_CLAIM"
            with self.subTest(field=field):
                self.assert_failure(execute_llm_request(self.request, lambda _: json.dumps(data)), "INVALID_RESPONSE", 1)

    def test_reason_limit_is_not_relaxed(self):
        data = json.dumps(dict(ranking=[1, 2, 3], reason="x" * 2001))
        self.assert_failure(execute_llm_request(self.request, lambda _: data), "INVALID_RESPONSE", 1)

    def test_duplicate_keys_and_nonfinite_json_not_relaxed(self):
        for raw in ('{"ranking":[1,2,3],"reason":"a","reason":"b"}',
                    '{"ranking":[1,2,NaN],"reason":"a"}'):
            with self.subTest(raw=raw):
                self.assert_failure(execute_llm_request(self.request, lambda _: raw), "INVALID_RESPONSE", 1)

    def test_wrong_response_return_types_rejected(self):
        for raw in (None, True, b"{}", {"ranking": [1, 2, 3], "reason": "fixture"}, []):
            client = mock.Mock(return_value=raw)
            with self.subTest(kind=type(raw).__name__):
                self.assert_failure(execute_llm_request(self.request, client), "INVALID_RESPONSE", 1)
                client.assert_called_once()

    def test_callable_instance_is_supported(self):
        response = self.response
        class Simulator:
            def __init__(self): self.calls = 0
            def __call__(self, request):
                self.calls += 1
                return response
        client = Simulator()
        self.assertEqual(execute_llm_request(self.request, client)["status"], "SUCCESS")
        self.assertEqual(client.calls, 1)

    def test_client_receives_detached_original_protocol_request(self):
        client = mock.Mock(return_value=self.response)
        execute_llm_request(self.request, client)
        passed = client.call_args.args[0]
        self.assertEqual(passed, self.request)
        self.assertIsNot(passed, self.request)
        self.assertIsNot(passed["candidate_map"], self.request["candidate_map"])

    def test_client_mutation_cannot_change_original_identity_mapping(self):
        before = copy.deepcopy(self.request)
        def mutate_client(payload):
            payload["candidate_map"][0]["candidate"]["method_id"] = "java-source-v1:fiction.Forged#method()"
            payload["candidate_map"].reverse()
            payload["shortlist"]["candidates"][0]["features"]["strings"].append("mutated")
            return self.response
        result = execute_llm_request(self.request, mutate_client)
        self.assertEqual(self.request, before)
        self.assertEqual(result["status"], "SUCCESS")
        original = self.request["candidate_map"][0]["candidate"]
        self.assertEqual(result["rerank_result"]["candidates"][1]["method_id"], original["method_id"])

    def test_client_argument_mutation_does_not_expand_response_allowlist(self):
        def mutate_client(payload):
            payload["candidate_map"].append(copy.deepcopy(payload["candidate_map"][0]))
            return json.dumps(dict(ranking=[1, 2, 3, 4], reason="fixture reason"))
        self.assert_failure(execute_llm_request(self.request, mutate_client), "INVALID_RESPONSE", 1)

    def test_success_and_failures_leave_inputs_unchanged(self):
        before = copy.deepcopy(self.request)
        for client in (mock.Mock(return_value=self.response), mock.Mock(return_value="bad"),
                       mock.Mock(side_effect=TimeoutError()), mock.Mock(side_effect=RuntimeError())):
            execute_llm_request(self.request, client)
            self.assertEqual(self.request, before)

    def test_result_does_not_alias_request(self):
        before = copy.deepcopy(self.request)
        result = execute_llm_request(self.request, lambda _: self.response)
        result["rerank_result"]["candidates"][0]["features"]["calls"].append("mutate")
        self.assertEqual(self.request, before)

    def test_determinism_without_timestamps_randomness_or_usage(self):
        one = execute_llm_request(self.request, lambda _: self.response)
        two = execute_llm_request(self.request, lambda _: self.response)
        self.assertEqual(json.dumps(one, sort_keys=True), json.dumps(two, sort_keys=True))
        self.assertEqual(execute_llm_request(self.request, mock.Mock(side_effect=RuntimeError("one"))),
                         execute_llm_request(self.request, mock.Mock(side_effect=RuntimeError("two"))))

    def test_no_cost_or_token_usage_is_fabricated(self):
        for client in (mock.Mock(return_value=self.response), mock.Mock(side_effect=TimeoutError()), mock.Mock(return_value="bad")):
            result = execute_llm_request(self.request, client)
            def inspect(value):
                if isinstance(value, dict):
                    self.assertFalse(set(value) & {"usage", "tokens", "input_tokens", "output_tokens", "cost", "fees", "elapsed", "timestamp"})
                    for v in value.values(): inspect(v)
                elif isinstance(value, list):
                    for v in value: inspect(v)
            inspect(result)

    def test_client_supplied_usage_not_accepted_as_protocol_data(self):
        raw = json.dumps(dict(ranking=[1, 2, 3], reason="fixture", usage={"tokens": 12}, cost=0))
        self.assert_failure(execute_llm_request(self.request, lambda _: raw), "INVALID_RESPONSE", 1)

    def test_no_network_files_environment_or_process_access(self):
        with mock.patch("builtins.open", side_effect=AssertionError("file")), \
             mock.patch.object(io, "open", side_effect=AssertionError("io")), \
             mock.patch.object(Path, "open", side_effect=AssertionError("path")), \
             mock.patch.object(Path, "read_bytes", side_effect=AssertionError("path")), \
             mock.patch.object(Path, "read_text", side_effect=AssertionError("path")), \
             mock.patch.object(socket, "socket", side_effect=AssertionError("network")), \
             mock.patch.object(socket, "create_connection", side_effect=AssertionError("network")), \
             mock.patch.object(subprocess, "run", side_effect=AssertionError("process")), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("process")), \
             mock.patch.object(os, "getenv", side_effect=AssertionError("environment")), \
             mock.patch.object(type(os.environ), "__getitem__", side_effect=AssertionError("environment")):
            client = mock.Mock(return_value=self.response)
            self.assertEqual(execute_llm_request(self.request, client)["status"], "SUCCESS")
            client.assert_called_once()

    def test_authorization_boolean_is_not_an_api(self):
        client = mock.Mock(return_value=self.response)
        with self.assertRaises(TypeError):
            execute_llm_request(self.request, client, authorization=True)
        client.assert_not_called()

    def test_hanging_client_is_not_falsely_reported_as_enforced_timeout(self):
        # No real hang/thread: a returning synchronous simulator has no invented
        # elapsed time or watchdog. TIMEOUT is reserved for raised TimeoutError.
        result = execute_llm_request(self.request, lambda _: self.response)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertNotIn("timeout_seconds", result)

    def test_keyboard_interrupt_not_silently_converted_to_success(self):
        client = mock.Mock(side_effect=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            execute_llm_request(self.request, client)
        client.assert_called_once()


if __name__ == "__main__": unittest.main()
