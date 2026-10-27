"""INT01: public B01 -> B02 -> B03 -> B04A, synthetic memory data only.

The report below is deliberately a self-reported fixture claim, NOT evidence of
an actual Validator execution, source snapshot, signed receipt or attestation.
No scorer/model is run, no real data is loaded and no GT decides selection.
"""
from collections import Counter
import copy
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import unittest
from unittest import mock

from src.high_recall_candidates import build_candidate_pool
from src.candidate_evidence_adapter import attach_candidate_evidence
from src.candidate_shortlist import build_shortlist
from src.llm_rerank_protocol import build_llm_request, validate_llm_response

IDENTITY = ("repo_id", "source_snapshot_id", "file_path", "method_id")
ISSUE = "Synthetic issue: fictional leaf serialization behaves unexpectedly.\n合成材料😀，不是实际缺陷报告。\n"
REASON = "Relative synthetic feature judgment only; no source or root cause is proven."


def identity(record):
    return tuple(record[k] for k in IDENTITY)


def update_summary(report):
    counts = Counter(e["status"] for e in report["evidence_results"])
    report["summary"] = dict(files_total=len(report["file_results"]), methods_total=len(report["method_results"]),
        evidence_total=len(report["evidence_results"]), verified=counts.get("VERIFIED", 0),
        unverifiable=counts.get("UNVERIFIABLE", 0),
        failed=sum(n for s, n in counts.items() if s not in {"VERIFIED", "UNVERIFIABLE"}),
        evidence_status_counts=dict(sorted(counts.items())), blocking_issues=len(report["issues"]))


def synthetic_inputs():
    """24 fictional declarations, including overloads and a nested type."""
    definitions = [("Alpha", "Alpha.java", "inspect", "inspect(int)"),
                   ("Alpha", "Alpha.java", "inspect", "inspect(String)"),
                   ("Beta", "Beta.java", "inspect", "inspect(int)"),
                   ("Outer.Inner", "Outer.java", "inspect", "inspect(int)")]
    definitions += [("Type%d" % i, "Type%d.java" % i, "process", "process(int)") for i in range(4, 24)]
    catalog = []; files = []; methods = []; evidence = []
    file_indices = {}; method_counts = {}
    for i, (cls, filename, name, signature) in enumerate(definitions):
        path = "src/main/java/fiction/" + filename
        if path not in file_indices:
            file_indices[path] = len(files)
            files.append(dict(record_pointer="/" + str(file_indices[path]), file_path=path, status="SOURCE_MATCH"))
        fi = file_indices[path]; mi = method_counts.get(path, 0); method_counts[path] = mi + 1
        mid = "java-source-v1:fiction." + cls + "#" + signature
        has_body = i % 5 != 0
        catalog.append(dict(repo_id="fictional/pipeline-fixture", source_snapshot_id="sha256:" + "f" * 64,
            file_path=path, method_id=mid, ordinal=i, has_body=has_body,
            features={"name": name, "class": cls.split(".")[-1], "signature": signature, "file": path,
                      "annotations": ["@Synthetic"], "strings": ["合成模式😀", "fixture leaf"], "calls": ["recordLeaf"]}))
        mp = "/%d/classes/0/methods/%d" % (fi, mi)
        methods.append(dict(record_pointer=mp, method_id=mid, status="CHECKED" if has_body else "NO_EVIDENCE"))
        if has_body:
            eid = "synthetic-unattested-occurrence-" + str(i)
            evidence.append(dict(record_pointer=mp + "/evidence_occurrences/0", evidence_id=eid, method_id=mid,
                evidence_type=("ANNOTATION", "STRING_LITERAL", "METHOD_CALL")[i % 3], file_path=path,
                status="VERIFIED", reason_code="ALL_SOURCE_CHECKS_PASSED",
                range=dict(start_line=10 + i, start_column=1, end_line=10 + i, end_column=12),
                basis=dict(expected_evidence_id=eid, computed_evidence_id=eid,
                    expected_source_sha256="b" * 64, actual_source_sha256="b" * 64,
                    expected_snippet_sha256="c" * 64, actual_snippet_sha256="c" * 64,
                    extracted_source_snippet="fictional();", checks_passed=["EXACT_SNIPPET"])))
    # Existing complete ranking is constructed, not retrieved/scored. Prefix 0..3
    # exercises overloads; remaining methods use deterministic finite tied scores.
    ranking = [{**{k: c[k] for k in IDENTITY}, "score": float(30 - i if i < 4 else (i * 7) % 12)}
               for i, c in enumerate(catalog)]
    ordinal = {identity(c): c["ordinal"] for c in catalog}
    ranking.sort(key=lambda r: (-r["score"], ordinal[identity(r)]))
    report = dict(report_schema_version="1.0", validator_version="1.0",
        verification_scope=dict(checks=["INDEX_BINDING", "EXACT_SNIPPET"], independent_ast_parse=False,
            evidence_type_origin="TASK001_JAVAPARSER_INDEX_DECLARATION",
            method_identity_scope="DECLARED_SIGNATURE_CONSISTENCY_NOT_SEMANTIC_RESOLUTION"),
        report_status="COMPLETE", overall_verdict="PASS", exit_code=0,
        inputs=dict(repo_root="IN_MEMORY_FIXTURE_ONLY", index_path="NOT_A_REAL_INDEX", metadata_path="NOT_A_REAL_METADATA",
                    index_sha256="d" * 64, metadata_sha256="e" * 64,
                    index_schema_version="2.0", position_convention="1-based-inclusive-UTF16-tab1"),
        index_integrity=dict(status="PASS", expected_sha256="d" * 64, actual_sha256="d" * 64),
        file_results=files, method_results=methods, evidence_results=evidence, issues=[])
    update_summary(report)
    return ranking, catalog, report


class CandidatePipelineIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.ranking, self.catalog, self.report = synthetic_inputs()

    def stage(self, name, function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            self.fail("%s interface failed: %s: %s" % (name, type(exc).__name__, exc))

    def pipeline(self, *, budget=20, shortlist_size=10, response=None):
        pool = self.stage("B01 build_candidate_pool", build_candidate_pool, self.ranking, self.catalog, budget)
        association = self.stage("B02 attach_candidate_evidence", attach_candidate_evidence, pool, self.report)
        self.assertNotEqual(association["report_check"]["status"], "REJECTED", "B02 rejected synthetic report shape")
        shortlist = self.stage("B03 build_shortlist", build_shortlist, pool, association, shortlist_size=shortlist_size)
        request = self.stage("B04A build_llm_request", build_llm_request, ISSUE, shortlist)
        if response is None:
            response = json.dumps(dict(ranking=list(range(len(shortlist["candidates"]), 0, -1)), reason=REASON))
        result = self.stage("B04A validate_llm_response", validate_llm_response, request, response)
        return dict(pool=pool, association=association, shortlist=shortlist, request=request, result=result)

    def assert_boundaries(self, trace):
        self.assertEqual(trace["pool"]["evidence_status"], "UNVERIFIED")
        for label in ("pool", "association", "shortlist", "result"):
            for c in trace[label]["candidates"]:
                self.assertEqual(c["feature_source"]["kind"], "INDEX_FEATURES", label)
                self.assertEqual(c["evidence_status"], "UNVERIFIED", label)
                if label != "pool":
                    ea = c["evidence_association"]
                    self.assertEqual(ea["status"], "NOT_ATTESTED", label)
                    self.assertEqual(ea["source_provenance_status"], "UNVERIFIABLE", label)
                    self.assertEqual(ea["issue_relevance_status"], "NOT_ASSESSED", label)
                    for ref in ea["possible_references"]:
                        self.assertEqual(ref["association_status"], "NOT_ATTESTED", label)
                        self.assertEqual(ref["source_provenance_status"], "UNVERIFIABLE", label)
        for row in trace["request"]["candidate_map"]:
            self.assertEqual(row["candidate"]["evidence_status"], "UNVERIFIED")
            self.assertEqual(row["candidate"]["evidence_association"]["source_provenance_status"], "UNVERIFIABLE")
        self.assertEqual(trace["result"]["model_reason"]["status"], "MODEL_GENERATED_UNVERIFIED")
        self.assertEqual(trace["result"]["issue_relevance_status"], "NOT_ASSESSED")

    def assert_invalid_response(self, raw):
        trace = self.pipeline(response=raw)
        result = trace["result"]
        self.assertEqual(result["status"], "INVALID_RESPONSE", "B04A must reject simulated malformed output")
        self.assertFalse(result["reranked"])
        self.assertFalse(result["fallback_applied"])
        self.assertEqual(result["ranking"], [])
        self.assertEqual(result["candidates"], [])
        self.assertIsNone(result["model_reason"])

    def test_complete_public_chain_24_to_20_to_10(self):
        trace = self.pipeline()
        self.assertEqual(len(self.catalog), 24)
        self.assertEqual(trace["pool"]["candidate_count"], 20, "B01 budget")
        self.assertEqual(trace["association"]["candidate_count"], 20, "B02 preservation")
        self.assertEqual(trace["shortlist"]["shortlist_count"], 10, "B03 shortlist")
        self.assertEqual(len(trace["request"]["candidate_map"]), 10, "B04A map")
        self.assertEqual(trace["result"]["status"], "SUCCESS")
        self.assertEqual(trace["result"]["reason_code"], "PROTOCOL_VALID_ONLY")

    def test_b01_b02_b03_preserve_bm25_prefix(self):
        trace = self.pipeline()
        for label, count in (("pool", 20), ("association", 20), ("shortlist", 10)):
            self.assertEqual([identity(c) for c in trace[label]["candidates"]],
                             [identity(r) for r in self.ranking[:count]], label)

    def test_original_scores_and_ranks_unchanged_through_every_stage(self):
        trace = self.pipeline()
        original = {identity(r): (rank, r["score"]) for rank, r in enumerate(self.ranking, 1)}
        groups = [trace[k]["candidates"] for k in ("pool", "association", "shortlist", "result")]
        groups.append([r["candidate"] for r in trace["request"]["candidate_map"]])
        for group in groups:
            for c in group:
                self.assertEqual((c["bm25_rank"], c["bm25_score"]), original[identity(c)])
                self.assertIs(type(c["bm25_score"]), float)

    def test_b02_references_use_exact_method_and_file(self):
        trace = self.pipeline()
        by_key = {(e["file_path"], e["method_id"]): e for e in self.report["evidence_results"]}
        for c in trace["association"]["candidates"]:
            expected = by_key.get((c["file_path"], c["method_id"]))
            refs = c["evidence_association"]["possible_references"]
            self.assertEqual(len(refs), 1 if expected else 0)
            if expected:
                for k in ("record_pointer", "evidence_id", "method_id", "file_path", "range", "basis"):
                    self.assertEqual(refs[0][k], expected[k], k)

    def test_overloads_same_name_other_classes_and_nested_type_not_merged(self):
        trace = self.pipeline()
        candidates = trace["shortlist"]["candidates"][:4]
        self.assertEqual([c["features"]["name"] for c in candidates], ["inspect"] * 4)
        self.assertEqual(len({identity(c) for c in candidates}), 4)
        self.assertNotEqual(candidates[0]["method_id"], candidates[1]["method_id"])
        self.assertEqual(candidates[0]["file_path"], candidates[1]["file_path"])
        self.assertNotEqual(candidates[1]["file_path"], candidates[2]["file_path"])
        self.assertIn("Outer.Inner#", candidates[3]["method_id"])

    def test_bodyless_method_without_evidence_stays_first(self):
        trace = self.pipeline()
        for label in ("pool", "association", "shortlist"):
            self.assertFalse(trace[label]["candidates"][0]["has_body"])
        self.assertEqual(trace["shortlist"]["candidates"][0]["evidence_association"]["possible_references"], [])

    def test_self_reported_pass_and_verified_never_attest_source(self):
        self.assertEqual(self.report["overall_verdict"], "PASS")
        self.assertTrue(all(e["status"] == "VERIFIED" for e in self.report["evidence_results"]))
        trace = self.pipeline()
        self.assertEqual(trace["association"]["report_check"]["status"], "CONSISTENT_SUCCESS_CLAIM")
        self.assertEqual(trace["request"]["shortlist"]["source_binding"]["status"], "NOT_ATTESTED")
        self.assert_boundaries(trace)

    def test_stable_candidate_ids_follow_shortlist_order(self):
        trace = self.pipeline()
        self.assertEqual([r["candidate_id"] for r in trace["request"]["candidate_map"]], list(range(1, 11)))
        for row, original in zip(trace["request"]["candidate_map"], self.ranking[:10]):
            self.assertEqual(identity(row["candidate"]), identity(original))

    def test_valid_nontrivial_complete_permutation_maps_back_exactly(self):
        permutation = [3, 1, 10, 6, 2, 8, 4, 9, 5, 7]
        trace = self.pipeline(response=json.dumps(dict(ranking=permutation, reason=REASON)))
        result = trace["result"]
        self.assertEqual(result["ranking"], permutation)
        for rank, (cid, c) in enumerate(zip(permutation, result["candidates"]), 1):
            original = trace["shortlist"]["candidates"][cid - 1]
            self.assertEqual({k: c[k] for k in original}, original)
            self.assertEqual(c["llm_candidate_id"], cid)
            self.assertEqual(c["llm_rank"], rank)

    def test_duplicate_model_number_fails_without_fallback(self):
        self.assert_invalid_response(json.dumps(dict(ranking=[1] * 10, reason=REASON)))

    def test_missing_model_number_fails_without_fallback(self):
        self.assert_invalid_response(json.dumps(dict(ranking=list(range(1, 10)), reason=REASON)))

    def test_truncated_candidate_cannot_be_reintroduced_by_number(self):
        self.assert_invalid_response(json.dumps(dict(ranking=list(range(1, 10)) + [11], reason=REASON)))

    def test_boolean_model_number_fails(self):
        self.assert_invalid_response(json.dumps(dict(ranking=[True] + list(range(2, 11)), reason=REASON)))

    def test_illegal_json_fails(self):
        self.assert_invalid_response('{"ranking": [1,2,3], "reason":')

    def test_extra_response_field_fails(self):
        self.assert_invalid_response(json.dumps(dict(ranking=list(range(1, 11)), reason=REASON, extra="not allowed")))

    def test_forged_evidence_and_root_cause_structured_claims_fail(self):
        for field, value in (("source_provenance_status", "VERIFIED"), ("evidence_id", "invented"),
                             ("root_cause_confirmed", True), ("confidence", 1.0)):
            raw = dict(ranking=list(range(1, 11)), reason=REASON); raw[field] = value
            with self.subTest(field=field): self.assert_invalid_response(json.dumps(raw))

    def test_response_cannot_return_truncated_method_identity(self):
        excluded = self.ranking[10]
        raw = dict(ranking=list(range(1, 11)), reason=REASON, method_id=excluded["method_id"])
        self.assert_invalid_response(json.dumps(raw))

    def test_duplicate_response_json_keys_fail(self):
        ranking = json.dumps(list(range(1, 11)))
        self.assert_invalid_response('{"ranking":' + ranking + ',"reason":"one","reason":"two"}')

    def test_budget_larger_than_shortlist_and_full_directory(self):
        trace = self.pipeline(budget=100)
        self.assertEqual(trace["pool"]["candidate_count"], 24)
        self.assertEqual(trace["pool"]["candidate_budget"], 100)
        self.assertEqual(trace["shortlist"]["shortlist_count"], 10)

    def test_smaller_candidate_pool_has_complete_smaller_permutation(self):
        trace = self.pipeline(budget=5)
        self.assertEqual(trace["pool"]["candidate_count"], 5)
        self.assertEqual(trace["shortlist"]["shortlist_count"], 5)
        self.assertEqual(trace["result"]["ranking"], [5, 4, 3, 2, 1])

    def test_b04a_rejects_eleven_candidates_from_valid_upstream_outputs(self):
        pool = build_candidate_pool(self.ranking, self.catalog, 20)
        association = attach_candidate_evidence(pool, self.report)
        shortlist = build_shortlist(pool, association, shortlist_size=11)
        self.assertEqual(shortlist["shortlist_count"], 11)
        with self.assertRaisesRegex(ValueError, "EMPTY_OR_OVERSIZE_SHORTLIST", msg="B04A capacity must reject, not truncate"):
            build_llm_request(ISSUE, shortlist)

    def test_excluded_candidates_absent_from_request_and_result_identity_sets(self):
        trace = self.pipeline()
        selected = {identity(r) for r in self.ranking[:10]}
        excluded = {identity(r) for r in self.ranking[10:]}
        mapped = {identity(r["candidate"]) for r in trace["request"]["candidate_map"]}
        returned = {identity(c) for c in trace["result"]["candidates"]}
        self.assertEqual(mapped, selected)
        self.assertEqual(returned, selected)
        self.assertFalse(mapped & excluded)
        self.assertFalse(returned & excluded)

    def test_tied_bm25_scores_keep_existing_input_order(self):
        trace = self.pipeline()
        tied = [r for r in self.ranking if r["score"] == 11.0]
        self.assertGreater(len(tied), 1)
        positions = {identity(c): i for i, c in enumerate(trace["shortlist"]["candidates"])}
        self.assertLess(positions[identity(tied[0])], positions[identity(tied[1])])

    def test_unicode_and_feature_truncation_survive_request_validation(self):
        self.catalog[0]["features"]["strings"] = ["中文😀" * 150] * 12
        trace = self.pipeline()
        c = trace["shortlist"]["candidates"][0]
        self.assertEqual(len(c["features"]["strings"]), 8)
        self.assertEqual(len(c["features"]["strings"][0]), 256)
        self.assertEqual(c["truncation"]["features"]["strings"]["omitted_count"], 4)
        self.assertEqual(trace["request"]["candidate_map"][0]["candidate"], c)
        self.assertEqual(trace["result"]["status"], "SUCCESS")

    def test_deterministic_entire_pipeline_json(self):
        self.assertEqual(json.dumps(self.pipeline(), ensure_ascii=False, sort_keys=True, allow_nan=False),
                         json.dumps(self.pipeline(), ensure_ascii=False, sort_keys=True, allow_nan=False))

    def test_all_original_and_intermediate_inputs_are_unchanged(self):
        original = copy.deepcopy((self.ranking, self.catalog, self.report))
        pool = build_candidate_pool(self.ranking, self.catalog, 20); pool_before = copy.deepcopy(pool)
        association = attach_candidate_evidence(pool, self.report); association_before = copy.deepcopy(association)
        shortlist = build_shortlist(pool, association); shortlist_before = copy.deepcopy(shortlist)
        request = build_llm_request(ISSUE, shortlist); request_before = copy.deepcopy(request)
        raw = json.dumps(dict(ranking=list(range(10, 0, -1)), reason=REASON))
        result = validate_llm_response(request, raw)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual((self.ranking, self.catalog, self.report), original)
        self.assertEqual(pool, pool_before); self.assertEqual(association, association_before)
        self.assertEqual(shortlist, shortlist_before); self.assertEqual(request, request_before)
        result["candidates"][0]["features"]["calls"].append("mutated-result")
        self.assertEqual(request, request_before)
        self.assertEqual(shortlist, shortlist_before)

    def test_entire_pipeline_without_files_network_processes_or_api_key_reads(self):
        with mock.patch("builtins.open", side_effect=AssertionError("file I/O forbidden")), \
             mock.patch.object(io, "open", side_effect=AssertionError("io.open forbidden")), \
             mock.patch.object(Path, "open", side_effect=AssertionError("path I/O forbidden")), \
             mock.patch.object(Path, "read_text", side_effect=AssertionError("read_text forbidden")), \
             mock.patch.object(Path, "read_bytes", side_effect=AssertionError("read_bytes forbidden")), \
             mock.patch.object(Path, "write_text", side_effect=AssertionError("write_text forbidden")), \
             mock.patch.object(Path, "write_bytes", side_effect=AssertionError("write_bytes forbidden")), \
             mock.patch.object(socket, "socket", side_effect=AssertionError("network forbidden")), \
             mock.patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")), \
             mock.patch.object(subprocess, "run", side_effect=AssertionError("external process forbidden")), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("external process forbidden")), \
             mock.patch.object(os, "getenv", side_effect=AssertionError("environment forbidden")), \
             mock.patch.object(type(os.environ), "__getitem__", side_effect=AssertionError("API key/environment read forbidden")):
            ranking, catalog, report = synthetic_inputs()
            pool = build_candidate_pool(ranking, catalog, 20)
            association = attach_candidate_evidence(pool, report)
            shortlist = build_shortlist(pool, association)
            request = build_llm_request(ISSUE, shortlist)
            result = validate_llm_response(request, json.dumps(dict(ranking=list(range(10, 0, -1)), reason=REASON)))
            self.assertEqual(result["status"], "SUCCESS")

    def test_no_gt_annotations_or_api_credentials_in_fixtures_or_dataflow(self):
        forbidden = {"gt", "ground_truth", "accepted_targets", "sidecar", "benchmark", "gt_path", "gt_sha256", "api_key"}
        def inspect(value):
            if isinstance(value, dict):
                self.assertFalse(set(value) & forbidden)
                for v in value.values(): inspect(v)
            elif isinstance(value, list):
                for v in value: inspect(v)
        inspect([self.ranking, self.catalog, self.report, self.pipeline()])

    def test_report_fail_can_only_flow_as_unattested_data(self):
        self.report.update(overall_verdict="FAIL", exit_code=1)
        trace = self.pipeline()
        self.assertEqual(trace["association"]["report_check"]["status"], "NOT_ELIGIBLE")
        self.assertEqual(trace["result"]["status"], "SUCCESS")
        self.assert_boundaries(trace)

    def test_missing_report_stops_at_b03_without_model_request(self):
        pool = build_candidate_pool(self.ranking, self.catalog, 20)
        association = attach_candidate_evidence(pool, None)
        self.assertEqual(association["report_check"]["status"], "REJECTED")
        with self.assertRaisesRegex(ValueError, "REJECTED_OR_INVALID_REPORT_ASSOCIATION", msg="B03 must reject missing report"):
            build_shortlist(pool, association)

    def test_duplicate_report_evidence_stops_at_b02_b03_boundary(self):
        self.report["evidence_results"][1]["evidence_id"] = self.report["evidence_results"][0]["evidence_id"]
        pool = build_candidate_pool(self.ranking, self.catalog, 20)
        association = attach_candidate_evidence(pool, self.report)
        self.assertEqual(association["report_check"]["status"], "REJECTED")
        self.assertIn("DUPLICATE_EVIDENCE", association["report_check"]["reason_code"])
        with self.assertRaises(ValueError, msg="B03 cannot consume B02 rejected report"):
            build_shortlist(pool, association)

    def test_cross_snapshot_ranking_stops_at_b01(self):
        self.ranking[0]["source_snapshot_id"] = "sha256:" + "a" * 64
        with self.assertRaisesRegex(ValueError, "MIXED_REPO_OR_SNAPSHOT", msg="B01 must reject mixed snapshots"):
            build_candidate_pool(self.ranking, self.catalog, 20)

    def test_empty_pool_is_retained_until_b04a_explicit_rejection(self):
        pool = build_candidate_pool([], [], 20)
        association = attach_candidate_evidence(pool, self.report)
        shortlist = build_shortlist(pool, association)
        self.assertEqual(shortlist["candidates"], [])
        with self.assertRaisesRegex(ValueError, "EMPTY_OR_OVERSIZE_SHORTLIST", msg="B04A must reject zero candidates"):
            build_llm_request(ISSUE, shortlist)

    def test_request_tampering_causes_explicit_invalid_request(self):
        trace = self.pipeline()
        request = copy.deepcopy(trace["request"])
        request["candidate_map"][0]["candidate"]["method_id"] = "java-source-v1:fiction.Other#invented()"
        result = validate_llm_response(request, json.dumps(dict(ranking=list(range(1, 11)), reason=REASON)))
        self.assertEqual(result["status"], "INVALID_REQUEST")
        self.assertFalse(result["reranked"])
        self.assertFalse(result["fallback_applied"])
        self.assertEqual(result["candidates"], [])


if __name__ == "__main__": unittest.main()
