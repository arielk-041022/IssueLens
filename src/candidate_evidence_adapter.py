"""Unattested, offline references between B01 candidates and Validator reports.

No controller attestation exists in these two input protocols: B01 has no index
digest, and Validator reports lack repo_id/source_snapshot_id and authentication.
Consequently this adapter NEVER emits a VERIFIED provenance conclusion. Matching
file/method strings identify possible references only, not cross-system identity.
No binding dictionary, boolean, filename or self-reported PASS can grant trust.
No source parsing, evidence-ID recomputation, file reads, ranking or CLI occurs.
"""
from collections import Counter
from copy import deepcopy
import json
import math
import re

IDENTITY = ("repo_id", "source_snapshot_id", "file_path", "method_id")
FEATURES = ("name", "class", "signature", "file", "annotations", "strings", "calls")
COORDS = ("start_line", "start_column", "end_line", "end_column")
EP = re.compile(r"/(0|[1-9][0-9]*)/classes/(0|[1-9][0-9]*)/methods/(0|[1-9][0-9]*)/evidence_occurrences/(0|[1-9][0-9]*)\Z")
MP = re.compile(r"/(0|[1-9][0-9]*)/classes/(0|[1-9][0-9]*)/methods/(0|[1-9][0-9]*)\Z")
BASIS = {"expected_evidence_id", "computed_evidence_id", "expected_source_sha256", "actual_source_sha256",
         "expected_snippet_sha256", "actual_snippet_sha256", "extracted_source_snippet", "checks_passed"}


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _text(value):
    return isinstance(value, str) and bool(value) and value == value.strip() and all(ord(c) >= 32 for c in value)


def _path(value):
    return (_text(value) and "\\" not in value and ":" not in value and value.endswith(".java")
            and all(p and p not in {".", ".."} and not p.endswith((".", " ")) for p in value.split("/")))


def _json(value):
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ValueError("NON_JSON_INPUT") from None


def _pool(pool):
    _json(pool)
    keys = {"schema_version", "candidate_budget", "budget_policy", "catalog_count", "candidate_count",
            "source", "evidence_status", "candidates"}
    _require(isinstance(pool, dict) and set(pool) == keys and pool["schema_version"] == "high-recall-candidates-v1", "INVALID_POOL_SCHEMA")
    _require(type(pool["candidate_budget"]) is int and pool["candidate_budget"] > 0
             and type(pool["catalog_count"]) is int and pool["catalog_count"] >= 0
             and type(pool["candidate_count"]) is int
             and isinstance(pool["candidates"], list)
             and pool["candidate_count"] == len(pool["candidates"]) == min(pool["candidate_budget"], pool["catalog_count"]), "INVALID_POOL_COUNTS")
    _require(pool["budget_policy"] == "CLAMP_TO_CATALOG_SIZE" and pool["source"] == "BM25"
             and pool["evidence_status"] == "UNVERIFIED", "INVALID_POOL_STATE")
    seen = set(); ordinals = set(); scope = None
    for rank, c in enumerate(pool["candidates"], 1):
        _require(isinstance(c, dict) and set(c) == set(IDENTITY) | {"bm25_rank", "bm25_score", "ordinal", "has_body",
                 "features", "source", "feature_source", "evidence_status", "verification_scope"}, "INVALID_CANDIDATE_FIELDS")
        _require(all(_text(c[k]) for k in IDENTITY) and _path(c["file_path"])
                 and c["method_id"].startswith("java-source-v1:"), "INVALID_CANDIDATE_IDENTITY")
        key = tuple(c[k] for k in IDENTITY)
        _require(key not in seen, "DUPLICATE_CANDIDATE_IDENTITY"); seen.add(key)
        if scope is None: scope = key[:2]
        _require(key[:2] == scope, "MIXED_REPO_OR_SNAPSHOT")
        _require(type(c["bm25_rank"]) is int and c["bm25_rank"] == rank
                 and type(c["ordinal"]) is int and 0 <= c["ordinal"] < pool["catalog_count"]
                 and c["ordinal"] not in ordinals and type(c["has_body"]) is bool, "INVALID_CANDIDATE_ORDER")
        ordinals.add(c["ordinal"])
        try: finite = type(c["bm25_score"]) in (int, float) and math.isfinite(c["bm25_score"])
        except OverflowError: finite = False
        _require(finite, "INVALID_CANDIDATE_SCORE")
        _require(c["source"] == "BM25" and c["evidence_status"] == "UNVERIFIED" and c["verification_scope"] == "NONE"
                 and c["feature_source"] == {"kind": "INDEX_FEATURES", "identity": {k: c[k] for k in IDENTITY}}, "INVALID_FEATURE_PROVENANCE")
        f = c["features"]
        _require(isinstance(f, dict) and set(f) == set(FEATURES)
                 and all(isinstance(f[k], str) for k in FEATURES[:4])
                 and all(isinstance(f[k], list) and all(isinstance(v, str) for v in f[k]) for k in FEATURES[4:])
                 and f["file"] == c["file_path"], "INVALID_FEATURES")


