"""Offline B01/B02 adapter: bounded data for a future LLM, never a prompt/run.

Selection is the original BM25 prefix, with no evidence-based scoring. Bounds
are static engineering limits, unrelated to any real issue/GT. Text slicing uses
Python Unicode code points (not bytes/tokens or grapheme clusters). Identifiers
are never shortened: overlong identifiers are rejected. The two inputs do not
authenticate provenance; all references remain NOT_ATTESTED/UNVERIFIABLE.
"""
from copy import deepcopy
import json
import re

try:
    from .candidate_evidence_adapter import attach_candidate_evidence
except ImportError:
    from candidate_evidence_adapter import attach_candidate_evidence

IDENTITY = ("repo_id", "source_snapshot_id", "file_path", "method_id")
LIMITS = {"max_candidates": 50, "max_identity_characters": 1024,
          "max_feature_text_characters": 256, "max_feature_array_items": 8,
          "max_evidence_references": 3, "max_reference_identifier_characters": 1024,
          "max_status_characters": 256, "basis_included": False}
FEATURES = ("name", "class", "signature", "file", "annotations", "strings", "calls")
COORDS = ("start_line", "start_column", "end_line", "end_column")
BASIS_FIELDS = {"expected_evidence_id", "computed_evidence_id", "expected_source_sha256", "actual_source_sha256",
                "expected_snippet_sha256", "actual_snippet_sha256", "extracted_source_snippet", "checks_passed"}
POINTER = re.compile(r"/(0|[1-9][0-9]*)/classes/(0|[1-9][0-9]*)/methods/(0|[1-9][0-9]*)/evidence_occurrences/(0|[1-9][0-9]*)\Z")


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _canonical(value):
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False, separators=(",", ":"))
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ValueError("INVALID_JSON_DATA") from None


def _text(value, limit):
    return (isinstance(value, str) and 0 < len(value) <= limit and value == value.strip()
            and all(ord(c) >= 32 for c in value))


def _validate_association(pool, association):
    # Reuse B02's public pool validation, not a second approximate B01 validator.
    template = attach_candidate_evidence(pool, None)
    _canonical(association)
    _require(isinstance(association, dict) and set(association) == set(template), "INVALID_ASSOCIATION_SCHEMA")
    for key in template:
        if key not in {"candidates", "report_check"}:
            _require(_canonical(association[key]) == _canonical(template[key]), "POOL_ASSOCIATION_METADATA_MISMATCH")
    check = association["report_check"]
    _require(isinstance(check, dict) and set(check) == {"status", "reason_code"}
             and isinstance(check["status"], str) and check["status"] in {"CONSISTENT_SUCCESS_CLAIM", "NOT_ELIGIBLE"}
             and check["reason_code"] == "TRUSTED_SOURCE_BINDING_UNAVAILABLE", "REJECTED_OR_INVALID_REPORT_ASSOCIATION")
    candidates = association["candidates"]
    _require(isinstance(candidates, list) and len(candidates) == len(pool["candidates"]), "MISSING_OR_EXTRA_CANDIDATES")
    seen_ids = set(); seen_pointers = set(); method_owners = {}
    pointer_methods = {}; pointer_files = {}; owner_pointers = {}
    for original, associated in zip(pool["candidates"], candidates):
        _require(isinstance(associated, dict) and set(associated) == set(original) | {"evidence_association"}, "INVALID_ASSOCIATED_CANDIDATE_FIELDS")
        _require(_canonical({k: associated[k] for k in original}) == _canonical(original), "CANDIDATE_ORDER_IDENTITY_OR_CONTENT_MISMATCH")
        _require(all(_text(original[k], LIMITS["max_identity_characters"]) for k in IDENTITY), "OVERLONG_OR_INVALID_IDENTITY")
        mid = original["method_id"]; owner = tuple(original[k] for k in IDENTITY)
        _require(mid not in method_owners or method_owners[mid] == owner, "CONFLICTING_METHOD_IDENTITY")
        method_owners[mid] = owner
        state = associated["evidence_association"]
        _require(isinstance(state, dict) and set(state) == {"status", "source_provenance_status", "issue_relevance_status", "reason_code", "possible_references"}, "INVALID_EVIDENCE_ASSOCIATION_FIELDS")
        refs = state["possible_references"]
        _require(isinstance(refs, list) and state["status"] == "NOT_ATTESTED"
                 and state["source_provenance_status"] == "UNVERIFIABLE" and state["issue_relevance_status"] == "NOT_ASSESSED"
                 and state["reason_code"] == ("SOURCE_BINDING_UNAVAILABLE" if refs else "NO_MATCHING_REPORT_REFERENCE"), "INVALID_OR_UPGRADED_EVIDENCE_STATE")
        for ref in refs:
            _require(isinstance(ref, dict) and set(ref) == {"record_pointer", "evidence_id", "evidence_type", "file_path", "method_id", "range", "basis",
                     "reported_status", "reported_reason_code", "association_status", "source_provenance_status", "basis_trust"}, "INVALID_REFERENCE_FIELDS")
            _require(ref["file_path"] == original["file_path"] and ref["method_id"] == mid, "REFERENCE_OWNERSHIP_MISMATCH")
            _require(_text(ref["record_pointer"], LIMITS["max_reference_identifier_characters"])
                     and POINTER.fullmatch(ref["record_pointer"]) and _text(ref["evidence_id"], LIMITS["max_reference_identifier_characters"]), "INVALID_REFERENCE_IDENTIFIER")
            _require(ref["evidence_id"] not in seen_ids and ref["record_pointer"] not in seen_pointers, "DUPLICATE_EVIDENCE_ID_OR_POINTER")
            seen_ids.add(ref["evidence_id"]); seen_pointers.add(ref["record_pointer"])
            mp = ref["record_pointer"].split("/evidence_occurrences/")[0]
            fp = mp.split("/classes/")[0]
            _require((mp not in pointer_methods or pointer_methods[mp] == owner)
                     and (fp not in pointer_files or pointer_files[fp] == original["file_path"])
                     and (owner not in owner_pointers or owner_pointers[owner] == mp), "CONFLICTING_REFERENCE_POINTER_OWNERSHIP")
            pointer_methods[mp] = owner; pointer_files[fp] = original["file_path"]; owner_pointers[owner] = mp
            _require(ref["association_status"] == "NOT_ATTESTED" and ref["source_provenance_status"] == "UNVERIFIABLE"
                     and ref["basis_trust"] == "UNATTESTED_REPORT_CLAIM"
                     and isinstance(ref["evidence_type"], str) and ref["evidence_type"] in {"ANNOTATION", "STRING_LITERAL", "METHOD_CALL"}
                     and all(_text(ref[k], LIMITS["max_status_characters"]) for k in ("reported_status", "reported_reason_code")), "INVALID_REFERENCE_STATE")
            r = ref["range"]
            _require(isinstance(r, dict) and set(r) == set(COORDS)
                     and all(v is None or type(v) is int and 0 < v <= 2147483647 for v in r.values()), "INVALID_REFERENCE_RANGE")
            if all(r[k] is not None for k in COORDS):
                _require((r["start_line"], r["start_column"]) <= (r["end_line"], r["end_column"]), "INVALID_REFERENCE_RANGE")
            basis = ref["basis"]
            _require(isinstance(basis, dict) and set(basis) <= BASIS_FIELDS
                     and all(isinstance(v, str) if k != "checks_passed" else isinstance(v, list) and all(isinstance(t, str) for t in v)
                             for k, v in basis.items()), "INVALID_REFERENCE_BASIS")


