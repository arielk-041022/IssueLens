"""Frozen v2 wire inputs, synthetic HTTP only. Never reads GT or old model outputs."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('live_v2_test',ROOT/'experiments/brd_v1/live_adapter.py')
live=importlib.util.module_from_spec(spec);spec.loader.exec_module(live)
SOURCE=ROOT/'experiments/brd_v1/blind-pairs-v2-20261023'
SEAL='5bad5935e9d2f10d551e7df08b946006887aaa05a1bd83738d91c6aa45231389'


class V2LiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        profile=json.loads((ROOT/'experiments/live_bridge01/verified-api-profile.json').read_bytes())
        profile['verified_at_utc']=datetime.now(timezone.utc).isoformat()
        profile['verification_method']='Synthetic offline fixture; not a pricing verification'
        self.profile=self.root/'profile.json';self.profile.write_bytes(live.canonical_json(profile))
        for target,name in ((socket.socket,'connect'),(socket,'getaddrinfo'),(live.bridge,'https_post_once')):
            blocker=patch.object(target,name,side_effect=AssertionError('REAL_NETWORK_FORBIDDEN'))
            blocker.start();self.addCleanup(blocker.stop)

    def prepare(self,issue='LANG-1638',suffix='prepared',source=SOURCE):
        out=self.root/suffix
        result=live.prepare_v2(source,SEAL,self.profile,out,issue)
        self.assertEqual(result['real_API_calls'],0)
        return out,result

    def token(self,out,arm):return live.sha((out/arm/'authorization.json').read_bytes())

    def response(self,extra=False):
        content={'ranking':list(range(20,0,-1)),'reason':'Synthetic mock comparison.'}
        if extra:content['extra']='must remain invalid'
        return 200,live.canonical_json({'object':'chat.completion','model':'deepseek-flash',
            'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,
                     'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':100},
            'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(content)}}]})

    def execute(self,out,arm='C',transport=None,token=None,cap='0.65'):
        return live.execute(out,arm,self.token(out,arm) if token is None else token,cap,
            identity_version='v2',transport=transport or (lambda _:self.response()))

    def copy_inputs(self):
        source=self.root/'frozen';source.mkdir()
        names=['artifacts.sha256.json','freeze-report.json']
        names += ['LANG-1638/'+name for name in ('paired-request.json','case-input.json','candidate-bundle.json',
                  'validation-report.json','C/http-payload.json','BRD/http-payload.json')]
        for name in names:
            dst=source/name;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes((SOURCE/name).read_bytes())
        return source

    def test_all_four_dry_runs_preserve_wire_and_fail_state(self):
        tokens=[]
        for issue in ('LANG-1638','LANG-1643'):
            out,result=self.prepare(issue,issue)
            live.verify_seal(out)
            self.assertEqual(json.loads((out/'evidence-state.json').read_bytes())['overall_verdict'],'FAIL')
            for arm in ('C','BRD'):
                wire=(out/arm/'http-payload.json').read_bytes()
                self.assertEqual(wire,(SOURCE/issue/arm/'http-payload.json').read_bytes())
                self.assertEqual(live.sha(wire),result['arms'][arm]['http_sha256'])
                tokens.append(self.token(out,arm))
                self.assertFalse((out/arm/'attempt').exists())
        self.assertEqual(len(set(tokens)),4)

    def test_four_successful_mock_calls_independent_locks(self):
        calls=[]
        for issue in ('LANG-1638','LANG-1643'):
            out,_=self.prepare(issue,issue)
            for arm in ('C','BRD'):
                def transport(wire):
                    calls.append(wire);return self.response()
                result=self.execute(out,arm,transport)
                self.assertEqual(result['status'],'SUCCESS')
                self.assertEqual(result['mode'],'MOCK_HTTP')
                self.assertEqual(result['evidence_state']['overall_verdict'],'FAIL')
                live.verify_seal(out/arm/'attempt')
                with self.assertRaises(ValueError):self.execute(out,arm,transport)
        self.assertEqual(len(calls),4)

    def test_no_authorization_wrong_group_issue_and_cap(self):
        out,_=self.prepare()
        other,_=self.prepare('LANG-1643','other')
        with self.assertRaisesRegex(ValueError,'AUTHORIZATION'):
            live.execute(out,'C',None,'0.65',identity_version='v2',live=True)
        for token in ('',self.token(out,'BRD'),self.token(other,'C'),'old-v1-authorization'):
            with self.assertRaisesRegex(ValueError,'AUTHORIZATION'):
                self.execute(out,token=token,transport=lambda _:self.fail('transport'))
        with self.assertRaisesRegex(ValueError,'CAP'):
            self.execute(out,cap='1',transport=lambda _:self.fail('transport'))
        self.assertFalse((out/'C/single-call.lock.json').exists())

    def test_version_confusion_rejected(self):
        out,_=self.prepare()
        with self.assertRaisesRegex(ValueError,'IDENTITY_VERSION_MISMATCH'):
            live.execute(out,'C',self.token(out,'C'),'0.65',transport=lambda _:self.fail('transport'))
        pair=json.loads((out/'request.json').read_bytes());pair['schema_version']='brd-rerank-request-v1'
        with self.assertRaisesRegex(ValueError,'IDENTITY_VERSION_MISMATCH'):live.check_pair_v2(pair)
        c=SOURCE/'LANG-1638/C/http-payload.json'
        with self.assertRaises(ValueError):live.prepare(c,live.sha(c.read_bytes()),self.profile,self.root/'v1','LANG-1638')

    def test_frozen_input_and_local_wire_tampering_rejected(self):
        source=self.copy_inputs();out,_=self.prepare(source=source)
        (source/'LANG-1638/C/http-payload.json').write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError,'FROZEN_INPUT_CHANGED'):
            self.execute(out,transport=lambda _:self.fail('transport'))
        out2,_=self.prepare(suffix='local')
        (out2/'C/http-payload.json').write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError,'SEALED_FILE_CHANGED'):
            self.execute(out2,transport=lambda _:self.fail('transport'))

    def test_budget_stale_price_and_dispatch_recheck_reject(self):
        profile=json.loads(self.profile.read_bytes())
        for n,changes in enumerate(({'single_call_cap_usd':'0.01'},{'verified_at_utc':'2020-01-01T00:00:00+00:00'})):
            self.profile.write_bytes(live.canonical_json({**profile,**changes}))
            with self.assertRaises(live.bridge.Rejected):self.prepare(suffix='budget'+str(n))
        self.profile.write_bytes(live.canonical_json(profile));out,_=self.prepare()
        with patch.object(live.bridge,'budget',side_effect=live.bridge.Rejected('PRICE_RECHECK_REQUIRED')):
            with self.assertRaises(live.bridge.Rejected):self.execute(out,transport=lambda _:self.fail('transport'))
        self.assertFalse((out/'C/single-call.lock.json').exists())

    def test_existing_lock_results_and_output_refuse(self):
        out,_=self.prepare()
        with self.assertRaisesRegex(ValueError,'OUTPUT_EXISTS'):self.prepare()
        (out/'C/single-call.lock.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'NO_RETRY'):self.execute(out,transport=lambda _:self.fail('transport'))
        (out/'BRD/execution-result.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'NO_RETRY'):self.execute(out,'BRD',lambda _:self.fail('transport'))

    def test_timeout_connection_http_protocol_failures_never_retry(self):
        cases=[('timeout','TIMEOUT_EXECUTION_UNCERTAIN'),('connection','CONNECTION_ERROR_EXECUTION_UNCERTAIN'),
               ('http','HTTP_ERROR'),('protocol','PROTOCOL_FAILED')]
        for name,status in cases:
            out,_=self.prepare(suffix=name);calls=[]
            def transport(wire):
                calls.append(wire)
                if name=='timeout':raise TimeoutError()
                if name=='connection':raise ConnectionError()
                if name=='http':return 503,b'actual mock HTTP failure'
                return self.response(extra=True)
            result=self.execute(out,transport=transport)
            self.assertEqual(result['status'],status)
            self.assertFalse((out/'C/attempt/reranking.json').exists())
            self.assertTrue((out/'C/single-call.lock.json').exists())
            with self.assertRaises(ValueError):self.execute(out,transport=transport)
            self.assertEqual(len(calls),1)
            if name=='protocol':
                self.assertIn(b'extra',(out/'C/attempt/http-response.body.raw').read_bytes())


if __name__=='__main__':unittest.main()