def _report(report):
    """Check report shape/references only; never perform source validation."""
    _json(report)
    required = {"report_schema_version", "validator_version", "verification_scope", "report_status", "overall_verdict",
                "exit_code", "inputs", "index_integrity", "file_results", "method_results", "evidence_results", "issues", "summary"}
    _require(isinstance(report, dict) and set(report) == required
             and report["report_schema_version"] == "1.0" and report["validator_version"] == "1.0", "INVALID_REPORT_SCHEMA")
    scope = report["verification_scope"]
    _require(isinstance(scope, dict) and scope.get("independent_ast_parse") is False, "UNSUPPORTED_VERIFICATION_SCOPE")
    _require(report["report_status"] in {"COMPLETE", "ABORTED"} and report["overall_verdict"] in {"PASS", "FAIL"}
             and type(report["exit_code"]) is int and report["exit_code"] in {0, 1, 2}, "INVALID_REPORT_STATE")
    _require(isinstance(report["inputs"], dict) and isinstance(report["index_integrity"], dict)
             and all(isinstance(report[k], list) for k in ("file_results", "method_results", "evidence_results", "issues")), "INVALID_REPORT_CONTAINERS")
    files = {}; methods = {}; mids = set(); ids = set(); pointers = set()
    for f in report["file_results"]:
        _require(isinstance(f, dict) and _text(f.get("record_pointer")) and re.fullmatch(r"/(0|[1-9][0-9]*)", f["record_pointer"])
                 and _path(f.get("file_path")) and _text(f.get("status")), "INVALID_FILE_REFERENCE")
        _require(f["record_pointer"] not in files and f["file_path"] not in {v["file_path"] for v in files.values()}, "DUPLICATE_FILE_REFERENCE")
        files[f["record_pointer"]] = f
    for m in report["method_results"]:
        _require(isinstance(m, dict) and _text(m.get("record_pointer")) and MP.fullmatch(m["record_pointer"])
                 and _text(m.get("method_id")) and _text(m.get("status")), "INVALID_METHOD_REFERENCE")
        _require(m["record_pointer"].split("/classes/")[0] in files, "UNKNOWN_METHOD_FILE_REFERENCE")
        _require(m["record_pointer"] not in methods and m["method_id"] not in mids, "CONFLICTING_METHOD_REFERENCE")
        methods[m["record_pointer"]] = m; mids.add(m["method_id"])
    for e in report["evidence_results"]:
        _require(isinstance(e, dict) and set(e) == {"record_pointer", "evidence_id", "method_id", "evidence_type", "file_path", "status", "reason_code", "range", "basis"}, "INVALID_EVIDENCE_FIELDS")
        _require(all(_text(e[k]) for k in ("record_pointer", "evidence_id", "method_id", "status", "reason_code"))
                 and EP.fullmatch(e["record_pointer"]) and _path(e["file_path"])
                 and e["evidence_type"] in {"ANNOTATION", "STRING_LITERAL", "METHOD_CALL"}, "INVALID_EVIDENCE_REFERENCE")
        _require(e["evidence_id"] not in ids and e["record_pointer"] not in pointers, "DUPLICATE_EVIDENCE_ID_OR_POINTER")
        ids.add(e["evidence_id"]); pointers.add(e["record_pointer"])
        mp = e["record_pointer"].split("/evidence_occurrences/")[0]
        fp = mp.split("/classes/")[0]
        _require(mp in methods and fp in files and methods[mp]["method_id"] == e["method_id"]
                 and files[fp]["file_path"] == e["file_path"], "CONFLICTING_EVIDENCE_OWNERSHIP")
        r = e["range"]
        _require(isinstance(r, dict) and set(r) == set(COORDS)
                 and all(v is None or type(v) is int and v > 0 for v in r.values()), "INVALID_DECLARED_RANGE")
        if all(r[k] is not None for k in COORDS):
            _require((r["start_line"], r["start_column"]) <= (r["end_line"], r["end_column"]), "INVALID_DECLARED_RANGE")
        _require(isinstance(e["basis"], dict) and set(e["basis"]) <= BASIS, "INVALID_BASIS_FIELDS")
        _require(all(isinstance(v, str) if k != "checks_passed" else isinstance(v, list) and all(isinstance(t, str) for t in v)
                     for k, v in e["basis"].items()), "INVALID_BASIS_TYPES")
    counts = Counter(e["status"] for e in report["evidence_results"])
    summary = report["summary"]
    expected = dict(files_total=len(files), methods_total=len(methods), evidence_total=len(pointers),
                    verified=counts.get("VERIFIED", 0), unverifiable=counts.get("UNVERIFIABLE", 0),
                    failed=sum(n for s, n in counts.items() if s not in {"VERIFIED", "UNVERIFIABLE"}),
                    evidence_status_counts=dict(sorted(counts.items())), blocking_issues=len(report["issues"]))
    _require(isinstance(summary, dict) and summary == expected
             and all(type(summary[k]) is int for k in expected if k != "evidence_status_counts"), "REPORT_COUNTS_MISMATCH")
    integrity = report["index_integrity"]
    hash_ok = (integrity.get("status") == "PASS" and isinstance(integrity.get("actual_sha256"), str)
               and re.fullmatch(r"[0-9a-f]{64}", integrity["actual_sha256"])
               and integrity.get("expected_sha256") == integrity["actual_sha256"]
               and report["inputs"].get("index_sha256") == integrity["actual_sha256"])
    success_claim = bool(report["report_status"] == "COMPLETE" and report["overall_verdict"] == "PASS"
                         and report["exit_code"] == 0 and hash_ok and not report["issues"]
                         and report["evidence_results"]
                         and all(f["status"] == "SOURCE_MATCH" for f in files.values())
                         and all(m["status"] in {"CHECKED", "NO_EVIDENCE"} for m in methods.values())
                         and all(e["status"] == "VERIFIED" and all(e["range"][k] is not None for k in COORDS)
                                 for e in report["evidence_results"]))
    return success_claim


