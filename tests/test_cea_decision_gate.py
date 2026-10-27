"""Synthetic-only gate tests: no GT, API, production Issue or labels."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from cea_evidence_audit import audit
from cea_decision_gate import decide, parse


def fixture(flip=False):
    rows = [{'method_id':f'method:{i}', 'ordinal':i,
             'features':{'name':'target' if i == 0 else 'other',
                         'strings':['target'] if i == 1 else [], 'calls':[]}}
            for i in range(6)]
    def score(features, keywords, issue):
        return (10*(features['name']=='target') + (7 if flip else 0)*len(features['strings']), {})
    return audit({'ranking':rows}, 'target', lambda s:[s], score)


class GateTests(unittest.TestCase):
    def test_flip_review(self):
        result=decide(fixture(True))
        self.assertEqual(result['status'],'REVIEW')
        self.assertEqual(result['triggered_interventions'],['name'])
        self.assertEqual(result['interventions'][0]['original_top1_rank_shift'],1)

    def test_all_stable(self):
        result=decide(fixture())
        self.assertEqual(result['status'],'STABLE_UNDER_TESTED_MASKS')
        self.assertEqual(result['triggered_interventions'],[])

    def test_missing_invalid(self):
        for value in (None,{},[],{'schema_version':'unsupported'}):
            with self.subTest(value=value):self.assertEqual(decide(value)['status'],'UNVERIFIABLE')

    def test_incomplete_masks_override_flip(self):
        value=fixture(True);value['interventions'].pop()
        self.assertEqual(decide(value)['status'],'UNVERIFIABLE')

    def test_identity_inconsistent(self):
        value=fixture();value['interventions'][0]['new_top1_method_id']='unknown'
        self.assertEqual(decide(value)['status'],'UNVERIFIABLE')

    def test_rank_overlap_and_bool_corruption(self):
        for key,value in [('original_top1_new_rank',True),('topk_overlap_count',0),('top1_changed',1)]:
            report=fixture();report['interventions'][0][key]=value
            with self.subTest(key=key):self.assertEqual(decide(report)['status'],'UNVERIFIABLE')

    def test_moves_must_match(self):
        report=fixture();report['interventions'][1]['original_topk_moves'][0]['method_id']='wrong'
        self.assertEqual(decide(report)['status'],'UNVERIFIABLE')

    def test_duplicate_masks(self):
        report=fixture();report['interventions'][1]['masked_field']='name'
        self.assertEqual(decide(report)['status'],'UNVERIFIABLE')

    def test_zero_score_effect_cannot_flip(self):
        report=fixture(True)
        report['interventions'][0]['candidates_with_score_change']=0
        self.assertEqual(decide(report)['status'],'UNVERIFIABLE')

    def test_top20_overlap_cannot_be_less_than_top5(self):
        report=fixture()
        report['interventions'][0]['top20_overlap_count']=0
        self.assertEqual(decide(report)['status'],'UNVERIFIABLE')

    def test_unchanged_deterministic(self):
        report=fixture(True);before=deepcopy(report)
        self.assertEqual(decide(report),decide(report))
        self.assertEqual(report,before)

    def test_labels_not_used(self):
        report=fixture(True);expected=decide(report)
        report['label']='CORRECT';report['GT']=[]
        self.assertEqual(decide(report),expected)

    def test_json_duplicate_and_nan_rejected(self):
        for raw in (b'{"x":1,"x":2}',b'{"x":NaN}',b'{} trailing'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):parse(raw)

    def test_small_catalog(self):
        rows={'ranking':[{'method_id':'only','ordinal':0,'features':{'name':'one','strings':[],'calls':[]}}]}
        report=audit(rows,'one',lambda s:[],lambda *args:0)
        result=decide(report)
        self.assertEqual(result['status'],'STABLE_UNDER_TESTED_MASKS')
        self.assertEqual(result['interventions'][0]['top5_effective_size'],1)

    def test_cli_missing_and_no_overwrite(self):
        script=Path(__file__).resolve().parents[1]/'src/cea_decision_gate.py'
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'report.json';dest=Path(tmp)/'gate.json'
            command=[sys.executable,'-B',str(script),'--report',str(source),'--output',str(dest)]
            first=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(first.returncode,0,first.stderr)
            self.assertEqual(json.loads(dest.read_text())['status'],'UNVERIFIABLE')
            before=dest.read_bytes()
            second=subprocess.run(command,capture_output=True,text=True)
            self.assertNotEqual(second.returncode,0)
            self.assertEqual(dest.read_bytes(),before)

    def test_cli_valid_report_unchanged(self):
        script=Path(__file__).resolve().parents[1]/'src/cea_decision_gate.py'
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'report.json';source.write_text(json.dumps(fixture(True)),encoding='utf-8')
            before=source.read_bytes()
            result=subprocess.run([sys.executable,'-B',str(script),'--report',str(source)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'],'REVIEW')
            self.assertEqual(source.read_bytes(),before)


if __name__=='__main__':unittest.main()
