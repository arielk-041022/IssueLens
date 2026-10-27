"""Synthetic confirmed GT exists only in temp fixtures, never in the six real cases."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("strict_eval_test_module", ROOT / "src/strict_evaluation.py")
se = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(se)


class StrictEvaluationTest(unittest.TestCase):
    def setUp(self):
        base = os.environ.get("TASK003_TEST_ROOT")
        if base:
            Path(base).mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="case-", dir=base))
        (self.temp / "test-id.txt").write_text(self.id(), encoding="utf-8")
        self.serial = 0
        self.repo = self.temp / "repo"
        self.index_path = self.temp / "index.v2.json"
        self.bench_path = self.temp / "benchmark.json"
        self.sidecar_path = self.temp / "gt.json"
        self.repo_id = "fixture-repo"
        self.ev = se.load_local("evidence_validator")
        self.index = []
        self.add_source("p", "A", [("f", "int x", "int"), ("f", "String x", "String"), ("g", "", "")])
        self.benchmark = [{"id": "fixture-case", "issue_text": "find f", "repo_path": str(self.repo),
                           "ground_truth_files": [self.index[0]["file"]], "ground_truth_methods": ["f"]}]
        self.sidecar = None
        self.refresh()

    def add_source(self, package, cls, methods):
        rel = "src/main/java/" + package.replace(".", "/") + "/" + cls + ".java"
        lines = ["package " + package + ";", "class " + cls + " {"]
        lines += ['  void ' + name + '(' + params + ') { sink("x"); }' for name, params, sig in methods]
        lines.append("}")
        raw = ("\n".join(lines) + "\n").encode()
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        sh = hashlib.sha256(raw).hexdigest()
        ms = []
        for i, (name, params, sig) in enumerate(methods):
            line = i + 3
            signature = name + "(" + sig + ")"
            mid = "java-source-v1:" + package + "." + cls + "#" + signature
            col = lines[i + 2].index("sink") + 1
            coords = [line, col, line, col + len('sink("x")') - 1]
            occurrence = dict(evidence_id=self.ev.compute_evidence_id(rel, sh, mid, "METHOD_CALL", coords),
                              evidence_type="METHOD_CALL", file_path=rel, source_snippet='sink("x")',
                              **dict(zip(self.ev.COORDS, coords)))
            ms.append(dict(name=name, line=line, annotations=[], strings=["x"], calls=["sink"],
                method_id=mid, signature=signature, qualified_class_name=package + "." + cls,
                identity_status="AVAILABLE", location_status="AVAILABLE", has_body=True,
                start_line=line, start_column=3, end_line=line, end_column=len(lines[i + 2]),
                evidence_omissions=0, evidence_occurrences=[occurrence]))
        self.index.append(dict(file=rel, package=package, classes=[dict(name=cls, methods=ms)],
                               source_sha256=sh, parse_status="PARSED", parse_problems=[], limitations=[]))

    def refresh(self):
        self.index_path.write_bytes(se.json_bytes(self.index))
        ms = [m for f in self.index for c in f["classes"] for m in c["methods"]]
        conflicts = sorted({"java-source-v1:" + m["qualified_class_name"] + "#" + m["signature"]
                            for m in ms if m.get("identity_status") == "CONFLICT"})
        meta = dict(schema_version="2.0", index_file=self.index_path.name,
                    index_sha256=se.digest(self.index_path.read_bytes()), parser_version="3.26.4",
                    method_id_scheme="java-source-v1", position_convention=self.ev.POSITION,
                    source_encoding="UTF-8", language_level="BLEEDING_EDGE", preprocess_unicode_escapes=False,
                    source_roots=["src/main/java"], files_total=len(self.index), methods_total=len(ms),
                    evidence_occurrences=sum(len(m.get("evidence_occurrences", [])) for m in ms),
                    identity_unavailable_methods=sum(m.get("identity_status") != "AVAILABLE" for m in ms),
                    location_unavailable_methods=sum(m.get("location_status") != "AVAILABLE" for m in ms),
                    identity_conflict_groups=len(conflicts), conflicting_method_ids=conflicts,
                    identity_conflict_methods=sum(m.get("identity_status") == "CONFLICT" for m in ms),
                    parse_counts={s: sum(f.get("parse_status") == s for f in self.index) for s in self.ev.PARSE_STATES})
        Path(str(self.index_path) + ".meta.json").write_bytes(se.json_bytes(meta))
        self.bench_path.write_bytes(se.json_bytes(self.benchmark))
        manifest = se.manifest_for(self.index)
        self.sid = "sha256:" + se.manifest_hash(manifest)
        snapshot = dict(repo_id=self.repo_id, source_snapshot_id=self.sid, phase="PRE_FIX",
                        source_manifest=manifest, source_manifest_sha256=se.manifest_hash(manifest),
                        index_sha256=se.digest(self.index_path.read_bytes()),
                        metadata_sha256=se.digest(Path(str(self.index_path) + ".meta.json").read_bytes()))
        if self.sidecar is None:
            target = dict(file_path=self.index[0]["file"], method_id=self.index[0]["classes"][0]["methods"][0]["method_id"])
            self.sidecar = dict(schema_version="1.0", protocol=dict(version="strict-localization-v1",
                evaluation_task="PRE_FIX_EXISTING_METHOD_LOCALIZATION", primary_hit_policy="ANY_CONFIRMED_TARGET",
                identity_scheme="java-source-v1"),
                cases=[dict(case_id="fixture-case", repo_id=self.repo_id, source_snapshot_id=self.sid,
                    annotation_status="CONFIRMED", target_set_status="FROZEN", file_gt_status="CONFIRMED",
                    accepted_targets=[target], proposed_targets=[], accepted_files=[self.index[0]["file"]],
                    basis=[dict(kind="HUMAN_REVIEW", reference="synthetic-fixture-only", sha256="a" * 64)],
                    review=dict(reviewer="unit-test-fixture", reviewed_at="fixture-only"),
                    required_groups=[], reason_codes=[])])
        self.sidecar.update(benchmark_sha256=se.digest(self.bench_path.read_bytes()), snapshots=[snapshot])
        for case in self.sidecar["cases"]:
            case["source_snapshot_id"] = self.sid
        self.save_sidecar()

    def save_sidecar(self):
        self.sidecar_path.write_bytes(se.json_bytes(self.sidecar))

    def catalog(self):
        return se.build_method_catalog(self.index, self.repo_id, self.sid)

    def target(self):
        t = self.sidecar["cases"][0]["accepted_targets"][0]
        return se.identity_key(se.identity(self.repo_id, self.sid, t["file_path"], t["method_id"]))

    def artifact(self, order=None, status="NOT_REQUESTED", reranked=None, scope="FULL", limit=None):
        keys = list(self.catalog()) if order is None else order
        rows = [dict(case_id=c["id"], repo_id=self.repo_id, source_snapshot_id=self.sid,
                     retrieval_ranking=[dict(zip(se.IDENTITY_FIELDS, k)) for k in keys], candidate_k=5,
                     rerank=dict(status=status, ranking=[] if reranked is None else
                                  [dict(zip(se.IDENTITY_FIELDS, k)) for k in reranked])) for c in self.benchmark]
        return dict(schema_version="strict-ranking-v1", benchmark_sha256=se.digest(self.bench_path.read_bytes()),
                    gt_sidecar_sha256=se.digest(self.sidecar_path.read_bytes()),
                    scoring_code_sha256=se.digest((ROOT / "src/retriever.py").read_bytes()), feature_profile="AS_IS_V2",
                    ranking_scope=scope, rank_limit=limit,
                    snapshots=[{k: s[k] for k in ("repo_id", "source_snapshot_id", "index_sha256", "metadata_sha256")}
                               for s in self.sidecar["snapshots"]], cases=rows)

    def run_eval(self, mode="audit", artifact=None, stage="rule"):
        rankings = None
        if mode == "replay":
            rankings = self.temp / "rankings.json"
            rankings.write_bytes(se.json_bytes(artifact if artifact is not None else self.artifact()))
        report = se.run_evaluation(self.bench_path, self.sidecar_path, {self.repo_id: str(self.repo)},
                                  {self.repo_id: str(self.index_path)}, mode, rankings, stage,
                                  runtime_root=self.temp / "runtime")
        self.serial += 1
        (self.temp / ("report-%03d.json" % self.serial)).write_bytes(se.json_bytes(report))
        return report

    def test_valid_audit_freezes_denominator_without_ranking(self):
        r = self.run_eval()
        self.assertEqual(0, r["exit_code"])
        self.assertEqual(dict(total=1, method=1, file=1, frozen_before_ranking=True), r["denominators"])
        self.assertIsNone(r["metrics"]["strict_method_top1"]["value"])

    def test_same_name_wrong_file_is_not_method_hit(self):
        self.add_source("p", "B", [("f", "int x", "int")]); self.refresh()
        wrong = next(k for k in self.catalog() if k[2].endswith("B.java"))
        r = self.run_eval("replay", self.artifact([wrong, self.target()]))
        self.assertEqual(0, r["metrics"]["strict_method_top1"]["value"])
        self.assertEqual(0, r["metrics"]["strict_file_top1"]["value"])
        self.assertEqual(0.5, r["metrics"]["MRR"]["value"])

    def test_overload_requires_exact_parameter_signature(self):
        wrong = next(k for k in self.catalog() if k[3].endswith("#f(String)"))
        r = self.run_eval("replay", self.artifact([wrong, self.target()]))
        self.assertEqual(0, r["metrics"]["strict_method_top1"]["value"])
        self.assertEqual(1, r["metrics"]["strict_file_top1"]["value"])

    def test_same_name_different_package(self):
        self.add_source("q", "A", [("f", "int x", "int")]); self.refresh()
        wrong = next(k for k in self.catalog() if k[2].startswith("src/main/java/q/"))
        r = self.run_eval("replay", self.artifact([wrong]))
        self.assertEqual(0, r["metrics"]["strict_method_top5"]["value"])

    def test_nested_type_identity_is_distinct(self):
        original = self.index[0]["classes"][0]["methods"][0]
        nested = copy.deepcopy(original)
        nested.update(qualified_class_name="p.A.Inner", method_id="java-source-v1:p.A.Inner#f(int)")
        self.index[0]["classes"].append(dict(name="Inner", methods=[nested]))
        catalog = self.catalog()
        self.assertEqual(4, len(catalog))
        other = next(k for k in catalog if "A.Inner#" in k[3])
        self.assertFalse(se.evaluate_order([other], [self.target()], [])['strict_method_top1'])

    def test_path_substring_and_case_are_not_hits(self):
        good = self.target()
        for path in ("src/main/java/p/prefixA.java", good[2] + ".backup", good[2].lower()):
            wrong = (good[0], good[1], path, good[3])
            self.assertFalse(se.evaluate_order([wrong], [good], [good[2]])["strict_method_top1"])
            self.assertFalse(se.evaluate_order([wrong], [good], [good[2]])["strict_file_top1"])

    def test_unknown_candidate_is_invalid_not_name_upgraded(self):
        artifact = self.artifact()
        artifact["cases"][0]["retrieval_ranking"][0]["method_id"] = "A#f"
        r = self.run_eval("replay", artifact)
        self.assertEqual(2, r["exit_code"])
        self.assertEqual(1, r["denominators"]["method"])
        self.assertIsNone(r["metrics"]["strict_method_top1"]["value"])

    def test_legacy_index_cannot_be_auto_upgraded(self):
        old = copy.deepcopy(self.index)
        for c in old[0]["classes"]:
            for m in c["methods"]:
                del m["method_id"]
        with self.assertRaisesRegex(se.ProtocolError, "LEGACY_OR_MISSING"):
            se.build_method_catalog(old, self.repo_id, self.sid)

    def test_duplicate_method_identity_even_across_files_is_rejected(self):
        duplicate = copy.deepcopy(self.index[0]); duplicate["file"] = "src/main/java/p/B.java"
        with self.assertRaisesRegex(se.ProtocolError, "CONFLICT"):
            se.build_method_catalog([self.index[0], duplicate], self.repo_id, self.sid)

    def test_unavailable_identity_is_rejected(self):
        self.index[0]["classes"][0]["methods"][0]["identity_status"] = "CONFLICT"
        with self.assertRaisesRegex(se.ProtocolError, "UNAVAILABLE_OR_CONFLICT"):
            self.catalog()

    def test_any_confirmed_target_and_required_group_coverage(self):
        gt = self.sidecar["cases"][0]
        second = next(k for k in self.catalog() if k[3].endswith("#g()"))
        new = dict(file_path=second[2], method_id=second[3])
        first = copy.deepcopy(gt["accepted_targets"][0])
        gt["accepted_targets"].append(new)
        gt["required_groups"] = [dict(group_id="branch-a", targets=[first]), dict(group_id="branch-b", targets=[new])]
        self.save_sidecar()
        r = self.run_eval("replay", self.artifact([second]))
        self.assertEqual(1, r["metrics"]["strict_method_top1"]["value"])
        self.assertEqual(0.5, r["cases"][0]["method_metrics"]["required_group_coverage_at5"])

    def test_pending_gt_and_proposals_never_contribute_hits(self):
        gt = self.sidecar["cases"][0]
        gt.update(annotation_status="PENDING_REVIEW", file_gt_status="PENDING_REVIEW", target_set_status="NOT_FROZEN",
                  proposed_targets=copy.deepcopy(gt["accepted_targets"]), accepted_targets=[], accepted_files=[])
        self.save_sidecar()
        r = self.run_eval("replay")
        self.assertEqual(0, r["denominators"]["method"])
        self.assertEqual(1, r["metrics"]["pending_gt_cases"])
        self.assertEqual("NOT_EVALUABLE", r["evaluation_status"])
        self.assertIsNone(r["metrics"]["strict_method_top1"]["value"])
        self.assertIsNone(r["cases"][0]["method_metrics"])

    def test_confirmation_requires_review_basis_and_nonempty_target_set(self):
        self.sidecar["cases"][0]["review"] = dict(reviewer=None, reviewed_at=None)
        self.save_sidecar()
        r = self.run_eval()
        self.assertEqual(0, r["denominators"]["method"])
        self.assertIn("INDEPENDENT_REVIEW_BASIS_MISSING", r["cases"][0]["method_reasons"])
        self.sidecar["cases"][0]["accepted_targets"] = []
        self.save_sidecar()
        self.assertIn("NO_CONFIRMED_TARGETS", self.run_eval()["cases"][0]["method_reasons"])

    def test_fixed_reference_cannot_be_pre_fix_experiment(self):
        self.sidecar["snapshots"][0]["phase"] = "FIXED_REFERENCE"
        self.save_sidecar()
        r = self.run_eval()
        self.assertEqual(0, r["denominators"]["method"])
        self.assertIn("SNAPSHOT_PHASE_MISMATCH", r["cases"][0]["method_reasons"])

    def test_explicit_reference_task_is_a_different_protocol_target(self):
        self.sidecar["snapshots"][0]["phase"] = "FIXED_REFERENCE"
        self.sidecar["protocol"]["evaluation_task"] = "REFERENCE_LOCATION_RETRIEVAL"
        self.save_sidecar()
        self.assertEqual(1, self.run_eval()["denominators"]["method"])

    def test_gt_absent_from_snapshot_is_not_a_retrieval_miss(self):
        self.sidecar["cases"][0]["accepted_targets"][0]["method_id"] = "java-source-v1:p.A#absent()"
        self.save_sidecar()
        r = self.run_eval("replay")
        self.assertEqual(0, r["denominators"]["method"])
        self.assertIn("GT_NOT_IN_SNAPSHOT_CATALOG", r["cases"][0]["method_reasons"])

    def test_source_changed_and_head_label_cannot_override_manifest(self):
        (self.repo / self.index[0]["file"]).write_bytes(b"changed source")
        self.sidecar["snapshots"][0]["git_observation"] = {"head": "a" * 40}
        self.save_sidecar()
        r = self.run_eval()
        self.assertEqual(2, r["exit_code"])
        self.assertEqual("INVALID_RUN", r["evaluation_status"])
        self.assertEqual(0, r["denominators"]["method"])

    def test_empty_candidates_remain_in_denominator_as_miss(self):
        r = self.run_eval("replay", self.artifact([]))
        self.assertEqual(0, r["exit_code"])
        self.assertEqual(1, r["denominators"]["method"])
        self.assertEqual(0, r["metrics"]["MRR"]["value"])
        self.assertEqual("RETRIEVAL_CANDIDATE_MISS", r["cases"][0]["failure_class"])

    def test_duplicate_candidates_invalidate_run_without_shrinking_denominator(self):
        r = self.run_eval("replay", self.artifact([self.target(), self.target()]))
        self.assertEqual(2, r["exit_code"])
        self.assertEqual(1, r["denominators"]["method"])
        self.assertEqual("INVALID_RANKING", r["cases"][0]["ranking_status"])
        self.assertIsNone(r["metrics"]["MRR"]["value"])

    def test_topk_and_mrr_math(self):
        target = self.target()
        wrong = [k for k in self.catalog() if k != target]
        r = self.run_eval("replay", self.artifact(wrong + [target]))
        self.assertEqual(0, r["metrics"]["strict_method_top1"]["value"])
        self.assertEqual(1, r["metrics"]["strict_method_top5"]["value"])
        self.assertAlmostEqual(1 / 3, r["metrics"]["MRR"]["value"])
        pure = se.evaluate_order([("r", "s", "x", str(i)) for i in range(5)] + [target], [target], [])
        self.assertFalse(pure["strict_method_top5"])
        self.assertEqual(1 / 6, pure["reciprocal_rank"])

    def test_truncated_ranking_is_explicit_mrr_at_k(self):
        artifact = self.artifact(scope="TOP_K", limit=5)
        r = self.run_eval("replay", artifact)
        self.assertIn("MRR@5", r["metrics"])
        self.assertNotIn("MRR", r["metrics"])
        artifact["rank_limit"] = 3
        self.assertEqual(2, self.run_eval("replay", artifact)["exit_code"])

    def test_reranking_error_is_distinct_from_candidate_miss(self):
        wrong = next(k for k in self.catalog() if k != self.target())
        artifact = self.artifact([self.target(), wrong], "VALID", [wrong, self.target()])
        r = self.run_eval("replay", artifact, stage="rerank")
        self.assertEqual("RERANKING_ERROR", r["cases"][0]["failure_class"])
        artifact = self.artifact([wrong], "VALID", [wrong])
        r = self.run_eval("replay", artifact, stage="rerank")
        self.assertEqual("RETRIEVAL_CANDIDATE_MISS", r["cases"][0]["failure_class"])

    def test_invalid_llm_permutation_is_not_evaluated(self):
        order = list(self.catalog())
        for bad in ([order[0]], [order[0]] * len(order), order + [order[0]]):
            with self.subTest(bad=bad):
                r = self.run_eval("replay", self.artifact(order, "VALID", bad), stage="rerank")
                self.assertEqual(2, r["exit_code"])
                self.assertIsNone(r["metrics"]["strict_method_top1"]["value"])

    def test_fallback_dry_run_and_missing_rerank_are_not_llm_success(self):
        for status in ("FALLBACK", "DRY_RUN", "UNAVAILABLE", "NOT_REQUESTED"):
            with self.subTest(status=status):
                r = self.run_eval("replay", self.artifact(status=status), stage="rerank")
                self.assertEqual(1, r["denominators"]["method"])
                self.assertEqual("RERANK_UNAVAILABLE", r["cases"][0]["ranking_status"])
                self.assertIsNone(r["metrics"]["MRR"]["value"])

    def test_full_tail_is_retained_after_prefix_reranking(self):
        keys = list(self.catalog()); target = self.target()
        wrong = [k for k in keys if k != target]
        artifact = self.artifact(wrong + [target], "VALID", list(reversed(wrong)))
        artifact["cases"][0]["candidate_k"] = 2
        r = self.run_eval("replay", artifact, stage="rerank")
        self.assertAlmostEqual(1 / 3, r["metrics"]["MRR"]["value"])
        self.assertEqual("RETRIEVAL_CANDIDATE_MISS", r["cases"][0]["failure_class"])

    def test_denominator_frozen_before_reading_bad_rankings(self):
        with mock.patch.object(se, "_validate_artifact", side_effect=se.ProtocolError("injected bad ranking")):
            r = self.run_eval("replay")
        self.assertEqual(1, r["denominators"]["method"])
        self.assertTrue(r["denominators"]["frozen_before_ranking"])
        self.assertEqual(1, len(r["cases"]))

    def test_file_denominator_is_independent_of_method_confirmation(self):
        self.sidecar["cases"][0]["annotation_status"] = "PENDING_REVIEW"
        self.save_sidecar()
        r = self.run_eval("replay")
        self.assertEqual(0, r["denominators"]["method"])
        self.assertEqual(1, r["denominators"]["file"])
        self.assertEqual(1, r["metrics"]["strict_file_top1"]["value"])
        self.assertIsNone(r["metrics"]["strict_method_top1"]["value"])

    def test_all_cases_retained_with_partial_gt(self):
        second_b = copy.deepcopy(self.benchmark[0]); second_b["id"] = "fixture-pending"
        self.benchmark.append(second_b)
        second_gt = copy.deepcopy(self.sidecar["cases"][0])
        second_gt.update(case_id="fixture-pending", annotation_status="PENDING_REVIEW", file_gt_status="PENDING_REVIEW")
        self.sidecar["cases"].append(second_gt); self.refresh()
        r = self.run_eval("replay")
        self.assertEqual(2, len(r["cases"]))
        self.assertEqual(1, r["denominators"]["method"])
        self.assertEqual(dict(numerator=1, denominator=2, value=0.5), r["metrics"]["method_coverage"])

    def test_artifact_hashes_and_snapshot_ids_are_bound(self):
        artifact = self.artifact()
        for key in ("benchmark_sha256", "gt_sidecar_sha256", "scoring_code_sha256"):
            bad = copy.deepcopy(artifact); bad[key] = "0" * 64
            self.assertEqual(2, self.run_eval("replay", bad)["exit_code"])
        bad = copy.deepcopy(artifact)
        bad["cases"][0]["retrieval_ranking"][0]["source_snapshot_id"] = "wrong-snapshot"
        self.assertEqual(2, self.run_eval("replay", bad)["exit_code"])

    def test_missing_identity_and_nan_are_rejected(self):
        bad = self.artifact()
        del bad["cases"][0]["retrieval_ranking"][0]["method_id"]
        self.assertEqual(2, self.run_eval("replay", bad)["exit_code"])
        with self.assertRaises(se.ProtocolError):
            se.validate_order([dict(se.identity(*self.target()), score=float("nan"))], self.catalog(), self.repo_id, self.sid)

    def test_benchmark_binding_and_sidecar_coverage_fail_closed(self):
        self.bench_path.write_bytes(self.bench_path.read_bytes() + b" ")
        r = self.run_eval()
        self.assertEqual(2, r["exit_code"])
        self.assertEqual(1, len(r["cases"]))
        self.refresh()
        self.sidecar["cases"] = []; self.save_sidecar()
        self.assertEqual(2, self.run_eval()["exit_code"])

    def test_duplicate_case_ids_fail_closed(self):
        self.benchmark.append(copy.deepcopy(self.benchmark[0])); self.refresh()
        self.assertEqual(2, self.run_eval()["exit_code"])

    def test_invalid_json_has_structured_failure_report(self):
        self.sidecar_path.write_bytes(b'{"x":1,"x":2}')
        r = self.run_eval()
        self.assertEqual(2, r["exit_code"])
        self.assertEqual("COMPLETE", r["report_status"])
        self.assertIsNone(r["metrics"]["MRR"]["value"])

    def test_rule_uses_existing_scorer_functions(self):
        original_loader = se.load_local
        scorer = original_loader("retriever")
        score = mock.Mock(wraps=scorer.score_method)
        keyword = mock.Mock(wraps=scorer.extract_keywords)
        scorer.score_method = score; scorer.extract_keywords = keyword
        with mock.patch.object(se, "load_local", side_effect=lambda n: scorer if n == "retriever" else original_loader(n)):
            r = self.run_eval("rule")
        self.assertEqual(0, r["exit_code"])
        self.assertEqual(len(self.catalog()), score.call_count)
        keyword.assert_called_once_with(self.benchmark[0]["issue_text"])
        self.assertEqual("AS_IS_V2", r["ranking_artifact"]["feature_profile"])

    def test_rule_and_replay_preserve_identical_order_and_scores(self):
        rule = self.run_eval("rule")
        replay = self.run_eval("replay", rule["ranking_artifact"])
        self.assertEqual(rule["cases"][0]["method_metrics"], replay["cases"][0]["method_metrics"])
        self.assertEqual(rule["ranking_artifact"], replay["ranking_artifact"])

    def test_replay_cannot_import_ranker_or_call_network(self):
        original = se.load_local
        def loader(name):
            self.assertNotEqual("retriever", name)
            self.assertNotEqual("llm_reranker", name)
            return original(name)
        with mock.patch.object(se, "load_local", side_effect=loader):
            self.assertEqual(0, self.run_eval("replay")["exit_code"])

    def test_reports_are_deterministic_and_inputs_unchanged(self):
        paths = [self.bench_path, self.sidecar_path, self.index_path, Path(str(self.index_path) + ".meta.json")]
        before = {p: p.read_bytes() for p in paths}
        a, b = self.run_eval(), self.run_eval()
        self.assertEqual(se.json_bytes(a), se.json_bytes(b))
        self.assertEqual(before, {p: p.read_bytes() for p in paths})

    def test_exclusive_report_output_and_repository_protection(self):
        report = self.run_eval()
        out = self.temp / "out.json"
        se.write_report(out, report)
        original = out.read_bytes()
        with self.assertRaises(FileExistsError):
            se.write_report(out, report)
        self.assertEqual(original, out.read_bytes())
        with self.assertRaises(se.ProtocolError):
            se.write_report(self.bench_path, report, [self.bench_path])
        with self.assertRaises(se.ProtocolError):
            se.write_report(self.repo / "new.json", report, repo_paths=[self.repo])

    def test_real_six_case_sidecar_is_pending_and_original_benchmark_protected(self):
        bench = ROOT / "data/benchmark.json"
        self.assertEqual("ab3498d971c55419962c624ca7a36efa16da4d8bdef297867ff5d27e91650b90", se.digest(bench.read_bytes()))
        real_b, bh = se.read_json(bench)
        real_s, _ = se.read_json(ROOT / "data/ground_truth.strict.v1.json")
        se.validate_gt_sidecar(real_b, bh, real_s)
        self.assertEqual(6, len(real_s["cases"]))
        self.assertEqual({c["id"] for c in real_b}, {c["case_id"] for c in real_s["cases"]})
        self.assertTrue(all(c["annotation_status"] == "PENDING_REVIEW" and c["accepted_targets"] == []
                            and c["accepted_files"] == [] for c in real_s["cases"]))

    def test_no_legacy_ranking_name_list_conversion(self):
        r = self.run_eval("replay", {"ranking": ["A#f", "A#g"]})
        self.assertEqual(2, r["exit_code"])
        self.assertIn("LEGACY_OR_INVALID_RANKING_FORMAT", r["issues"][0]["detail"])

    def test_invalid_group_and_duplicate_targets_are_rejected(self):
        gt = self.sidecar["cases"][0]
        gt["accepted_targets"].append(copy.deepcopy(gt["accepted_targets"][0])); self.save_sidecar()
        self.assertEqual(2, self.run_eval()["exit_code"])

    def test_boolean_or_negative_candidate_k_is_rejected(self):
        for k in (True, -1, 0):
            artifact = self.artifact(); artifact["cases"][0]["candidate_k"] = k
            self.assertEqual(2, self.run_eval("replay", artifact)["exit_code"])

    def test_cli_writes_failure_report_and_exit_code(self):
        self.sidecar_path.write_bytes(b"not-json")
        out = self.temp / "cli.json"
        args = [sys.executable, "-B", str(ROOT / "src/strict_evaluation.py"), "--mode", "audit",
                "--benchmark", str(self.bench_path), "--gt-sidecar", str(self.sidecar_path), "--out", str(out)]
        p = subprocess.run(args, capture_output=True)
        (self.temp / "cli.stdout.log").write_bytes(p.stdout)
        (self.temp / "cli.stderr.log").write_bytes(p.stderr)
        self.assertEqual(2, p.returncode)
        self.assertEqual("INVALID_RUN", json.loads(out.read_bytes())["evaluation_status"])

    def test_new_unindexed_source_invalidates_snapshot(self):
        extra = self.repo / "src/main/java/p/New.java"
        extra.write_text("package p; class New {}", encoding="utf-8")
        r = self.run_eval()
        self.assertEqual(2, r["exit_code"])
        self.assertIn("SNAPSHOT_SOURCE_SET_MISMATCH", r["snapshot_results"][0]["reason"])

    def test_missing_ranking_case_is_not_silently_skipped(self):
        artifact = self.artifact(); artifact["cases"] = []
        r = self.run_eval("replay", artifact)
        self.assertEqual(2, r["exit_code"])
        self.assertEqual(1, len(r["cases"]))
        self.assertEqual(1, r["denominators"]["method"])

    def test_illegal_gt_path_is_not_normalized(self):
        self.sidecar["cases"][0]["accepted_targets"][0]["file_path"] = "src/main/java/p/../p/A.java"
        self.save_sidecar()
        self.assertEqual(2, self.run_eval()["exit_code"])

    def test_wrong_repo_identity_rejected_even_with_same_source_hash(self):
        artifact = self.artifact()
        artifact["cases"][0]["retrieval_ranking"][0]["repo_id"] = "another-repo"
        r = self.run_eval("replay", artifact)
        self.assertEqual(2, r["exit_code"])
        self.assertIn("SNAPSHOT_OR_REPO_MISMATCH", r["cases"][0]["ranking_reason"])

    def test_method_id_case_is_not_ignored(self):
        artifact = self.artifact()
        artifact["cases"][0]["retrieval_ranking"][0]["method_id"] = self.target()[3].replace("p.A", "p.a")
        self.assertEqual(2, self.run_eval("replay", artifact)["exit_code"])

    def test_declared_conflict_cannot_produce_strict_metrics(self):
        m = self.index[0]["classes"][0]["methods"][0]
        m.update(identity_status="CONFLICT", method_id=None, evidence_occurrences=[])
        self.refresh()
        r = self.run_eval()
        self.assertEqual(2, r["exit_code"])
        self.assertEqual(0, r["denominators"]["method"])
        self.assertIsNone(r["metrics"]["strict_method_top1"]["value"])

    def test_rankings_are_read_only_after_all_gt_resolutions(self):
        events = []
        original_read = se.read_json
        original_resolve = se.resolve_ground_truth
        def read(path):
            if Path(path).name == "rankings.json":
                events.append("rankings_read")
            return original_read(path)
        def resolve(*args, **kwargs):
            events.append("gt_resolved")
            return original_resolve(*args, **kwargs)
        with mock.patch.object(se, "read_json", side_effect=read), mock.patch.object(se, "resolve_ground_truth", side_effect=resolve):
            r = self.run_eval("replay")
        self.assertEqual(0, r["exit_code"])
        self.assertLess(events.index("gt_resolved"), events.index("rankings_read"))

    def test_invalid_ranking_cannot_hide_behind_pending_gt(self):
        gt = self.sidecar["cases"][0]
        gt.update(annotation_status="PENDING_REVIEW", file_gt_status="PENDING_REVIEW")
        self.save_sidecar()
        artifact = self.artifact([self.target(), self.target()])
        r = self.run_eval("replay", artifact)
        self.assertEqual(2, r["exit_code"])
        self.assertEqual(0, r["denominators"]["method"])
        self.assertEqual("INVALID_RANKING", r["cases"][0]["ranking_status"])

    def test_snapshot_id_and_metadata_hash_cannot_be_rebound_by_head(self):
        self.sidecar["snapshots"][0]["metadata_sha256"] = "0" * 64
        self.save_sidecar()
        r = self.run_eval()
        self.assertEqual(2, r["exit_code"])
        self.assertEqual(0, r["denominators"]["method"])

    def test_no_api_execution_in_rule_or_replay(self):
        import urllib.request
        with mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("Network forbidden")):
            rule = self.run_eval("rule")
            replay = self.run_eval("replay", rule["ranking_artifact"])
        self.assertEqual(0, rule["exit_code"])
        self.assertEqual(0, replay["exit_code"])

    def test_conflicting_file_and_method_gt_is_invalid(self):
        self.sidecar["cases"][0]["accepted_files"] = ["src/main/java/p/Other.java"]
        self.save_sidecar()
        r = self.run_eval()
        self.assertEqual(2, r["exit_code"])
        self.assertIn("METHOD_FILE_GT_INCONSISTENT", r["issues"][0]["detail"])

    def test_unknown_identity_scheme_cannot_claim_strict_protocol(self):
        self.sidecar["protocol"]["identity_scheme"] = "Class#name"
        self.save_sidecar()
        self.assertEqual(2, self.run_eval()["exit_code"])

    def test_method_container_mismatch_is_not_a_valid_catalog(self):
        self.index[0]["classes"][0]["name"] = "WrongOwner"
        with self.assertRaisesRegex(se.ProtocolError, "METHOD_CONTAINER_MISMATCH"):
            self.catalog()


if __name__ == "__main__":
    unittest.main()