def _clip(value):
    clipped = value[:LIMITS["max_feature_text_characters"]]
    return clipped, {"original_characters": len(value), "included_characters": len(clipped), "truncated": value != clipped}


def build_shortlist(candidate_pool, evidence_association, *, shortlist_size=10):
    """Validate ALL input candidates/references before selecting the BM25 prefix.

    Both inputs must be exact B01/B02 outputs for the same ordered candidates,
    including unchanged scores/features. B02 REJECTED reports are refused; valid
    NOT_ELIGIBLE reports may supply only explicitly untrusted references. Invalid
    data raises ValueError without repair. Empty valid pools return an empty list.
    Oversize budgets clamp to min(pool size, 50). Text caps do not affect identity,
    selection, scores or status. Basis blobs are omitted, not promoted to facts.
    Returned strings/features are untrusted DATA, never instructions or reasoning.
    No input authenticity or real-source validation is established by this module.
    """
    _require(type(shortlist_size) is int and shortlist_size > 0, "INVALID_SHORTLIST_SIZE")
    _validate_association(candidate_pool, evidence_association)
    selected = evidence_association["candidates"][:min(shortlist_size, LIMITS["max_candidates"])]
    result = []
    for c in selected:
        features = {}; truncation = {}
        for field in FEATURES:
            value = c["features"][field]
            if field == "file":
                features[field] = value  # exact identity path; already bounded
                truncation[field] = {"original_characters": len(value), "included_characters": len(value), "truncated": False}
            elif isinstance(value, str):
                features[field], truncation[field] = _clip(value)
            else:
                items = [_clip(v) for v in value[:LIMITS["max_feature_array_items"]]]
                features[field] = [v for v, _ in items]
                truncation[field] = {"original_count": len(value), "included_count": len(items),
                                     "omitted_count": len(value) - len(items), "item_text": [t for _, t in items]}
        ea = c["evidence_association"]; refs = []
        for ref in ea["possible_references"][:LIMITS["max_evidence_references"]]:
            refs.append({k: deepcopy(v) for k, v in ref.items() if k != "basis"})
        result.append({**{k: c[k] for k in IDENTITY}, "bm25_rank": c["bm25_rank"], "bm25_score": c["bm25_score"],
                       "ordinal": c["ordinal"], "has_body": c["has_body"], "source": "BM25", "features": features,
                       "feature_source": deepcopy(c["feature_source"]), "evidence_status": "UNVERIFIED",
                       "verification_scope": "NONE",
                       "evidence_association": {**{k: v for k, v in ea.items() if k != "possible_references"}, "possible_references": refs},
                       "truncation": {"features": truncation, "evidence_references": {
                           "original_count": len(ea["possible_references"]), "included_count": len(refs),
                           "omitted_count": len(ea["possible_references"]) - len(refs), "basis_omitted": True}}})
    return {"schema_version": "candidate-shortlist-v1", "selection_policy": "BM25_PREFIX_ONLY",
            "requested_shortlist_size": shortlist_size, "candidate_pool_count": candidate_pool["candidate_count"],
            "shortlist_count": len(result), "candidates_omitted": candidate_pool["candidate_count"] - len(result),
            "candidate_limit_applied": shortlist_size > LIMITS["max_candidates"] and candidate_pool["candidate_count"] > LIMITS["max_candidates"],
            "limits": dict(LIMITS), "source_binding": deepcopy(evidence_association["source_binding"]),
            "report_check": deepcopy(evidence_association["report_check"]),
            "verification_scope": deepcopy(evidence_association["verification_scope"]),
            "issue_relevance_status": "NOT_ASSESSED", "content_role": "UNTRUSTED_DATA_ONLY", "candidates": result}
