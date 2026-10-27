"""Synthetic-only BM25 arithmetic, protocol, isolation and B1 integration tests."""
import ast
import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bm = load(ROOT / "src/bm25_baseline.py", "bm25_tested")
adapter = load(ROOT / "src/experiment_adapter.py", "bm25_existing_adapter")
helpers = load(ROOT / "tests/test_experiment_adapter.py", "bm25_existing_synthetic_fixtures")


class BM25Test(unittest.TestCase):
    def setUp(self):
        base = os.environ.get("EXP001_B2_TEST_ROOT")
        if base:
            Path(base).mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="b2-case-", dir=base))
        (self.temp / "test-id.txt").write_text(self.id(), encoding="utf-8")

    def request(self, n=8, query="alpha"):
        sid = "sha256:" + "1" * 64
        catalog = []
        for i in range(n):
            file = "src/main/java/p/A.java"
            record = dict(repo_id="fixture/repo", source_snapshot_id=sid, file_path=file,
                          method_id="java-source-v1:p.A#f" + str(i) + "()", ordinal=i,
                          has_body=i != 2, features=dict(name="f" + str(i), **{"class": "A"},
                          signature="f" + str(i) + "()", file=file, annotations=[], strings=[], calls=[]))
            catalog.append(record)
        request = dict(schema_version="prediction-request-v1", case_id="fixture-bm25", repo_id="fixture/repo",
            source_snapshot_id=sid, issue_text=query, input_file_sha256="2"*64,
            issue_text_sha256=bm.sha(query.encode()), index_sha256="3"*64, metadata_sha256="4"*64,
            source_manifest_sha256="1"*64, candidate_k=5, catalog=catalog,
            catalog_sha256=bm.sha(bm.canonical(catalog)), code_sha256=adapter.code_bindings())
        return request

    def rehash(self, request):
        request["catalog_sha256"] = bm.sha(bm.canonical(request["catalog"]))
        return request

    def assert_bad(self, request):
        with self.assertRaises((bm.BM25Error, ValueError, TypeError, OverflowError)):
            bm.predict(request)

    def fixture_package(self):
        h = helpers.ExperimentAdapterTest(methodName="runTest")
        with mock.patch.dict(os.environ, {"EXP001_B1_TEST_ROOT": str(self.temp)}):
            h.setUp()
        f = h.fixture("fixture-bm25-integration")
        package = h.package(f)
        return h, f, package

    def test_hand_calculation(self):
        scores, stats = bm.score_tokens([["a", "a", "b"], ["b"]], ["a", "b"])
        idf_a = math.log(2.0)
        idf_b = math.log(1.2)
        expected0 = idf_a*2*2.2/(2+1.2*(.25+.75*3/2)) + idf_b*2.2/(1+1.2*(.25+.75*3/2))
        expected1 = idf_b*2.2/(1+1.2*(.25+.75*1/2))
        self.assertAlmostEqual(scores[0], expected0, places=14)
        self.assertAlmostEqual(scores[1], expected1, places=14)
        self.assertEqual(stats["avgdl"], 2)

    def test_tf_increase_same_length_saturates(self):
        scores, _ = bm.score_tokens([["a", "x", "x"], ["a", "a", "x"], ["a", "a", "a"]], ["a"])
        self.assertLess(scores[0], scores[1]); self.assertLess(scores[1], scores[2])
        self.assertLess(scores[2]-scores[1], scores[1]-scores[0])

    def test_df_counts_documents_not_occurrences(self):
        _, stats = bm.score_tokens([["a"]*9, ["a", "b"], []], ["a", "b", "absent"])
        self.assertEqual(stats["df"], {"a": 2, "b": 1, "absent": 0})
        self.assertAlmostEqual(stats["idf"]["a"], math.log(1+(3-2+.5)/(2+.5)))
        self.assertLess(stats["idf"]["a"], stats["idf"]["b"])

    def test_length_normalization(self):
        scores, _ = bm.score_tokens([["hit"], ["hit", "x", "x", "x"]], ["hit"])
        self.assertGreater(scores[0], scores[1])

    def test_empty_document_in_average_and_n(self):
        scores, stats = bm.score_tokens([["hit", "hit"], []], ["hit"])
        self.assertEqual(stats["n"], 2); self.assertEqual(stats["avgdl"], 1)
        self.assertEqual(scores[1], 0)
        self.assertAlmostEqual(scores[0], math.log(2)*2*2.2/(2+1.2*(.25+.75*2)))

    def test_repeated_query_terms_not_weighted(self):
        a, _ = bm.score_tokens([["hit", "a"], ["a"]], ["hit"])
        b, _ = bm.score_tokens([["hit", "a"], ["a"]], ["hit"]*99)
        self.assertEqual(a, b)

    def test_query_order_does_not_change_floating_sum(self):
        docs = [["a", "b", "c", "d"], ["a"]]
        self.assertEqual(bm.score_tokens(docs, ["d", "a", "b", "c"]), bm.score_tokens(docs, ["c", "b", "a", "d"]))

    def test_no_matching_terms_zero(self):
        self.assertEqual(bm.score_tokens([["a"], ["b"]], ["none"])[0], [0.0, 0.0])

    def test_empty_query_zero(self):
        self.assertEqual(bm.score_tokens([["a"], []], [])[0], [0.0, 0.0])

    def test_zero_average_length(self):
        scores, stats = bm.score_tokens([[], []], ["a"])
        self.assertEqual(scores, [0.0, 0.0]); self.assertEqual(stats["avgdl"], 0)

    def test_zero_documents(self):
        scores, stats = bm.score_tokens([], ["a"])
        self.assertEqual(scores, []); self.assertEqual(stats["n"], 0)

    def test_camel_and_acronyms(self):
        self.assertEqual(bm.tokenize("getURLValue HTTPServer XMLParser findOwner"),
                         ["get", "url", "value", "http", "server", "xml", "parser", "find", "owner"])

    def test_underscore_and_punctuation(self):
        self.assertEqual(bm.tokenize("__find_owner$Class#method(int,String)"),
                         ["find", "owner", "class", "method", "int", "string"])

    def test_digits_frozen_rule(self):
        self.assertEqual(bm.tokenize("HTTP2Parser version42 123 2D x2y v1_2"),
                         ["http2", "parser", "version42", "123", "2", "d", "x2y", "v1", "2"])

    def test_unicode_no_normalization_or_jieba(self):
        self.assertEqual(bm.tokenize("École 中文错误 STRASSE Straße"), ["école", "中文错误", "strasse", "straße"])
        self.assertNotEqual(bm.tokenize("é"), bm.tokenize("e\u0301"))

    def test_case_and_empty_text(self):
        self.assertEqual(bm.tokenize("Alpha alpha ALPHA"), ["alpha"]*3)
        self.assertEqual(bm.tokenize("\t\r\n___!!!"), [])

    def test_document_exact_order_and_duplicate_arrays(self):
        f = dict(name="name", **{"class": "Class"}, signature="name(int)", file="src/A.java",
                 annotations=["@A", "@A"], strings=["same", "same"], calls=["call", "call"])
        self.assertEqual(bm.document(f), "name\nClass\nname(int)\nsrc/A.java\n@A\n@A\nsame\nsame\ncall\ncall")
        self.assertEqual(bm.tokenize(bm.document(f)).count("same"), 2)

    def test_cross_field_duplicates_count(self):
        f = {k: [] if k in bm.FIELDS[4:] else "same" for k in bm.FIELDS}
        self.assertEqual(bm.tokenize(bm.document(f)), ["same"]*4)

    def test_no_stemming_stopwords_or_identifier_augmentation(self):
        self.assertEqual(bm.tokenize("the handles handleOwner"), ["the", "handles", "handle", "owner"])

    def test_full_catalog_not_top_five(self):
        r = self.request(12); r["catalog"][11]["features"]["strings"] = ["alpha"]
        result = bm.predict(self.rehash(r))
        self.assertEqual(len(result["ranking"]), 12)
        self.assertEqual(result["ranking"][0]["method_id"], r["catalog"][11]["method_id"])
        self.assertEqual(result["telemetry"]["scored_count"], 12)
        adapter.validate_result(result, r)

    def test_ties_follow_original_ordinal(self):
        r = self.request(query="unmatchable")
        result = bm.predict(r)
        self.assertEqual([x["method_id"] for x in result["ranking"]], [x["method_id"] for x in r["catalog"]])
        self.assertTrue(all(x["score"] == 0 for x in result["ranking"]))

    def test_reordered_catalog_changes_only_tie_order(self):
        r = self.request(query="unmatchable"); r["catalog"].reverse()
        for i, rec in enumerate(r["catalog"]): rec["ordinal"] = i
        result = bm.predict(self.rehash(r))
        self.assertEqual(result["ranking"][0]["method_id"], "java-source-v1:p.A#f7()")

    def test_overloads_and_nested_types_keep_full_identity(self):
        r = self.request(3)
        mids = ["java-source-v1:p.A#f(int)", "java-source-v1:p.A#f(String)", "java-source-v1:p.A.Inner#f(int)"]
        for rec, mid in zip(r["catalog"], mids): rec["method_id"] = mid
        result = bm.predict(self.rehash(r))
        self.assertEqual({x["method_id"] for x in result["ranking"]}, set(mids))
        self.assertTrue(all(all(k in x for k in bm.IDENTITY) for x in result["ranking"]))

    def test_bodyless_method_kept(self):
        r = self.request(); r["catalog"][2]["features"]["strings"] = ["alpha"]
        self.assertFalse(r["catalog"][2]["has_body"])
        self.assertEqual(bm.predict(self.rehash(r))["ranking"][0]["method_id"], r["catalog"][2]["method_id"])

    def test_empty_issue_returns_full_zero_ranking(self):
        r = self.request(query="")
        result = bm.predict(r)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(len(result["ranking"]), 8)
        self.assertTrue(all(x["score"] == 0 for x in result["ranking"]))

    def test_empty_catalog_explicit_empty(self):
        r = self.request(0)
        result = bm.predict(r); adapter.validate_result(result, r)
        self.assertEqual(result["status"], "EMPTY"); self.assertEqual(result["ranking"], [])

    def test_direct_output_deterministic_and_input_unmodified(self):
        r = self.request(); original = copy.deepcopy(r)
        a = bm.canonical(bm.predict(r)); b = bm.canonical(bm.predict(r))
        self.assertEqual(a, b); self.assertEqual(r, original)

    def test_cross_snapshot_rejected(self):
        r = self.request(); r["catalog"][0]["source_snapshot_id"] = "sha256:"+"a"*64
        self.assert_bad(self.rehash(r))

    def test_wrong_repo_rejected(self):
        r = self.request(); r["catalog"][0]["repo_id"] = "other/repo"
        self.assert_bad(self.rehash(r))

    def test_duplicate_method_id_rejected(self):
        r = self.request(); r["catalog"][1]["method_id"] = r["catalog"][0]["method_id"]
        self.assert_bad(self.rehash(r))

    def test_missing_method_id_rejected(self):
        r = self.request(); del r["catalog"][0]["method_id"]
        self.assert_bad(self.rehash(r))

    def test_legacy_id_rejected(self):
        r = self.request(); r["catalog"][0]["method_id"] = "A#f"
        self.assert_bad(self.rehash(r))

    def test_invalid_paths_rejected(self):
        for path in ["src/main/java/../A.java", "C:/A.java", "src\\main\\java\\A.java", "/src/main/java/A.java"]:
            with self.subTest(path=path):
                r = self.request(); r["catalog"][0]["file_path"] = path
                r["catalog"][0]["features"]["file"] = path
                self.assert_bad(self.rehash(r))

    def test_bad_ordinal_rejected(self):
        for ordinal in [True, 1, -1, 0.0]:
            with self.subTest(ordinal=ordinal):
                r = self.request(); r["catalog"][0]["ordinal"] = ordinal
                self.assert_bad(self.rehash(r))

    def test_catalog_tamper_rejected(self):
        r = self.request(); r["catalog"][0]["features"]["strings"] = ["modified"]
        self.assert_bad(r)

    def test_issue_tamper_rejected(self):
        r = self.request(); r["issue_text"] += " modified"
        self.assert_bad(r)

    def test_gt_request_field_rejected(self):
        r = self.request(); r["accepted_targets"] = []
        self.assert_bad(r)

    def test_rule_top5_field_rejected(self):
        r = self.request(); r["rule_top5"] = r["catalog"][:5]
        self.assert_bad(r)

    def test_body_or_extra_feature_rejected(self):
        r = self.request(); r["catalog"][0]["features"]["body"] = "secret"
        self.assert_bad(self.rehash(r))

    def test_invalid_feature_types_rejected(self):
        for field, value in [("name", None), ("strings", "not-list"), ("calls", [None]), ("annotations", [float("nan")])]:
            with self.subTest(field=field):
                r = self.request(); r["catalog"][0]["features"][field] = value
                self.assert_bad(self.rehash(r) if field != "annotations" else r)

    def test_snapshot_manifest_mismatch_rejected(self):
        r = self.request(); r["source_manifest_sha256"] = "a"*64
        self.assert_bad(r)

    def test_nonfinite_scores_fail_closed(self):
        for bad in [math.nan, math.inf, -math.inf]:
            with self.subTest(score=bad):
                with mock.patch.object(bm.math, "fsum", return_value=bad):
                    self.assert_bad(self.request())

    def test_invalid_token_lists_rejected(self):
        for docs, query in [("doc", []), ([[1]], []), ([[]], [None]), ([[]], "q")]:
            with self.subTest(docs=docs):
                with self.assertRaises(bm.BM25Error): bm.score_tokens(docs, query)
        with self.assertRaises(bm.BM25Error): bm.tokenize(None)

    def test_no_gt_io_or_rule_import_during_predict(self):
        r = self.request()
        def forbidden(*args, **kwargs): raise AssertionError("unexpected I/O")
        with mock.patch("builtins.open", forbidden), mock.patch.object(Path, "read_bytes", forbidden), \
             mock.patch.object(Path, "read_text", forbidden):
            result = bm.predict(r)
        self.assertEqual(result["status"], "SUCCESS")
        tree = ast.parse((ROOT/"src/bm25_baseline.py").read_text(encoding="utf-8"))
        imports = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)}
        imports |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertEqual(imports, {"collections", "hashlib", "json", "math", "re", "unicodedata"})

    def test_spec_matches_frozen_json_and_predefined_parameters(self):
        spec = json.loads((ROOT/"data/bm25_protocol.exp001_b2.v1.json").read_text(encoding="utf-8"))
        self.assertEqual(spec["spec"], bm.SPEC)
        self.assertEqual(spec["spec_sha256"], bm.sha(bm.canonical(bm.SPEC)))
        self.assertEqual((bm.K1, bm.B), (1.2, .75))
        self.assertFalse(spec["real_ranking_authorized"])

    def test_real_case_execution_blocked_before_scoring(self):
        for cid in ["jsoup-2130", "jsoup-2266", "jsoup-2068", "jsoup-2078"]:
            with self.subTest(cid=cid), mock.patch.object(bm, "score_tokens", side_effect=AssertionError("must not score")):
                r = self.request(); r["case_id"] = cid
                with self.assertRaisesRegex(bm.BM25Error, "B2_SYNTHETIC_ONLY"): bm.predict(r)

    def test_b1_worker_full_prediction_and_hash_binding(self):
        h, f, package = self.fixture_package()
        request, _, _ = adapter.load_package(package)
        output = h.out("bm25-prediction")
        receipt = adapter.predict(package, ROOT/"src/bm25_baseline.py", output)
        self.assertEqual(receipt["prediction_status"], "SUCCESS", (output/"prediction.json").read_text())
        artifact, ah = adapter.read(output/"prediction.json")
        self.assertEqual(ah, receipt["prediction_sha256"])
        self.assertEqual(artifact["producer_code_sha256"], bm.sha((ROOT/"src/bm25_baseline.py").read_bytes()))
        self.assertEqual(artifact["ranking"], bm.predict(request)["ranking"])
        self.assertEqual(artifact["candidate_count"], 6)
        self.assertEqual(len(artifact["ranking"]), 6)
        self.assertFalse(request["catalog"][2]["has_body"])
        self.assertEqual(artifact["telemetry"]["protocol_sha256"], bm.sha(bm.canonical(bm.SPEC)))
        self.assertNotIn("GT_SECRET_CANARY", (output/"prediction.json").read_text())
        self.assertNotIn("gt_sidecar_sha256", artifact)
        (self.temp/"integration-result.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    def test_b1_gt_changes_leave_prediction_identical(self):
        h, f, package = self.fixture_package()
        first = h.out("first"); a = adapter.predict(package, ROOT/"src/bm25_baseline.py", first)
        f["gt"].write_text("GT deliberately invalid; producer must not read", encoding="utf-8")
        second = h.out("second"); b = adapter.predict(package, ROOT/"src/bm25_baseline.py", second)
        self.assertEqual(a["prediction_status"], "SUCCESS"); self.assertEqual(b["prediction_status"], "SUCCESS")
        self.assertEqual(a["prediction_sha256"], b["prediction_sha256"])

    def test_b1_output_overwrite_rejected(self):
        h, f, package = self.fixture_package(); output = h.out("existing")
        output.mkdir(); sentinel = output/"sentinel"; sentinel.write_bytes(b"keep")
        with self.assertRaises(adapter.AdapterError): adapter.predict(package, ROOT/"src/bm25_baseline.py", output)
        self.assertEqual(sentinel.read_bytes(), b"keep")

    def test_b1_corrupt_request_json_saved_as_failure(self):
        h, f, package = self.fixture_package(); (package/"request.json").write_bytes(b"{")
        output = h.out("bad-json"); receipt = adapter.predict(package, ROOT/"src/bm25_baseline.py", output)
        self.assertEqual(receipt["prediction_status"], "INVALID_PREDICTION")
        self.assertTrue((output/"prediction.json").is_file())

    def test_b1_tampered_prediction_rejected_by_evaluator(self):
        h, f, package = self.fixture_package(); output = h.out("tamper")
        receipt = adapter.predict(package, ROOT/"src/bm25_baseline.py", output)
        artifact = adapter.read(output/"prediction.json")[0]; artifact["ranking"].reverse()
        h.save(output/"prediction.json", artifact)
        entry = dict(prediction_dir=str(output), prediction_sha256=receipt["prediction_sha256"])
        row = adapter.read(f["benchmark"])[0][0]
        with self.assertRaisesRegex(adapter.AdapterError, "PREDICTION_FILE_HASH_MISMATCH"):
            adapter._read_prediction(entry, row, None, None)

    def test_self_consistent_top5_subset_cannot_pass_full_index_evaluation(self):
        h, f, package = self.fixture_package()
        request, _, info = adapter.load_package(package)
        request["catalog"] = request["catalog"][:5]
        self.rehash(request)
        h.save(package/"request.json", request)
        info["request_sha256"] = bm.sha((package/"request.json").read_bytes()); info["candidate_count"] = 5
        h.save(package/"package.json", info)
        output = h.out("self-consistent-subset")
        receipt = adapter.predict(package, ROOT/"src/bm25_baseline.py", output)
        # A request alone cannot establish complete-source coverage. The
        # independent B1 evaluation must reject even rehashed catalog truncation.
        self.assertEqual(receipt["prediction_status"], "SUCCESS")
        row = adapter.read(f["benchmark"])[0][0]; sidecar = adapter.read(f["gt"])[0]
        entry = dict(prediction_dir=str(output), prediction_sha256=receipt["prediction_sha256"], index=str(f["index"]))
        audit = dict(inputs=dict(benchmark_sha256=bm.sha(f["benchmark"].read_bytes())))
        with self.assertRaisesRegex(adapter.AdapterError, "REQUEST_CATALOG_DIFFERS_FROM_INDEX"):
            adapter._read_prediction(entry, row, sidecar, audit)

    def test_four_synthetic_cases_reuse_unchanged_strict_aggregation(self):
        h = helpers.ExperimentAdapterTest(methodName="runTest")
        with mock.patch.dict(os.environ, {"EXP001_B1_TEST_ROOT": str(self.temp)}): h.setUp()
        cases = []
        for cid in ["fixture-a", "fixture-b", "fixture-c", "fixture-d"]:
            f = h.fixture(cid); package = h.package(f); output = h.out("bm25")
            receipt = adapter.predict(package, ROOT/"src/bm25_baseline.py", output)
            self.assertEqual(receipt["prediction_status"], "SUCCESS")
            cases.append(dict(case_id=cid, benchmark=str(f["benchmark"]), gt_sidecar=str(f["gt"]),
                repo=str(f["repo"]), index=str(f["index"]), prediction_dir=str(output), prediction_sha256=receipt["prediction_sha256"]))
        job = dict(schema_version="evaluation-cohort-v1", candidate_k=5, stage="rule",
                   case_ids=[c["case_id"] for c in cases], cases=cases)
        jp = h.out("cohort.json"); h.save(jp, job)
        result = adapter.aggregate(jp, h.out("aggregate"))
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(len(result["cases"]), 4)

    def test_parameters_cannot_be_overridden_by_request(self):
        for field, value in [("k1", 9), ("b", 0), ("fields", ["name"]), ("top_k", 5)]:
            with self.subTest(field=field):
                r = self.request(); r[field] = value
                self.assert_bad(r)

    def test_invalid_body_flag_and_feature_file_rejected(self):
        r = self.request(); r["catalog"][0]["has_body"] = 1
        self.assert_bad(self.rehash(r))
        r = self.request(); r["catalog"][0]["features"]["file"] = "src/main/java/other/B.java"
        self.assert_bad(self.rehash(r))


if __name__ == "__main__":
    unittest.main()
