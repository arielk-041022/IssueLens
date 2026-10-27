"""B04B-2A: synthetic usage/envelopes, mock transport, isolated temporary SQLite.

No real keys, HTTP, prices queried by tests, live approval, GT or EXP001 data.
Ledger seed rows below are synthetic history fixtures, not executed API calls.
"""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

from src import deepseek_budget_client as d
from src.controlled_llm_executor import execute_llm_request
from src.llm_rerank_protocol import build_llm_request, validate_llm_response
from src.high_recall_candidates import build_candidate_pool
from src.candidate_evidence_adapter import attach_candidate_evidence
from src.candidate_shortlist import build_shortlist

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("budget_synthetic_helpers", ROOT / "tests/test_candidate_pipeline_integration.py")
helpers = importlib.util.module_from_spec(spec); spec.loader.exec_module(helpers)


def usage(prompt=100, hit=20, completion=30):
    return dict(prompt_tokens=prompt, prompt_cache_hit_tokens=hit, prompt_cache_miss_tokens=prompt - hit,
                completion_tokens=completion, total_tokens=prompt + completion,
                prompt_tokens_details={"cached_tokens": hit}, completion_tokens_details={"reasoning_tokens": 0})


def http_response(content=None, tokens=None, finish="stop"):
    body = dict(id="synthetic-completion", object="chat.completion", model="deepseek-flash", created=0,
                choices=[dict(index=0, finish_reason=finish, logprobs=None,
                              message=dict(role="assistant", content=content if content is not None else json.dumps(
                                  dict(ranking=[2, 1, 3], reason="Synthetic relative judgment; no root-cause proof."))))],
                usage=usage() if tokens is None else tokens)
    return dict(status_code=200, body=json.dumps(body))


