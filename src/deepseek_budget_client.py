"""B04B-2A: official DeepSeek wire format, INJECTED MOCK HTTP ONLY.

No live transport/key loading or approval issuer exists in this phase. Passing
allow_real_network=True always fails; DEEPSEEK_API_KEY is only a future name.
Injected Python transport must be trusted and offline: not an OS/network sandbox.
Timeout/retry parameters are a transport contract, not a synchronous watchdog.

Ledger: one explicitly created local SQLite file shared by ALL calls in this
budget. FULL synchronous commits and BEGIN IMMEDIATE reserve before dispatch.
A RESERVED/UNKNOWN row after failure/crash blocks further calls indefinitely;
there is no automatic reconciliation, refund, retry, reserve enable or repair.
SQLite crash guarantees require working local filesystem locks/fsync. This is
not protection against deletion, coherent owner tampering, another ledger/API
key, filesystem failure or provider repricing. No absolute final-bill guarantee.

Price snapshot observed in PUBLIC docs 2026-10-21, not an account/API query:
https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
Peak CNY / million: cached .04, uncached 2, output 8. Estimates use peak rates
irrespective of time, deliberately not invoice amounts. All usage/charges in
this release are MOCK_HTTP / SIMULATED_ESTIMATE, not measured paid API usage.
Actual execution would require separate approval, runtime rate verification,
key handling and a reviewed live transport; none is silently enabled here.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3

try:
    from .llm_rerank_protocol import build_llm_request, validate_llm_response
except ImportError:
    from llm_rerank_protocol import build_llm_request, validate_llm_response

MODEL = "deepseek-flash"
ENDPOINT = "https://api.deepseek.com/chat/completions"
API_KEY_ENV = "DEEPSEEK_API_KEY"  # NEVER read in this phase.
MAX_TOKENS = 2048
MAX_TOTAL_TOKENS = 1000000
MAX_PAYLOAD_BYTES = 200000  # An additional admission cap, never silent truncation.
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 30.0
NANO_CNY = 1000000000
PRICE = {"version": "deepseek-flash-cny-20261021-peak-upper-v1", "observed_date": "2026-10-21",
         "source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
         "basis": "PUBLIC_SNAPSHOT_PEAK_UPPER_ESTIMATE_NOT_INVOICE", "currency": "CNY",
         "cached_input_nano_per_token": 40, "uncached_input_nano_per_token": 2000,
         "output_nano_per_token": 8000, "live_rate_recheck_required": True}
RESERVATION_NANO = MAX_TOTAL_TOKENS * 2000 + MAX_TOKENS * 8000
CONFIG = {"schema_version": "deepseek-budget-ledger-v1", "mode": "OFFLINE_SIMULATED_ESTIMATES",
          "budget_nano": 20 * NANO_CNY, "safety_margin_nano": 2 * NANO_CNY,
          "planned_calls": 10, "reserve_calls": 2, "maximum_calls": 12,
          "reserve_enabled": False, "allow_real_network": False, "automatic_retries": False,
          "model": MODEL, "price": PRICE, "reservation_nano": RESERVATION_NANO}
SCHEMA = {
    "config": "CREATE TABLE config (value TEXT NOT NULL)",
    "calls": "CREATE TABLE calls (seq INTEGER PRIMARY KEY, request_hash TEXT UNIQUE NOT NULL, state TEXT NOT NULL, reserved_nano INTEGER NOT NULL, prompt_tokens INTEGER, cache_hit_tokens INTEGER, cache_miss_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER, estimated_charge_nano INTEGER, outcome TEXT NOT NULL)"}
OUTCOMES = {"RESERVED", "TRANSPORT_TIMEOUT", "TRANSPORT_ERROR", "HTTP_ERROR", "INVALID_ENVELOPE",
            "USAGE_UNKNOWN", "NONSTOP_RESPONSE", "INVALID_MODEL_RESPONSE", "SUCCESS"}


class BudgetClientError(Exception):
    """Only fixed, non-sensitive error codes cross this boundary."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _require(ok, code):
    if not ok:
        raise BudgetClientError(code)


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _parse(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            _require(key not in out, "INVALID_ENVELOPE")
            out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: _require(False, "INVALID_ENVELOPE"))


