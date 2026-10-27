import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("candidate_selection", ROOT / "src/bm25_candidate_selection.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class SelectionTests(unittest.TestCase):
    def fixture(self):
        rows = [{"rank": i, "ordinal": i-1, "score": 100-i, "method_id": f"method:{i}",
                 "features": {"name": f"parseCounters{i}", "class": "Example", "calls": []},
                 "identity_signature": f"Example#parseCounters{i}()"} for i in range(1, 25)]
        rows[0]["features"]["calls"] = [r["features"]["name"] for r in rows[20:]]
        return {"algorithm": "bm25", "status": "SUCCESS", "catalog_count": 24, "ranking": rows}

    def test_default_unchanged(self):
        r = self.fixture()
        self.assertEqual(m.select_candidates(r, "parse counters")["ranking"], r["ranking"][:20])

    def test_no_match_unchanged(self):
        r = self.fixture()
        self.assertEqual(m.select_candidates(r, "unrelated", structural=True)["ranking"], r["ranking"][:20])

    def test_two_max_identity_scores_and_input_unchanged(self):
        r = self.fixture()
        before = copy.deepcopy(r)
        result = m.select_candidates(r, "parse counters", structural=True)
        self.assertEqual(r, before)
        self.assertEqual(len(result["ranking"]), 20)
        self.assertEqual(result["ranking"][:18], r["ranking"][:18])
        self.assertEqual(len(result["promotions"]), 2)
        for row in result["ranking"]:
            self.assertEqual(row, r["ranking"][row["rank"]-1])

    def test_one_replacement_preserves_nineteen(self):
        r = self.fixture()
        r["ranking"][0]["features"]["calls"] = ["parseCounters21"]
        result = m.select_candidates(r, "parse counters", structural=True)
        self.assertEqual(len(result["promotions"]), 1)
        self.assertEqual(len({x["method_id"] for x in result["ranking"]} &
                             {x["method_id"] for x in r["ranking"][:20]}), 19)

    def test_incomplete_duplicate_or_reordered_fail_closed(self):
        for kind in ("count", "duplicate", "order"):
            r = self.fixture()
            if kind == "count": r["catalog_count"] = 25
            if kind == "duplicate": r["ranking"][1]["method_id"] = "method:1"
            if kind == "order": r["ranking"][1]["score"] = 101
            with self.assertRaises(ValueError): m.select_candidates(r, "parse counters", structural=True)

    def test_private_path_rejected(self):
        with self.assertRaises(ValueError): m.public_path("private-gt/example.json")


if __name__ == "__main__":
    unittest.main()
