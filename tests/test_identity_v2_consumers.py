"""Java/Python integration with synthetic Java only; no scorer or network."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'src'))
from src import metadata_experiment_v2 as v2
from src import brd_rerank_strategy_v2 as brd
from src import brd_rerank_strategy as old_brd
import metadata_ranking_runner as runner
import metadata_experiment as old


class ConsumersV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.source = cls.root / 'src/main/java/A.java'
        cls.source.parent.mkdir(parents=True)
        code = 'import java.util.Collection;\nclass A {\n' + '\n'.join(
            f'<T extends Collection<?>> void m{i}(T x) {{ x.size(); }}' for i in range(20))
        code += '\nvoid escaped(){ String s="\\u0061"; }\n}\n'
        cls.source.write_bytes(code.encode())
        jars = [Path.home()/'.m2/repository/com/github/javaparser/javaparser-core/3.26.4/javaparser-core-3.26.4.jar']
        jars += [Path.home()/f'.m2/repository/com/fasterxml/jackson/core/{a}/2.18.2/{a}-2.18.2.jar'
                 for a in ('jackson-databind','jackson-core','jackson-annotations')]
        java = Path(r'C:\Program Files\Java\jdk-25\bin\java.exe')
        cp = ';'.join(str(p) for p in [ROOT/'java-indexer/target/classes',*jars])
        proc = subprocess.run([str(java) if java.exists() else shutil.which('java'),'-cp',cp,'Main',str(cls.root),
            '--identity-v2','--output',str(cls.root/'new.json')],capture_output=True,timeout=40)
        if proc.returncode: raise AssertionError(proc.stderr.decode(errors='replace'))
        cls.index = json.loads((cls.root/'new.json').read_bytes())
        cls.meta = json.loads((cls.root/'new.json.meta.json').read_bytes())
        cls.case = {'case_id':'synthetic-v2','repo_id':'synthetic/example','source_snapshot_id':'sha256:'+v2.digest([
            {'file_path':f['file'],'source_sha256':f['source_sha256']} for f in cls.index]),
            'issue_text':'Original synthetic title ', 'input_version_status':'PRE_FIX_VERIFIED'}
        cls.source_bytes = cls.source.read_bytes()

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def setUp(self):
        self.index = deepcopy(self.__class__.index)
        self.meta = deepcopy(self.__class__.meta)
        self.source.write_bytes(self.source_bytes)

    def build(self):
        raw = v2.canonical(self.index)
        self.meta['index_sha256'] = hashlib.sha256(raw).hexdigest()
        meta_raw = v2.canonical(self.meta)
        case = deepcopy(self.case)
        case['source_snapshot_id'] = 'sha256:'+v2.digest([{'file_path':f['file'],'source_sha256':f['source_sha256']} for f in self.index])
        return v2.build_metadata_request(case,raw,meta_raw,self.root,
            index_sha256=hashlib.sha256(raw).hexdigest(),metadata_sha256=hashlib.sha256(meta_raw).hexdigest())

    def methods(self): return self.index[0]['classes'][0]['methods']

    def ranking(self, request):
        # Synthetic injected scores; never executes BM25 or Rule.
        with patch.object(runner,'_scores',return_value=[100-i for i in range(request['catalog_count'])]) as scorer:
            result=runner.rank_metadata_v2(request,deepcopy(request),algorithm='bm25')
            self.assertEqual(scorer.call_count,1)
        return result

    def pair(self):
        req=self.build(); return brd.build_pair(req,deepcopy(req),self.ranking(req))

    def test_mixed_positions_keep_file_failure_and_all_candidates(self):
        req=self.build()
        self.assertEqual(req['catalog_count'],21)
        self.assertEqual([r['location_status'] for r in req['catalog']].count('AVAILABLE'),20)
        self.assertEqual(req['validator_summary']['overall_verdict'],'FAIL')
        self.assertEqual(len(req['validator_summary']['issues']),1)
        self.assertEqual(v2.validate_metadata_request(req,deepcopy(req))['status'],'VALID')

    def test_java_python_identity_and_evidence(self):
        req=self.build()
        self.assertEqual(req['catalog'][0]['features']['signature'],'m0(T)')
        self.assertEqual(req['catalog'][0]['method_id'],'java-source-v2:A#m0(java.util.Collection)')
        e=self.methods()[0]['evidence_occurrences'][0]
        self.assertEqual(e['evidence_id'],v2.evidence_id('src/main/java/A.java',self.index[0]['source_sha256'],
            self.methods()[0]['method_id'],e['evidence_type'],[e[k] for k in ('start_line','start_column','end_line','end_column')]))

    def test_stale_source_rejected(self):
        self.source.write_bytes(self.source_bytes+b' ')
        with self.assertRaisesRegex(ValueError,'V2_SOURCE_HASH'): self.build()

    def test_range_and_slice_hash_rejected(self):
        self.methods()[0]['start_line']=99999
        with self.assertRaisesRegex(ValueError,'V2_INVALID_RANGE'): self.build()
        self.index=deepcopy(self.__class__.index)
        self.methods()[0]['source_slice_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'V2_SLICE_HASH'): self.build()

    def test_bom_never_promotes_available(self):
        self.source.write_bytes(b'\xef\xbb\xbf'+self.source_bytes)
        self.index[0]['source_sha256']=hashlib.sha256(self.source.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError,'V2_UNSAFE_FILE_MAPPING'): self.build()

    def test_dangerous_escape_never_promotes_available(self):
        self.source.write_bytes(self.source_bytes.replace(b'\\u0061',b'\\u000a'))
        self.index[0]['source_sha256']=hashlib.sha256(self.source.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError,'V2_UNSAFE_FILE_MAPPING'): self.build()

    def test_unavailable_and_duplicates_reject_entire_case(self):
        self.methods()[0]['identity_status']='CONFLICT'; self.methods()[0]['method_id']=None
        with self.assertRaisesRegex(ValueError,'V2_IDENTITY_UNAVAILABLE'): self.build()
        self.index=deepcopy(self.__class__.index)
        for key in ('method_id','identity_signature','name','signature'):
            self.methods()[1][key]=self.methods()[0][key]
        with self.assertRaisesRegex(ValueError,'V2_DUPLICATE_IDENTITY'): self.build()

    def test_version_isolation(self):
        self.meta['schema_version']='2.0'
        with self.assertRaisesRegex(ValueError,'V2_INDEX_VERSION'): self.build()
        self.meta=deepcopy(self.__class__.meta)
        req=self.build()
        with self.assertRaises(ValueError): old.validate_metadata_request(req,req)
        pair=self.pair()
        with self.assertRaises(ValueError): old_brd.build_brd_request(pair['C'])

    def test_pair_immutable_deterministic_and_prompt_frozen(self):
        with patch.object(socket.socket,'connect',side_effect=AssertionError('NETWORK')):
            req=self.build(); before=deepcopy(req); rank=self.ranking(req)
            pair=brd.build_pair(req,deepcopy(req),rank)
            self.assertEqual(req,before)
            self.assertEqual(pair,brd.build_pair(req,deepcopy(req),rank))
            restored=deepcopy(pair['BRD']); restored['messages'][0]['content']=pair['C']['messages'][0]['content']
            self.assertEqual(pair['C'],restored)
            self.assertEqual(pair['BRD_prompt_sha256'],'0d836881c725e6d363c6b795c8b27dfd054d1ab3afbe7acf5f3f8c72ff5b6b48')
            self.assertEqual(json.loads(pair['C']['messages'][1]['content'])['candidates'][0]['candidate']['features'],req['catalog'][0]['features'])

    def test_strict_permutation_protocol(self):
        pair=self.pair()
        valid={'ranking':list(range(20,0,-1)),'reason':'Synthetic judgment.'}
        for arm in ('C','BRD'):
            self.assertEqual(brd.validate_response(pair,json.dumps(valid),arm=arm)['status'],'SUCCESS')
        invalid=[{**valid,'extra':1},{**valid,'reason':'x'*2001}, {**valid,'ranking':[True]+list(range(2,21))},
                 {**valid,'ranking':list(range(1,20))}, {**valid,'ranking':[1]*20},
                 {**valid,'ranking':list(range(2,22))}]
        for value in invalid:
            self.assertEqual(brd.validate_response(pair,json.dumps(value),arm='BRD')['status'],'INVALID_RESPONSE')

    def test_candidate_tamper_and_evidence_domain_rejected(self):
        req=self.build(); rank=self.ranking(req)
        rank['ranking'][0]['features']['signature']='faked()'
        with self.assertRaisesRegex(ValueError,'V2_CANDIDATE_CHANGED'): brd.build_pair(req,deepcopy(req),rank)
        self.methods()[0]['evidence_occurrences'][0]['evidence_id']='sha256:'+'0'*64
        with self.assertRaisesRegex(ValueError,'V2_EVIDENCE_ID'): self.build()

    def test_external_reference_and_failure_state_immutable(self):
        req=self.build(); reference=deepcopy(req)
        req['validator_summary']['overall_verdict']='PASS'
        with self.assertRaises(ValueError): v2.validate_metadata_request(req,reference)

    def test_unavailable_source_is_retained_when_ranked_in_top20(self):
        req=self.build()
        scores=[1]*20+[2]
        with patch.object(runner,'_scores',return_value=scores):
            rank=runner.rank_metadata_v2(req,deepcopy(req),algorithm='bm25')
        pair=brd.build_pair(req,deepcopy(req),rank)
        rows=json.loads(pair['C']['messages'][1]['content'])['candidates']
        self.assertEqual(len(rows),20)
        self.assertEqual(rows[0]['candidate']['location_status'],'UNAVAILABLE_MAPPING')
        self.assertIsNone(rows[0]['candidate']['source_implementation']['method_declaration_and_body'])


if __name__=='__main__': unittest.main()
