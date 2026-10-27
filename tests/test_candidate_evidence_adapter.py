"""Synthetic, explicitly UNATTESTED report claims; no real data or validation runs."""
from collections import Counter
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "src" / (name + ".py"))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


b01 = load("high_recall_candidates")
adapter = load("candidate_evidence_adapter")
attach = adapter.attach_candidate_evidence


def summarize(report):
    counts = Counter(e["status"] for e in report["evidence_results"])
    report["summary"] = dict(files_total=len(report["file_results"]), methods_total=len(report["method_results"]),
        evidence_total=len(report["evidence_results"]), verified=counts.get("VERIFIED", 0),
        unverifiable=counts.get("UNVERIFIABLE", 0),
        failed=sum(n for s, n in counts.items() if s not in {"VERIFIED", "UNVERIFIABLE"}),
        evidence_status_counts=dict(sorted(counts.items())), blocking_issues=len(report["issues"]))


def fixture():
    file0 = "src/main/java/fixture/Outer.java"; file1 = "src/main/java/fixture/Nested.java"
    mids = ["java-source-v1:fixture.Outer#same(int)", "java-source-v1:fixture.Outer#same(String)",
            "java-source-v1:fixture.Nested.Inner#same(int)"]
    catalog = []
    for i in range(3):
        path = file0 if i < 2 else file1
        catalog.append(dict(repo_id="fixture/repo", source_snapshot_id="sha256:" + "a" * 64,
            file_path=path, method_id=mids[i], ordinal=i, has_body=i != 2,
            features={"name": "same", "class": "Outer" if i < 2 else "Inner", "signature": mids[i].split("#")[1],
                      "file": path, "annotations": [], "strings": ["fixture text"], "calls": ["check"]}))
    ranking = [{**{k: c[k] for k in b01.IDENTITY}, "score": float(i)} for i, c in enumerate(catalog)]
    ranking.reverse(); pool = b01.build_candidate_pool(ranking, catalog, 5)
    methods = [dict(record_pointer="/0/classes/0/methods/0", method_id=mids[0], status="CHECKED"),
               dict(record_pointer="/0/classes/0/methods/1", method_id=mids[1], status="CHECKED"),
               dict(record_pointer="/1/classes/0/methods/0", method_id=mids[2], status="NO_EVIDENCE")]
    evidence = []
    for i in range(2):
        evidence.append(dict(record_pointer=methods[i]["record_pointer"] + "/evidence_occurrences/0",
            evidence_id="fixture-evidence-" + str(i), method_id=mids[i], evidence_type="METHOD_CALL", file_path=file0,
            status="VERIFIED", reason_code="ALL_SOURCE_CHECKS_PASSED",
            range=dict(start_line=10 + i, start_column=1, end_line=10 + i, end_column=7),
            basis=dict(expected_evidence_id="fixture-evidence-" + str(i), computed_evidence_id="fixture-evidence-" + str(i),
                       expected_source_sha256="b" * 64, actual_source_sha256="b" * 64,
                       expected_snippet_sha256="c" * 64, actual_snippet_sha256="c" * 64,
                       extracted_source_snippet="check()", checks_passed=["EXACT_SNIPPET"])))
    # These are self-reported fixture claims, NOT authentic validation proofs.
    report = dict(report_schema_version="1.0", validator_version="1.0",
        verification_scope=dict(checks=["INDEX_BINDING"], independent_ast_parse=False,
            evidence_type_origin="TASK001_JAVAPARSER_INDEX_DECLARATION",
            method_identity_scope="DECLARED_SIGNATURE_CONSISTENCY_NOT_SEMANTIC_RESOLUTION"),
        report_status="COMPLETE", overall_verdict="PASS", exit_code=0,
        inputs=dict(repo_root="UNTRUSTED_FIXTURE_PATH", index_path="UNTRUSTED_FIXTURE_INDEX", metadata_path="UNTRUSTED_FIXTURE_META",
                    index_sha256="d" * 64, metadata_sha256="e" * 64, index_schema_version="2.0", position_convention="1-based-inclusive-UTF16-tab1"),
        index_integrity=dict(status="PASS", expected_sha256="d" * 64, actual_sha256="d" * 64),
        file_results=[dict(record_pointer="/0", file_path=file0, status="SOURCE_MATCH"),
                      dict(record_pointer="/1", file_path=file1, status="SOURCE_MATCH")],
        method_results=methods, evidence_results=evidence, issues=[])
    summarize(report)
    return pool, report


