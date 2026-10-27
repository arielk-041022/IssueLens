"""GT-free, in-memory projection of an existing complete BM25 ranking.

Call build_candidate_pool(predict_result['ranking'], catalog, budget). No scorer,
request package, file path input, authorization, or CLI is provided. Features are
index metadata, not independently verified source evidence. This function checks
structural consistency only; it cannot authenticate ranking or index provenance.
"""
from copy import deepcopy
import math

IDENTITY = ("repo_id", "source_snapshot_id", "file_path", "method_id")
FIELDS = ("name", "class", "signature", "file", "annotations", "strings", "calls")


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _identity(record):
    _require(all(isinstance(record[k], str) and record[k]
                 and record[k] == record[k].strip()
                 and all(ord(c) >= 32 for c in record[k]) for k in IDENTITY),
             "INVALID_METHOD_IDENTITY")
    path = record["file_path"]
    parts = path.split("/")
    _require("\\" not in path and ":" not in path and path.endswith(".java")
             and all(p and p not in {".", ".."} and not p.endswith((".", " ")) for p in parts),
             "INVALID_RELATIVE_FILE_PATH")
    _require(record["method_id"].startswith("java-source-v1:")
             and len(record["method_id"]) > len("java-source-v1:"), "INVALID_METHOD_ID_SCHEME")
    return tuple(record[k] for k in IDENTITY)


def build_candidate_pool(ranking, catalog, budget):
    """Return the first min(budget, len(catalog)) ranked methods as detached data.

    Inputs must be lists with exact BM25 ranking/catalog record fields. Validate
    the entire ranking and catalog, including entries beyond the budget. Catalog
    ordinal must retain its original zero-based list index. One repo/snapshot is
    allowed per pool. Empty lists are legal; budget must be a positive integer.
    Unknown fields (including GT annotations) are rejected, never propagated.
    Finite numeric scores and supplied order are preserved, not recomputed or
    sorted. Completeness means equality with the supplied catalog, not proof of
    full-repository coverage. All invalid inputs raise ValueError.
    """
    _require(type(budget) is int and budget > 0, "INVALID_BUDGET")
    _require(isinstance(ranking, list) and isinstance(catalog, list), "INPUT_LISTS_REQUIRED")
    by_id = {}
    scope = None
    for ordinal, record in enumerate(catalog):
        _require(isinstance(record, dict) and set(record) == set(IDENTITY) | {"ordinal", "has_body", "features"},
                 "INVALID_CATALOG_FIELDS")
        key = _identity(record)
        _require(key not in by_id, "DUPLICATE_CATALOG_IDENTITY")
        if scope is None:
            scope = key[:2]
        _require(key[:2] == scope, "MIXED_REPO_OR_SNAPSHOT")
        _require(type(record["ordinal"]) is int and record["ordinal"] == ordinal, "INVALID_ORDINAL")
        _require(type(record["has_body"]) is bool, "INVALID_HAS_BODY")
        features = record["features"]
        _require(isinstance(features, dict) and set(features) == set(FIELDS), "INVALID_FEATURE_FIELDS")
        _require(all(isinstance(features[k], str) for k in FIELDS[:4]), "INVALID_FEATURE_TEXT")
        _require(all(isinstance(features[k], list) and all(isinstance(v, str) for v in features[k])
                     for k in FIELDS[4:]), "INVALID_FEATURE_LIST")
        _require(features["file"] == record["file_path"], "FEATURE_FILE_MISMATCH")
        by_id[key] = record

    seen = set()
    for record in ranking:
        _require(isinstance(record, dict) and set(record) == set(IDENTITY) | {"score"}, "INVALID_RANKING_FIELDS")
        key = _identity(record)
        _require(key not in seen, "DUPLICATE_RANKING_IDENTITY")
        _require(scope is None or key[:2] == scope, "MIXED_REPO_OR_SNAPSHOT")
        _require(key in by_id, "UNKNOWN_RANKING_IDENTITY")
        score = record["score"]
        try:
            finite = type(score) in (int, float) and math.isfinite(score)
        except (OverflowError, ValueError):
            finite = False
        _require(finite, "INVALID_SCORE")
        seen.add(key)
    _require(seen == set(by_id), "INCOMPLETE_RANKING")

    candidates = []
    for rank, entry in enumerate(ranking[:budget], 1):
        record = by_id[tuple(entry[k] for k in IDENTITY)]
        identity = {k: entry[k] for k in IDENTITY}
        candidates.append({**identity, "bm25_rank": rank, "bm25_score": entry["score"],
                           "ordinal": record["ordinal"], "has_body": record["has_body"],
                           "features": deepcopy(record["features"]), "source": "BM25",
                           "feature_source": {"kind": "INDEX_FEATURES", "identity": dict(identity)},
                           "evidence_status": "UNVERIFIED", "verification_scope": "NONE"})
    return {"schema_version": "high-recall-candidates-v1", "candidate_budget": budget,
            "budget_policy": "CLAMP_TO_CATALOG_SIZE", "catalog_count": len(catalog),
            "candidate_count": len(candidates), "source": "BM25",
            "evidence_status": "UNVERIFIED", "candidates": candidates}
