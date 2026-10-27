"""C1: synthetic permits only. No real Issue ranking or real permit issuance."""
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


def load(path, name):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


auth = load(ROOT/"src/controlled_authorization.py", "tested_controlled_authorization")
ea = load(ROOT/"src/experiment_adapter.py", "c1_existing_adapter")
bridge = load(ROOT/"src/baseline_producers.py", "c1_existing_bridge")
bm = load(ROOT/"src/bm25_baseline.py", "c1_existing_bm25")
helpers = load(ROOT/"tests/test_experiment_adapter.py", "c1_fixture_helpers")
try:
    import jieba
    RULE_AVAILABLE = True
except ImportError: RULE_AVAILABLE = False
RULE_REQUIRED = unittest.skipUnless(RULE_AVAILABLE, "Actual Jieba required; ordinary-user Python310 acceptance PENDING")


class ControlledAuthorizationTest(unittest.TestCase):
    def setUp(self):
        base = os.environ.get("EXP001_C1_TEST_ROOT")
        if base: Path(base).mkdir(parents=True, exist_ok=True)
        self.temp = Path(tempfile.mkdtemp(prefix="c1-case-", dir=base))
        (self.temp/"test-id.txt").write_text(self.id(), encoding="utf-8")
        self.h = helpers.ExperimentAdapterTest(methodName="runTest")
        with mock.patch.dict(os.environ, {"EXP001_B1_TEST_ROOT": str(self.temp)}): self.h.setUp()

    def setup_campaign(self):
        fs = [self.h.fixture("fixture-c1-"+str(i)) for i in range(4)]
        job = dict(schema_version="evaluation-cohort-v1", candidate_k=5, stage="rule",
                   case_ids=[f["cid"] for f in fs], cases=[dict(case_id=f["cid"], benchmark=str(f["benchmark"]),
                   gt_sidecar=str(f["gt"]), repo=str(f["repo"]), index=str(f["index"])) for f in fs])
        jp=self.temp/"qualification-cohort.json";self.h.save(jp,job)
        issuer=self.temp/"issuer"
        eligibility=auth.qualify_synthetic(issuer,jp,self.temp/"qualification")
        packages=[self.h.package(f) for f in fs]
        cases=[auth.describe(f["benchmark"],f["index"],f["repo"],p) for f,p in zip(fs,packages)]
        pp=self.temp/"permit.json";output=self.temp/"campaign"
        ph=auth.issue_synthetic(issuer,"synthetic-run",eligibility,cases,output,pp)
        return dict(fs=fs,packages=packages,cases=cases,issuer=issuer,eligibility=eligibility,permit=pp,ph=ph,output=output,job=jp)

    def ticket(self,c,system="BM25-only",index=0):
        return dict(permit_path=str(c["permit"]),permit_sha256=c["ph"],run_id="synthetic-run",system=system,case_id=c["cases"][index]["case_id"])

    def resign(self,c,mutate):
        permit=auth.read(c["permit"]);mutate(permit["body"])
        key=auth.read(c["issuer"]/"registry.json")["key"];permit["mac"]=auth.sign(key,permit["body"])
        # Test controller owns only a synthetic registry. Repair issuance pin so
        # deeper binding checks, not merely the outer signature, are exercised.
        self.h.save(c["permit"],permit)
        self.h.save(auth.run_slot(c["issuer"],"synthetic-run","issued"),dict(payload_sha256=auth.digest(auth.canonical(permit["body"]))))
        c["ph"]=auth.file_hash(c["permit"])

    def start_job(self,c,system="BM25-only"):
        ticket=self.ticket(c,system);permit,_=auth.verify_disk(ticket)
        auth.put(auth.run_slot(c["issuer"],"synthetic-run","started"),dict(permit_sha256=c["ph"]))
        target=auth.job_output(permit["body"],ticket["case_id"],system);target.mkdir(parents=True)
        capsule=self.temp/"controlled-producer.py"
        capsule.write_bytes(bridge.capsule_bytes("bm25" if system=="BM25-only" else "rule",controlled=True)[0])
        request=auth.read(c["packages"][0]/"request.json")
        return ticket,target,capsule,request

    def test_default_real_cases_still_denied(self):
        f=self.h.fixture("jsoup-2130");p=self.h.package(f)
        out=self.h.out();result=ea.predict(p,self.h.producer(),out)
        self.assertEqual(result["prediction_status"],"INVALID_PREDICTION")
        self.assertIn("B1_REAL_PREDICTION_DISABLED",auth.read(out/'prediction.json')["error"]["detail"])

    def test_no_authorized_boolean(self):
        c=self.setup_campaign();result=ea.predict(c["packages"][0],self.h.producer(),self.h.out(),authorization={"authorized":True})
        self.assertEqual(result["prediction_status"],"INVALID_PREDICTION")

    def test_synthetic_issuer_binds_four_cases_and_two_systems(self):
        c=self.setup_campaign();p,registry=auth.verify_disk(self.ticket(c))
        self.assertEqual(len(p["body"]["cases"]),4);self.assertEqual(p["body"]["systems"],auth.SYSTEMS)
        self.assertEqual(registry["real_approval_pins"],{})

    def test_missing_case_issuance_rejected(self):
        c=self.setup_campaign()
        with self.assertRaises(ValueError):auth.issue_synthetic(c["issuer"],"other",c["eligibility"],c["cases"][:3],self.temp/"other-output",self.temp/"other-permit.json")

    def test_duplicate_run_id_rejected(self):
        c=self.setup_campaign()
        with self.assertRaises(FileExistsError):auth.issue_synthetic(c["issuer"],"synthetic-run",c["eligibility"],c["cases"],self.temp/"other-output",self.temp/"other-permit.json")

    def test_wrong_run_id_rejected(self):
        c=self.setup_campaign();t=self.ticket(c);t["run_id"]="different"
        with self.assertRaisesRegex(ValueError,"RUN_ID_MISMATCH"):auth.verify_disk(t)

    def test_wrong_permit_hash_rejected(self):
        c=self.setup_campaign();t=self.ticket(c);t["permit_sha256"]="0"*64
        with self.assertRaisesRegex(ValueError,"PERMIT_FILE_HASH_MISMATCH"):auth.verify_disk(t)

    def test_wrong_signature_rejected(self):
        c=self.setup_campaign();p=auth.read(c["permit"]);p["mac"]="0"*64;self.h.save(c["permit"],p);c["ph"]=auth.file_hash(c["permit"])
        with self.assertRaisesRegex(ValueError,"INVALID_PERMIT_SIGNATURE"):auth.verify_disk(self.ticket(c))

    def test_unknown_gt_field_rejected(self):
        c=self.setup_campaign();self.resign(c,lambda b:b.update(gt_sidecar="DO_NOT_READ"))
        with self.assertRaisesRegex(ValueError,"INVALID_PERMIT_BODY"):auth.verify_disk(self.ticket(c))

    def test_runtime_tamper_rejected(self):
        c=self.setup_campaign();self.resign(c,lambda b:b['runtime'].update(version="wrong"))
        with self.assertRaisesRegex(ValueError,"RUNTIME_MISMATCH"):auth.verify_disk(self.ticket(c))

    def test_code_tamper_rejected(self):
        c=self.setup_campaign();self.resign(c,lambda b:b['code_sha256'].update({'retriever.py':'0'*64}))
        with self.assertRaisesRegex(ValueError,"CODE_HASH_MISMATCH"):auth.verify_disk(self.ticket(c))

    def test_specification_tamper_rejected(self):
        c=self.setup_campaign();self.resign(c,lambda b:b['specification'].update(version="other"))
        with self.assertRaisesRegex(ValueError,"SPECIFICATION_MISMATCH"):auth.verify_disk(self.ticket(c))

    def test_system_set_tamper_rejected(self):
        c=self.setup_campaign();self.resign(c,lambda b:b.update(systems=['Rule-only','LLM']))
        with self.assertRaisesRegex(ValueError,"INVALID_EXPERIMENT_SCOPE"):auth.verify_disk(self.ticket(c))

    def test_n_k_tamper_rejected(self):
        c=self.setup_campaign();self.resign(c,lambda b:b.update(K=4))
        with self.assertRaisesRegex(ValueError,"INVALID_EXPERIMENT_SCOPE"):auth.verify_disk(self.ticket(c))

    def test_extra_case_system_rejected(self):
        c=self.setup_campaign()
        for key,val in [('case_id','fixture-extra'),('system','LLM-only')]:
            t=self.ticket(c);t[key]=val
            with self.assertRaisesRegex(ValueError,"JOB_OUTSIDE_PERMIT"):auth.verify_disk(t)

    def test_source_change_rejected(self):
        c=self.setup_campaign();p=c['fs'][0]['repo']/'src/main/java/p/A.java';p.write_bytes(p.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError,"STALE_SOURCE"):auth.verify_disk(self.ticket(c))

    def test_extra_source_file_rejected(self):
        c=self.setup_campaign();(c['fs'][0]['repo']/'src/main/java/p/B.java').write_text('class B {}')
        with self.assertRaisesRegex(ValueError,"SOURCE_FILE_SET_MISMATCH"):auth.verify_disk(self.ticket(c))

    def test_index_and_metadata_tamper_rejected(self):
        c=self.setup_campaign();p=Path(str(c['fs'][0]['index'])+'.meta.json');p.write_bytes(p.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,"INPUT_INDEX_METADATA_MISMATCH"):auth.verify_disk(self.ticket(c))

    def test_input_tamper_rejected(self):
        c=self.setup_campaign();p=c['fs'][0]['benchmark'];p.write_bytes(p.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,"INPUT_INDEX_METADATA_MISMATCH"):auth.verify_disk(self.ticket(c))

    def test_catalog_tamper_rejected(self):
        c=self.setup_campaign();p=c['packages'][0]/'request.json';r=auth.read(p);r['catalog'].pop();self.h.save(p,r)
        with self.assertRaises(ValueError):auth.verify_disk(self.ticket(c))

    def test_manifest_binding_tamper_rejected(self):
        c=self.setup_campaign();self.resign(c,lambda b:b['cases'][0].update(source_manifest_sha256='0'*64))
        with self.assertRaisesRegex(ValueError,"REQUEST_BINDING_MISMATCH"):auth.verify_disk(self.ticket(c))

    def test_project_output_rejected(self):
        c=self.setup_campaign();self.resign(c,lambda b:b.update(output=str(ROOT/'forbidden-c1')))
        with self.assertRaisesRegex(ValueError,"OUTPUT_PROTECTED_ROOT"):auth.verify_disk(self.ticket(c))

    def test_output_claim_binding_rejected(self):
        c=self.setup_campaign();t,target,p,r=self.start_job(c)
        with self.assertRaisesRegex(ValueError,"OUTPUT_BINDING_MISMATCH"):auth.claim(t,r,p,self.temp/'wrong','parent')

    def test_parent_claim_cannot_replay(self):
        c=self.setup_campaign();t,target,p,r=self.start_job(c)
        auth.claim(t,r,p,target,'parent')
        with self.assertRaises(FileExistsError):auth.claim(t,r,p,target,'parent')

    def test_worker_requires_parent_and_cannot_replay(self):
        c=self.setup_campaign();t,target,p,r=self.start_job(c)
        with self.assertRaises(FileNotFoundError):auth.claim(t,r,p,target,'worker')
        auth.claim(t,r,p,target,'parent')
        previous=Path.cwd()
        try:
            os.chdir(target);ctx=auth.claim(t,r,p,target,'worker')
            auth.validate_context(ctx,r,'BM25-only')
            with self.assertRaises(FileExistsError):auth.claim(t,r,p,target,'worker')
        finally:os.chdir(previous)

    def test_producer_context_three_layers_and_tampering(self):
        c=self.setup_campaign();t,target,p,r=self.start_job(c);auth.claim(t,r,p,target,'parent')
        previous=Path.cwd()
        try:
            os.chdir(target);ctx=auth.claim(t,r,p,target,'worker')
            bridge.validate_worker_request(r,authorization=ctx,system='bm25')
            bm.validate_request(r,authorization=ctx)
            bad=copy.deepcopy(ctx);bad['proof']['pid']=-1
            with self.assertRaisesRegex(ValueError,'INVALID_WORKER_PROOF'):bm.predict(r,authorization=bad)
            bad=copy.deepcopy(ctx);bad['verifier_source']='not trusted'
            with self.assertRaisesRegex(ValueError,'UNTRUSTED_AUTHORIZATION_CODE'):bm.predict(r,authorization=bad)
            bad=copy.deepcopy(ctx);bad['permit']['body']['run_id']='self-signed-other-run'
            bad['permit']['mac']=auth.sign(bad['key'],bad['permit']['body'])
            bad['proof']['permit_sha256']=auth.digest(auth.canonical(bad['permit']))
            bad['proof_mac']=auth.sign(bad['key'],bad['proof'])
            with self.assertRaisesRegex(ValueError,'CONTEXT_NOT_REGISTERED_BY_WORKER'):bm.predict(r,authorization=bad)
        finally:os.chdir(previous)

    def test_unregistered_producer_rejected(self):
        c=self.setup_campaign();t,target,p,r=self.start_job(c);p.write_bytes(p.read_bytes()+b'\n# changed')
        with self.assertRaisesRegex(ValueError,'UNREGISTERED_PRODUCER'):auth.claim(t,r,p,target,'parent')

    def test_no_real_approval_record_exists(self):
        c=self.setup_campaign()
        # Incomplete, explicitly invalid specimen, never an executable real permit.
        p=auth.read(c['permit']);p['body']['scope']='REAL_OFFLINE_ONCE'
        key=auth.read(c['issuer']/'registry.json')['key'];p['mac']=auth.sign(key,p['body'])
        with self.assertRaisesRegex(ValueError,'REAL_RESEARCH_APPROVAL_ABSENT'):auth.validate_shape(p,key,None)

    def test_fixture_rename_of_real_snapshot_rejected(self):
        c=self.setup_campaign();self.resign(c,lambda b:b['cases'][0].update(source_snapshot_id=next(iter(auth.REAL_SNAPSHOTS))))
        with self.assertRaisesRegex(ValueError,'REAL_SNAPSHOT_RENAMING'):auth.verify_disk(self.ticket(c))

    def test_prediction_verification_does_not_read_gt(self):
        c=self.setup_campaign();original=Path.read_bytes
        gts={f['gt'] for f in c['fs']}
        def guarded(path):
            if Path(path) in gts:raise AssertionError('GT READ DURING PREDICTION VERIFICATION')
            return original(path)
        with mock.patch.object(Path,'read_bytes',guarded):auth.verify_disk(self.ticket(c))
        serialized=c['permit'].read_text()
        self.assertNotIn('accepted_targets',serialized);self.assertNotIn('gt_sidecar',serialized)
        self.assertNotIn('GT_SECRET_CANARY',serialized)
        for gt in gts:self.assertNotIn(str(gt),serialized)

    def test_missing_rule_dependency_keeps_fixed_four(self):
        if RULE_AVAILABLE:
            # Failure injection changes only one generated launch result, never
            # the scorer, tokenizer, permission hook, or a real case.
            c=self.setup_campaign();original=ea.subprocess.run
            def failure_once(*args,**kwargs):
                if str(kwargs.get('cwd','')).endswith('0-rule'):
                    return subprocess.CompletedProcess(args[0],3,b'{"synthetic_error":"worker interrupted"}',b'synthetic injected interruption')
                return original(*args,**kwargs)
            with mock.patch.object(ea.subprocess,'run',side_effect=failure_once):seal=auth.execute(c['permit'],c['ph'])
        else:
            c=self.setup_campaign();seal=auth.execute(c['permit'],c['ph'])
        reports=auth.evaluate_sealed(c['output'],seal,c['job'],self.temp/'evaluation')
        self.assertEqual(reports['Rule-only']['tool_status'],'INVALID_RUN')
        self.assertEqual(reports['Rule-only']['denominators']['method'],4)
        self.assertIsNone(reports['Rule-only']['metrics']['strict_method_top1']['value'])
        self.assertEqual(reports['BM25-only']['tool_status'],'OK')
        with self.assertRaises(ValueError):auth.execute(c['permit'],c['ph'])

    @RULE_REQUIRED
    def test_end_to_end_eight_jobs_same_request_seal_and_independent_score(self):
        c=self.setup_campaign();seal=auth.execute(c['permit'],c['ph']);campaign=auth.read(c['output']/'campaign.json')
        self.assertEqual(len(campaign['results']),8)
        self.assertTrue(all(r['prediction_status']=='SUCCESS' for r in campaign['results']),campaign['results'])
        for i in range(4):
            left,right=campaign['results'][2*i:2*i+2]
            self.assertEqual(left['request_sha256'],right['request_sha256'])
            self.assertEqual(auth.read(Path(left['prediction_dir'])/'prediction.json')['candidate_count'],6)
        reports=auth.evaluate_sealed(c['output'],seal,c['job'],self.temp/'evaluation')
        self.assertTrue(all(r['tool_status']=='OK' for r in reports.values()))

    def test_json_parse_failure_closed(self):
        p=self.temp/'bad.json';p.write_bytes(b'{')
        with self.assertRaises(ValueError):auth.execute(p,'0'*64)

    def test_worker_independently_rejects_wrong_run_id(self):
        c=self.setup_campaign();t,target,p,r=self.start_job(c);auth.claim(t,r,p,target,'parent')
        t['run_id']='different';ticket=self.temp/'bad-ticket.json';auth.put(ticket,t)
        request_path=target/'request.json';request_path.write_bytes((c['packages'][0]/'request.json').read_bytes())
        proc=subprocess.run([sys.executable,'-I','-B',str(ROOT/'src/experiment_adapter.py'),'_worker',str(target),str(p),str(ticket)],cwd=target,capture_output=True,text=True,timeout=20)
        (self.temp/'rejected-worker.stderr.log').write_text(proc.stderr,encoding='utf-8')
        self.assertNotEqual(proc.returncode,0);self.assertIn('RUN_ID_MISMATCH',proc.stderr)

    def test_authorized_worker_retains_gt_network_and_write_guard(self):
        c=self.setup_campaign();t,target,p,r=self.start_job(c);auth.claim(t,r,p,target,'parent')
        ticket=self.temp/'ticket.json';auth.put(ticket,t)
        code='''import importlib.util,json,sys,socket
from pathlib import Path
def load(path,name):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
qa=load(AUTH_LITERAL,'guard_probe_authority');ea=load(ADAPTER_LITERAL,'guard_probe_adapter')
ticket=qa.read(TICKET_LITERAL);request=qa.read(REQUEST_LITERAL)
ctx=qa.claim(ticket,request,PRODUCER_LITERAL,OUTPUT_LITERAL,'worker')
ea._install_worker_guard(OUTPUT_LITERAL,PRODUCER_LITERAL)
qa.validate_context(ctx,request,'BM25-only')
denied=[]
for name,action in [('gt',lambda:Path(GT_LITERAL).read_bytes()),('write',lambda:open(Path(OUTPUT_LITERAL)/'unexpected.txt','xb')),('network',lambda:socket.socket())]:
 try:
  obj=action()
  if hasattr(obj,'close'):obj.close()
  raise AssertionError('SECURITY_BOUNDARY_WEAKENED:'+name)
 except PermissionError:denied.append(name)
print(json.dumps(denied))
'''
        values={'AUTH_LITERAL':str(ROOT/'src/controlled_authorization.py'),'ADAPTER_LITERAL':str(ROOT/'src/experiment_adapter.py'),
          'TICKET_LITERAL':str(ticket),'REQUEST_LITERAL':str(c['packages'][0]/'request.json'),'PRODUCER_LITERAL':str(p),
          'OUTPUT_LITERAL':str(target),'GT_LITERAL':str(c['fs'][0]['gt'])}
        for key,value in values.items():code=code.replace(key,repr(value))
        proc=subprocess.run([sys.executable,'-B','-c',code],cwd=target,capture_output=True,text=True,timeout=20)
        (self.temp/'guard-probe.stdout.log').write_text(proc.stdout,encoding='utf-8');(self.temp/'guard-probe.stderr.log').write_text(proc.stderr,encoding='utf-8')
        self.assertEqual(proc.returncode,0,proc.stderr);self.assertEqual(json.loads(proc.stdout),['gt','write','network'])
        self.assertFalse((target/'unexpected.txt').exists())

    def test_producer_rejects_modified_request_after_worker_claim(self):
        c=self.setup_campaign();t,target,p,r=self.start_job(c);auth.claim(t,r,p,target,'parent');previous=Path.cwd()
        try:
            os.chdir(target);ctx=auth.claim(t,r,p,target,'worker')
            r['catalog'][0]['features']['strings']=['changed'];r['catalog_sha256']=auth.digest(auth.canonical(r['catalog']))
            with self.assertRaises(ValueError):bm.predict(r,authorization=ctx)
        finally:os.chdir(previous)

    def test_sealed_output_tamper_rejected_before_gt(self):
        c=self.setup_campaign();seal=auth.execute(c['permit'],c['ph'])
        (c['output']/'new-file.txt').write_text('not in seal')
        with self.assertRaisesRegex(ValueError,'SEALED_FILE_SET_CHANGED'):
            auth.evaluate_sealed(c['output'],seal,c['job'],self.temp/'eval')

    def test_duplicate_json_keys_rejected(self):
        p=self.temp/'duplicate.json';p.write_text('{"run_id":"a","run_id":"b"}')
        with self.assertRaisesRegex(ValueError,'DUPLICATE_JSON_KEY'):auth.read(p)

    def test_renamed_real_namespace_rejected_by_legacy_parent_and_producers(self):
        f=self.h.fixture('fixture-renamed');package=self.h.package(f)
        request=auth.read(package/'request.json');request['repo_id']='jhy/jsoup'
        for rec in request['catalog']:rec['repo_id']='jhy/jsoup'
        request['catalog_sha256']=auth.digest(auth.canonical(request['catalog']))
        self.h.save(package/'request.json',request);info=auth.read(package/'package.json')
        info['request_sha256']=auth.file_hash(package/'request.json');self.h.save(package/'package.json',info)
        result=ea.predict(package,self.h.producer(),self.h.out())
        self.assertEqual(result['prediction_status'],'INVALID_PREDICTION')
        with self.assertRaisesRegex(ValueError,'REAL_INPUT_RENAMING'):bm.predict(request)
        with self.assertRaisesRegex(ValueError,'REAL_INPUT_RENAMING'):bridge.validate_worker_request(request)

    def test_changed_query_cannot_retain_original_frozen_input_binding(self):
        c=self.setup_campaign();request=auth.read(c['packages'][0]/'request.json')
        request['issue_text']='changed query';request['issue_text_sha256']=auth.digest(b'changed query')
        self.h.save(c['packages'][0]/'request.json',request)
        info=auth.read(c['packages'][0]/'package.json');info['request_sha256']=auth.file_hash(c['packages'][0]/'request.json');self.h.save(c['packages'][0]/'package.json',info)
        new=auth.describe(c['fs'][0]['benchmark'],c['fs'][0]['index'],c['fs'][0]['repo'],c['packages'][0])
        self.resign(c,lambda b:b['cases'].__setitem__(0,new))
        with self.assertRaisesRegex(ValueError,'FROZEN_INPUT_REQUEST_MISMATCH'):auth.verify_disk(self.ticket(c))

    def test_source_directory_qualification_output_rejected(self):
        c=self.setup_campaign()
        with self.assertRaisesRegex(ValueError,'OUTPUT_PROTECTED_ROOT'):
            auth.qualify_synthetic(c['fs'][0]['repo']/'new-issuer',c['job'],self.temp/'new-qualification')

    def test_issuer_cannot_create_state_inside_source(self):
        c=self.setup_campaign();issuer=c['fs'][0]['repo']/'forbidden-issuer'
        with self.assertRaisesRegex(ValueError,'OUTPUT_PROTECTED_ROOT'):
            auth.issue_synthetic(issuer,'other',c['eligibility'],c['cases'],self.temp/'other-output',self.temp/'other-permit.json')
        self.assertFalse(issuer.exists())

    def test_permit_cannot_be_written_inside_source(self):
        c=self.setup_campaign();permit=c['fs'][0]['repo']/'forbidden-permit.json'
        with self.assertRaisesRegex(ValueError,'OUTPUT_PROTECTED_ROOT'):
            auth.issue_synthetic(c['issuer'],'other',c['eligibility'],c['cases'],self.temp/'other-output',permit)
        self.assertFalse(permit.exists())


if __name__=='__main__':unittest.main()
