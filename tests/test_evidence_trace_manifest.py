"""Synthetic linkage only; no GT, API, source execution or real case labels."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from evidence_trace_manifest import trace, final_rows, sha, CHECKS
from evidence_validator import compute_evidence_id


def fixture():
    mid='java-source-v1:example.Sample#run()';path='src/main/java/example/Sample.java'
    sourcehash='a'*64;snippet='helper()';coords=dict(start_line=2,start_column=3,end_line=2,end_column=10)
    eid=compute_evidence_id(path,sourcehash,mid,'METHOD_CALL',list(coords.values()))
    evidence={'evidence_id':eid,'evidence_type':'METHOD_CALL','source_snippet':snippet,**coords}
    index=[{'file':path,'source_sha256':sourcehash,'classes':[{'methods':[
        {'method_id':mid,'identity_status':'AVAILABLE','evidence_occurrences':[evidence]}]}]}]
    ih=sha(json.dumps(index).encode())
    metadata={'index_sha256':ih,'schema_version':'2.0','method_id_scheme':'java-source-v1'}
    mh=sha(json.dumps(metadata).encode())
    snapshot={'repo_id':'example/repo','source_snapshot_id':'saved-snapshot-label',
              'index_sha256':ih,'metadata_sha256':mh}
    candidate={'repo_id':'example/repo','source_snapshot_id':'saved-snapshot-label',
               'method_id':mid,'file_path':path,'rank':7,'ordinal':0,'index_sha256':ih}
    saved={'evidence_id':eid,'evidence_type':'METHOD_CALL','file_path':path,'method_id':mid,
           'range':coords,'status':'VERIFIED','reason_code':'ALL_LOCAL_SOURCE_CHECKS_PASSED',
           'basis':{'computed_evidence_id':eid,'expected_evidence_id':eid,
                    'expected_source_sha256':sourcehash,'actual_source_sha256':sourcehash,
                    'expected_snippet_sha256':sha(snippet.encode()),'actual_snippet_sha256':sha(snippet.encode()),
                    'extracted_source_snippet':snippet,'checks_passed':sorted(CHECKS)}}
    report={'validator_version':'evidence-local-v2','overall_verdict':'FAIL',
            'index_integrity':{'status':'PASS'},'inputs':{'index_sha256':ih,'metadata_sha256':mh},
            'verification_scope':{'protocol':'evidence-local-v2','certification':'PINNED_INDEX_SOURCE_BYTE_CORRESPONDENCE'},
            'evidence_results':[saved]}
    return [[candidate],index,metadata,report,snapshot,{'index':ih,'metadata':mh}]


class TraceTests(unittest.TestCase):
    def test_exact_link(self):
        data=fixture();r=trace(*data)['candidates'][0]
        self.assertEqual(r['trace_status'],'BOUND')
        self.assertEqual(r['source_fact_status'],'SOURCE_FACT_MATCH_RECORDED')
        self.assertEqual((r['final_rank'],r['original_rank']),(1,7))

    def test_method_mismatch(self):
        data=fixture();data[0][0]['method_id']='java-source-v1:example.Other#run()'
        self.assertEqual(trace(*data)['candidates'][0]['trace_status'],'UNBOUND')

    def test_snapshot_mismatch(self):
        data=fixture();data[4]['source_snapshot_id']='another-snapshot'
        self.assertIn('SOURCE_SNAPSHOT_BINDING_NOT_PROVEN',trace(*data)['candidates'][0]['unverified_reasons'])

    def test_missing_saved_candidate_index_pin(self):
        data=fixture();data[0][0].pop('index_sha256')
        self.assertEqual(trace(*data)['candidates'][0]['trace_status'],'UNBOUND')

    def test_failed_index_integrity_not_upgraded(self):
        data=fixture();data[3]['index_integrity']['status']='FAIL'
        self.assertEqual(trace(*data)['candidates'][0]['trace_status'],'UNBOUND')

    def test_missing_hashes_do_not_bind(self):
        data=fixture();data[5]={}
        self.assertEqual(trace(*data)['candidates'][0]['trace_status'],'UNBOUND')

    def test_local_pass_repository_fail(self):
        result=trace(*fixture())
        self.assertEqual(result['validator_original_overall_verdict'],'FAIL')
        self.assertEqual(result['candidates'][0]['source_fact_status'],'SOURCE_FACT_MATCH_RECORDED')

    def test_no_evidence(self):
        data=fixture();data[1][0]['classes'][0]['methods'][0]['evidence_occurrences']=[]
        result=trace(*data)['candidates'][0]
        self.assertEqual(result['source_fact_status'],'UNVERIFIABLE')
        self.assertIn('NO_INDEXED_EVIDENCE_FOR_CANDIDATE',result['unverified_reasons'])

    def test_valid_model_not_verified_evidence(self):
        data=fixture();data[0]={'status':'VALID','ranking':data[0]}
        data[3]['evidence_results'][0].update(status='UNVERIFIABLE',reason_code='SOURCE_NOT_CHECKED')
        result=trace(*data)['candidates'][0]
        self.assertEqual(result['source_fact_status'],'UNVERIFIABLE')
        self.assertEqual(result['evidence'][0]['validator_result']['reason_code'],'SOURCE_NOT_CHECKED')

    def test_source_mismatch_preserved(self):
        data=fixture();data[3]['evidence_results'][0].update(status='STALE_SOURCE',reason_code='SOURCE_HASH_MISMATCH')
        result=trace(*data)['candidates'][0]
        self.assertEqual(result['source_fact_status'],'SOURCE_FACT_INCONSISTENCY_RECORDED')
        self.assertEqual(result['evidence'][0]['validator_result']['reason_code'],'SOURCE_HASH_MISMATCH')

    def test_v2_incompatible(self):
        data=fixture();data[0][0]['method_id']='java-source-v2:example.Sample#run()'
        result=trace(*data)['candidates'][0]
        self.assertEqual(result['trace_status'],'UNBOUND')
        self.assertIn('IDENTITY_PROTOCOL_INCOMPATIBLE_WITH_SAVED_VALIDATOR',result['unverified_reasons'])

    def test_duplicate_validator_evidence(self):
        data=fixture();data[3]['evidence_results']*=2
        self.assertEqual(trace(*data)['candidates'][0]['source_fact_status'],'UNVERIFIABLE')

    def test_wrong_report_method_id(self):
        data=fixture();data[3]['evidence_results'][0]['method_id']='different'
        self.assertEqual(trace(*data)['candidates'][0]['source_fact_status'],'UNVERIFIABLE')

    def test_missing_record(self):
        data=fixture();data[3]['evidence_results']=[]
        self.assertEqual(trace(*data)['candidates'][0]['source_fact_status'],'UNVERIFIABLE')

    def test_determinism_and_input_unchanged(self):
        data=fixture();before=deepcopy(data)
        self.assertEqual(trace(*data),trace(*data))
        self.assertEqual(data,before)

    def test_runtime_tests_not_inferred(self):
        data=fixture();data[0][0]['test_command']='mvn test'
        self.assertEqual(trace(*data)['runtime_tests']['status'],'NOT_ESTABLISHED_BY_TRACE')

    def test_positional_order(self):
        rows=[{'method_id':'a','rank':1},{'method_id':'b','rank':2}]
        result=final_rows({'status':'SUCCESS','ranking':[2,1]},{'candidates':rows})
        self.assertEqual([r['method_id'] for r in result],['b','a'])
        self.assertEqual([r['rank'] for r in result],[2,1])

    def test_invalid_positional_not_repaired(self):
        for ranking in ([True,2],[1,1],[2]):
            with self.subTest(ranking=ranking),self.assertRaises(ValueError):
                final_rows({'status':'SUCCESS','ranking':ranking},{'candidates':[{},{}]})

    def test_cli_no_overwrite(self):
        script=Path(__file__).resolve().parents[1]/'src/evidence_trace_manifest.py'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=fixture();flags=['ranking','index','metadata','validator-report','snapshot']
            command=[sys.executable,'-B',str(script)]
            # CLI pins actual serialized bytes, as opposed to the pure fixture's supplied hash dictionary.
            data[5]['index']=sha(json.dumps(data[1]).encode())
            for flag,value in zip(flags,data[:5]):
                p=root/(flag+'.json');p.write_text(json.dumps(value),encoding='utf-8');command+=['--'+flag,str(p)]
            output=root/'trace.json';command+=['--output',str(output)]
            first=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(first.returncode,0,first.stderr)
            before=output.read_bytes()
            second=subprocess.run(command,capture_output=True,text=True)
            self.assertNotEqual(second.returncode,0)
            self.assertEqual(output.read_bytes(),before)


if __name__=='__main__':unittest.main()