class DeepSeekBudgetClientTest(unittest.TestCase):
    def setUp(self):
        base = os.environ.get("B04B2A_TEST_ROOT")
        if base: Path(base).mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="budget-case-", dir=base))
        self.ledger = d.BudgetLedger.create(self.temp / "ledger.sqlite3")
        ranking, catalog, report = helpers.synthetic_inputs()
        pool = build_candidate_pool(ranking, catalog, 20)
        self.shortlist = build_shortlist(pool, attach_candidate_evidence(pool, report), shortlist_size=3)
        self.request = self.request_for(0)
        self.transport = mock.Mock(return_value=http_response())
        self.client = d.DeepSeekBudgetClient(self.ledger, self.transport)

    def request_for(self, number):
        return build_llm_request("Synthetic budget fixture issue " + str(number), self.shortlist)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(d.BudgetClientError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def assert_unknown_blocks(self):
        snap = self.ledger.snapshot()
        self.assertTrue(snap["blocked"])
        self.assertEqual(snap["unknown_charge_count"], 1)
        self.assertIsNone(snap["total_estimated_charge_nano"])
        self.assertIsNone(snap["calls"][-1]["estimated_charge_nano"])
        before = self.transport.call_count
        self.assert_code("UNRESOLVED_CHARGE_BLOCKS_CALLS", self.client, self.request_for(999))
        self.assertEqual(self.transport.call_count, before)

    def seed_history(self, count, tokens=None):
        """Consistent MOCK receipt history only; no network/approval is created."""
        u = usage() if tokens is None else tokens
        cost = u["prompt_cache_hit_tokens"] * 40 + u["prompt_cache_miss_tokens"] * 2000 + u["completion_tokens"] * 8000
        with sqlite3.connect(self.ledger.path) as conn:
            for i in range(count):
                rh = hashlib.sha256(("synthetic-existing-slot-" + str(i)).encode()).hexdigest()
                conn.execute("INSERT INTO calls VALUES (?,?,'SETTLED',?,?,?,?,?,?,?,'SUCCESS')",
                    (i + 1, rh, d.RESERVATION_NANO, u["prompt_tokens"], u["prompt_cache_hit_tokens"],
                     u["prompt_cache_miss_tokens"], u["completion_tokens"], u["total_tokens"], cost))

    def test_success_usage_and_exact_integer_cost(self):
        content = self.client(self.request)
        self.transport.assert_called_once()
        self.assertEqual(validate_llm_response(self.request, content)["status"], "SUCCESS")
        snap = self.ledger.snapshot(); row = snap["calls"][0]
        self.assertFalse(snap["blocked"])
        self.assertEqual(row["state"], "SETTLED")
        self.assertEqual(row["estimated_charge_nano"], 20 * 40 + 80 * 2000 + 30 * 8000)
        self.assertEqual(row["prompt_tokens"], 100)
        self.assertEqual(snap["mode"], "OFFLINE_SIMULATED_ESTIMATES")

    def test_official_payload_model_json_format_limits_and_timeouts(self):
        self.client(self.request)
        sent = self.transport.call_args.kwargs
        self.assertEqual(sent["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(sent["json"]["model"], "deepseek-flash")
        self.assertEqual(sent["json"]["messages"], self.request["messages"])
        self.assertEqual(sent["json"]["response_format"], {"type": "json_object"})
        self.assertEqual(sent["json"]["max_tokens"], 2048)
        self.assertEqual(sent["json"]["thinking"], {"type": "disabled"})
        self.assertIs(sent["json"]["stream"], False)
        self.assertEqual(sent["connect_timeout"], 5.0); self.assertEqual(sent["read_timeout"], 30.0)
        self.assertEqual(sent["max_retries"], 0); self.assertIs(sent["allow_redirects"], False)
        self.assertEqual(sent["headers"], {"Content-Type": "application/json"})

    def test_fixed_budget_and_price_snapshot_not_live_claim(self):
        self.assertEqual(d.CONFIG["budget_nano"], 20 * 10 ** 9)
        self.assertEqual(d.CONFIG["safety_margin_nano"], 2 * 10 ** 9)
        self.assertEqual((d.CONFIG["planned_calls"], d.CONFIG["reserve_calls"], d.CONFIG["maximum_calls"]), (10, 2, 12))
        self.assertFalse(d.CONFIG["allow_real_network"]); self.assertFalse(d.CONFIG["automatic_retries"])
        self.assertIn("NOT_INVOICE", d.PRICE["basis"])
        self.assertTrue(d.PRICE["live_rate_recheck_required"])
        self.assertTrue(d.PRICE["source"].startswith("https://api-docs.deepseek.com/"))

    def test_invalid_b04a_request_zero_transport_and_zero_reservation(self):
        for request in (None, {}, {**self.request, "request_sha256": "0" * 64}, {**self.request, "accepted_targets": []}):
            with self.subTest(kind=type(request).__name__):
                self.assert_code("INVALID_REQUEST", self.client, request)
        self.transport.assert_not_called()
        self.assertEqual(self.ledger.snapshot()["call_count"], 0)

    def test_oversize_wire_payload_rejected_without_truncation_or_reservation(self):
        ranking, catalog, report = helpers.synthetic_inputs()
        for c in catalog:
            for field in ("annotations", "strings", "calls"):
                c["features"][field] = ["😀" * 256] * 8
        pool = build_candidate_pool(ranking, catalog, 20)
        shortlist = build_shortlist(pool, attach_candidate_evidence(pool, report))
        request = build_llm_request("Synthetic payload-size boundary", shortlist)
        self.assert_code("PAYLOAD_LIMIT_EXCEEDED", self.client, request)
        self.transport.assert_not_called()
        self.assertEqual(self.ledger.snapshot()["call_count"], 0)

    def test_budget_insufficient_zero_transport(self):
        self.seed_history(8, usage(prompt=997952, hit=0, completion=2048))
        self.assert_code("INSUFFICIENT_BUDGET", self.client, self.request)
        self.transport.assert_not_called()
        self.assertEqual(self.ledger.snapshot()["call_count"], 8)

    def test_ten_planned_calls_reserve_slots_never_automatic(self):
        for i in range(10):
            self.client(self.request_for(i))
        self.assert_code("RESERVE_CALLS_NOT_AUTHORIZED", self.client, self.request_for(10))
        self.assertEqual(self.transport.call_count, 10)
        self.assertEqual(self.ledger.snapshot()["call_count"], 10)

    def test_absolute_twelve_consumed_slots_reject_before_transport(self):
        self.seed_history(12)
        self.assert_code("TOTAL_CALL_LIMIT", self.client, self.request)
        self.transport.assert_not_called()

    def test_duplicate_request_no_retry_even_after_success(self):
        self.client(self.request)
        self.assert_code("DUPLICATE_REQUEST_NO_RETRY", self.client, self.request)
        self.transport.assert_called_once()

    def test_http_error_unknown_not_zero_and_future_calls_block(self):
        for status in (301, 400, 401, 429, 500):
            with self.subTest(status=status):
                ledger = d.BudgetLedger.create(self.temp / ("http-%d.sqlite3" % status))
                transport = mock.Mock(return_value=dict(status_code=status, body="UNTRUSTED_SECRET_ERROR_BODY"))
                client = d.DeepSeekBudgetClient(ledger, transport)
                with self.assertRaises(d.BudgetClientError) as caught: client(self.request)
                self.assertNotIn("UNTRUSTED_SECRET", str(caught.exception))
                self.assertIsNone(ledger.snapshot()["total_estimated_charge_nano"])
                self.assert_code("UNRESOLVED_CHARGE_BLOCKS_CALLS", client, self.request_for(status))
                transport.assert_called_once()

    def test_connection_error_unknown_and_no_retry(self):
        self.transport.side_effect = ConnectionError("Authorization: Bearer SYNTHETIC_SECRET_NOT_A_KEY")
        self.assert_code("TRANSPORT_ERROR_CHARGE_UNKNOWN", self.client, self.request)
        self.transport.assert_called_once(); self.assert_unknown_blocks()

    def test_read_timeout_unknown_and_no_retry(self):
        self.transport.side_effect = TimeoutError("SYNTHETIC_SECRET_TIMEOUT")
        with self.assertRaises(TimeoutError) as caught: self.client(self.request)
        self.assertNotIn("SYNTHETIC_SECRET_TIMEOUT", str(caught.exception))
        self.transport.assert_called_once(); self.assert_unknown_blocks()

    def test_missing_usage_unknown_and_no_more_calls(self):
        response = http_response(); body = json.loads(response["body"]); del body["usage"]
        response["body"] = json.dumps(body); self.transport.return_value = response
        self.assert_code("USAGE_UNKNOWN_CHARGE_UNKNOWN", self.client, self.request)
        self.assert_unknown_blocks()

    def test_illegal_usage_types_or_sums_fail_closed(self):
        for key, value in (("prompt_tokens", True), ("completion_tokens", -1), ("total_tokens", 999),
                           ("prompt_cache_hit_tokens", 101), ("prompt_cache_miss_tokens", "80")):
            u = usage(); u[key] = value
            ledger = d.BudgetLedger.create(self.temp / (key + ".sqlite3"))
            transport = mock.Mock(return_value=http_response(tokens=u))
            with self.subTest(key=key):
                self.assert_code("USAGE_UNKNOWN_CHARGE_UNKNOWN", d.DeepSeekBudgetClient(ledger, transport), self.request)
                self.assertTrue(ledger.snapshot()["blocked"])

    def test_usage_detail_disagreement_or_token_cap_rejected(self):
        values = [usage(completion=2049), usage(prompt=1000001), usage()]
        values[-1]["prompt_tokens_details"]["cached_tokens"] = 99
        for i, u in enumerate(values):
            ledger = d.BudgetLedger.create(self.temp / ("usage-cap-%d.sqlite3" % i))
            with self.subTest(i=i):
                client = d.DeepSeekBudgetClient(ledger, mock.Mock(return_value=http_response(tokens=u)))
                self.assert_code("USAGE_UNKNOWN_CHARGE_UNKNOWN", client, self.request)

    def test_finish_length_is_billed_known_but_never_success(self):
        self.transport.return_value = http_response(finish="length")
        self.assert_code("NONSTOP_RESPONSE", self.client, self.request)
        row = self.ledger.snapshot()["calls"][0]
        self.assertEqual(row["state"], "SETTLED")
        self.assertEqual(row["outcome"], "NONSTOP_RESPONSE")
        self.assertIsNotNone(row["estimated_charge_nano"])

    def test_all_other_finish_reasons_rejected(self):
        for i, finish in enumerate((None, "content_filter", "tool_calls", "aborted")):
            ledger = d.BudgetLedger.create(self.temp / ("finish-%d.sqlite3" % i))
            client = d.DeepSeekBudgetClient(ledger, mock.Mock(return_value=http_response(finish=finish)))
            with self.subTest(finish=finish): self.assert_code("NONSTOP_RESPONSE", client, self.request)

    def test_empty_or_invalid_http_json_is_unknown(self):
        for i, raw in enumerate(("", "not JSON", "null", '{"object":"chat.completion","object":"other"}', "NaN")):
            ledger = d.BudgetLedger.create(self.temp / ("bad-json-%d.sqlite3" % i))
            client = d.DeepSeekBudgetClient(ledger, mock.Mock(return_value=dict(status_code=200, body=raw)))
            with self.subTest(raw=raw):
                self.assert_code("INVALID_ENVELOPE_CHARGE_UNKNOWN", client, self.request)
                self.assertTrue(ledger.snapshot()["blocked"])

    def test_wrong_response_model_unknown(self):
        response = http_response(); body = json.loads(response["body"]); body["model"] = "other-model"
        response["body"] = json.dumps(body); self.transport.return_value = response
        self.assert_code("INVALID_ENVELOPE_CHARGE_UNKNOWN", self.client, self.request)
        self.assert_unknown_blocks()

    def test_invalid_model_ranking_cannot_bypass_b04a(self):
        raw = json.dumps(dict(ranking=[1, 1, 3], reason="SYNTHETIC_MODEL_FREE_TEXT_NOT_LOGGED"))
        self.transport.return_value = http_response(content=raw)
        self.assert_code("INVALID_MODEL_RESPONSE", self.client, self.request)
        row = self.ledger.snapshot()["calls"][0]
        self.assertEqual(row["state"], "SETTLED")
        self.assertEqual(row["outcome"], "INVALID_MODEL_RESPONSE")

    def test_structured_verified_claim_or_outside_method_is_rejected(self):
        for i, extra in enumerate(({"status": "VERIFIED"}, {"method_id": "outside#method"})):
            data = dict(ranking=[1, 2, 3], reason="fixture"); data.update(extra)
            ledger = d.BudgetLedger.create(self.temp / ("model-forge-%d.sqlite3" % i))
            client = d.DeepSeekBudgetClient(ledger, mock.Mock(return_value=http_response(content=json.dumps(data))))
            self.assert_code("INVALID_MODEL_RESPONSE", client, self.request)

    def test_empty_or_wrong_role_model_message_is_not_success(self):
        for i, mutation in enumerate(("empty", "role", "tools")):
            response = http_response(); body = json.loads(response["body"]); message = body["choices"][0]["message"]
            if mutation == "empty": message["content"] = ""
            elif mutation == "role": message["role"] = "system"
            else: message["tool_calls"] = [{"id": "synthetic-tool"}]
            response["body"] = json.dumps(body)
            ledger = d.BudgetLedger.create(self.temp / ("message-%d.sqlite3" % i))
            self.assert_code("INVALID_MODEL_RESPONSE", d.DeepSeekBudgetClient(ledger, mock.Mock(return_value=response)), self.request)

    def test_ledger_has_no_issue_reason_key_headers_or_exception_details(self):
        self.client(self.request)
        snapshot = self.ledger.snapshot(); text = json.dumps(snapshot)
        self.assertNotIn(self.request["issue_text"], text)
        self.assertNotIn("Synthetic relative judgment", text)
        self.assertNotIn("Authorization", text)
        self.assertNotIn("messages", text)
        self.assertNotIn("api_key", text)
        self.assertNotIn("reason", text)

    def test_no_live_flag_can_read_key_or_enable_network(self):
        with mock.patch.object(os, "getenv", side_effect=AssertionError("no env")), \
             mock.patch.object(type(os.environ), "__getitem__", side_effect=AssertionError("no key")):
            for flag in (True, 1, "authorized", {"approved": True}):
                with self.subTest(flag=flag):
                    self.assert_code("LIVE_NETWORK_NOT_IMPLEMENTED_OR_AUTHORIZED", d.DeepSeekBudgetClient,
                                     self.ledger, self.transport, allow_real_network=flag)
        self.transport.assert_not_called()

    def test_mock_only_operation_without_network_environment_process_or_source_reads(self):
        # Explicit temporary SQLite I/O is allowed; all Python input-file access
        # and network/key/process operations are forbidden in this execution.
        with mock.patch("builtins.open", side_effect=AssertionError("source read")), \
             mock.patch.object(Path, "open", side_effect=AssertionError("source read")), \
             mock.patch.object(socket, "socket", side_effect=AssertionError("network")), \
             mock.patch.object(socket, "create_connection", side_effect=AssertionError("network")), \
             mock.patch.object(subprocess, "run", side_effect=AssertionError("process")), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("process")), \
             mock.patch.object(os, "getenv", side_effect=AssertionError("key")), \
             mock.patch.object(type(os.environ), "__getitem__", side_effect=AssertionError("key")):
            content = self.client(self.request)
            self.assertEqual(validate_llm_response(self.request, content)["status"], "SUCCESS")
        self.transport.assert_called_once()

    def test_concurrent_last_planned_slot_cannot_oversubscribe(self):
        self.seed_history(9)
        entered = threading.Event(); release = threading.Event(); results = []
        def slow_mock(**kwargs):
            entered.set()
            if not release.wait(5): raise TimeoutError("synthetic synchronization timeout")
            return http_response()
        first = d.DeepSeekBudgetClient(self.ledger, slow_mock)
        second_transport = mock.Mock(return_value=http_response())
        second = d.DeepSeekBudgetClient(d.BudgetLedger(self.ledger.path), second_transport)
        def worker():
            try: results.append(first(self.request))
            except Exception as exc: results.append(type(exc).__name__)
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(5), "first reservation must precede mock dispatch")
            self.assert_code("UNRESOLVED_CHARGE_BLOCKS_CALLS", second, self.request_for(1))
            second_transport.assert_not_called()
        finally:
            release.set(); thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 1)
        self.assertEqual(validate_llm_response(self.request, results[0])["status"], "SUCCESS")
        self.assertEqual(self.ledger.snapshot()["call_count"], 10)

    def test_sqlite_write_lock_rejects_other_reservation(self):
        conn = sqlite3.connect(self.ledger.path, isolation_level=None)
        conn.execute("BEGIN IMMEDIATE")
        try:
            self.assert_code("LEDGER_IO_ERROR", self.client, self.request)
        finally:
            conn.rollback(); conn.close()
        self.transport.assert_not_called()

    def test_reservation_persistence_failure_is_zero_dispatch(self):
        with mock.patch.object(d.sqlite3, "connect", side_effect=OSError("SYNTHETIC_SECRET_IO")):
            self.assert_code("LEDGER_IO_ERROR", self.client, self.request)
        self.transport.assert_not_called()
        self.assertEqual(self.ledger.snapshot()["call_count"], 0)

    def test_commit_failure_before_send_rolls_back_and_sends_nothing(self):
        self.run_commit_failure(fail_number=1)
        self.transport.assert_not_called()
        self.assertEqual(self.ledger.snapshot()["call_count"], 0)

    def test_settlement_commit_failure_keeps_durable_reservation_after_reopen(self):
        self.run_commit_failure(fail_number=2)
        self.transport.assert_called_once()
        snapshot = d.BudgetLedger(self.ledger.path).snapshot()
        self.assertTrue(snapshot["blocked"])
        self.assertEqual(snapshot["calls"][0]["state"], "RESERVED")
        self.assertIsNone(snapshot["total_estimated_charge_nano"])
        self.assert_code("LEDGER_REVIEW_REQUIRED", self.client, self.request_for(2))
        new_client = d.DeepSeekBudgetClient(d.BudgetLedger(self.ledger.path), self.transport)
        self.assert_code("UNRESOLVED_CHARGE_BLOCKS_CALLS", new_client, self.request_for(3))
        self.transport.assert_called_once()

    def run_commit_failure(self, fail_number):
        original = sqlite3.connect; commits = []
        class ConnectionProxy:
            def __init__(self, conn): object.__setattr__(self, "inner", conn)
            def __setattr__(self, name, value): setattr(self.inner, name, value)
            def __getattr__(self, name): return getattr(self.inner, name)
            def execute(self, sql, *args):
                if sql == "COMMIT":
                    commits.append(sql)
                    if len(commits) == fail_number: raise sqlite3.OperationalError("SYNTHETIC_COMMIT_FAILURE")
                return self.inner.execute(sql, *args)
        def connect(*args, **kwargs): return ConnectionProxy(original(*args, **kwargs))
        with mock.patch.object(d.sqlite3, "connect", side_effect=connect):
            with self.assertRaises(d.BudgetClientError): self.client(self.request)

    def test_process_loss_after_reservation_blocks_reopen_no_automatic_refund(self):
        self.ledger.reserve(self.request["request_sha256"])
        reopened = d.BudgetLedger(self.ledger.path)
        client = d.DeepSeekBudgetClient(reopened, self.transport)
        self.assert_code("UNRESOLVED_CHARGE_BLOCKS_CALLS", client, self.request_for(1))
        self.transport.assert_not_called()
        self.assertEqual(reopened.snapshot()["calls"][0]["state"], "RESERVED")

    def test_keyboard_interrupt_during_dispatch_keeps_unknown_charge_block(self):
        self.transport.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): self.client(self.request)
        self.assert_unknown_blocks()

    def test_invalid_ledger_is_not_repaired(self):
        path = self.temp / "not-sqlite.bin"; path.write_bytes(b"SYNTHETIC_INVALID_LEDGER")
        before = path.read_bytes()
        client = d.DeepSeekBudgetClient(d.BudgetLedger(path), self.transport)
        self.assert_code("LEDGER_IO_ERROR", client, self.request)
        self.assertEqual(path.read_bytes(), before)
        self.transport.assert_not_called()

    def test_ledger_config_or_charge_tamper_not_repaired(self):
        self.client(self.request)
        with sqlite3.connect(self.ledger.path) as conn:
            conn.execute("UPDATE calls SET estimated_charge_nano=0")
        before = self.ledger.path.read_bytes()
        self.assert_code("INVALID_LEDGER_CHARGE", self.client, self.request_for(1))
        self.assertEqual(self.ledger.path.read_bytes(), before)
        self.transport.assert_called_once()

    def test_wrong_price_config_is_not_silently_upgraded(self):
        with sqlite3.connect(self.ledger.path) as conn:
            conn.execute("UPDATE config SET value=?", ('{"version":"old-price"}',))
        before = self.ledger.path.read_bytes()
        self.assert_code("LEDGER_CONFIG_MISMATCH", self.client, self.request)
        self.assertEqual(self.ledger.path.read_bytes(), before)
        self.transport.assert_not_called()

    def test_missing_ledger_never_auto_created(self):
        path = self.temp / "missing.sqlite3"
        self.assert_code("LEDGER_IO_ERROR", d.DeepSeekBudgetClient(d.BudgetLedger(path), self.transport), self.request)
        self.assertFalse(path.exists()); self.transport.assert_not_called()

    def test_create_refuses_existing_ledger(self):
        before = self.ledger.path.read_bytes()
        self.assert_code("LEDGER_CREATION_FAILED", d.BudgetLedger.create, self.ledger.path)
        self.assertEqual(self.ledger.path.read_bytes(), before)

    def test_invalid_ledger_schema_rejected(self):
        with sqlite3.connect(self.ledger.path) as conn: conn.execute("CREATE TABLE extra (secret TEXT)")
        self.assert_code("INVALID_LEDGER_SCHEMA", self.client, self.request)
        self.transport.assert_not_called()

    def test_unexpected_trigger_cannot_reset_call_count(self):
        with sqlite3.connect(self.ledger.path) as conn:
            conn.execute("CREATE TRIGGER erase_calls AFTER INSERT ON calls BEGIN DELETE FROM calls; END")
        before = self.ledger.path.read_bytes()
        self.assert_code("INVALID_LEDGER_SCHEMA", self.client, self.request)
        self.assertEqual(self.ledger.path.read_bytes(), before)
        self.transport.assert_not_called()

    def test_invalid_settlement_cannot_write_inconsistent_known_unknown_state(self):
        seq = self.ledger.reserve(self.request["request_sha256"])
        for outcome, u in (("SUCCESS", None), ("HTTP_ERROR", usage())):
            with self.subTest(outcome=outcome):
                self.assert_code("INVALID_OUTCOME", self.ledger.finish, seq, outcome, u)
        self.assertEqual(self.ledger.snapshot()["calls"][0]["state"], "RESERVED")
        self.transport.assert_not_called()

    def test_more_than_absolute_limit_is_invalid_ledger_not_repaired(self):
        self.seed_history(13)
        before = self.ledger.path.read_bytes()
        self.assert_code("INVALID_LEDGER_CALL_COUNT", self.client, self.request)
        self.assertEqual(self.ledger.path.read_bytes(), before)
        self.transport.assert_not_called()

    def test_default_transport_never_falls_back_to_network(self):
        self.assert_code("OFFLINE_TRANSPORT_REQUIRED", d.DeepSeekBudgetClient(self.ledger), self.request)
        self.assertEqual(self.ledger.snapshot()["call_count"], 0)

    def test_raw_request_and_response_not_mutated(self):
        before_request = copy.deepcopy(self.request); response = http_response(); before_response = copy.deepcopy(response)
        self.transport.return_value = response
        self.client(self.request)
        self.assertEqual(self.request, before_request); self.assertEqual(response, before_response)

    def test_outer_executor_can_reuse_client_without_changing_protocol(self):
        result = execute_llm_request(self.request, self.client)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["client_call_count"], 1)
        self.assertEqual(result["rerank_result"]["model_reason"]["status"], "MODEL_GENERATED_UNVERIFIED")
        self.assertFalse(result["fallback_applied"])
        self.transport.assert_called_once()

    def test_outer_executor_failure_is_never_model_success(self):
        self.transport.return_value = http_response(content="not model JSON")
        result = execute_llm_request(self.request, self.client)
        self.assertNotEqual(result["status"], "SUCCESS")
        self.assertFalse(result["reranked"])
        self.assertFalse(result["fallback_applied"])
        self.transport.assert_called_once()


if __name__ == "__main__": unittest.main()