def attach_candidate_evidence(candidate_pool, validation_report):
    """Attach untrusted possible report references, preserving B01 order/features.

    Invalid pool => ValueError. Missing/malformed/conflicting report => structured
    REJECTED result with no references. A structurally consistent report is still
    NOT_ATTESTED, even with PASS and matching supplied hashes. This API has no
    attestation parameter: caller-provided trust flags cannot establish provenance.
    A future trusted controller must bind pool/catalog/index/report bytes to the
    same repo/snapshot and authenticated validation execution before any upgrade.
    """
    _pool(candidate_pool)
    check = "REJECTED"; reason = "MISSING_REPORT"; rows = []
    if validation_report is not None:
        try:
            success_claim = _report(validation_report)
            check = "CONSISTENT_SUCCESS_CLAIM" if success_claim else "NOT_ELIGIBLE"
            reason = "TRUSTED_SOURCE_BINDING_UNAVAILABLE"
            rows = validation_report["evidence_results"]
        except ValueError as exc:
            reason = str(exc)
        except (TypeError, KeyError):
            reason = "INVALID_REPORT_STRUCTURE"
    by_pair = {}
    for e in rows:
        by_pair.setdefault((e["file_path"], e["method_id"]), []).append(e)
    output = deepcopy(candidate_pool)
    output["candidate_pool_schema_version"] = output["schema_version"]
    output["schema_version"] = "candidate-evidence-associations-v1"
    output["source_binding"] = {"status": "NOT_ATTESTED", "reason_code": "TRUSTED_SOURCE_BINDING_UNAVAILABLE"}
    output["report_check"] = {"status": check, "reason_code": reason}
    output["verification_scope"] = {"independent_ast_parse": False, "source_validation_performed": False,
                                     "report_reference_checks_only": True}
    for c in output["candidates"]:
        refs = []
        for e in by_pair.get((c["file_path"], c["method_id"]), []):
            refs.append({k: deepcopy(e[k]) for k in ("record_pointer", "evidence_id", "evidence_type", "file_path", "method_id", "range", "basis")})
            refs[-1].update(reported_status=e["status"], reported_reason_code=e["reason_code"],
                            association_status="NOT_ATTESTED", source_provenance_status="UNVERIFIABLE",
                            basis_trust="UNATTESTED_REPORT_CLAIM")
        c["evidence_association"] = dict(status="NOT_ATTESTED", source_provenance_status="UNVERIFIABLE",
                                         issue_relevance_status="NOT_ASSESSED",
                                         reason_code=reason if check == "REJECTED" else "SOURCE_BINDING_UNAVAILABLE" if refs else "NO_MATCHING_REPORT_REFERENCE",
                                         possible_references=refs)
    return output
