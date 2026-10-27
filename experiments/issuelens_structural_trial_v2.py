"""IssueLens: optional query-aware structural candidate selection (offline trial).

Run from the IssueLens repository root:
    python -B experiments/issuelens_structural_trial_v2.py --self-test
    python -B experiments/issuelens_structural_trial_v2.py
    python -B experiments/issuelens_structural_trial_v2.py --out-dir experiments/structural-selection-output

Reads frozen BM25 metadata only. Does NOT read GT, call APIs, modify the original
ranking, or write output files unless --out-dir is explicitly given. The LANG-1645/1646 diagnostics are development
cases, not independent evaluation of Recall@20.

This is not a resolved Java call graph: it uses same-class method-name matches
in AST-extracted call lists. The final candidate set always has at most 20 entries.
"""
from __future__ import annotations

import argparse
import json
import re
import unittest
from pathlib import Path

ROOT = Path("experiments/acc006-frozen-prediction-20261023-v1")
TOP_K = 20
SEED_K = 5
MAX_REPLACEMENTS = 2
STOP = frozenset(
    "a an the and or but for to from with without by of on in into out at as is are was "
    "be been being it its this that these those all get set do does did not no can could "
    "should would will has have had".split()
)


def tokens(text: str) -> set[str]:
    """Common CamelCase/snake-case/prose tokenizer with simple plural trimming."""
    parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+", text)
    if not parts:
        parts = re.findall(r"[a-z0-9]+", text.lower())
    result = set()
    for part in parts:
        word = part.lower()
        if len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
            word = word[:-1]
        if word not in STOP and len(word) > 1:
            result.add(word)
    return result


def select(rows: list[dict], issue: str) -> tuple[list[dict], list[tuple]]:
    """Return selected rows and promotion explanations, without mutating rows.

    Mechanism: keep the first 18 BM25 results; selectively replace up to two
    tail slots using a top-5 seed's same-class call-name relation AND an
    Issue-to-candidate-name token intersection. No GT is read.
    """
    ordered = sorted(rows, key=lambda r: r["rank"])
    base = ordered[:TOP_K]
    if len(base) < TOP_K:
        return base, []
    issue_words = tokens(issue)
    eligible = []
    for candidate in ordered[TOP_K:]:
        features = candidate["features"]
        name = features.get("name")
        cls = features.get("class")
        if not isinstance(name, str) or not name or not isinstance(cls, str) or not cls:
            continue
        matched = issue_words & tokens(name)
        if not matched:
            continue
        callers = []
        for seed in ordered[:SEED_K]:
            s = seed["features"]
            calls = s.get("calls") or []
            if s.get("class") == cls and isinstance(calls, list) and name in calls:
                callers.append(seed)
        if callers:
            eligible.append((candidate, matched, callers))
    eligible.sort(key=lambda x: (-len(x[1]), -len(x[2]), x[0]["rank"], x[0]["ordinal"]))
    chosen = eligible[:MAX_REPLACEMENTS]
    result = base[: TOP_K - MAX_REPLACEMENTS] + [c for c, _, _ in chosen]
    present = {r["method_id"] for r in result}
    for row in base[TOP_K - MAX_REPLACEMENTS :]:
        if len(result) >= TOP_K:
            break
        if row["method_id"] not in present:
            result.append(row)
            present.add(row["method_id"])
    if len(result) != TOP_K or len({r["method_id"] for r in result}) != TOP_K:
        raise ValueError("INVALID_SELECTED_CANDIDATES")
    return result, chosen


