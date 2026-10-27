"""Full CLI execution using synthetic files only; network is forbidden."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
import evidence_replay as replay
import issue_input


class IssueInputCLITest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='issuelens-input-')
        self.base = Path(self.tmp.name)
        self.repo = self.base/'repo'; self.repo.mkdir()
        index = [{'file':'src/main/java/example/Fruit.java','classes':[{'name':'Fruit','methods':[
            {'name':'findCitrus','signature':'findCitrus(String)','annotations':[],
             'strings':['citrus missing'],'calls':['readFruit'],'line':3}]}]}]
        (self.repo/'index.json').write_text(json.dumps(index),encoding='utf-8')
        self.bench = self.base/'cases.json'
        self.bench.write_text(json.dumps([{'id':'fixture','repo_path':str(self.repo),
            'issue_text':'legacy citrus title','test_command':'mvn -Dtest=Fixture test'}]),encoding='utf-8')
        self.record = self.base/'input-record.json'

    def tearDown(self): self.tmp.cleanup()

    def cli(self, flags, entry='evidence_replay.py'):
        # Forbid network in the subprocess, including any accidental reranker call.
        code = "import runpy,sys,urllib.request,socket; def_block=None\n" + \
            "def blocked(*a,**k): raise SystemExit('NETWORK_FORBIDDEN')\n" + \
            "urllib.request.urlopen=blocked; socket.create_connection=blocked\n" + \
            "sys.argv=sys.argv[1:]; import os; sys.path.insert(0,os.path.dirname(sys.argv[0])); runpy.run_path(sys.argv[0],run_name='__main__')"
        args = [sys.executable,'-B','-c',code,str(ROOT/'src'/entry),*flags]
        if entry == 'evidence_replay.py': args += ['--no-llm','--auto','0']
        env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        return subprocess.run(args,cwd=self.base,env=env,capture_output=True,text=True,encoding='utf-8',timeout=40)

    def direct(self, flags, entry='evidence_replay.py'):
        return self.cli(['--repo',str(self.repo),'--issue-record',str(self.record),*flags],entry)

    def assert_record(self, text):
        data = json.loads(self.record.read_bytes())
        self.assertEqual(data['issue_text'],text)
        self.assertEqual(data['provenance']['issue_text_sha256'],hashlib.sha256(text.encode()).hexdigest())
        return data

    def test_title_only(self):
        r=self.direct(['--issue-title','citrus missing'])
        self.assertEqual(r.returncode,0,r.stderr); self.assert_record('citrus missing')
        self.assertIn('Step 5/5',r.stdout)

    def test_body_and_unicode(self):
        title='  水果查询 🍋  '; body='\r\n```java\r\nreadFruit("柠檬");\r\n```\r\n'
        r=self.direct(['--issue-title',title,'--issue-body',body])
        self.assertEqual(r.returncode,0,r.stderr); self.assert_record(title+'\n\n'+body)

    def test_full_file_bom_crlf_preserved(self):
        raw=b'\xef\xbb\xbf'+ '标题 🍋\r\n\r\n原始正文\r\n'.encode()
        p=self.base/'issue.txt';p.write_bytes(raw)
        r=self.direct(['--issue-file',str(p)])
        self.assertEqual(r.returncode,0,r.stderr)
        data=self.assert_record(raw.decode('utf-8'))
        self.assertEqual(data['provenance']['sources'][0]['sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(p.read_bytes(),raw)

    def test_body_file(self):
        p=self.base/'body.txt';p.write_bytes('复现\r\n```log\r\nmissing\r\n```'.encode())
        r=self.direct(['--issue-title','title','--issue-body-file',str(p)])
        self.assertEqual(r.returncode,0,r.stderr);self.assert_record('title\n\n'+p.read_bytes().decode())

    def test_empty_body(self):
        r=self.direct(['--issue-title','title','--issue-body',''])
        self.assertEqual(r.returncode,0,r.stderr)
        d=self.assert_record('title');self.assertEqual(d['provenance']['body'],'')

    def test_whitespace_body_not_stripped(self):
        r=self.direct(['--issue-title','title','--issue-body',' \r\n '])
        self.assertEqual(r.returncode,0,r.stderr);self.assert_record('title\n\n \r\n ')

    def test_long_file_rejected_before_index(self):
        p=self.base/'long.txt';p.write_bytes(b'x'*(issue_input.DEFAULT_MAX_BYTES+1))
        r=self.direct(['--issue-file',str(p)])
        self.assertEqual(r.returncode,2);self.assertIn('ISSUE_INPUT_TOO_LARGE',r.stderr)
        self.assertFalse(self.record.exists())

    def test_combined_utf8_budget_and_override(self):
        r=self.direct(['--issue-title','中','--issue-body','文','--max-issue-bytes','7'])
        self.assertEqual(r.returncode,2)  # 3+2+3 bytes, not four characters.
        r=self.direct(['--issue-title','中','--issue-body','文','--max-issue-bytes','8'])
        self.assertEqual(r.returncode,0,r.stderr);self.assert_record('中\n\n文')

    def test_invalid_encoding(self):
        p=self.base/'bad.txt';p.write_bytes(b'\xff\xfeinvalid')
        r=self.direct(['--issue-file',str(p)])
        self.assertEqual(r.returncode,2);self.assertIn('INVALID_UTF8',r.stderr)
        self.assertFalse(self.record.exists())

    def test_empty_full_file(self):
        p=self.base/'empty.txt';p.write_bytes(b' \r\n')
        r=self.direct(['--issue-file',str(p)])
        self.assertEqual(r.returncode,2);self.assertFalse(self.record.exists())

    def test_legacy_interface_and_verification_command(self):
        before=self.bench.read_bytes()
        r=self.cli(['--benchmark',str(self.bench),'--id','fixture','--issue-record',str(self.record)])
        self.assertEqual(r.returncode,0,r.stderr)
        self.assert_record('legacy citrus title')
        self.assertIn('mvn -Dtest=Fixture test',r.stdout)
        self.assertEqual(self.bench.read_bytes(),before)

    def test_retriever_entry(self):
        r=self.direct(['--issue-title','generic title','--issue-body','citrus readFruit'],'retriever.py')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assert_record('generic title\n\ncitrus readFruit')
        self.assertIn('findCitrus',r.stdout)

    def test_legacy_retriever_interface(self):
        r=self.cli(['--benchmark',str(self.bench),'--id','fixture','--index',str(self.repo/'index.json')], 'retriever.py')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('findCitrus',r.stdout)
        self.assertIn('mvn -Dtest=Fixture test',r.stdout)

    def test_record_never_overwritten(self):
        self.record.write_bytes(b'preserve')
        r=self.direct(['--issue-title','title'])
        self.assertEqual(r.returncode,2);self.assertEqual(self.record.read_bytes(),b'preserve')

    def test_conflicting_input_sources(self):
        for flags in [['--issue-title','title','--issue-file','file'],
                      ['--issue-file','file','--issue-body','body'],
                      ['--issue-body','body'], ['--issue-title','title','--id','fixture']]:
            with self.subTest(flags=flags):
                r=self.direct(flags);self.assertEqual(r.returncode,2)

    def test_protected_record_path(self):
        target=ROOT/'experiments'/'should-never-be-created-input.json'
        r=self.cli(['--repo',str(self.repo),'--issue-title','title','--issue-record',str(target)])
        self.assertEqual(r.returncode,2);self.assertIn('INPUT_RECORD_PROTECTED_PATH',r.stderr)
        self.assertFalse(target.exists())

    def test_full_body_reaches_existing_scorer(self):
        body='prefix '+('x'*300)+' citrus-marker'
        argv=['replay','--repo',str(self.repo),'--issue-title','title','--issue-body',body,'--no-llm','--auto','0']
        original=replay.score_method
        with patch.object(sys,'argv',argv),patch.object(replay,'score_method',wraps=original) as scorer, \
             patch.object(replay,'rerank_candidates',side_effect=AssertionError('No API')), \
             contextlib.redirect_stdout(io.StringIO()):
            replay.main()
        self.assertEqual(scorer.call_args.args[2],'title\n\n'+body)


if __name__=='__main__': unittest.main()
