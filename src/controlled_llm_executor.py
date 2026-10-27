"""Offline injected-client orchestration; no real API transport or authority.

The caller must inject a trusted synchronous OFFLINE simulator. This function
cannot sandbox arbitrary Python client code, stop a hanging callback or limit
operations hidden inside it. TIMEOUT means the client raised TimeoutError, not
that a wall-clock deadline was enforced. One invocation per execute call, never
a retry. No keys, environment, files, network, processes, timings or usage reads.
"""
from copy import deepcopy
import json

try:
    from .llm_rerank_protocol import build_llm_request, validate_llm_response
except ImportError:
    from llm_rerank_protocol import build_llm_request, validate_llm_response


def _canonical(value):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    text.encode("utf-8", errors="strict")
    return text


def _result(status, count, error_category=None, rerank_result=None, request_hash=None):
    return {"schema_version": "controlled-llm-execution-v1", "execution_mode": "INJECTED_OFFLINE_CLIENT",
            "status": status, "client_call_attempted": count == 1, "client_call_count": count,
            "reranked": status == "SUCCESS", "fallback_applied": False, "error_category": error_category,
            "request_sha256": request_hash, "rerank_result": rerank_result}


def execute_llm_request(request, client):
    """Validate -> invoke client(detached_request) once -> validate raw JSON.

    No authorization parameter exists; a boolean cannot enable network access.
    The client contract is a synchronous callable returning a raw JSON string.
    It receives a detached full B04A request, including messages/candidate map.
    B04A public request building performs schema validation; an exact canonical
    comparison verifies the supplied messages, map, hash and unknown-field rules.
    The independent rebuilt request is kept for response validation, preventing
    mutation of the client argument from changing the accepted candidate map.

    INVALID_REQUEST: zero calls on admission failure. CLIENT_ERROR: noncallable
    client (zero calls) or ordinary callback exception (one call). TIMEOUT: client
    TimeoutError (one call). INVALID_RESPONSE: response rejected (one call).
    SUCCESS: protocol-valid response only, never proof of real model execution,
    verified provenance or correct reasoning. All failures keep reranked=False
    and fallback_applied=False. No exception messages, classes, traces, raw
    responses or exception objects are recorded. KeyboardInterrupt/SystemExit
    are not swallowed. No cost/token values are fabricated.
    """
    try:
        if not isinstance(request, dict) or "issue_text" not in request or "shortlist" not in request:
            return _result("INVALID_REQUEST", 0, "REQUEST_VALIDATION_FAILED")
        validated = build_llm_request(request["issue_text"], request["shortlist"])
        if _canonical(request) != _canonical(validated):
            return _result("INVALID_REQUEST", 0, "REQUEST_VALIDATION_FAILED")
        client_input = deepcopy(validated)
    except Exception:
        return _result("INVALID_REQUEST", 0, "REQUEST_VALIDATION_FAILED")
    request_hash = validated["request_sha256"]
    if not callable(client):
        return _result("CLIENT_ERROR", 0, "CLIENT_NOT_CALLABLE", request_hash=request_hash)
    try:
        response = client(client_input)  # The only callback invocation. No loop/retry.
    except TimeoutError:
        return _result("TIMEOUT", 1, "CLIENT_TIMEOUT", request_hash=request_hash)
    except Exception:
        return _result("CLIENT_ERROR", 1, "CLIENT_EXCEPTION", request_hash=request_hash)
    try:
        result = validate_llm_response(validated, response)
    except Exception:
        return _result("INVALID_RESPONSE", 1, "RESPONSE_VALIDATION_FAILED", request_hash=request_hash)
    if result["status"] == "SUCCESS":
        return _result("SUCCESS", 1, rerank_result=result, request_hash=request_hash)
    if result["status"] == "INVALID_REQUEST":
        return _result("INVALID_REQUEST", 1, "REQUEST_VALIDATION_FAILED", result, request_hash)
    return _result("INVALID_RESPONSE", 1, "RESPONSE_VALIDATION_FAILED", result, request_hash)