def run(root: Path, case_id: str, out_dir: Path | None = None) -> None:
    case = json.loads((root / "paired-inputs" / case_id / "case-input.json").read_text(encoding="utf-8-sig"))
    ranking = json.loads((root / "bm25" / case_id / "full-ranking.json").read_text(encoding="utf-8-sig"))["ranking"]
    issue = "\n".join(
        str(value) for value in (
            case.get("issue_title") or case.get("title") or "",
            case["issue_text"],
        ) if value
    )
    ordered = sorted(ranking, key=lambda row: row["rank"])
    selected, additions = select(ordered, issue)
    old = {row["method_id"] for row in ordered[:TOP_K]}
    new = {row["method_id"] for row in selected}
    print(f"\n=== {case_id} ===")
    print(f"Catalog: {len(ranking)}, candidates: {len(selected)}, BM25 Top20 retained: {len(old & new)}")
    print(f"Added: {len(additions)}; removed: {len(old - new)}")
    if not additions:
        print("No query-supported same-class direct-call candidate outside Top20; unchanged.")
    for row, matched, callers in additions:
        print(f"  ADD BM25#{row['rank']} {row['method_id']}")
        print(f"      issue-name overlap: {', '.join(sorted(matched))}; callers: "
              + ", ".join(f"BM25#{s['rank']}:{s['features'].get('name')}" for s in callers))
    for row in ordered[:TOP_K]:
        if row["method_id"] not in new:
            print(f"  DROP BM25#{row['rank']} {row['method_id']}")
    if out_dir is not None:
        # A durable opt-in experiment output, kept separate from frozen inputs.
        if out_dir.resolve() == root.resolve() or root.resolve() in out_dir.resolve().parents:
            raise ValueError("REFUSE_OUTPUT_INSIDE_FROZEN_INPUTS")
        out_dir.mkdir(parents=True, exist_ok=True)
        detail = {
            "schema_version": "issuelens-structural-selection-trial-v1",
            "case_id": case_id,
            "status": "DEVELOPMENT_DIAGNOSTIC_NOT_INDEPENDENT_EVALUATION",
            "algorithm": "bm25-top18-plus-query-aware-same-class-call-up-to2",
            "candidate_budget": TOP_K,
            "original_bm25_top20_retained": len(old & new),
            "selection": [
                {"selection_rank": rank, "original_bm25_rank": row["rank"],
                 "method_id": row["method_id"], "features": row["features"]}
                for rank, row in enumerate(selected, 1)
            ],
            "promotions": [
                {"method_id": row["method_id"], "original_bm25_rank": row["rank"],
                 "issue_name_overlap": sorted(matched),
                 "caller_method_ids": [seed["method_id"] for seed in callers]}
                for row, matched, callers in additions
            ],
            "displaced_bm25_method_ids": [row["method_id"] for row in ordered[:TOP_K]
                                         if row["method_id"] not in new],
        }
        dest = out_dir / f"{case_id}.structural-selection.json"
        with dest.open("x", encoding="utf-8") as f:
            json.dump(detail, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"Output: {dest}")


class SyntheticTests(unittest.TestCase):
    """No real Issue IDs, production GT, or hardcoded domain method targets."""

    @staticmethod
    def fixture():
        rows = [
            {"method_id": f"m{i}", "rank": i, "ordinal": i - 1,
             "features": {"name": f"method{i}", "class": "C", "calls": []}}
            for i in range(1, 25)
        ]
        rows[0]["features"]["calls"] = ["parseCounters", "handleTokens", "doOther"]
        rows[20]["features"]["name"] = "parseCounters"
        rows[21]["features"]["name"] = "handleTokens"
        rows[22]["features"]["name"] = "doOther"
        return rows

    def test_absent_support_preserves_original_top20_and_order(self):
        rows = self.fixture()
        selected, additions = select(rows, "a completely unrelated error")
        self.assertEqual([r["method_id"] for r in selected], [r["method_id"] for r in rows[:20]])
        self.assertEqual(additions, [])

    def test_single_promotion_keeps_nineteen_and_drops_only_tail(self):
        rows = self.fixture()
        selected, additions = select(rows, "parsing counters stopped")
        ids = [r["method_id"] for r in selected]
        self.assertEqual(len(ids), 20)
        self.assertEqual(len(additions), 1)
        self.assertIn("m21", ids)
        self.assertNotIn("m20", ids)
        self.assertIn("m19", ids)

    def test_cap_two_and_hold_top18(self):
        rows = self.fixture()
        selected, additions = select(rows, "parse counters and handle tokens")
        ids = [r["method_id"] for r in selected]
        self.assertEqual(len(ids), 20)
        self.assertEqual(len(set(ids)), 20)
        self.assertEqual(len(additions), 2)
        self.assertEqual(ids[:18], [r["method_id"] for r in rows[:18]])
        self.assertEqual(set(ids[18:]), {"m21", "m22"})

    def test_same_class_guard(self):
        rows = self.fixture()
        rows[20]["features"]["class"] = "Other"
        selected, additions = select(rows, "parse counters")
        self.assertEqual(additions, [])
        self.assertEqual([r["method_id"] for r in selected], [r["method_id"] for r in rows[:20]])

    def test_missing_call_list_does_not_hallucinate_edges(self):
        rows = self.fixture()
        rows[0]["features"]["calls"] = None
        selected, additions = select(rows, "parse counters")
        self.assertEqual(additions, [])
        self.assertEqual([r["method_id"] for r in selected], [r["method_id"] for r in rows[:20]])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="run synthetic unit tests without external data")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--out-dir", type=Path, help="optional new output directory; refuse overwrites and frozen input paths")
    parser.add_argument("--case", action="append", help="case ID; default: all locally frozen BM25 cases")
    args = parser.parse_args()
    if args.self_test:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(SyntheticTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(0 if result.wasSuccessful() else 1)
    case_ids = args.case or sorted(p.parent.name for p in (args.root / "bm25").glob("*/full-ranking.json"))
    if not case_ids:
        parser.error(f"No BM25 full-ranking.json found under {args.root}")
    for case_id in case_ids:
        run(args.root, case_id, args.out_dir)


if __name__ == "__main__":
    main()
