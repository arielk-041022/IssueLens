"""Synthetic in-memory fixtures only; never loads experimental artifacts."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "high_recall_candidates", Path(__file__).resolve().parents[1] / "src/high_recall_candidates.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
build_candidate_pool = MODULE.build_candidate_pool


def fixture(size=60):
    catalog = []
    for i in range(size):
        path = "src/main/java/fixture/Type%d.java" % i
        catalog.append(dict(repo_id="fixture/repo", source_snapshot_id="sha256:" + "a" * 64,
                            file_path=path, method_id="java-source-v1:fixture.Type%d#same(int)" % i,
                            ordinal=i, has_body=i % 2 == 0,
                            features={"name": "same", "class": "Type%d" % i, "signature": "same(int)",
                                      "file": path, "annotations": ["@Fixture"],
                                      "strings": ["raw", "raw"], "calls": ["check", "check"]}))
    ranking = [{**{k: r[k] for k in MODULE.IDENTITY}, "score": float(i)}
               for i, r in enumerate(catalog)]
    ranking.reverse()
    return ranking, catalog


class HighRecallCandidatesTest(unittest.TestCase):
    def setUp(self):
        self.ranking, self.catalog = fixture()

    def test_normal_extraction_and_provenance(self):
        result = build_candidate_pool(self.ranking, self.catalog, 5)
        self.assertEqual(result["schema_version"], "high-recall-candidates-v1")
        self.assertEqual(result["candidate_budget"], 5)
        self.assertEqual(result["catalog_count"], 60)
        for rank, r in enumerate(result["candidates"], 1):
            expected = self.ranking[rank - 1]
            self.assertEqual({k: r[k] for k in MODULE.IDENTITY}, {k: expected[k] for k in MODULE.IDENTITY})
            self.assertEqual(r["bm25_rank"], rank)
            self.assertEqual(r["bm25_score"], expected["score"])
            self.assertEqual(r["feature_source"]["identity"], {k: expected[k] for k in MODULE.IDENTITY})

    def test_budgets_and_exact_order(self):
        for budget in (5, 10, 20, 50, 100):
            with self.subTest(budget=budget):
                result = build_candidate_pool(self.ranking, self.catalog, budget)
                self.assertEqual(result["candidate_count"], min(budget, 60))
                self.assertEqual([r["method_id"] for r in result["candidates"]],
                                 [r["method_id"] for r in self.ranking[:budget]])

    def test_oversize_budget_explicitly_clamps(self):
        result = build_candidate_pool(self.ranking, self.catalog, 1000)
        self.assertEqual(result["candidate_budget"], 1000)
        self.assertEqual(result["candidate_count"], 60)
        self.assertEqual(result["budget_policy"], "CLAMP_TO_CATALOG_SIZE")

    def test_empty_catalog_and_ranking(self):
        self.assertEqual(build_candidate_pool([], [], 5)["candidates"], [])

    def test_duplicate_ranking(self):
        self.ranking.append(copy.deepcopy(self.ranking[0]))
        with self.assertRaisesRegex(ValueError, "DUPLICATE_RANKING"):
            build_candidate_pool(self.ranking, self.catalog, 5)

    def test_duplicate_catalog(self):
        self.catalog.append(copy.deepcopy(self.catalog[0]))
        with self.assertRaisesRegex(ValueError, "DUPLICATE_CATALOG"):
            build_candidate_pool(self.ranking, self.catalog, 5)

    def test_missing_ranked_method(self):
        with self.assertRaisesRegex(ValueError, "INCOMPLETE_RANKING"):
            build_candidate_pool(self.ranking[:-1], self.catalog, 5)

    def test_extra_ranked_method(self):
        self.ranking[-1]["method_id"] = "java-source-v1:fixture.Unknown#same(int)"
        with self.assertRaisesRegex(ValueError, "UNKNOWN_RANKING"):
            build_candidate_pool(self.ranking, self.catalog, 5)

    def test_same_name_different_classes_files_not_merged(self):
        result = build_candidate_pool(self.ranking, self.catalog, 100)
        self.assertEqual(len(result["candidates"]), 60)
        self.assertEqual({r["features"]["name"] for r in result["candidates"]}, {"same"})

    def test_overloads_and_nested_types_remain_distinct(self):
        ranking, catalog = fixture(3)
        for r, mid in zip(catalog, ["fixture.Outer.Inner#same(int)", "fixture.Outer.Inner#same(String)", "other.Other#same(int)"]):
            r["method_id"] = "java-source-v1:" + mid
        ranking = [{**{k: r[k] for k in MODULE.IDENTITY}, "score": 0.0} for r in catalog]
        result = build_candidate_pool(ranking, catalog, 5)
        self.assertEqual(len({r["method_id"] for r in result["candidates"]}), 3)

    def test_cross_snapshot_in_ranking(self):
        self.ranking[0]["source_snapshot_id"] = "sha256:" + "b" * 64
        with self.assertRaisesRegex(ValueError, "MIXED_REPO_OR_SNAPSHOT"):
            build_candidate_pool(self.ranking, self.catalog, 5)

    def test_cross_snapshot_in_catalog(self):
        self.catalog[-1]["source_snapshot_id"] = "sha256:" + "b" * 64
        with self.assertRaisesRegex(ValueError, "MIXED_REPO_OR_SNAPSHOT"):
            build_candidate_pool(self.ranking, self.catalog, 5)

    def test_cross_repo(self):
        self.ranking[-1]["repo_id"] = "fixture/other"
        with self.assertRaisesRegex(ValueError, "MIXED_REPO_OR_SNAPSHOT"):
            build_candidate_pool(self.ranking, self.catalog, 5)

    def test_features_associated_by_identity_not_position(self):
        result = build_candidate_pool(self.ranking, self.catalog, 20)
        for candidate in result["candidates"]:
            self.assertEqual(candidate["features"], self.catalog[candidate["ordinal"]]["features"])
            self.assertEqual(candidate["features"]["strings"], ["raw", "raw"])

    def test_bodyless_method_retained(self):
        result = build_candidate_pool(self.ranking, self.catalog, 1)
        self.assertFalse(result["candidates"][0]["has_body"])

    def test_invalid_budget(self):
        for budget in (0, -1, True, 5.0, "5", None):
            with self.subTest(budget=budget), self.assertRaisesRegex(ValueError, "INVALID_BUDGET"):
                build_candidate_pool(self.ranking, self.catalog, budget)

    def test_invalid_scores(self):
        for score in (True, "1", None, float("nan"), float("inf"), -float("inf"), 10 ** 1000):
            with self.subTest(score=str(score)[:20]), self.assertRaisesRegex(ValueError, "INVALID_SCORE"):
                ranking = copy.deepcopy(self.ranking); ranking[-1]["score"] = score
                build_candidate_pool(ranking, self.catalog, 5)

    def test_invalid_feature_types(self):
        for field, value in (("name", None), ("class", []), ("signature", 4), ("file", False),
                             ("annotations", "@X"), ("strings", [4]), ("calls", ("call",))):
            catalog = copy.deepcopy(self.catalog); catalog[-1]["features"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                build_candidate_pool(self.ranking, catalog, 5)

    def test_invalid_ordinal_and_body(self):
        for field, value in (("ordinal", True), ("ordinal", 0), ("has_body", 1)):
            catalog = copy.deepcopy(self.catalog); catalog[-1][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                build_candidate_pool(self.ranking, catalog, 5)

    def test_missing_fields(self):
        for key in (*MODULE.IDENTITY, "score"):
            ranking = copy.deepcopy(self.ranking); del ranking[-1][key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                build_candidate_pool(ranking, self.catalog, 5)

    def test_invalid_identity_fields(self):
        for key in MODULE.IDENTITY:
            for value in (None, "", " bad ", 123):
                ranking = copy.deepcopy(self.ranking); ranking[-1][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    build_candidate_pool(ranking, self.catalog, 5)

    def test_path_and_feature_file_mismatch(self):
        for value in ("/tmp/File.java", "../File.java", "C:/File.java", "src\\File.java", "a//File.java"):
            catalog = copy.deepcopy(self.catalog); catalog[0]["file_path"] = value
            with self.subTest(path=value), self.assertRaises(ValueError):
                build_candidate_pool(self.ranking, catalog, 5)
        self.catalog[0]["features"]["file"] = "other/File.java"
        with self.assertRaisesRegex(ValueError, "FEATURE_FILE_MISMATCH"):
            build_candidate_pool(self.ranking, self.catalog, 5)

    def test_old_method_name_key_rejected(self):
        self.catalog[0]["method_id"] = "Class#same"
        with self.assertRaisesRegex(ValueError, "INVALID_METHOD_ID_SCHEME"):
            build_candidate_pool(self.ranking, self.catalog, 5)

    def test_wrong_container_types_and_wrapper_rejected(self):
        for ranking, catalog in (({}, self.catalog), (self.ranking, None), ([None], []),
                                 (self.ranking, [None]), ({"status": "SUCCESS", "ranking": self.ranking}, self.catalog)):
            with self.subTest(), self.assertRaises(ValueError):
                build_candidate_pool(ranking, catalog, 5)

    def test_deterministic_json_and_unchanged_inputs(self):
        before = copy.deepcopy((self.ranking, self.catalog))
        one = build_candidate_pool(self.ranking, self.catalog, 20)
        two = build_candidate_pool(self.ranking, self.catalog, 20)
        self.assertEqual(json.dumps(one, allow_nan=False), json.dumps(two, allow_nan=False))
        self.assertEqual((self.ranking, self.catalog), before)
        one["candidates"][0]["features"]["strings"].append("changed")
        self.assertEqual((self.ranking, self.catalog), before)

    def test_ties_and_numeric_scores_preserved_without_sorting(self):
        for r in self.ranking:
            r["score"] = 0
        result = build_candidate_pool(self.ranking, self.catalog, 5)
        self.assertEqual([r["ordinal"] for r in result["candidates"]], [59, 58, 57, 56, 55])
        self.assertTrue(all(type(r["bm25_score"]) is int for r in result["candidates"]))

    def test_gt_unknown_fields_rejected_at_all_levels(self):
        for level in ("ranking", "catalog", "features"):
            ranking, catalog = copy.deepcopy((self.ranking, self.catalog))
            dest = ranking[-1] if level == "ranking" else catalog[-1] if level == "catalog" else catalog[-1]["features"]
            dest["accepted_targets"] = ["SYNTHETIC_ANSWER_SENTINEL"]
            with self.subTest(level=level), self.assertRaises(ValueError):
                build_candidate_pool(ranking, catalog, 5)

    def test_no_gt_fields_or_verified_claims(self):
        result = build_candidate_pool(self.ranking, self.catalog, 5)
        self.assertEqual(result["evidence_status"], "UNVERIFIED")
        for r in result["candidates"]:
            self.assertEqual(r["evidence_status"], "UNVERIFIED")
            self.assertEqual(r["verification_scope"], "NONE")
            self.assertEqual(r["feature_source"]["kind"], "INDEX_FEATURES")
            self.assertNotIn("gt", r); self.assertNotIn("accepted_targets", r)

    def test_function_does_not_read_files_or_call_network_or_retrieval(self):
        # Module imports only copy/math; scorer and experimental modules are absent.
        with mock.patch("builtins.open", side_effect=AssertionError("file access")), \
             mock.patch("pathlib.Path.open", side_effect=AssertionError("path access")), \
             mock.patch("socket.socket", side_effect=AssertionError("network")):
            self.assertEqual(build_candidate_pool(self.ranking, self.catalog, 5)["candidate_count"], 5)


if __name__ == "__main__":
    unittest.main()
