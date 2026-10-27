"""B03 synthetic in-memory inputs only. No LLM, real experiments or source reads."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


b03 = load("src/candidate_shortlist.py", "src.candidate_shortlist")
b02 = load("tests/test_candidate_evidence_adapter.py", "b03_synthetic_b02_helpers")
b01 = load("tests/test_high_recall_candidates.py", "b03_synthetic_b01_helpers")
build = b03.build_shortlist


class CandidateShortlistTest(unittest.TestCase):
    def setUp(self):
        self.pool, self.report = b02.fixture()
        self.association = b02.attach(self.pool, self.report)

    def rejected(self, text=None, **kwargs):
        with self.assertRaisesRegex(ValueError, text or ".+"):
            build(self.pool, self.association, **kwargs)

    def assert_unverified(self, result):
        self.assertEqual(result["source_binding"]["status"], "NOT_ATTESTED")
        self.assertEqual(result["issue_relevance_status"], "NOT_ASSESSED")
        self.assertFalse(result["verification_scope"]["independent_ast_parse"])
        self.assertFalse(result["verification_scope"]["source_validation_performed"])
        for c in result["candidates"]:
            self.assertEqual(c["evidence_status"], "UNVERIFIED")
            self.assertEqual(c["feature_source"]["kind"], "INDEX_FEATURES")
            ea = c["evidence_association"]
            self.assertEqual(ea["status"], "NOT_ATTESTED")
            self.assertEqual(ea["source_provenance_status"], "UNVERIFIABLE")
            self.assertEqual(ea["issue_relevance_status"], "NOT_ASSESSED")

    def test_normal_default(self):
        result = build(self.pool, self.association)
        self.assertEqual(result["schema_version"], "candidate-shortlist-v1")
        self.assertEqual(result["requested_shortlist_size"], 10)
        self.assertEqual(result["shortlist_count"], 3)
        self.assertEqual(result["selection_policy"], "BM25_PREFIX_ONLY")
        self.assert_unverified(result)

    def test_bm25_order_ranks_scores_and_identity_preserved(self):
        result = build(self.pool, self.association, shortlist_size=2)
        for before, after in zip(self.pool["candidates"], result["candidates"]):
            for k in (*b03.IDENTITY, "bm25_rank", "bm25_score", "ordinal", "has_body"):
                self.assertEqual(before[k], after[k])
                self.assertIs(type(before[k]), type(after[k]))

    def test_no_evidence_count_reranking(self):
        result = build(self.pool, self.association, shortlist_size=1)
        self.assertEqual(result["candidates"][0]["method_id"], self.pool["candidates"][0]["method_id"])
        self.assertEqual(result["candidates"][0]["evidence_association"]["possible_references"], [])

    def test_budget_boundary(self):
        for size, count in ((1, 1), (2, 2), (3, 3), (50, 3), (100, 3)):
            with self.subTest(size=size):
                self.assertEqual(build(self.pool, self.association, shortlist_size=size)["shortlist_count"], count)

    def test_invalid_budget(self):
        for size in (0, -1, True, 1.0, "10", None):
            with self.subTest(size=size): self.rejected("INVALID_SHORTLIST_SIZE", shortlist_size=size)

    def test_candidate_hard_cap_is_explicit(self):
        ranking, catalog = b01.fixture(80)
        pool = b01.build_candidate_pool(ranking, catalog, 100)
        association = b02.attach(pool, self.report)
        result = build(pool, association, shortlist_size=100)
        self.assertEqual(result["shortlist_count"], 50)
        self.assertEqual(result["candidates_omitted"], 30)
        self.assertTrue(result["candidate_limit_applied"])
        self.assertEqual([r["method_id"] for r in result["candidates"]], [r["method_id"] for r in ranking[:50]])

    def test_empty_valid_pool(self):
        pool = b01.build_candidate_pool([], [], 5)
        result = build(pool, b02.attach(pool, self.report))
        self.assertEqual(result["shortlist_count"], 0)
        self.assertEqual(result["candidates"], [])

    def test_duplicate_candidate_identity_rejected(self):
        self.pool["candidates"][1].update({k: self.pool["candidates"][0][k] for k in b03.IDENTITY})
        self.rejected()

    def test_cross_repo_association_rejected(self):
        self.association["candidates"][0]["repo_id"] = "fixture/other"
        self.rejected("MISMATCH")

    def test_cross_snapshot_association_rejected(self):
        self.association["candidates"][0]["source_snapshot_id"] = "sha256:" + "f" * 64
        self.rejected("MISMATCH")

    def test_missing_candidate_rejected(self):
        self.association["candidates"].pop()
        self.rejected("MISSING_OR_EXTRA_CANDIDATES")

    def test_extra_candidate_rejected(self):
        self.association["candidates"].append(copy.deepcopy(self.association["candidates"][0]))
        self.rejected("MISSING_OR_EXTRA_CANDIDATES")

    def test_reordered_association_rejected(self):
        self.association["candidates"].reverse()
        self.rejected("MISMATCH")

    def test_association_feature_conflict_rejected(self):
        self.association["candidates"][0]["features"]["name"] = "changed"
        self.rejected("MISMATCH")

    def test_score_type_tamper_not_equal_via_python_boolean(self):
        self.association["candidates"][1]["bm25_score"] = True
        self.rejected("MISMATCH")

    def test_report_rejected_is_not_silently_repaired(self):
        self.association = b02.attach(self.pool, None)
        self.rejected("REJECTED_OR_INVALID_REPORT")

    def test_truncated_association_rejected(self):
        del self.association["verification_scope"]
        self.rejected("INVALID_ASSOCIATION_SCHEMA")

    def test_report_fail_can_only_pass_as_unattested_data(self):
        self.report.update(overall_verdict="FAIL", exit_code=1)
        association = b02.attach(self.pool, self.report)
        result = build(self.pool, association)
        self.assertEqual(result["report_check"]["status"], "NOT_ELIGIBLE")
        self.assert_unverified(result)

    def test_evidence_upgrade_rejected(self):
        self.association["candidates"][1]["evidence_association"]["source_provenance_status"] = "VERIFIED"
        self.rejected("INVALID_OR_UPGRADED")

    def test_reference_upgrade_rejected(self):
        self.association["candidates"][1]["evidence_association"]["possible_references"][0]["association_status"] = "VERIFIED"
        self.rejected("INVALID_REFERENCE_STATE")

    def test_root_cause_claim_rejected(self):
        self.association["candidates"][0]["evidence_association"]["issue_relevance_status"] = "ROOT_CAUSE_CONFIRMED"
        self.rejected("INVALID_OR_UPGRADED")

    def test_self_reported_verified_remains_only_a_report_claim(self):
        result = build(self.pool, self.association)
        ref = result["candidates"][1]["evidence_association"]["possible_references"][0]
        self.assertEqual(ref["reported_status"], "VERIFIED")
        self.assertEqual(ref["association_status"], "NOT_ATTESTED")
        self.assertEqual(ref["source_provenance_status"], "UNVERIFIABLE")
        self.assert_unverified(result)

    def test_array_and_text_truncation(self):
        for c in self.pool["candidates"]:
            for field in ("annotations", "strings", "calls"):
                c["features"][field] = ["X" * 300] * 12
            for field in ("name", "class", "signature"):
                c["features"][field] = "Y" * 300
        result = build(self.pool, b02.attach(self.pool, self.report))
        c = result["candidates"][0]
        for field in ("annotations", "strings", "calls"):
            self.assertEqual(len(c["features"][field]), 8)
            self.assertEqual(len(c["features"][field][0]), 256)
            self.assertEqual(c["truncation"]["features"][field]["omitted_count"], 4)
        for field in ("name", "class", "signature"):
            self.assertEqual(len(c["features"][field]), 256)
            self.assertTrue(c["truncation"]["features"][field]["truncated"])

    def test_exact_text_boundary_not_truncated(self):
        self.pool["candidates"][0]["features"]["name"] = "a" * 256
        result = build(self.pool, b02.attach(self.pool, self.report))
        self.assertFalse(result["candidates"][0]["truncation"]["features"]["name"]["truncated"])

    def test_unicode_codepoints_not_utf8_bytes(self):
        text = "中文😀e\u0301" * 100
        self.pool["candidates"][0]["features"]["strings"] = [text]
        result = build(self.pool, b02.attach(self.pool, self.report))
        value = result["candidates"][0]["features"]["strings"][0]
        self.assertEqual(value, text[:256])
        self.assertEqual(len(value), 256)
        self.assertIn("😀", value)
        json.dumps(result, ensure_ascii=False, allow_nan=False)

    def test_reference_limit_preserves_original_ids_ranges_and_omits_basis(self):
        original = self.report["evidence_results"][1]
        for i in range(1, 6):
            ref = copy.deepcopy(original)
            ref.update(evidence_id="fixture-extra-" + str(i),
                       record_pointer="/0/classes/0/methods/1/evidence_occurrences/" + str(i))
            self.report["evidence_results"].append(ref)
        b02.summarize(self.report)
        association = b02.attach(self.pool, self.report)
        result = build(self.pool, association)
        c = result["candidates"][1]
        before = association["candidates"][1]["evidence_association"]["possible_references"]
        after = c["evidence_association"]["possible_references"]
        self.assertEqual(len(after), 3)
        for a, b in zip(before, after):
            for k in ("record_pointer", "evidence_id", "range", "file_path", "method_id"):
                self.assertEqual(a[k], b[k])
            self.assertNotIn("basis", b)
        self.assertEqual(c["truncation"]["evidence_references"]["omitted_count"], 3)

    def test_identity_not_silently_truncated(self):
        c = self.pool["candidates"][0]
        c["method_id"] += "x" * 1100
        c["feature_source"]["identity"]["method_id"] = c["method_id"]
        self.association = b02.attach(self.pool, self.report)
        self.rejected("OVERLONG_OR_INVALID_IDENTITY")

    def test_same_input_same_complete_json(self):
        self.assertEqual(json.dumps(build(self.pool, self.association), allow_nan=False),
                         json.dumps(build(self.pool, self.association), allow_nan=False))

    def test_input_immutability_and_detached_output(self):
        before = copy.deepcopy((self.pool, self.association))
        result = build(self.pool, self.association)
        result["candidates"][1]["features"]["strings"].append("mutate")
        result["candidates"][1]["evidence_association"]["possible_references"][0]["range"]["start_line"] = 900
        self.assertEqual((self.pool, self.association), before)

    def test_gt_fields_at_all_levels_rejected(self):
        for level in ("pool", "association", "candidate", "features", "evidence", "reference", "basis"):
            self.setUp()
            c = self.association["candidates"][1]
            ref = c["evidence_association"]["possible_references"][0]
            dest = {"pool": self.pool, "association": self.association, "candidate": c, "features": c["features"],
                    "evidence": c["evidence_association"], "reference": ref, "basis": ref["basis"]}[level]
            dest["accepted_targets"] = ["SYNTHETIC_GT_SENTINEL"]
            with self.subTest(level=level): self.rejected()

    def test_no_files_network_or_external_calls(self):
        with mock.patch("builtins.open", side_effect=AssertionError("file")), \
             mock.patch("pathlib.Path.open", side_effect=AssertionError("path")), \
             mock.patch("socket.socket", side_effect=AssertionError("network")):
            self.assertEqual(build(self.pool, self.association)["shortlist_count"], 3)

    def test_bad_reference_owner_rejected(self):
        ref = self.association["candidates"][1]["evidence_association"]["possible_references"][0]
        ref["file_path"] = "src/main/java/fixture/Other.java"
        self.rejected("OWNERSHIP_MISMATCH")

    def test_duplicate_evidence_id_rejected(self):
        a = self.association["candidates"][1]["evidence_association"]["possible_references"][0]
        b = self.association["candidates"][2]["evidence_association"]["possible_references"][0]
        b["evidence_id"] = a["evidence_id"]
        self.rejected("DUPLICATE_EVIDENCE")

    def test_pointer_ownership_conflict_rejected(self):
        a = self.association["candidates"][1]["evidence_association"]["possible_references"][0]
        a["record_pointer"] = "/0/classes/0/methods/0/evidence_occurrences/1"
        self.rejected("POINTER_OWNERSHIP")

    def test_conflicting_method_id_across_files_rejected(self):
        c = self.pool["candidates"][0]
        c["method_id"] = self.pool["candidates"][1]["method_id"]
        c["feature_source"]["identity"]["method_id"] = c["method_id"]
        self.association = b02.attach(self.pool, self.report)
        self.rejected("CONFLICTING_METHOD_IDENTITY")

    def test_invalid_data_beyond_shortlist_is_still_rejected(self):
        self.association["candidates"][-1]["evidence_association"]["possible_references"][0]["range"]["start_line"] = True
        self.rejected("INVALID_REFERENCE_RANGE", shortlist_size=1)

    def test_malformed_association_types(self):
        for value in (None, [], "file.json", {}, {**self.association, "candidates": None}):
            with self.subTest(value=type(value).__name__), self.assertRaises(ValueError):
                build(self.pool, value)

    def test_invalid_feature_types(self):
        self.pool["candidates"][0]["features"]["strings"] = "not a list"
        self.rejected()

    def test_nonfinite_score_rejected(self):
        self.pool["candidates"][0]["bm25_score"] = float("nan")
        self.rejected()

    def test_limits_returned_as_detached_data(self):
        result = build(self.pool, self.association)
        result["limits"]["max_candidates"] = 10000
        self.assertEqual(build(self.pool, self.association)["limits"]["max_candidates"], 50)


if __name__ == "__main__": unittest.main()
