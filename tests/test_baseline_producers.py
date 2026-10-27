"""B3 synthetic-only integration. Real Rule tests REQUIRE actual installed Jieba.

Unavailable dependency means explicit skips, never a simulated tokenizer/scorer.
The ordinary-user acceptance runner rejects any skip.
"""
import base64
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(path, name):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
    return m


bridge = load(ROOT/"src/baseline_producers.py", "tested_baseline_producers")
adapter = load(ROOT/"src/experiment_adapter.py", "b3_test_adapter")
bm25 = load(ROOT/"src/bm25_baseline.py", "b3_reference_bm25")
fixtures = load(ROOT/"tests/test_experiment_adapter.py", "b3_synthetic_fixtures")
b2tests = load(ROOT/"tests/test_bm25_baseline.py", "b3_request_fixtures")
try:
    import jieba
    RULE_AVAILABLE = True
except ImportError:
    RULE_AVAILABLE = False
RULE_REQUIRED = unittest.skipUnless(RULE_AVAILABLE, "Real Jieba unavailable; no substitute; ordinary-user Python310 acceptance REQUIRED")


class BaselineProducerTest(unittest.TestCase):
    def setUp(self):
        base = os.environ.get("EXP001_B3_TEST_ROOT")
        if base: Path(base).mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="b3-case-", dir=base))
        (self.temp/"test-id.txt").write_text(self.id(), encoding="utf-8")
        self.h = fixtures.ExperimentAdapterTest(methodName="runTest")
        with mock.patch.dict(os.environ, {"EXP001_B1_TEST_ROOT": str(self.temp)}): self.h.setUp()

    def request(self, query="alpha", n=8):
        return b2tests.BM25Test(methodName="runTest").request(n, query)

    def rehash(self, request):
        request["catalog_sha256"] = bridge.sha(bridge.canonical(request["catalog"]))
        return request

    def embedded(self, system, request):
        name, _ = bridge.SOURCES[system]
        return bridge.predict_embedded(request, system, (ROOT/"src"/name).read_bytes(),
                                       bridge.sha((ROOT/"src/baseline_producers.py").read_bytes()))

    def package(self, cid="fixture-b3"):
        f = self.h.fixture(cid)
        return f, self.h.package(f)

    def run_producer(self, system, package):
        out = self.h.out(system)
        result = bridge.run(system, package, out)
        return out, result

    def artifact(self, result):
        return adapter.read(Path(result["prediction_dir"])/"prediction.json")[0]

    def reference_rule(self, request):
        scorer = bridge.source_module((ROOT/"src/retriever.py").read_bytes(), "b3_real_reference_rule", "retriever.py")
        keywords = scorer.extract_keywords(request["issue_text"])
        rows = []
        for rec in request["catalog"]:
            score, detail = scorer.score_method(copy.deepcopy(rec["features"]), keywords, request["issue_text"])
            rows.append({**{k: rec[k] for k in bridge.IDENTITY}, "score": score, "detail": detail})
        rows.sort(key=lambda row: row["score"], reverse=True)
        return rows, keywords

    def test_capsules_contain_byte_identical_original_sources(self):
        for system in bridge.SOURCES:
            raw, payload = bridge.capsule_bytes(system)
            self.assertEqual(base64.b64decode(payload["scorer"]), (ROOT/"src"/bridge.SOURCES[system][0]).read_bytes())
            self.assertEqual(base64.b64decode(payload["bridge"]), (ROOT/"src/baseline_producers.py").read_bytes())
            self.assertNotIn(b"site-packages", raw)
            self.assertEqual(payload["scorer_sha256"], bridge.SOURCES[system][1])

    def test_capsule_generation_deterministic(self):
        for system in bridge.SOURCES: self.assertEqual(bridge.capsule_bytes(system), bridge.capsule_bytes(system))

    def test_scorer_source_tamper_rejected_before_execution(self):
        for system in bridge.SOURCES:
            raw = (ROOT/"src"/bridge.SOURCES[system][0]).read_bytes()+b"\n# changed"
            with self.assertRaisesRegex(bridge.ProducerError, "SCORER_SOURCE_HASH_MISMATCH"):
                bridge.predict_embedded(self.request(), system, raw, "0"*64)

    def test_bm25_exact_full_scores_and_spec(self):
        r = self.request(n=12); r["catalog"][-1]["features"]["strings"] = ["alpha"]; self.rehash(r)
        expected = bm25.predict(r); result = self.embedded("bm25", r)
        self.assertEqual(result["ranking"], expected["ranking"])
        self.assertEqual(result["telemetry"]["protocol"], expected["telemetry"]["protocol"])
        self.assertEqual(result["telemetry"]["protocol_sha256"], expected["telemetry"]["protocol_sha256"])
        self.assertEqual(result["ranking"][0]["method_id"], r["catalog"][-1]["method_id"])
        self.assertEqual(len(result["ranking"]), 12)

    def test_bm25_worker_and_receipt(self):
        f, package = self.package(); request, _, _ = adapter.load_package(package)
        out, result = self.run_producer("bm25", package)
        self.assertEqual(result["prediction_status"], "SUCCESS", result)
        artifact = self.artifact(result)
        self.assertEqual(artifact["ranking"], bm25.predict(request)["ranking"])
        self.assertEqual(result["prediction_sha256"], bridge.sha((Path(result["prediction_dir"])/"prediction.json").read_bytes()))
        self.assertEqual(artifact["producer_code_sha256"], bridge.sha((out/"producer-capsule.py").read_bytes()))
        self.assertEqual(artifact["telemetry"]["scorer_source_sha256"], bridge.SOURCES["bm25"][1])
        self.assertGreaterEqual(artifact["telemetry"]["elapsed_seconds"], 0)

    def test_empty_bm25_catalog_and_zero_query(self):
        self.assertEqual(self.embedded("bm25", self.request(n=0))["status"], "EMPTY")
        result = self.embedded("bm25", self.request(query=""))
        self.assertEqual(len(result["ranking"]), 8)
        self.assertTrue(all(r["score"] == 0 for r in result["ranking"]))

    def test_bodyless_and_overloads_bm25(self):
        r = self.request(n=3)
        for rec, mid in zip(r["catalog"], ["java-source-v1:p.A#f(int)", "java-source-v1:p.A#f(String)", "java-source-v1:p.A.Inner#f(int)"]): rec["method_id"] = mid
        result = self.embedded("bm25", self.rehash(r))
        self.assertEqual({x["method_id"] for x in result["ranking"]}, {x["method_id"] for x in r["catalog"]})
        self.assertFalse(r["catalog"][2]["has_body"])

    def test_bm25_ordinal_ties_preserved(self):
        r = self.request(query="unmatchable"); r["catalog"].reverse()
        for i, rec in enumerate(r["catalog"]): rec["ordinal"] = i
        result = self.embedded("bm25", self.rehash(r))
        self.assertEqual([r["method_id"] for r in result["ranking"]], [r["method_id"] for r in r["catalog"]])

    def test_cross_snapshot_rejected_by_both_before_rule_import(self):
        r = self.request(); r["catalog"][0]["source_snapshot_id"] = "sha256:"+"a"*64; self.rehash(r)
        for system in bridge.SOURCES:
            with self.assertRaisesRegex(bridge.ProducerError, "CROSS_SNAPSHOT_IDENTITY"): self.embedded(system, r)

    def test_wrong_feature_types_rejected_by_both(self):
        for field, value in [("name", None), ("calls", "text"), ("annotations", [None])]:
            r = self.request(); r["catalog"][0]["features"][field] = value; self.rehash(r)
            for system in bridge.SOURCES:
                with self.subTest(field=field, system=system), self.assertRaises(bridge.ProducerError): self.embedded(system, r)

    def test_duplicate_and_missing_method_id_rejected(self):
        for mode in ["duplicate", "missing"]:
            r = self.request()
            if mode == "duplicate": r["catalog"][1]["method_id"] = r["catalog"][0]["method_id"]
            else: del r["catalog"][0]["method_id"]
            self.rehash(r)
            for system in bridge.SOURCES:
                with self.subTest(mode=mode, system=system), self.assertRaises(bridge.ProducerError): self.embedded(system, r)

    def test_wrong_repo_path_ordinal_and_body_flag_rejected(self):
        for field, value in [("repo_id", "other/repo"), ("file_path", "src/main/java/../A.java"), ("ordinal", True), ("has_body", 1)]:
            r = self.request(); r["catalog"][0][field] = value; self.rehash(r)
            for system in bridge.SOURCES:
                with self.subTest(field=field, system=system), self.assertRaises(bridge.ProducerError): self.embedded(system, r)

    def test_gt_and_other_producer_candidates_rejected(self):
        for key in ["gt_sidecar", "accepted_targets", "rule_top5", "bm25_top5"]:
            r = self.request(); r[key] = []
            for system in bridge.SOURCES:
                with self.subTest(key=key, system=system), self.assertRaisesRegex(bridge.ProducerError, "INVALID_OR_GT_REQUEST_FIELDS"):
                    self.embedded(system, r)

    def test_no_body_or_patch_feature(self):
        for key in ["body", "patch", "repair_test"]:
            r = self.request(); r["catalog"][0]["features"][key] = "hidden"; self.rehash(r)
            for system in bridge.SOURCES:
                with self.subTest(key=key, system=system), self.assertRaises(bridge.ProducerError): self.embedded(system, r)

    def test_real_cases_disabled_before_loading_scorer(self):
        for cid in ["jsoup-2130", "jsoup-2266", "jsoup-2068", "jsoup-2078"]:
            r = self.request(); r["case_id"] = cid
            for system in bridge.SOURCES:
                with self.subTest(cid=cid, system=system), self.assertRaisesRegex(bridge.ProducerError, "B3_REAL_CASE_DISABLED"):
                    self.embedded(system, r)

    def test_invalid_issue_hash_rejected(self):
        r = self.request(); r["issue_text"] += "changed"
        for system in bridge.SOURCES:
            with self.assertRaisesRegex(bridge.ProducerError, "ISSUE_HASH_MISMATCH"): self.embedded(system, r)

    def test_unknown_system_rejected_not_fallback(self):
        f, package = self.package(); out, result = self.run_producer("other", package)
        self.assertEqual(result["prediction_status"], "INVALID_PREDICTION")
        self.assertNotIn("ranking", result)
        self.assertTrue((out/"result.json").is_file())

    def test_output_overwrite_refused(self):
        f, package = self.package(); out = self.h.out(); out.mkdir(); (out/"keep").write_text("keep")
        with self.assertRaisesRegex(ValueError, "OUTPUT_ALREADY_EXISTS"): bridge.run("bm25", package, out)
        self.assertEqual((out/"keep").read_text(), "keep")

    def test_output_inside_source_refused(self):
        f, package = self.package()
        with self.assertRaisesRegex(ValueError, "OUTPUT_INSIDE_PROTECTED_ROOT"): bridge.run("bm25", package, f["repo"]/"new")

    def test_gt_never_read_in_transport(self):
        f, package = self.package(); original = Path.read_bytes
        def guarded(path):
            if Path(path) == f["gt"]: raise AssertionError("GT read")
            return original(path)
        with mock.patch.object(Path, "read_bytes", guarded): out, result = self.run_producer("bm25", package)
        self.assertEqual(result["prediction_status"], "SUCCESS")
        self.assertNotIn("GT_SECRET_CANARY", (Path(result["prediction_dir"])/"request.json").read_text())

    def test_prediction_hash_tamper_rejected(self):
        f, package = self.package(); out, result = self.run_producer("bm25", package)
        p = Path(result["prediction_dir"])/"prediction.json"; p.write_bytes(p.read_bytes()+b" ")
        with self.assertRaisesRegex(adapter.AdapterError, "PREDICTION_FILE_HASH_MISMATCH"):
            adapter._read_prediction(dict(prediction_dir=result["prediction_dir"], prediction_sha256=result["prediction_sha256"]), adapter.read(f["benchmark"])[0][0], None, None)

    def test_bm25_no_network_or_rule_dependency(self):
        r = self.request()
        def deny(*args, **kwargs): raise AssertionError("network called")
        with mock.patch.object(socket.socket, "connect", deny), mock.patch.dict(__import__('sys').modules, {"jieba": None}):
            result = self.embedded("bm25", r)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["telemetry"]["requests"], [])

    def test_r1_uncreated_cache_slot_avoids_windows_tempfile_retry_loop(self):
        script = '''import importlib.util,json,os,sys,tempfile
from pathlib import Path
s=importlib.util.spec_from_file_location('r1_original_guard', ADAPTER_LITERAL)
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
work=Path(WORK_LITERAL)
count=[0]
class BoundedRetryObserved(RuntimeError): pass
def observe(event,args):
    if event=='tempfile.mkstemp':
        count[0]+=1
        if count[0]>=3:raise BoundedRetryObserved('bounded test-only observer')
sys.addaudithook(observe)
m._install_worker_guard(work,work/'producer.py')
results=[]
for name,path in [('existing',work),('uncreated_child',work/'.jieba-cache-r1')]:
    count[0]=0
    try:
        tempfile.mkstemp(dir=str(path))
        raise AssertionError('write boundary weakened')
    except (PermissionError,BoundedRetryObserved) as exc:
        results.append(dict(name=name,error_type=type(exc).__name__,attempts=count[0]))
print(json.dumps(dict(platform=os.name,results=results)))
'''.replace('ADAPTER_LITERAL', repr(str(ROOT/'src/experiment_adapter.py'))).replace('WORK_LITERAL', repr(str(self.temp)))
        process = subprocess.run([__import__('sys').executable, '-B', '-c', script], cwd=self.temp,
                                 capture_output=True, text=True, timeout=10)
        (self.temp/'tempfile-probe.stdout.log').write_text(process.stdout, encoding='utf-8')
        (self.temp/'tempfile-probe.stderr.log').write_text(process.stderr, encoding='utf-8')
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report['results'][1], dict(name='uncreated_child', error_type='PermissionError', attempts=1))
        if report['platform'] == 'nt':
            self.assertEqual(report['results'][0]['error_type'], 'BoundedRetryObserved')
        self.assertFalse((self.temp/'.jieba-cache-r1').exists())

    def test_controller_keeps_real_case_execution_closed(self):
        for system in bridge.SOURCES:
            f, package = self.package("jsoup-2130")  # synthetic repository/text only
            out, result = self.run_producer(system, package)
            self.assertEqual(result["prediction_status"], "INVALID_PREDICTION")
            self.assertIn("B3_REAL_CASE_DISABLED", result["error"]["detail"])
            self.assertFalse((out/"producer-capsule.py").exists())

    def test_capsule_tamper_fails_without_other_producer_fallback(self):
        f, package = self.package(); raw, payload = bridge.capsule_bytes("bm25")
        old = payload["scorer"]
        altered = base64.b64encode(base64.b64decode(old)+b"\n# tamper").decode("ascii")
        path = self.h.out("tampered.py"); path.write_bytes(raw.replace(old.encode("ascii"), altered.encode("ascii")))
        failed_out = self.h.out("failed")
        receipt = adapter.predict(package, path, failed_out)
        self.assertEqual(receipt["prediction_status"], "EXECUTION_ERROR")
        artifact = adapter.read(failed_out/"prediction.json")[0]
        self.assertIn("SCORER_PAYLOAD_HASH_MISMATCH", artifact["error"]["detail"])
        self.assertNotIn("ranking", artifact)

    def test_missing_jieba_failure_never_becomes_bm25_prediction(self):
        # Real missing dependency, not a replacement implementation of Rule.
        f, package = self.package()
        if RULE_AVAILABLE:
            with mock.patch.dict(__import__('sys').modules, {"jieba": None}), self.assertRaises(ModuleNotFoundError):
                self.embedded("rule", self.request())
        else:
            out, result = self.run_producer("rule", package)
            self.assertEqual(result["prediction_status"], "EXECUTION_ERROR")
            self.assertIn("ModuleNotFoundError", self.artifact(result)["error"]["detail"])
            self.assertNotIn("ranking", self.artifact(result))
        other_out, other = self.run_producer("bm25", package)
        self.assertEqual(other["prediction_status"], "SUCCESS")

    def test_one_failure_invalidates_fixed_four_aggregate(self):
        entries = []
        for i, cid in enumerate(["fixture-a", "fixture-b", "fixture-c", "fixture-d"]):
            f, package = self.package(cid)
            if i == 3: (package/"request.json").write_bytes(b"{")
            out, result = self.run_producer("bm25", package)
            if "prediction_dir" not in result:
                # Controller failure is represented by the deliberately missing
                # ranking directory; evaluator must keep the fourth slot.
                prediction_dir = out/"missing-prediction"; prediction_hash = "0"*64
            else: prediction_dir = result["prediction_dir"]; prediction_hash = result["prediction_sha256"]
            entries.append(dict(case_id=cid, benchmark=str(f["benchmark"]), gt_sidecar=str(f["gt"]), repo=str(f["repo"]),
                                index=str(f["index"]), prediction_dir=str(prediction_dir), prediction_sha256=prediction_hash))
        jp = self.h.out("job.json"); self.h.save(jp, dict(schema_version="evaluation-cohort-v1", case_ids=[e["case_id"] for e in entries], candidate_k=5, stage="rule", cases=entries))
        result = adapter.aggregate(jp, self.h.out("aggregate"))
        self.assertNotEqual(result["exit_code"], 0); self.assertEqual(len(result["cases"]), 4)
        self.assertEqual(result["tool_status"], "INVALID_RUN")
        self.assertEqual(result["denominators"]["method"], 4)
        self.assertIsNone(result["metrics"]["strict_method_top1"]["value"])

    @RULE_REQUIRED
    def test_rule_matches_actual_scorer_english_chinese_and_bonuses(self):
        for query in ["find alpha owner not found exception", "添加宠物 重复 校验 参数", "process missing request 500"]:
            r = self.request(query=query)
            r["catalog"][0]["features"].update(name="findOwner", strings=["owner not found"], calls=["orElseThrow"], annotations=["@Controller"])
            r["catalog"][1]["features"].update(name="processOwner", annotations=["@Service"])
            self.rehash(r)
            expected, keywords = self.reference_rule(r); result = self.embedded("rule", r)
            self.assertEqual(result["ranking"], expected)
            self.assertEqual(result["telemetry"]["keywords"], keywords)

    @RULE_REQUIRED
    def test_rule_worker_exact_raw_index_reference(self):
        f, package = self.package(); r, _, _ = adapter.load_package(package)
        out, result = self.run_producer("rule", package)
        self.assertEqual(result["prediction_status"], "SUCCESS", result)
        scorer = bridge.source_module((ROOT/"src/retriever.py").read_bytes(), "b3_index_reference", "retriever.py")
        keywords = scorer.extract_keywords(r["issue_text"]); expected = []
        for method in scorer.iter_methods(f["raw_index"]):
            score, detail = scorer.score_method(method, keywords, r["issue_text"])
            expected.append(dict(repo_id=r["repo_id"], source_snapshot_id=r["source_snapshot_id"], file_path=method["file"], method_id=method["method_id"], score=score, detail=detail))
        expected.sort(key=lambda row: row["score"], reverse=True)
        self.assertEqual(self.artifact(result)["ranking"], expected)
        self.assertEqual(self.artifact(result)["telemetry"]["scorer_source_sha256"], bridge.SOURCES["rule"][1])

    @RULE_REQUIRED
    def test_both_workers_share_identical_request_and_full_catalog(self):
        f, package = self.package(); r, _, _ = adapter.load_package(package)
        products = [self.run_producer(system, package)[1] for system in ["rule", "bm25"]]
        artifacts = [self.artifact(result) for result in products]
        self.assertTrue(all(a["prediction_status"] == "SUCCESS" for a in artifacts))
        for field in ["request_sha256", "catalog_sha256", "index_sha256", "source_snapshot_id", "candidate_count"]:
            self.assertEqual(artifacts[0][field], artifacts[1][field])
        expected = {tuple(row[k] for k in bridge.IDENTITY) for row in r["catalog"]}
        for a in artifacts: self.assertEqual({tuple(row[k] for k in bridge.IDENTITY) for row in a["ranking"]}, expected)
        for a in artifacts: self.assertEqual(len(a["ranking"]), 6)

    @RULE_REQUIRED
    def test_rule_stable_ties_bodyless_and_overloads(self):
        r = self.request(query="", n=3)
        r["catalog"][0]["method_id"] = "java-source-v1:p.A#f(int)"
        r["catalog"][1]["method_id"] = "java-source-v1:p.A#f(String)"
        r["catalog"].reverse()
        for i, rec in enumerate(r["catalog"]): rec["ordinal"] = i
        result = self.embedded("rule", self.rehash(r))
        self.assertEqual([x["method_id"] for x in result["ranking"]], [x["method_id"] for x in r["catalog"]])
        self.assertTrue(all(x["score"] == 0 for x in result["ranking"]))

    @RULE_REQUIRED
    def test_rule_gt_inaccessible_and_no_network(self):
        f, package = self.package(); original = Path.read_bytes
        def guarded(path):
            if Path(path) == f["gt"]: raise AssertionError("GT read")
            return original(path)
        with mock.patch.object(Path, "read_bytes", guarded): out, result = self.run_producer("rule", package)
        self.assertEqual(result["prediction_status"], "SUCCESS", result)
        self.assertEqual(self.artifact(result)["telemetry"]["requests"], [])
        # Worker runs with B1's unchanged network/file capability checks.
        self.assertEqual(adapter.read(Path(result["prediction_dir"])/"invocation.json")[0]["api_secrets_forwarded"], False)

    @RULE_REQUIRED
    def test_rule_negative_penalty_not_clamped(self):
        r = self.request(query="", n=2)
        r["catalog"][0]["features"]["name"] = "processThing"; self.rehash(r)
        result = self.embedded("rule", r)
        expected, _ = self.reference_rule(r)
        self.assertEqual(result["ranking"], expected)
        self.assertEqual(result["ranking"][-1]["score"], -5)

    @RULE_REQUIRED
    def test_rule_empty_catalog_is_only_completed_empty(self):
        result = self.embedded("rule", self.request(n=0))
        self.assertEqual(result["status"], "EMPTY")
        self.assertEqual(result["ranking"], [])

    @RULE_REQUIRED
    def test_r1_cold_worker_initializes_after_denied_cache_write(self):
        f, package = self.package()
        out, result = self.run_producer("rule", package)
        self.assertEqual(result["prediction_status"], "SUCCESS", result)
        artifact = self.artifact(result); directory = Path(result["prediction_dir"])
        runtime = artifact["telemetry"]["cache_runtime"]
        self.assertFalse(runtime["initialized_before"])
        self.assertTrue(runtime["initialized_after"])
        self.assertTrue(runtime["tmp_dir_restored"])
        self.assertFalse(runtime["prewarm_outside_worker"])
        self.assertEqual(Path(runtime["directory"]), directory/".jieba-cache-r1")
        self.assertFalse((directory/".jieba-cache-r1").exists())
        self.assertFalse((directory/"jieba.cache").exists())
        stderr = (directory/"worker.stderr.log").read_text(encoding="utf-8")
        self.assertIn("PermissionError", stderr)
        self.assertIn("READ_OR_WRITE_OUTSIDE_PREDICTION_CAPABILITY", stderr)
        r, _, _ = adapter.load_package(package)
        self.assertEqual(artifact["ranking"], self.reference_rule(r)[0])

    @RULE_REQUIRED
    def test_r1_explicit_tmp_dir_avoids_default_tempdir_probe(self):
        f, package = self.package(); raw, _ = bridge.capsule_bytes("rule")
        probe = self.h.out("no-default-probe.py")
        probe.write_bytes(raw + b'''
_r1_predict = predict
def predict(request):
    import jieba, tempfile
    assert not jieba.dt.initialized, 'must be a cold worker'
    previous = tempfile.gettempdir
    def forbidden():
        raise AssertionError('UNEXPECTED_DEFAULT_TEMPDIR_DISCOVERY')
    tempfile.gettempdir = forbidden
    try:
        return _r1_predict(request)
    finally:
        tempfile.gettempdir = previous
''')
        output = self.h.out("no-default-probe")
        receipt = adapter.predict(package, probe, output)
        self.assertEqual(receipt["prediction_status"], "SUCCESS", (output/"prediction.json").read_text())
        self.assertTrue(adapter.read(output/"prediction.json")[0]["telemetry"]["cache_runtime"]["initialized_after"])

    @RULE_REQUIRED
    def test_r1_existing_untrusted_cache_is_not_selected_or_changed(self):
        f, package = self.package()
        original_run = subprocess.run; seeded = []
        def seed_cache_then_launch(*args, **kwargs):
            # Test controller only, BEFORE the unchanged B1 child starts. No
            # external dictionary or user cache is used, copied, or trusted.
            directory = Path(kwargs["cwd"])
            cache = directory/"jieba.cache"
            cache.write_bytes(b'SYNTHETIC_UNTRUSTED_CACHE_DO_NOT_LOAD')
            seeded.append(cache)
            return original_run(*args, **kwargs)
        with mock.patch.object(subprocess, "run", side_effect=seed_cache_then_launch):
            out, result = self.run_producer("rule", package)
        self.assertEqual(result["prediction_status"], "SUCCESS", result)
        self.assertEqual(len(seeded), 1)
        self.assertEqual(seeded[0].read_bytes(), b'SYNTHETIC_UNTRUSTED_CACHE_DO_NOT_LOAD')
        artifact = self.artifact(result)
        self.assertFalse(artifact["telemetry"]["cache_runtime"]["initialized_before"])
        self.assertTrue(artifact["telemetry"]["cache_runtime"]["initialized_after"])
        stderr = (Path(result["prediction_dir"])/"worker.stderr.log").read_text(encoding="utf-8")
        self.assertNotIn("Loading model from cache", stderr)
        self.assertIn("READ_OR_WRITE_OUTSIDE_PREDICTION_CAPABILITY", stderr)
        r, _, _ = adapter.load_package(package)
        self.assertEqual(artifact["ranking"], self.reference_rule(r)[0])

    @RULE_REQUIRED
    def test_r1_unexpected_existing_cache_slot_fails_closed(self):
        f, package = self.package(); original_run = subprocess.run
        def create_slot_then_launch(*args, **kwargs):
            slot = Path(kwargs["cwd"])/".jieba-cache-r1"
            slot.mkdir()
            (slot/"jieba.cache").write_bytes(b'SYNTHETIC_DO_NOT_TRUST')
            return original_run(*args, **kwargs)
        with mock.patch.object(subprocess, "run", side_effect=create_slot_then_launch):
            out, result = self.run_producer("rule", package)
        self.assertEqual(result["prediction_status"], "EXECUTION_ERROR")
        artifact = self.artifact(result)
        self.assertIn("WORKER_CACHE_SLOT_EXISTS", artifact["error"]["detail"])
        self.assertNotIn("ranking", artifact)

    @RULE_REQUIRED
    def test_r1_rule_worker_retains_gt_write_and_network_denials(self):
        f, package = self.package(); raw, _ = bridge.capsule_bytes("rule")
        probe = self.h.out("guard-probe.py")
        tail = '''
_r1_predict = predict
def predict(request):
    result = _r1_predict(request)
    import socket
    from pathlib import Path
    denied = {}
    for name, action in [
        ('gt_read', lambda: open(GT_PATH_LITERAL, 'rb')),
        ('extra_write', lambda: open(Path(__file__).parent/'r1-forbidden.txt', 'xb')),
        ('network', lambda: socket.socket().connect(('127.0.0.1', 9)))
    ]:
        try:
            handle = action()
            if hasattr(handle, 'close'): handle.close()
        except PermissionError:
            denied[name] = True
        else:
            raise AssertionError('B1_BOUNDARY_WEAKENED: '+name)
    result['telemetry']['r1_guard_probe'] = denied
    return result
'''.replace('GT_PATH_LITERAL', repr(str(f["gt"])))
        probe.write_bytes(raw + tail.encode("utf-8"))
        output = self.h.out("guard-probe")
        receipt = adapter.predict(package, probe, output)
        self.assertEqual(receipt["prediction_status"], "SUCCESS", (output/"prediction.json").read_text())
        artifact = adapter.read(output/"prediction.json")[0]
        self.assertEqual(artifact["telemetry"]["r1_guard_probe"], dict(gt_read=True, extra_write=True, network=True))
        self.assertFalse((output/"r1-forbidden.txt").exists())


if __name__ == "__main__": unittest.main()
