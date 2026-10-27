"""EXP001-B1: only synthetic issues, repositories, GT, producers and rankings."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("experiment_adapter_tested", ROOT / "src/experiment_adapter.py")
ea = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ea)


class ExperimentAdapterTest(unittest.TestCase):
    def setUp(self):
        base = os.environ.get("EXP001_B1_TEST_ROOT")
        if base: Path(base).mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="case-", dir=base))
        (self.temp / "test-id.txt").write_text(self.id(), encoding="utf-8")
        self.serial = 0

    def out(self, name="output"):
        self.serial += 1
        return self.temp / (str(self.serial) + "-" + name)

    def save(self, path, value):
        Path(path).write_bytes(ea.SE.json_bytes(value))

    def fixture(self, cid="fixture-a"):
        folder = self.out("fixture"); folder.mkdir()
        repo = folder / "repo"; rel = "src/main/java/p/A.java"
        declarations = [("f", "int x", "int", True), ("f", "String x", "String", True),
                        ("g", "", "", False), ("h", "", "", True), ("i", "", "", True), ("j", "", "", True)]
        lines = ["package p;", "abstract class A { // " + cid]
        for name, params, sig, body in declarations:
            lines.append('  void '+name+'('+params+') { sink("x"); }' if body else '  abstract void '+name+'();')
        lines.append("}")
        raw = ("\n".join(lines)+"\n").encode(); source = repo / rel
        source.parent.mkdir(parents=True); source.write_bytes(raw)
        sh = ea.digest(raw); ev = ea.SE.load_local("evidence_validator"); methods = []
        for i, (name, params, sig, body) in enumerate(declarations):
            lineno = i+3; signature = name+"("+sig+")"; mid = "java-source-v1:p.A#"+signature
            evidence = []
            if body:
                col = lines[i+2].index("sink")+1; snippet='sink("x")'; coords=[lineno,col,lineno,col+len(snippet)-1]
                evidence = [dict(evidence_id=ev.compute_evidence_id(rel,sh,mid,"METHOD_CALL",coords),
                                 evidence_type="METHOD_CALL",file_path=rel,source_snippet=snippet,**dict(zip(ev.COORDS,coords)))]
            methods.append(dict(name=name,line=lineno,signature=signature,qualified_class_name="p.A",method_id=mid,
                annotations=[],strings=["x"] if body else [],calls=["sink"] if body else [],has_body=body,
                identity_status="AVAILABLE",location_status="AVAILABLE",start_line=lineno,start_column=3,
                end_line=lineno,end_column=len(lines[i+2]),evidence_occurrences=evidence,evidence_omissions=0))
        index=[dict(file=rel,package="p",classes=[dict(name="A",methods=methods)],source_sha256=sh,
                    parse_status="PARSED",parse_problems=[],limitations=[])]
        ip=folder/"index.v2.json";self.save(ip,index)
        meta=dict(schema_version="2.0",index_file=ip.name,index_sha256=ea.digest(ip.read_bytes()),parser_version="3.26.4",
          method_id_scheme="java-source-v1",position_convention=ev.POSITION,source_encoding="UTF-8",language_level="BLEEDING_EDGE",
          preprocess_unicode_escapes=False,source_roots=["src/main/java"],files_total=1,methods_total=len(methods),
          evidence_occurrences=sum(len(m["evidence_occurrences"]) for m in methods),identity_unavailable_methods=0,
          location_unavailable_methods=0,identity_conflict_groups=0,identity_conflict_methods=0,conflicting_method_ids=[],
          parse_counts={s:int(s=="PARSED") for s in ev.PARSE_STATES})
        self.save(Path(str(ip)+".meta.json"),meta)
        manifest=ea.SE.manifest_for(index);sid="sha256:"+ea.SE.manifest_hash(manifest)
        title="  Synthetic title";body="unchanged body\r\r\nlast line\n";query=title+"\n\n"+body
        row=dict(id=cid,repo_id="fixture/repo",repo_path=str(repo),source_snapshot_id=sid,issue_text=query,
          issue_input=dict(title=title,title_sha256=ea.digest(title.encode()),original_body=body,body_sha256=ea.digest(body.encode()),
            body_bytes=len(body.encode()),issue_text_sha256=ea.digest(query.encode()),issue_text_bytes=len(query.encode()),
            contract="TITLE_LF_LF_ORIGINAL_BODY_V1",separator="\n\n",encoding="UTF-8"))
        bp=folder/"input.json";self.save(bp,[row]);patch=folder/"synthetic.patch";patch.write_text("fixture patch\n")
        target=dict(file_path=rel,method_id=methods[0]["method_id"])
        side=dict(schema_version="1.0",benchmark_sha256=ea.digest(bp.read_bytes()),protocol=dict(version="strict-localization-v1",
          evaluation_task="PRE_FIX_EXISTING_METHOD_LOCALIZATION",identity_scheme="java-source-v1",primary_hit_policy="ANY_CONFIRMED_TARGET"),
          snapshots=[dict(repo_id="fixture/repo",source_snapshot_id=sid,phase="PRE_FIX",source_manifest=manifest,
            source_manifest_sha256=ea.SE.manifest_hash(manifest),index_sha256=ea.digest(ip.read_bytes()),metadata_sha256=ea.digest(Path(str(ip)+".meta.json").read_bytes()))],
          cases=[dict(case_id=cid,repo_id="fixture/repo",source_snapshot_id=sid,annotation_status="CONFIRMED",file_gt_status="CONFIRMED",
            target_set_status="FROZEN",accepted_targets=[target],accepted_files=[rel],proposed_targets=[],required_groups=[],
            reason_codes=["SYNTHETIC_ONLY"],review=dict(reviewer="synthetic fixture",reviewed_at="2026-10-21"),
            basis=[dict(kind="PATCH",reference=str(patch),sha256=ea.digest(patch.read_bytes()),note="GT_SECRET_CANARY_7821")])])
        gp=folder/"gt.json";self.save(gp,side)
        return dict(cid=cid,folder=folder,repo=repo,index=ip,benchmark=bp,gt=gp,raw_index=index,sid=sid)

    def package(self, fixture):
        p=self.out("package")
        result=ea.prepare(fixture["benchmark"],fixture["index"],fixture["repo"],fixture["cid"],p)
        self.assertEqual(result["status"],"PREPARED",result)
        return p

    def producer(self, tail=None, order=None, empty=False):
        p=self.out("producer.py")
        body="keys=('repo_id','source_snapshot_id','file_path','method_id')\nranking=[{k:r[k] for k in keys} for r in request['catalog']]\n"
        if order is not None:body+="ranking=[ranking[i] for i in "+repr(order)+"]\n"
        body+=tail if tail is not None else ("return {'status':'EMPTY','ranking':[]}\n" if empty else "return {'status':'SUCCESS','ranking':ranking}\n")
        p.write_text("def predict(request):\n"+textwrap.indent(body,"    "),encoding="utf-8")
        return p

    def prediction(self, package, producer=None, timeout=30):
        out=self.out("prediction");receipt=ea.predict(package,producer or self.producer(),out,timeout)
        return out,receipt

    def cohort(self, kinds=None, stage="rule"):
        entries=[];fixtures=[];predictions=[]
        for i,cid in enumerate(["fixture-a","fixture-b","fixture-c","fixture-d"]):
            f=self.fixture(cid);p=self.package(f);kind=(kinds or [None]*4)[i]
            if kind=="error":producer=self.producer("raise RuntimeError('synthetic crash')\n")
            elif kind=="empty":producer=self.producer(empty=True)
            elif kind=="rerank":producer=self.producer("return {'status':'SUCCESS','ranking':ranking,'rerank':{'status':'VALID','ranking':ranking[:5][::-1]}}\n")
            elif kind=="fallback":producer=self.producer("return {'status':'SUCCESS','ranking':ranking,'rerank':{'status':'FALLBACK','ranking':[]}}\n")
            else:producer=self.producer(order=kind)
            pred,receipt=self.prediction(p,producer)
            entries.append(dict(case_id=cid,benchmark=str(f["benchmark"]),gt_sidecar=str(f["gt"]),repo=str(f["repo"]),
                                index=str(f["index"]),prediction_dir=str(pred),prediction_sha256=receipt["prediction_sha256"]))
            fixtures.append(f);predictions.append(pred)
        job=dict(schema_version="evaluation-cohort-v1",case_ids=[e["case_id"] for e in entries],candidate_k=5,stage=stage,cases=entries)
        jp=self.out("cohort.json");self.save(jp,job)
        return jp,job,fixtures,predictions

    def aggregate(self,jp):return ea.aggregate(jp,self.out("aggregate"))

    def test_prepare_preserves_raw_issue_special_line_endings(self):
        f=self.fixture();p=self.package(f);r,_,_=ea.load_package(p)
        self.assertEqual(r["issue_text"],ea.read(f["benchmark"])[0][0]["issue_text"])
        self.assertTrue(r["issue_text"].startswith("  "));self.assertIn("\r\r\n",r["issue_text"])

    def test_catalog_keeps_overloads_bodyless_and_ordinal(self):
        p=self.package(self.fixture());r,_,_=ea.load_package(p)
        self.assertEqual([x["ordinal"] for x in r["catalog"]],list(range(6)))
        self.assertNotEqual(r["catalog"][0]["method_id"],r["catalog"][1]["method_id"])
        self.assertFalse(r["catalog"][2]["has_body"])

    def test_preparation_does_not_read_gt_or_follow_source_reference(self):
        f=self.fixture();b=ea.read(f["benchmark"])[0];b[0]["issue_input"]["source_reference"]=str(f["gt"]);self.save(f["benchmark"],b)
        original=Path.read_bytes
        def guarded(path):
            if Path(path)==f["gt"]:raise AssertionError("GT opened during prepare")
            return original(path)
        with mock.patch.object(Path,"read_bytes",guarded):p=self.package(f)
        raw=(p/"request.json").read_text();self.assertNotIn("GT_SECRET_CANARY",raw);self.assertNotIn(str(f["gt"]),raw)
        self.assertNotIn("accepted_targets",raw)

    def test_gt_field_in_input_rejected(self):
        f=self.fixture();b=ea.read(f["benchmark"])[0];b[0]["accepted_targets"]=[];self.save(f["benchmark"],b)
        result=ea.prepare(f["benchmark"],f["index"],f["repo"],f["cid"],self.out())
        self.assertEqual(result["status"],"INVALID_INPUT")

    def test_invalid_input_json_retains_failure(self):
        f=self.fixture();f["benchmark"].write_bytes(b"{");out=self.out()
        self.assertEqual(ea.prepare(f["benchmark"],f["index"],f["repo"],f["cid"],out)["status"],"INVALID_INPUT")
        self.assertTrue((out/"prepare-error.json").exists())

    def test_duplicate_index_method_not_prepared(self):
        f=self.fixture();idx=f["raw_index"];idx[0]["classes"][0]["methods"].append(copy.deepcopy(idx[0]["classes"][0]["methods"][0]));self.save(f["index"],idx)
        self.assertEqual(ea.prepare(f["benchmark"],f["index"],f["repo"],f["cid"],self.out())["status"],"INVALID_INPUT")

    def test_missing_method_id_not_inferred(self):
        f=self.fixture();idx=f["raw_index"];del idx[0]["classes"][0]["methods"][0]["method_id"];self.save(f["index"],idx)
        self.assertEqual(ea.prepare(f["benchmark"],f["index"],f["repo"],f["cid"],self.out())["status"],"INVALID_INPUT")

    def test_changed_source_is_not_prepared(self):
        f=self.fixture();(f["repo"]/'src/main/java/p/A.java').write_text("changed")
        self.assertEqual(ea.prepare(f["benchmark"],f["index"],f["repo"],f["cid"],self.out())["status"],"INVALID_INPUT")

    def test_extra_unindexed_java_is_not_prepared(self):
        f=self.fixture();(f["repo"]/'src/main/java/p/B.java').write_text("class B {}")
        self.assertEqual(ea.prepare(f["benchmark"],f["index"],f["repo"],f["cid"],self.out())["status"],"INVALID_INPUT")

    def test_output_directory_never_overwritten(self):
        f=self.fixture();out=self.out();out.mkdir();(out/'sentinel').write_text('keep')
        with self.assertRaises(ea.AdapterError):ea.prepare(f["benchmark"],f["index"],f["repo"],f["cid"],out)
        self.assertEqual((out/'sentinel').read_text(),'keep')

    def test_output_inside_source_repository_rejected(self):
        f=self.fixture()
        with self.assertRaises(ea.AdapterError):ea.prepare(f["benchmark"],f["index"],f["repo"],f["cid"],f['repo']/'new')

    def test_successful_prediction_full_identity_and_detached_hash(self):
        p=self.package(self.fixture());out,receipt=self.prediction(p);a,h=ea.read(out/'prediction.json')
        self.assertEqual(a['prediction_status'],'SUCCESS',a);self.assertEqual(receipt['prediction_sha256'],h)
        self.assertEqual(len(a['ranking']),6);self.assertEqual(a['candidate_count'],6)
        self.assertEqual(a['telemetry']['usage']['status'],'UNKNOWN');self.assertNotIn('gt_sidecar_sha256',a)

    def test_worker_cannot_read_adjacent_gt(self):
        f=self.fixture();p=self.package(f)
        body="open("+repr(str(f['gt']))+").read()\nreturn {'status':'EMPTY','ranking':[]}\n"
        out,_=self.prediction(p,self.producer(body));a,_=ea.read(out/'prediction.json')
        self.assertEqual(a['prediction_status'],'EXECUTION_ERROR')
        self.assertIn('OUTSIDE_PREDICTION_CAPABILITY',a['error']['detail'])
        self.assertNotIn('GT_SECRET_CANARY_7821',(out/'worker.stdout.log').read_text())

    def test_api_secret_not_forwarded_to_worker(self):
        p=self.package(self.fixture());producer=self.producer("import os\nassert 'DEEPSEEK_API_KEY' not in os.environ\nreturn {'status':'SUCCESS','ranking':ranking}\n")
        with mock.patch.dict(os.environ,{'DEEPSEEK_API_KEY':'synthetic-secret'}):out,_=self.prediction(p,producer)
        self.assertEqual(ea.read(out/'prediction.json')[0]['prediction_status'],'SUCCESS')

    def test_exception_cannot_be_empty_prediction(self):
        out,_=self.prediction(self.package(self.fixture()),self.producer("raise RuntimeError('synthetic crash')\n"))
        a,_=ea.read(out/'prediction.json');self.assertEqual(a['prediction_status'],'EXECUTION_ERROR');self.assertNotIn('ranking',a)

    def test_completed_empty_prediction_is_distinct(self):
        out,_=self.prediction(self.package(self.fixture()),self.producer(empty=True))
        a,_=ea.read(out/'prediction.json');self.assertEqual(a['prediction_status'],'EMPTY');self.assertEqual(a['ranking'],[])

    def test_timeout_retained_as_execution_error(self):
        out,_=self.prediction(self.package(self.fixture()),self.producer("import time\ntime.sleep(2)\n"),timeout=.1)
        a,_=ea.read(out/'prediction.json');self.assertEqual(a['prediction_status'],'EXECUTION_ERROR');self.assertEqual(a['error']['reason_code'],'WORKER_TIMEOUT')

    def test_json_noise_is_invalid_not_empty(self):
        out,_=self.prediction(self.package(self.fixture()),self.producer("print('not-json')\nreturn {'status':'SUCCESS','ranking':ranking}\n"))
        self.assertEqual(ea.read(out/'prediction.json')[0]['prediction_status'],'INVALID_PREDICTION')
        self.assertIn('not-json',(out/'worker.stdout.log').read_text())

    def test_cross_snapshot_identity_invalid(self):
        out,_=self.prediction(self.package(self.fixture()),self.producer("ranking[0]['source_snapshot_id']='sha256:'+'0'*64\nreturn {'status':'SUCCESS','ranking':ranking}\n"))
        self.assertEqual(ea.read(out/'prediction.json')[0]['prediction_status'],'INVALID_PREDICTION')

    def test_same_name_wrong_file_invalid(self):
        out,_=self.prediction(self.package(self.fixture()),self.producer("ranking[0]['file_path']='src/main/java/other/A.java'\nreturn {'status':'SUCCESS','ranking':ranking}\n"))
        self.assertEqual(ea.read(out/'prediction.json')[0]['prediction_status'],'INVALID_PREDICTION')

    def test_duplicate_prediction_identity_invalid(self):
        out,_=self.prediction(self.package(self.fixture()),self.producer(order=[0,0,2,3,4,5]))
        self.assertEqual(ea.read(out/'prediction.json')[0]['prediction_status'],'INVALID_PREDICTION')

    def test_missing_method_in_full_order_invalid(self):
        out,_=self.prediction(self.package(self.fixture()),self.producer(order=[0,1,2,3,4]))
        self.assertEqual(ea.read(out/'prediction.json')[0]['prediction_status'],'INVALID_PREDICTION')

    def test_invalid_rerank_permutation_invalid(self):
        tail="return {'status':'SUCCESS','ranking':ranking,'rerank':{'status':'VALID','ranking':[ranking[5]]+ranking[1:5]}}\n"
        out,_=self.prediction(self.package(self.fixture()),self.producer(tail))
        self.assertEqual(ea.read(out/'prediction.json')[0]['prediction_status'],'INVALID_PREDICTION')

    def test_fallback_may_not_change_order(self):
        tail="return {'status':'SUCCESS','ranking':ranking,'rerank':{'status':'FALLBACK','ranking':ranking[:5][::-1]}}\n"
        out,_=self.prediction(self.package(self.fixture()),self.producer(tail))
        self.assertEqual(ea.read(out/'prediction.json')[0]['prediction_status'],'INVALID_PREDICTION')

    def test_b1_real_case_guard_without_real_issue_data(self):
        f=self.fixture('jsoup-2130');out,_=self.prediction(self.package(f))
        a,_=ea.read(out/'prediction.json');self.assertEqual(a['prediction_status'],'INVALID_PREDICTION')
        self.assertIn('B1_REAL_PREDICTION_DISABLED',a['error']['reason_code']);self.assertFalse((out/'worker.stdout.log').exists())

    def test_package_hash_tampering_not_executed(self):
        p=self.package(self.fixture());r,_=ea.read(p/'request.json');r['issue_text']='changed';self.save(p/'request.json',r)
        out,_=self.prediction(p);a,_=ea.read(out/'prediction.json');self.assertEqual(a['prediction_status'],'INVALID_PREDICTION');self.assertFalse((out/'worker.stdout.log').exists())

    def test_stable_score_tie_uses_original_ordinal(self):
        p=self.package(self.fixture());r,_,_=ea.load_package(p)
        scored=[{**{k:x[k] for k in ea.FIELDS},'score':1} for x in r['catalog'][::-1]]
        result=ea.stable_score_order(scored,r['catalog']);self.assertEqual([x['method_id'] for x in result],[x['method_id'] for x in r['catalog']])

    def test_nan_and_boolean_scores_rejected(self):
        p=self.package(self.fixture());r,_,_=ea.load_package(p)
        for bad in [float('nan'),True]:
            scored=[{**{k:x[k] for k in ea.FIELDS},'score':bad} for x in r['catalog']]
            with self.assertRaises(ea.AdapterError):ea.stable_score_order(scored,r['catalog'])

    def test_four_case_metrics_full_mrr_and_empty_miss(self):
        jp,job,_,_=self.cohort([None,[1,0,2,3,4,5],[1,2,3,4,5,0],'empty']);r=self.aggregate(jp)
        self.assertEqual(r['tool_status'],'OK',r['issues']);self.assertEqual(r['denominators']['method'],4)
        self.assertEqual(r['metrics']['strict_method_top1']['value'],.25);self.assertEqual(r['metrics']['strict_method_top5']['value'],.5)
        self.assertAlmostEqual(r['metrics']['MRR']['value'],5/12);self.assertEqual(r['first_stage_recall_at5']['value'],.5)

    def test_partial_execution_failure_keeps_four_and_null_aggregate(self):
        jp,_,_,_=self.cohort([None,None,'error',None]);r=self.aggregate(jp)
        self.assertEqual(r['tool_status'],'INVALID_RUN');self.assertEqual(len(r['cases']),4)
        self.assertEqual(r['denominators']['method'],4);self.assertIsNone(r['metrics']['strict_method_top1']['value'])
        self.assertEqual(sum(x.get('method_metrics') is not None for x in r['cases']),3)

    def test_missing_case_not_silently_removed(self):
        jp,job,_,_=self.cohort();job['cases'].pop();self.save(jp,job);r=self.aggregate(jp)
        self.assertEqual(len(r['cases']),4);self.assertEqual(r['cases'][3]['ranking_status'],'MISSING_CASE')
        self.assertEqual(r['denominators']['total'],4);self.assertIsNone(r['metrics']['MRR']['value'])
        self.assertEqual(r['metrics']['method_coverage']['numerator'],3)

    def test_cohort_cannot_change_n_or_k(self):
        for field,value in [('case_ids',['fixture-a']),('candidate_k',3)]:
            jp,job,_,_=self.cohort();job[field]=value;self.save(jp,job);r=self.aggregate(jp)
            self.assertEqual(r['exit_code'],2);self.assertEqual(r['denominators']['total'],4);self.assertEqual(len(r['cases']),4)

    def test_prediction_content_hash_mismatch_invalidates_run(self):
        jp,_,_,pred=self.cohort();a,_=ea.read(pred[0]/'prediction.json');a['ranking'].reverse();self.save(pred[0]/'prediction.json',a)
        r=self.aggregate(jp);self.assertEqual(r['exit_code'],2);self.assertIn('PREDICTION_FILE_HASH_MISMATCH',str(r['issues']))

    def test_receipt_hash_cannot_override_external_pin(self):
        jp,_,_,pred=self.cohort();receipt,_=ea.read(pred[0]/'receipt.json');receipt['prediction_sha256']='0'*64;self.save(pred[0]/'receipt.json',receipt)
        self.assertEqual(self.aggregate(jp)['exit_code'],2)

    def test_corrupt_prediction_json_preserves_all_cases(self):
        jp,_,_,pred=self.cohort();(pred[0]/'prediction.json').write_bytes(b'{');r=self.aggregate(jp)
        self.assertEqual(r['exit_code'],2);self.assertEqual(len(r['cases']),4);self.assertIsNone(r['metrics']['MRR']['value'])

    def test_corrupt_cohort_json_has_structured_failure(self):
        jp=self.out('bad.json');jp.write_bytes(b'{');r=self.aggregate(jp)
        self.assertEqual(r['exit_code'],2);self.assertEqual(len(r['cases']),4);self.assertEqual(r['denominators']['total'],4)

    def test_duplicate_cohort_identity_invalid(self):
        jp,job,_,_=self.cohort();job['cases'][1]=copy.deepcopy(job['cases'][0]);self.save(jp,job)
        self.assertEqual(self.aggregate(jp)['exit_code'],2)

    def test_all_gt_preflight_before_any_prediction_file_read(self):
        jp,job,_,_=self.cohort();events=[];original=ea.read
        def traced(path):
            events.append(Path(path).name);return original(path)
        with mock.patch.object(ea,'read',side_effect=traced):r=self.aggregate(jp)
        self.assertEqual(r['exit_code'],0);self.assertEqual(events[:events.index('prediction.json')].count('gt.json'),4)

    def test_gt_ineligible_prevents_opening_any_prediction(self):
        jp,_,fixtures,_=self.cohort();side=ea.read(fixtures[0]['gt'])[0];side['cases'][0]['annotation_status']='PENDING_REVIEW';self.save(fixtures[0]['gt'],side)
        original=ea.read
        def traced(path):
            if Path(path).name=='prediction.json':raise AssertionError('prediction read before eligibility')
            return original(path)
        with mock.patch.object(ea,'read',side_effect=traced):r=self.aggregate(jp)
        self.assertEqual(r['exit_code'],2);self.assertEqual(r['denominators']['total'],4)

    def test_gt_hash_added_only_in_separate_conversion(self):
        jp,_,_,pred=self.cohort();self.assertNotIn('gt_sidecar_sha256',ea.read(pred[0]/'prediction.json')[0])
        out=self.out('aggregate');r=ea.aggregate(jp,out);self.assertEqual(r['exit_code'],0)
        self.assertIn('gt_sidecar_sha256',ea.read(out/'0.strict-ranking.json')[0])

    def test_rerank_fixed_pool_separate_from_full_tail_mrr(self):
        jp,_,_,_=self.cohort(['rerank']*4,stage='rerank');r=self.aggregate(jp)
        self.assertEqual(r['exit_code'],0,r['issues']);self.assertIn('MRR@5',r['fixed_pool_rerank'])
        self.assertAlmostEqual(r['fixed_pool_rerank']['MRR@5']['value'],.2);self.assertEqual(r['first_stage_recall_at5']['value'],1)

    def test_fallback_is_not_valid_llm_accuracy(self):
        jp,_,_,_=self.cohort(['fallback']*4,stage='rerank');r=self.aggregate(jp)
        self.assertEqual(r['exit_code'],2);self.assertIsNone(r['metrics']['strict_method_top1']['value'])
        self.assertEqual(r['first_stage_recall_at5']['value'],1)

    def test_producer_source_hash_tampering_detected(self):
        jp,_,_,pred=self.cohort();(pred[0]/'producer.py').write_text('# changed')
        self.assertIn('PRODUCER_SOURCE_HASH_MISMATCH',str(self.aggregate(jp)['issues']))

    def test_prediction_output_cannot_write_into_source_repo(self):
        f=self.fixture();p=self.package(f)
        with self.assertRaises(ea.AdapterError):ea.predict(p,self.producer(),f['repo']/'new')

    def test_preflight_needs_no_predictions_and_has_no_accuracy(self):
        entries=[]
        for cid in ['fixture-a','fixture-b','fixture-c','fixture-d']:
            f=self.fixture(cid);entries.append(dict(case_id=cid,benchmark=str(f['benchmark']),gt_sidecar=str(f['gt']),repo=str(f['repo']),index=str(f['index'])))
        job=dict(schema_version='evaluation-cohort-v1',candidate_k=5,stage='rule',case_ids=[e['case_id'] for e in entries],cases=entries)
        jp=self.out('cohort.json');self.save(jp,job)
        with mock.patch.object(ea,'_read_prediction',side_effect=AssertionError('must not read predictions')):
            r=ea.aggregate(jp,self.out(),preflight_only=True)
        self.assertEqual(r['exit_code'],0);self.assertEqual(r['evaluation_status'],'NOT_EVALUABLE')
        self.assertEqual(r['eligible_method_cases'],4);self.assertIsNone(r['metrics']['strict_method_top1']['value'])
        self.assertTrue(all(x['ranking_status']=='NOT_REQUESTED' for x in r['cases']))

    def test_changing_gt_cannot_change_prediction(self):
        f=self.fixture();p=self.package(f);producer=self.producer();a,ra=self.prediction(p,producer)
        side=ea.read(f['gt'])[0];side['cases'][0]['accepted_targets'][0]['method_id']='java-source-v1:p.A#j()';self.save(f['gt'],side)
        b,rb=self.prediction(p,producer)
        self.assertEqual(ra['prediction_sha256'],rb['prediction_sha256'])
        self.assertEqual((a/'prediction.json').read_bytes(),(b/'prediction.json').read_bytes())

    def test_self_consistent_hashes_cannot_fake_catalog_index_binding(self):
        jp,job,_,pred=self.cohort();directory=pred[0]
        req,_=ea.read(directory/'request.json');req['catalog'][2]['has_body']=True
        req['catalog_sha256']=ea.digest(ea.canonical(req['catalog']));self.save(directory/'request.json',req)
        rh=ea.digest((directory/'request.json').read_bytes());a,_=ea.read(directory/'prediction.json')
        a['request_sha256']=rh;a['catalog_sha256']=req['catalog_sha256'];self.save(directory/'prediction.json',a)
        ah=ea.digest((directory/'prediction.json').read_bytes());receipt,_=ea.read(directory/'receipt.json');receipt.update(request_sha256=rh,prediction_sha256=ah);self.save(directory/'receipt.json',receipt)
        job['cases'][0]['prediction_sha256']=ah;self.save(jp,job)
        r=self.aggregate(jp);self.assertIn('REQUEST_CATALOG_DIFFERS_FROM_INDEX',str(r['issues']))

    def test_request_with_gt_extension_is_blocked_before_producer(self):
        p=self.package(self.fixture());req,_=ea.read(p/'request.json');req['accepted_targets']=[];self.save(p/'request.json',req)
        info,_=ea.read(p/'package.json');info['request_sha256']=ea.digest((p/'request.json').read_bytes());self.save(p/'package.json',info)
        out,_=self.prediction(p);a,_=ea.read(out/'prediction.json')
        self.assertEqual(a['prediction_status'],'INVALID_PREDICTION');self.assertFalse((out/'worker.stdout.log').exists())

    def test_duplicate_request_method_ids_are_not_silently_deduplicated(self):
        p=self.package(self.fixture());req,_,_=ea.load_package(p);req['catalog'][1]['method_id']=req['catalog'][0]['method_id'];req['catalog_sha256']=ea.digest(ea.canonical(req['catalog']))
        with self.assertRaises(ea.AdapterError):ea.validate_request(req)

    def test_directory_access_to_gt_folder_denied(self):
        f=self.fixture();p=self.package(f);tail='import os\nos.listdir('+repr(str(f['folder']))+")\nreturn {'status':'EMPTY','ranking':[]}\n"
        out,_=self.prediction(p,self.producer(tail));a,_=ea.read(out/'prediction.json')
        self.assertEqual(a['prediction_status'],'EXECUTION_ERROR');self.assertIn('DIRECTORY_OUTSIDE_CAPABILITY',a['error']['detail'])

    def test_partial_run_metrics_are_null_not_successes_divided_by_three(self):
        jp,_,_,_=self.cohort([None,None,None,'error']);r=self.aggregate(jp)
        for key in ['strict_method_top1','strict_method_top5','strict_file_top1','MRR']:
            self.assertEqual(r['metrics'][key]['denominator'],4);self.assertIsNone(r['metrics'][key]['value'])

    def test_fixed_pool_mrr_does_not_count_unmodified_tail(self):
        jp,job,_,_=self.cohort([[1,2,3,4,5,0]]*4)
        # Replace only the synthetic sealed predictions with valid no-change Top-5 reranks.
        for e in job['cases']:
            directory=Path(e['prediction_dir']);a,_=ea.read(directory/'prediction.json')
            a['rerank']={'status':'VALID','ranking':a['ranking'][:5]};self.save(directory/'prediction.json',a)
            ah=ea.digest((directory/'prediction.json').read_bytes());receipt,_=ea.read(directory/'receipt.json');receipt['prediction_sha256']=ah;self.save(directory/'receipt.json',receipt);e['prediction_sha256']=ah
        job['stage']='rerank';self.save(jp,job);r=self.aggregate(jp)
        self.assertEqual(r['exit_code'],0,r['issues']);self.assertAlmostEqual(r['metrics']['MRR']['value'],1/6)
        self.assertEqual(r['fixed_pool_rerank']['MRR@5']['value'],0);self.assertEqual(r['first_stage_recall_at5']['value'],0)

    def test_unknown_or_mixed_cohort_ids_are_rejected(self):
        jp,job,_,_=self.cohort();job['case_ids'][0]='unapproved-case';self.save(jp,job)
        r=self.aggregate(jp);self.assertEqual(r['exit_code'],2);self.assertEqual(len(r['cases']),4)

    def test_non_object_cohort_has_structured_four_slot_failure(self):
        jp=self.out('bad-list.json');self.save(jp,[]);r=self.aggregate(jp)
        self.assertEqual(r['exit_code'],2);self.assertEqual(len(r['cases']),4);self.assertEqual(r['metrics']['method_coverage']['value'],0)

    def test_worker_interrupt_is_not_a_legal_empty(self):
        out,_=self.prediction(self.package(self.fixture()),self.producer('raise KeyboardInterrupt()\n'))
        a,_=ea.read(out/'prediction.json');self.assertEqual(a['prediction_status'],'EXECUTION_ERROR')
        self.assertIn('KeyboardInterrupt',a['error']['detail'])

    def test_empty_result_with_reported_exception_is_rejected(self):
        tail="return {'status':'EMPTY','ranking':[],'telemetry':{'exceptions':[{'reason':'failed'}]}}\n"
        out,_=self.prediction(self.package(self.fixture()),self.producer(tail))
        self.assertEqual(ea.read(out/'prediction.json')[0]['prediction_status'],'INVALID_PREDICTION')

    def test_missing_prediction_directory_is_not_an_empty_result(self):
        jp,job,_,_=self.cohort();job['cases'][0]['prediction_dir']=str(self.out('not-created'));self.save(jp,job)
        r=self.aggregate(jp);self.assertEqual(r['exit_code'],2);self.assertEqual(len(r['cases']),4)
        self.assertEqual(r['cases'][0]['ranking_status'],'INVALID_PREDICTION');self.assertIsNone(r['metrics']['MRR']['value'])


if __name__ == '__main__':
    unittest.main()