class CandidateEvidenceAdapterTest(unittest.TestCase):
    def setUp(self):
        self.pool, self.report = fixture()

    def assert_unattested(self, result):
        self.assertEqual(result["source_binding"]["status"], "NOT_ATTESTED")
        self.assertFalse(result["verification_scope"]["independent_ast_parse"])
        for c in result["candidates"]:
            self.assertEqual(c["evidence_status"], "UNVERIFIED")
            self.assertEqual(c["evidence_association"]["source_provenance_status"], "UNVERIFIABLE")
            self.assertEqual(c["evidence_association"]["issue_relevance_status"], "NOT_ASSESSED")
            for r in c["evidence_association"]["possible_references"]:
                self.assertEqual(r["association_status"], "NOT_ATTESTED")
                self.assertEqual(r["source_provenance_status"], "UNVERIFIABLE")

    def rejected(self, reason=None):
        result = attach(self.pool, self.report)
        self.assertEqual(result["report_check"]["status"], "REJECTED")
        if reason:
            self.assertIn(reason, result["report_check"]["reason_code"])
        self.assertTrue(all(not c["evidence_association"]["possible_references"] for c in result["candidates"]))
        self.assert_unattested(result)

    def test_normal_association_is_explicitly_only_possible_reference(self):
        result = attach(self.pool, self.report)
        self.assertEqual(result["schema_version"], "candidate-evidence-associations-v1")
        self.assertEqual(result["report_check"]["status"], "CONSISTENT_SUCCESS_CLAIM")
        refs = result["candidates"][1]["evidence_association"]["possible_references"]
        self.assertEqual(refs[0]["evidence_id"], "fixture-evidence-1")
        for k in ("record_pointer", "range", "basis", "evidence_type"):
            self.assertEqual(refs[0][k], self.report["evidence_results"][1][k])
        self.assert_unattested(result)

    def test_no_matching_evidence_retains_candidate(self):
        result = attach(self.pool, self.report)
        self.assertEqual(result["candidate_count"], 3)
        a = result["candidates"][0]["evidence_association"]
        self.assertEqual(a["reason_code"], "NO_MATCHING_REPORT_REFERENCE")
        self.assertEqual(a["possible_references"], [])

    def test_same_name_overloads_and_nested_type_not_confused(self):
        result = attach(self.pool, self.report)
        counts = [len(c["evidence_association"]["possible_references"]) for c in result["candidates"]]
        self.assertEqual(counts, [0, 1, 1])
        for c in result["candidates"]:
            for r in c["evidence_association"]["possible_references"]:
                self.assertEqual(r["method_id"], c["method_id"])
                self.assertEqual(r["file_path"], c["file_path"])

    def test_cross_repo_cannot_be_inferred_from_report(self):
        for c in self.pool["candidates"]:
            c["repo_id"] = "another/fixture"
            c["feature_source"]["identity"]["repo_id"] = c["repo_id"]
        self.assert_unattested(attach(self.pool, self.report))

    def test_cross_snapshot_cannot_be_inferred_from_report(self):
        for c in self.pool["candidates"]:
            c["source_snapshot_id"] = "sha256:" + "f" * 64
            c["feature_source"]["identity"]["source_snapshot_id"] = c["source_snapshot_id"]
        self.assert_unattested(attach(self.pool, self.report))

    def test_mixed_snapshot_pool_rejected(self):
        self.pool["candidates"][0]["source_snapshot_id"] = "other"
        with self.assertRaises(ValueError): attach(self.pool, self.report)

    def test_duplicate_evidence_id_rejected(self):
        self.report["evidence_results"][1]["evidence_id"] = self.report["evidence_results"][0]["evidence_id"]
        self.rejected("DUPLICATE_EVIDENCE")

    def test_duplicate_pointer_rejected(self):
        self.report["evidence_results"][1]["record_pointer"] = self.report["evidence_results"][0]["record_pointer"]
        self.rejected("DUPLICATE_EVIDENCE")

    def test_file_ownership_mismatch_rejected(self):
        self.report["evidence_results"][0]["file_path"] = "src/main/java/fixture/Other.java"
        self.rejected("OWNERSHIP")

    def test_method_ownership_mismatch_rejected(self):
        self.report["evidence_results"][0]["method_id"] = self.report["method_results"][1]["method_id"]
        self.rejected("OWNERSHIP")

    def test_conflicting_method_identity_rejected(self):
        self.report["method_results"][1]["method_id"] = self.report["method_results"][0]["method_id"]
        self.rejected("CONFLICTING_METHOD")

    def test_unknown_method_pointer_rejected(self):
        self.report["evidence_results"][0]["record_pointer"] = "/0/classes/0/methods/99/evidence_occurrences/0"
        self.rejected("OWNERSHIP")

    def test_illegal_pointer_rejected(self):
        self.report["evidence_results"][0]["record_pointer"] = "/../private"
        self.rejected("INVALID_EVIDENCE")

    def test_report_fail_not_eligible(self):
        self.report.update(overall_verdict="FAIL", exit_code=1)
        result = attach(self.pool, self.report)
        self.assertEqual(result["report_check"]["status"], "NOT_ELIGIBLE")
        self.assert_unattested(result)

    def test_aborted_not_eligible(self):
        self.report.update(report_status="ABORTED", exit_code=2)
        result = attach(self.pool, self.report)
        self.assertEqual(result["report_check"]["status"], "NOT_ELIGIBLE")
        self.assert_unattested(result)

    def test_any_issue_blocks_success_claim(self):
        self.report["issues"] = [dict(stage="SOURCE", status="UNVERIFIABLE", reason_code="FIXTURE_ISSUE", detail="fixture", record_pointer="/0")]
        summarize(self.report)
        result = attach(self.pool, self.report)
        self.assertEqual(result["report_check"]["status"], "NOT_ELIGIBLE")
        self.assert_unattested(result)

    def test_unverifiable_single_row_not_upgraded(self):
        self.report["evidence_results"][0].update(status="UNVERIFIABLE", reason_code="PRECONDITION_FAILED")
        summarize(self.report)
        result = attach(self.pool, self.report)
        self.assertEqual(result["report_check"]["status"], "NOT_ELIGIBLE")
        self.assert_unattested(result)

    def test_missing_report_is_structured_failure(self):
        self.report = None
        self.rejected("MISSING_REPORT")

    def test_self_claimed_pass_and_matching_hashes_are_not_attestation(self):
        result = attach(self.pool, self.report)
        self.assert_unattested(result)
        self.assertFalse(result["verification_scope"]["source_validation_performed"])

    def test_no_trust_boolean_or_binding_dict_parameter(self):
        with self.assertRaises(TypeError):
            attach(self.pool, self.report, trusted=True)
        with self.assertRaises(TypeError):
            attach(self.pool, self.report, source_binding={"verified": True})

    def test_empty_evidence_preserves_all_candidates(self):
        self.report["evidence_results"] = []; summarize(self.report)
        result = attach(self.pool, self.report)
        self.assertEqual(len(result["candidates"]), 3)
        self.assertTrue(all(not c["evidence_association"]["possible_references"] for c in result["candidates"]))
        self.assert_unattested(result)

    def test_inputs_unchanged_and_output_detached(self):
        before = copy.deepcopy((self.pool, self.report))
        result = attach(self.pool, self.report)
        result["candidates"][1]["features"]["calls"].append("mutate")
        result["candidates"][1]["evidence_association"]["possible_references"][0]["basis"]["checks_passed"].append("mutate")
        self.assertEqual((self.pool, self.report), before)

    def test_deterministic_json(self):
        self.assertEqual(json.dumps(attach(self.pool, self.report), allow_nan=False),
                         json.dumps(attach(self.pool, self.report), allow_nan=False))

    def test_no_file_network_scorer_or_validator_invocation(self):
        with mock.patch("builtins.open", side_effect=AssertionError("file")), \
             mock.patch("pathlib.Path.open", side_effect=AssertionError("path")), \
             mock.patch("socket.socket", side_effect=AssertionError("network")):
            self.assert_unattested(attach(self.pool, self.report))

    def test_no_gt_or_internal_report_paths_in_output(self):
        self.report["inputs"]["untrusted_gt_path"] = "SYNTHETIC_SECRET_GT_PATH"
        text = json.dumps(attach(self.pool, self.report))
        self.assertNotIn("SYNTHETIC_SECRET_GT_PATH", text)
        self.assertNotIn("UNTRUSTED_FIXTURE_PATH", text)
        self.assertNotIn("accepted_targets", text)

    def test_original_identity_order_score_and_features_preserved(self):
        result = attach(self.pool, self.report)
        for old, new in zip(self.pool["candidates"], result["candidates"]):
            self.assertEqual({k: new[k] for k in old}, old)

    def test_not_root_cause_or_independent_ast(self):
        result = attach(self.pool, self.report)
        self.assert_unattested(result)
        self.assertNotIn("confidence", json.dumps(result))
        self.assertNotIn("ROOT_CAUSE_CONFIRMED", json.dumps(result))

    def test_truncated_report_rejected(self):
        del self.report["method_results"]
        self.rejected("INVALID_REPORT_SCHEMA")

    def test_truncated_evidence_counts_rejected(self):
        self.report["evidence_results"].pop()
        self.rejected("COUNTS_MISMATCH")

    def test_integrity_mismatch_and_exit_error_not_eligible(self):
        for field in ("index_integrity", "exit_code"):
            pool, report = fixture()
            if field == "exit_code": report[field] = 1
            else: report[field]["actual_sha256"] = "f" * 64
            result = attach(pool, report)
            self.assertEqual(result["report_check"]["status"], "NOT_ELIGIBLE")
            self.assert_unattested(result)

    def test_forged_independent_ast_claim_rejected(self):
        self.report["verification_scope"]["independent_ast_parse"] = True
        self.rejected("VERIFICATION_SCOPE")

    def test_illegal_basis_fields_rejected(self):
        self.report["evidence_results"][0]["basis"]["gt"] = "SYNTHETIC_SECRET"
        self.rejected("BASIS_FIELDS")

    def test_unavailable_range_remains_unverified(self):
        self.report["evidence_results"][0]["range"]["start_column"] = None
        result = attach(self.pool, self.report)
        self.assertEqual(result["report_check"]["status"], "NOT_ELIGIBLE")
        self.assert_unattested(result)

    def test_invalid_range_and_basis_types_rejected(self):
        for key in ("range", "basis"):
            self.pool, self.report = fixture()
            if key == "range": self.report["evidence_results"][0][key]["start_line"] = True
            else: self.report["evidence_results"][0][key]["checks_passed"] = "CHECK"
            self.rejected()

    def test_malformed_report_types_fail_closed(self):
        for report in ({}, [], "truncated json", {**self.report, "report_status": []},
                       {**self.report, "exit_code": False}):
            self.report = report
            self.rejected()

    def test_invalid_pool_rejected_instead_of_silent_repair(self):
        self.pool["candidates"][0]["bm25_rank"] = 99
        with self.assertRaises(ValueError): attach(self.pool, self.report)

    def test_gt_fields_in_pool_rejected(self):
        self.pool["accepted_targets"] = ["SYNTHETIC_SECRET"]
        with self.assertRaises(ValueError): attach(self.pool, self.report)

    def test_empty_pool_supported(self):
        result = attach(b01.build_candidate_pool([], [], 5), self.report)
        self.assertEqual(result["candidates"], [])
        self.assert_unattested(result)


if __name__ == "__main__": unittest.main()
