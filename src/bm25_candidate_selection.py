"""Opt-in postprocessing of a complete BM25 ranking; no scorer or transport.

Does not grant experimental execution authorization or certify source binding.
Call only after the upstream ranking/source validators have succeeded.
"""
from copy import deepcopy
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path

SELECTOR = Path(__file__).resolve().parents[1] / "experiments/issuelens_structural_trial_v2.py"


def select_candidates(ranking, issue_text, *, structural=False):
    """Select before truncation, preserving original rows including score/identity."""
    rows = ranking["ranking"]
    if (ranking.get("algorithm") != "bm25" or ranking.get("status") != "SUCCESS"
            or ranking.get("catalog_count") != len(rows) or len(rows) < 20):
        raise ValueError("COMPLETE_BM25_RANKING_REQUIRED")
    ids, ordinals = set(), set()
    for rank, row in enumerate(rows, 1):
        mid, ordinal, score = row.get("method_id"), row.get("ordinal"), row.get("score")
        if (type(row.get("rank")) is not int or row["rank"] != rank
                or not isinstance(mid, str) or not mid or mid in ids
                or type(ordinal) is not int or not 0 <= ordinal < len(rows) or ordinal in ordinals
                or type(score) not in (int, float) or not math.isfinite(score) or score < 0
                or not isinstance(row.get("features"), dict)):
            raise ValueError("INVALID_FULL_RANKING")
        ids.add(mid)
        ordinals.add(ordinal)
    if rows != sorted(rows, key=lambda r: (-r["score"], r["ordinal"])):
        raise ValueError("BM25_ORDER_MISMATCH")
    if not isinstance(issue_text, str):
        raise ValueError("INVALID_ISSUE_TEXT")
    selected, additions = rows[:20], []
    if structural:
        spec = importlib.util.spec_from_file_location("structural_selection_v2", SELECTOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        selected, additions = module.select(rows, issue_text)
    if (len(selected) != 20 or len({r["method_id"] for r in selected}) != 20
            or selected[:18] != rows[:18]
            or any(r != rows[r["rank"] - 1] for r in selected)):
        raise ValueError("SELECTION_INVARIANT_FAILED")
    return {
        "schema_version": "bm25-candidate-selection-v1",
        "mode": "structural-v2-opt-in" if structural else "bm25-top20",
        "ranking": deepcopy(selected),
        "promotions": [{"method_id": row["method_id"], "original_bm25_rank": row["rank"],
                        "relation": "SAME_CLASS_CALL_NAME_NOT_RESOLVED_JAVA_EDGE",
                        "class": row["features"].get("class"),
                        "issue_name_overlap": sorted(matched),
                        "supporting_seeds": [{"method_id": s["method_id"], "original_bm25_rank": s["rank"]}
                                             for s in callers]}
                       for row, matched, callers in additions],
        "assurance": "UPSTREAM_SOURCE_AND_IDENTITY_VALIDATION_REQUIRED",
    }


def public_path(value):
    path = Path(value).resolve()
    if any("private" in part.lower() for part in path.parts):
        raise ValueError("PRIVATE_PATH_FORBIDDEN")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ranking", required=True, type=public_path)
    parser.add_argument("--issue-file", required=True, type=public_path, help="exact UTF-8 public Issue text")
    parser.add_argument("--structural", action="store_true", help="opt in; replace at most the final two slots")
    parser.add_argument("--output", type=public_path, help="new file only; omitted: print JSON")
    args = parser.parse_args()
    raw, issue = args.ranking.read_bytes(), args.issue_file.read_bytes()
    result = select_candidates(json.loads(raw.decode("utf-8-sig")), issue.decode("utf-8-sig"),
                               structural=args.structural)
    result["input_sha256"] = {"ranking": hashlib.sha256(raw).hexdigest(),
                              "issue": hashlib.sha256(issue).hexdigest(),
                              "selector": hashlib.sha256(SELECTOR.read_bytes()).hexdigest()}
    output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(output)
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