def _usage(value):
    fields = ("prompt_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "completion_tokens", "total_tokens")
    _require(isinstance(value, dict) and all(type(value.get(k)) is int and value[k] >= 0 for k in fields), "USAGE_UNKNOWN")
    p, hit, miss, completion, total = (value[k] for k in fields)
    _require(p > 0 and p == hit + miss and total == p + completion and total <= MAX_TOTAL_TOKENS
             and completion <= MAX_TOKENS, "USAGE_UNKNOWN")
    if "prompt_tokens_details" in value:
        d = value["prompt_tokens_details"]
        _require(isinstance(d, dict) and type(d.get("cached_tokens")) is int and d["cached_tokens"] == hit, "USAGE_UNKNOWN")
    if "completion_tokens_details" in value:
        d = value["completion_tokens_details"]
        _require(isinstance(d, dict) and type(d.get("reasoning_tokens")) is int
                 and 0 <= d["reasoning_tokens"] <= completion, "USAGE_UNKNOWN")
    return dict(zip(fields, (p, hit, miss, completion, total)))


def _charge(usage):
    return (usage["prompt_cache_hit_tokens"] * PRICE["cached_input_nano_per_token"]
            + usage["prompt_cache_miss_tokens"] * PRICE["uncached_input_nano_per_token"]
            + usage["completion_tokens"] * PRICE["output_nano_per_token"])


class BudgetLedger:
    """Explicit create/open; never auto-create, fix corrupt files or clear claims."""
    def __init__(self, path):
        self.path = Path(path)
        _require(self.path.is_absolute() and not str(self.path).startswith("\\\\"), "LOCAL_ABSOLUTE_LEDGER_PATH_REQUIRED")

    @classmethod
    def create(cls, path):
        ledger = cls(path)
        try:
            with ledger.path.open("xb"):
                pass
            with ledger._transaction(check=False) as conn:
                for sql in SCHEMA.values(): conn.execute(sql)
                conn.execute("INSERT INTO config VALUES (?)", (_canonical(CONFIG),))
        except BudgetClientError:
            raise
        except Exception:
            raise BudgetClientError("LEDGER_CREATION_FAILED") from None
        return ledger

    @contextmanager
    def _transaction(self, check=True):
        conn = None
        try:
            conn = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA busy_timeout=0")
            _require(conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete", "UNSUPPORTED_LEDGER_JOURNAL_MODE")
            conn.execute("BEGIN IMMEDIATE")
            if check: self._check(conn)
            yield conn
            conn.execute("COMMIT")
        except BudgetClientError:
            if conn is not None and conn.in_transaction: conn.rollback()
            raise
        except Exception:
            if conn is not None and conn.in_transaction: conn.rollback()
            raise BudgetClientError("LEDGER_IO_ERROR") from None
        finally:
            if conn is not None: conn.close()

    def _check(self, conn):
        _require(conn.execute("PRAGMA quick_check").fetchone()[0] == "ok", "INVALID_LEDGER")
        schema = dict(conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'").fetchall())
        _require(schema == SCHEMA, "INVALID_LEDGER_SCHEMA")
        _require(conn.execute("SELECT count(*) FROM sqlite_master WHERE type!='table' AND sql IS NOT NULL").fetchone()[0] == 0,
                 "INVALID_LEDGER_SCHEMA")
        config = conn.execute("SELECT value FROM config").fetchall()
        _require(len(config) == 1 and config[0][0] == _canonical(CONFIG), "LEDGER_CONFIG_MISMATCH")
        rows = conn.execute("SELECT * FROM calls ORDER BY seq").fetchall()
        _require(len(rows) <= 12, "INVALID_LEDGER_CALL_COUNT")
        for seq, row in enumerate(rows, 1):
            _require(row["seq"] == seq and re.fullmatch(r"[0-9a-f]{64}", row["request_hash"])
                     and row["state"] in {"RESERVED", "UNKNOWN", "SETTLED"}
                     and row["reserved_nano"] == RESERVATION_NANO and row["outcome"] in OUTCOMES, "INVALID_LEDGER_ROW")
            numeric = [row[k] for k in ("prompt_tokens", "cache_hit_tokens", "cache_miss_tokens", "completion_tokens", "total_tokens", "estimated_charge_nano")]
            if row["state"] != "SETTLED":
                _require(all(v is None for v in numeric)
                         and ((row["state"] == "RESERVED" and row["outcome"] == "RESERVED")
                              or (row["state"] == "UNKNOWN" and row["outcome"] in {
                                  "TRANSPORT_TIMEOUT", "TRANSPORT_ERROR", "HTTP_ERROR", "INVALID_ENVELOPE", "USAGE_UNKNOWN"})), "INVALID_LEDGER_UNCERTAINTY")
            else:
                u = _usage(dict(prompt_tokens=row["prompt_tokens"], prompt_cache_hit_tokens=row["cache_hit_tokens"],
                                prompt_cache_miss_tokens=row["cache_miss_tokens"], completion_tokens=row["completion_tokens"], total_tokens=row["total_tokens"]))
                _require(type(row["estimated_charge_nano"]) is int and row["estimated_charge_nano"] == _charge(u)
                         and row["estimated_charge_nano"] <= RESERVATION_NANO
                         and row["outcome"] in {"SUCCESS", "NONSTOP_RESPONSE", "INVALID_MODEL_RESPONSE"}, "INVALID_LEDGER_CHARGE")
        pending = [r for r in rows if r["state"] != "SETTLED"]
        _require(len(pending) <= 1 and (not pending or pending[0]["seq"] == len(rows)), "INVALID_LEDGER_PENDING_ORDER")
        committed = sum(r["estimated_charge_nano"] if r["state"] == "SETTLED" else r["reserved_nano"] for r in rows)
        _require(committed <= CONFIG["budget_nano"] - CONFIG["safety_margin_nano"], "INVALID_LEDGER_BUDGET")

    def reserve(self, request_hash):
        _require(isinstance(request_hash, str) and re.fullmatch(r"[0-9a-f]{64}", request_hash), "INVALID_REQUEST_HASH")
        with self._transaction() as conn:
            rows = conn.execute("SELECT * FROM calls ORDER BY seq").fetchall()
            _require(all(r["state"] == "SETTLED" for r in rows), "UNRESOLVED_CHARGE_BLOCKS_CALLS")
            _require(not any(r["request_hash"] == request_hash for r in rows), "DUPLICATE_REQUEST_NO_RETRY")
            _require(len(rows) < CONFIG["maximum_calls"], "TOTAL_CALL_LIMIT")
            _require(len(rows) < CONFIG["planned_calls"], "RESERVE_CALLS_NOT_AUTHORIZED")
            spent = sum(r["estimated_charge_nano"] for r in rows)
            _require(spent + RESERVATION_NANO <= CONFIG["budget_nano"] - CONFIG["safety_margin_nano"], "INSUFFICIENT_BUDGET")
            seq = len(rows) + 1
            conn.execute("INSERT INTO calls (seq,request_hash,state,reserved_nano,outcome) VALUES (?,?,'RESERVED',?,'RESERVED')",
                         (seq, request_hash, RESERVATION_NANO))
        return seq

    def finish(self, seq, outcome, usage=None):
        _require(type(seq) is int and seq > 0 and isinstance(outcome, str)
                 and outcome in (OUTCOMES - {"RESERVED", "SUCCESS", "NONSTOP_RESPONSE", "INVALID_MODEL_RESPONSE"}
                                 if usage is None else {"SUCCESS", "NONSTOP_RESPONSE", "INVALID_MODEL_RESPONSE"}), "INVALID_OUTCOME")
        with self._transaction() as conn:
            row = conn.execute("SELECT state FROM calls WHERE seq=?", (seq,)).fetchone()
            _require(row is not None and row[0] == "RESERVED", "INVALID_SETTLEMENT_STATE")
            if usage is None:
                conn.execute("UPDATE calls SET state='UNKNOWN',outcome=? WHERE seq=?", (outcome, seq))
            else:
                u = _usage(usage); cost = _charge(u)
                _require(cost <= RESERVATION_NANO, "RESERVATION_EXCEEDED")
                conn.execute("UPDATE calls SET state='SETTLED',outcome=?,prompt_tokens=?,cache_hit_tokens=?,cache_miss_tokens=?,completion_tokens=?,total_tokens=?,estimated_charge_nano=? WHERE seq=?",
                             (outcome, u["prompt_tokens"], u["prompt_cache_hit_tokens"], u["prompt_cache_miss_tokens"],
                              u["completion_tokens"], u["total_tokens"], cost, seq))

    def snapshot(self):
        with self._transaction() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM calls ORDER BY seq").fetchall()]
        unknown = sum(r["state"] != "SETTLED" for r in rows)
        known = sum(r["estimated_charge_nano"] for r in rows if r["state"] == "SETTLED")
        return dict(mode=CONFIG["mode"], price_version=PRICE["version"], call_count=len(rows),
                    known_estimate_subtotal_nano=known, total_estimated_charge_nano=None if unknown else known,
                    unknown_charge_count=unknown, blocked=bool(unknown), calls=rows)


class DeepSeekBudgetClient:
    """Callable compatible with execute_llm_request; HTTP transport is mock-only.

    transport(**kwargs) -> {'status_code': int, 'body': raw JSON str}. One call,
    no retries/redirects, no Authorization header. Live mode is unavailable, even
    with a boolean request. No real approval material is accepted or fabricated.
    """
    def __init__(self, ledger, transport=None, *, allow_real_network=False):
        _require(allow_real_network is False, "LIVE_NETWORK_NOT_IMPLEMENTED_OR_AUTHORIZED")
        _require(isinstance(ledger, BudgetLedger), "EXPLICIT_LEDGER_REQUIRED")
        self.ledger = ledger
        self.transport = transport
        self._ledger_failed = False

    def _finish(self, seq, outcome, usage=None):
        try:
            self.ledger.finish(seq, outcome, usage)
        except Exception:
            self._ledger_failed = True
            raise BudgetClientError("LEDGER_PERSISTENCE_FAILED_REVIEW_REQUIRED") from None

    def __call__(self, request):
        _require(not self._ledger_failed, "LEDGER_REVIEW_REQUIRED")
        try:
            _require(isinstance(request, dict), "INVALID_REQUEST")
            validated = build_llm_request(request["issue_text"], request["shortlist"])
            _require(_canonical(request) == _canonical(validated), "INVALID_REQUEST")
        except Exception:
            raise BudgetClientError("INVALID_REQUEST") from None
        _require(callable(self.transport), "OFFLINE_TRANSPORT_REQUIRED")
        payload = dict(model=MODEL, messages=deepcopy(validated["messages"]), response_format={"type": "json_object"},
                       max_tokens=MAX_TOKENS, stream=False, thinking={"type": "disabled"}, temperature=0)
        _require(len(_canonical(payload).encode("utf-8")) <= MAX_PAYLOAD_BYTES, "PAYLOAD_LIMIT_EXCEEDED")
        seq = self.ledger.reserve(validated["request_sha256"])
        try:
            response = self.transport(url=ENDPOINT, json=payload, headers={"Content-Type": "application/json"},
                                      connect_timeout=CONNECT_TIMEOUT, read_timeout=READ_TIMEOUT,
                                      max_retries=0, allow_redirects=False)
        except TimeoutError:
            self._finish(seq, "TRANSPORT_TIMEOUT")
            raise TimeoutError("MOCK_HTTP_TIMEOUT_CHARGE_UNKNOWN") from None
        except Exception:
            self._finish(seq, "TRANSPORT_ERROR")
            raise BudgetClientError("TRANSPORT_ERROR_CHARGE_UNKNOWN") from None
        error = "INVALID_ENVELOPE"
        try:
            _require(isinstance(response, dict) and set(response) == {"status_code", "body"}
                     and type(response["status_code"]) is int, "INVALID_ENVELOPE")
            error = "HTTP_ERROR"
            _require(response["status_code"] == 200, "HTTP_ERROR")
            error = "INVALID_ENVELOPE"
            _require(isinstance(response["body"], str) and 0 < len(response["body"]) <= 2000000, "INVALID_ENVELOPE")
            envelope = _parse(response["body"])
            _require(isinstance(envelope, dict) and envelope.get("object") == "chat.completion"
                     and envelope.get("model") == MODEL, "INVALID_ENVELOPE")
            error = "USAGE_UNKNOWN"
            usage = _usage(envelope.get("usage"))
        except Exception:
            self._finish(seq, error)
            raise BudgetClientError(error + "_CHARGE_UNKNOWN") from None
        outcome = "INVALID_MODEL_RESPONSE"; content = None
        try:
            choices = envelope["choices"]
            _require(isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict)
                     and type(choices[0].get("index")) is int and choices[0]["index"] == 0, "INVALID_MODEL_RESPONSE")
            choice = choices[0]
            if choice.get("finish_reason") != "stop":
                outcome = "NONSTOP_RESPONSE"
            else:
                message = choice["message"]
                _require(isinstance(message, dict) and message.get("role") == "assistant"
                         and not message.get("tool_calls") and not message.get("reasoning_content"), "INVALID_MODEL_RESPONSE")
                content = message.get("content")
                checked = validate_llm_response(validated, content)
                if checked["status"] == "SUCCESS": outcome = "SUCCESS"
        except Exception:
            pass
        self._finish(seq, outcome, usage)
        _require(outcome == "SUCCESS", outcome)
        return content
