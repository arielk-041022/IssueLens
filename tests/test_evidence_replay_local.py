"""Display integration tests; validator safety is tested independently."""
import contextlib
import copy
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import evidence_replay as replay


class ReplayLocalTest(unittest.TestCase):
    def setUp(self):
        self.top = [{'method':{'method_id':'java-source-v1:p.A#f()', 'file':'src/main/java/p/A.java',
                     'evidence_occurrences':[{'evidence_id':'sha256:'+str(i)*64} for i in range(1,10)]}}]
        self.report = dict(overall_verdict='FAIL',summary={'blocking_issues':1},evidence_results=[
            dict(method_id='java-source-v1:p.A#f()',file_path='src/main/java/p/A.java',
                 evidence_id=e['evidence_id'],status='VERIFIED',reason_code='ALL_LOCAL_SOURCE_CHECKS_PASSED',
                 basis={'extracted_source_snippet':'sink()'}) for e in self.top[0]['method']['evidence_occurrences']])

    def display(self):
        buffer = io.StringIO()
        with patch('evidence_validator.validate_evidence_v2',return_value=self.report) as validator, \
             contextlib.redirect_stdout(buffer):
            replay.show_local_evidence({'repo_path':'repo'},self.top,'index','a'*64,'b'*64)
        return buffer.getvalue(),validator

    def test_local_nine_pass_and_global_fail_both_visible(self):
        output,validator = self.display()
        self.assertIn('Local evidence: PASS (9/9 VERIFIED)',output)
        self.assertIn('Repository evidence: FAIL',output)
        self.assertIn('NOT semantic correctness or root-cause proof',output)
        self.assertIn('Runtime tests: NOT_RUN',output)
        self.assertEqual(1,validator.call_count)
        self.assertEqual('a'*64,validator.call_args.kwargs['index_sha256'])

    def test_missing_result_not_pass_and_inputs_unchanged(self):
        before = copy.deepcopy(self.top)
        self.report['evidence_results'].pop()
        output,_ = self.display()
        self.assertNotIn('Local evidence: PASS',output)
        self.assertEqual(before,self.top)

    def test_failed_occurrence_not_counted_as_pass(self):
        self.report['evidence_results'][0]['status']='STALE_SOURCE'
        output,_ = self.display()
        self.assertIn('Local evidence: UNVERIFIABLE (8/9 VERIFIED)',output)
        self.assertIn('STALE_SOURCE',output)

    def test_other_method_results_cannot_certify_candidate(self):
        for row in self.report['evidence_results']:
            row['method_id']='java-source-v1:p.A#other()'
        self.assertNotIn('Local evidence: PASS',self.display()[0])

    def test_no_evidence_does_not_claim_repository_checked(self):
        self.top[0]['method']['evidence_occurrences']=[]
        output,validator = self.display()
        self.assertIn('Repository evidence: NOT_CHECKED',output)
        validator.assert_not_called()

    def test_cli_refuses_local_protocol_without_no_llm_or_pins(self):
        for arguments in (['--evidence-protocol','evidence-local-v2'],
                          ['--evidence-protocol','evidence-local-v2','--no-llm'],
                          ['--evidence-index-sha256','a'*64]):
            with self.subTest(arguments=arguments),patch.object(sys,'argv',['replay',*arguments]), \
                 patch.object(replay,'rerank_candidates',side_effect=AssertionError('No API')), \
                 contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                replay.main()
            self.assertEqual(2,error.exception.code)


if __name__=='__main__':
    unittest.main()
