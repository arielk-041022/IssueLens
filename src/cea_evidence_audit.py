"""IssueLens CEA v0.1: deterministic *metadata-field* counterfactual audit.

This is an offline analysis of the existing Rule scorer, not a new retriever,
calibrated confidence score, evidence verification, or a GT/recall evaluation.
The caller supplies a full method ranking/catalog and public Issue text.

We recompute Rule scores over the exact seven saved metadata feature fields,
then independently remove one field ('name', 'strings', or 'calls') from a
DEEP COPY of every candidate and recompute the complete ordering.

Importantly, fields overlap semantically. Removing 'name' does not remove name
information possibly present in 'signature', 'file', or other fields, nor does
this simulate intervention on executable source code. No causal correctness
claim follows from a stable/unstable ranking.

Install from this deliverable into <repo>/src/cea_evidence_audit.py.
Run from project root:
  python -B src/cea_evidence_audit.py --self-test
  python -B src/cea_evidence_audit.py --ranking <full-ranking.json> \
      --issue-file <public-issue.txt> --output <new-audit.json>

No GT, LLM API, automatic retries, or changes to source/experiment files.
Outputs are created only with exclusive creation ('x'), never overwritten.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable

CHANNELS = ("name", "strings", "calls")
SCHEMA = "issuelens-cea-metadata-intervention-v1"
TOPK = 5


class AuditError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rows(ranking_doc: dict[str, Any]) -> list[dict[str, Any]]:
    _require(isinstance(ranking_doc, dict), "RANKING_NOT_OBJECT")
    rows = ranking_doc.get("ranking")
    _require(isinstance(rows, list) and len(rows) > 0, "NO_FULL_RANKING")
    ids, ordinals = set(), set()
    for row in rows:
        _require(isinstance(row, dict), "INVALID_RANKING_ROW")
        ident, ordinal, features = row.get("method_id"), row.get("ordinal"), row.get("features")
        _require(isinstance(ident, str) and bool(ident), "INVALID_METHOD_ID")
        _require(ident not in ids, "DUPLICATE_METHOD_ID")
        ids.add(ident)
        _require(type(ordinal) is int and ordinal >= 0, "INVALID_ORDINAL")
        _require(ordinal not in ordinals, "DUPLICATE_ORDINAL")
        ordinals.add(ordinal)
        _require(isinstance(features, dict), "MISSING_FEATURES")
        # The frozen metadata path has these fields. Do not create missing data.
        for key in CHANNELS:
            _require(key in features, "MISSING_CHANNEL_" + key.upper())
        _require(isinstance(features["name"], str), "INVALID_NAME_TYPE")
        for key in ("strings", "calls"):
            _require(features[key] is None or isinstance(features[key], list),
                     "INVALID_CHANNEL_TYPE_" + key.upper())
    return rows


def _rank(
    rows: list[dict[str, Any]], keywords: Any, issue_text: str,
    score_method: Callable, masked_channel: str | None,
) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        features = deepcopy(row["features"])
        if masked_channel == "name":
            features["name"] = ""
        elif masked_channel in ("calls", "strings"):
            features[masked_channel] = []
        try:
            score_result = score_method(features, keywords, issue_text)
            score = score_result[0] if isinstance(score_result, tuple) else score_result
        except Exception as exc:
            raise AuditError("SCORER_FAILED_" + str(masked_channel or "baseline")) from exc
        _require(type(score) in (int, float) and math.isfinite(score), "INVALID_RULE_SCORE")
        result.append({
            "method_id": row["method_id"],
            "ordinal": row["ordinal"],
            "score": float(score),
        })
    result.sort(key=lambda x: (-x["score"], x["ordinal"]))
    return result


def audit(
    ranking_doc: dict[str, Any], issue_text: str,
    extract_keywords: Callable[[str], Any],
    score_method: Callable,
    *, topk: int = TOPK,
) -> dict[str, Any]:
    """Pure analysis; no I/O, no GT, no input mutation.

    Baseline and interventions use the *same* saved catalog and Rule scorer.
    BM25 ranks/scores are not passed off as a Rule baseline. Ties break by
    original 'ordinal', matching the existing metadata ranking runner.
    """
    rows = _rows(ranking_doc)
    _require(isinstance(issue_text, str) and bool(issue_text.strip()), "EMPTY_ISSUE_TEXT")
    _require(type(topk) is int and topk > 0, "INVALID_TOPK")
    try:
        keywords = extract_keywords(issue_text)
    except Exception as exc:
        raise AuditError("KEYWORD_EXTRACTION_FAILED") from exc
    original = _rank(rows, keywords, issue_text, score_method, None)
    baseline_ids = [r["method_id"] for r in original]
    base_rank = {r["method_id"]: i for i, r in enumerate(original, 1)}
    base_scores = {r["method_id"]: r["score"] for r in original}
    base_top = baseline_ids[:topk]
    base_top20 = set(baseline_ids[:20])
    base_winner = baseline_ids[0]
    interventions = []
    for channel in CHANNELS:
        scored = _rank(rows, keywords, issue_text, score_method, channel)
        ids = [r["method_id"] for r in scored]
        rank = {r["method_id"]: i for i, r in enumerate(scored, 1)}
        changed_scores = {r["method_id"]: r["score"] for r in scored}
        interventions.append({
            "masked_field": channel,
            "nonempty_channel_candidates": sum(
                bool(row["features"][channel]) for row in rows
            ),
            "candidates_with_score_change": sum(
                base_scores[ident] != changed_scores[ident] for ident in baseline_ids
            ),
            "top1_changed": ids[0] != base_winner,
            "new_top1_method_id": ids[0],
            "original_top1_new_rank": rank[base_winner],
            "original_top1_score_delta": base_scores[base_winner] - changed_scores[base_winner],
            "topk_overlap_count": len(set(base_top) & set(ids[:topk])),
            "top20_overlap_count": len(base_top20 & set(ids[:20])),
            "original_topk_moves": [
                {
                    "method_id": ident,
                    "original_rank": base_rank[ident],
                    "intervened_rank": rank[ident],
                    "rank_shift": rank[ident] - base_rank[ident],
                    "original_score": base_scores[ident],
                    "intervened_score": changed_scores[ident],
                    "score_drop": base_scores[ident] - changed_scores[ident],
                }
                for ident in base_top
            ],
            "intervened_topk_method_ids": ids[:topk],
        })
    return {
        "schema_version": SCHEMA,
        "status": "OFFLINE_COUNTERFACTUAL_METADATA_DIAGNOSTIC_NOT_CORRECTNESS_PROOF",
        "base_ranker": "retriever.score_method(full_saved_metadata; score_desc_then_ordinal)",
        "source_algorithm": ranking_doc.get("algorithm"),
        "catalog_count": len(rows),
        "topk": topk,
        "masked_fields": list(CHANNELS),
        "base_top1_method_id": base_winner,
        "base_topk_method_ids": base_top,
        "base_top20_method_ids": baseline_ids[:20],
        "interventions": interventions,
        "interpretation_limits": [
            "field_masking_not_source_code_intervention",
            "masked_information_may_remain_in_other_features",
            "ranking_sensitivity_not_root_cause_correctness_or_calibrated_confidence",
            "metadata_only_may_differ_from_full_source_rule_scorer",
            "no_gt_no_recall_no_api",
        ],
    }


def run_cli(ranking_file: Path, issue_file: Path, output_file: Path, *, topk: int = TOPK) -> dict[str, Any]:
    if output_file.exists():
        raise AuditError("OUTPUT_ALREADY_EXISTS")
    ranking_real = ranking_file.resolve(strict=True)
    issue_real = issue_file.resolve(strict=True)
    out_real = output_file.resolve()
    _require(out_real not in (ranking_real, issue_real), "OUTPUT_OVERWRITES_INPUT")
    # Never place audits inside the input ranking's case directory or below it.
    _require(ranking_real.parent not in out_real.parents, "OUTPUT_WITHIN_RANKING_CASE")
    ranking_bytes = ranking_file.read_bytes()
    issue_bytes = issue_file.read_bytes()
    try:
        document = json.loads(ranking_bytes.decode("utf-8-sig"))
        issue_text = issue_bytes.decode("utf-8-sig")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditError("INPUT_ENCODING_OR_JSON_INVALID") from exc

    # Import the project's current implementation rather than copying weights.
    from retriever import extract_keywords, score_method

    result = audit(document, issue_text, extract_keywords, score_method, topk=topk)
    result["input_provenance"] = {
        "ranking_sha256": _sha256(ranking_bytes),
        "issue_sha256": _sha256(issue_bytes),
        "retriever_py_sha256": _sha256(Path(__file__).resolve().with_name("retriever.py").read_bytes()),
        "audit_script_sha256": _sha256(Path(__file__).read_bytes()),
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("x", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, allow_nan=False)
        f.write("\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ranking", type=Path, help="full-ranking.json with catalog method features")
    parser.add_argument("--issue-file", type=Path, help="original public Issue text, UTF-8")
    parser.add_argument("--output", type=Path, help="a NEW audit JSON file, never overwritten")
    parser.add_argument("--topk", type=int, default=TOPK)
    parser.add_argument("--self-test", action="store_true", help="run bundled tests without project files")
    args = parser.parse_args()
    if args.self_test:
        import unittest
        here = Path(__file__).resolve().parent
        test_dir = here.parent / "tests"
        if not (test_dir / "test_cea_evidence_audit.py").is_file():
            test_dir = here  # local unpacked standalone copies
        suite = unittest.defaultTestLoader.discover(
            start_dir=str(test_dir), pattern="test_cea_evidence_audit.py"
        )
        outcome = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(0 if outcome.wasSuccessful() else 1)
    if any(value is None for value in (args.ranking, args.issue_file, args.output)):
        parser.error("--ranking, --issue-file and --output are required")
    try:
        report = run_cli(args.ranking, args.issue_file, args.output, topk=args.topk)
    except (AuditError, FileNotFoundError, OSError) as exc:
        parser.exit(2, f"CEA_ERROR: {exc}\n")
    print("CEA_AUDIT_OK")
    print(f"catalog={report['catalog_count']} baseline_top1={report['base_top1_method_id']}")
    for item in report["interventions"]:
        print(f"remove-{item['masked_field']}: top1_changed={item['top1_changed']} "
              f"original_top1_new_rank={item['original_top1_new_rank']} "
              f"top5_overlap={item['topk_overlap_count']} "
              f"top20_overlap={item['top20_overlap_count']}")
    print(f"report={args.output}")


if __name__ == "__main__":
    main()
