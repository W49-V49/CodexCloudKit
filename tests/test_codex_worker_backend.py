"""Adapter protocol tests using explicit fake CLI subprocesses, never real model proof."""
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from cloudkit.runtime import codex_worker_backend as backend


class CodexWorkerBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='codex-backend-fixture-', dir='/tmp')
        self.root = Path(self.temp.name)
        self.fake = self.root/'explicit_fake_cli.py'
        self.fake.write_text('''import json,re,sys,time,subprocess,os
from pathlib import Path
mode=sys.argv[1];prompt=sys.stdin.read()
nonce=re.search(r'CLOUDKIT_MODEL_PROOF_[0-9a-f]+',prompt).group(0)
Path('request-proof.json').write_text(json.dumps({'argv':sys.argv[2:],'prompt':prompt,'cwd':os.getcwd()}))
def emit(value):print(json.dumps(value),flush=True)
if mode=='version':print('codex-cli 0.164.0');sys.exit()
if mode=='receipt':emit({'capability':'model-worker','model_response':nonce});sys.exit()
if mode=='retry':emit({'type':'error','message':'temporary stream retry'})
if mode=='command':emit({'type':'item.completed','item':{'type':'command_execution','aggregated_output':nonce}})
elif mode=='reasoning':emit({'type':'item.completed','item':{'type':'reasoning','text':nonce}})
elif mode=='wrong':emit({'type':'item.completed','item':{'type':'agent_message','text':'does not contain the requested proof'}})
elif mode=='timeout':
 subprocess.Popen([sys.executable,'-c',"import time;from pathlib import Path;Path('child-started').write_text('ready');time.sleep(0.7);Path('late-child-output').write_text('should not exist')"])
 time.sleep(5)
elif mode=='tree':
 for n in range(3):
  grandchild="import time;from pathlib import Path;Path('grandchild-'+str("+str(n)+")).write_text('ready');time.sleep(1.2);Path('late-grandchild-'+str("+str(n)+")).write_text('should not exist')"
  child="import subprocess,sys,time;from pathlib import Path;Path('child-'+str("+str(n)+")).write_text('ready');subprocess.Popen([sys.executable,'-c',"+repr(grandchild)+"]);time.sleep(5)"
  subprocess.Popen([sys.executable,'-c',child])
 time.sleep(5)
elif mode=='credentials':
 print('token='+('ghp_'+'x'*36),file=sys.stderr)
 print('https://example.invalid/file?'+'token='+('a'*30),file=sys.stderr)
 emit({'type':'item.completed','item':{'type':'agent_message','text':nonce+' '+('github_pat_'+'y'*36)}})
else:emit({'type':'item.completed','item':{'type':'agent_message','text':nonce+' explicit test fixture'}})
if mode!='no-turn':emit({'type':'turn.failed' if mode=='failed-turn' else 'turn.completed'})
if mode=='late-error':emit({'type':'error','message':'final request failed'})
if mode=='nonzero':sys.exit(7)
''')
        self.capability = {'kind':'capability','challenge':'nonce-from-supervisor','backend_id':'fixture-only'}

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, mode='good', request=None, timeout=5):
        return backend.run(request or self.capability, [sys.executable,str(self.fake),mode], timeout, self.root)

    def test_capability_requires_agent_nonce_and_completed_turn(self):
        result,code=self.invoke()
        self.assertEqual(code,0);self.assertTrue(result['passed'])
        self.assertEqual(result['capability'],'model-worker')
        self.assertEqual(result['challenge'],self.capability['challenge'])
        self.assertTrue(result['proof']['nonce_found_in_agent_message'])
        self.assertTrue(result['proof']['turn_completed'])
        self.assertFalse(result['acceptance_run_by_backend'])

    def test_plain_version_and_receipt_echo_are_not_model_proof(self):
        for mode in ('version','receipt'):
            with self.subTest(mode=mode):
                result,code=self.invoke(mode)
                self.assertEqual(code,65);self.assertFalse(result['model_output_received'])
                self.assertNotIn('capability',result)

    def test_tools_reasoning_wrong_nonce_and_incomplete_turn_are_rejected(self):
        for mode in ('command','reasoning','wrong','no-turn'):
            with self.subTest(mode=mode):
                result,code=self.invoke(mode)
                self.assertNotEqual(code,0);self.assertFalse(result['passed'])

    def test_failed_turn_rejects_an_earlier_agent_nonce(self):
        result,code=self.invoke('failed-turn')
        self.assertEqual(code,65);self.assertEqual(result['failure'],'codex-turn-failed')

    def test_nonzero_exit_is_preserved_and_not_success(self):
        result,code=self.invoke('nonzero')
        self.assertEqual(code,7);self.assertEqual(result['returncode'],7)
        self.assertFalse(result['passed']);self.assertEqual(result['model_response'],'')

    def test_completed_retry_recovers_but_late_error_rejects(self):
        result,code=self.invoke('retry')
        self.assertEqual(code,0);self.assertTrue(result['proof']['turn_completed'])
        result,code=self.invoke('late-error')
        self.assertEqual(code,65);self.assertTrue(result['proof']['turn_failed'])

    def test_work_receipt_includes_previous_independent_acceptance_in_prompt(self):
        request={'kind':'work','request_id':'work-1','goal':'Repair the total','project_id':'example/project',
                 'source_sha':'a'*40,'acceptance_commands':[[sys.executable,'-m','unittest']],
                 'previous_acceptance':{'passed':False,'evidence':'test_total failed'},'checkpoint':{'round':2}}
        result,code=self.invoke(request=request)
        self.assertEqual(code,0);self.assertEqual(result['request_id'],'work-1')
        recorded=json.loads((self.root/'request-proof.json').read_text())
        self.assertEqual(recorded['cwd'],str(self.root))
        self.assertIn('test_total failed',recorded['prompt'])
        self.assertIn('supervisor will run acceptance',recorded['prompt'])
        self.assertIn('--json',recorded['argv']);self.assertIn('--ephemeral',recorded['argv'])
        self.assertEqual(recorded['argv'][-1],'-')
        self.assertEqual(recorded['argv'][recorded['argv'].index('-C')+1],str(self.root))
        self.assertNotIn('--ignore-user-config',recorded['argv'])

    def test_credential_and_signed_url_output_is_redacted(self):
        result,code=self.invoke('credentials')
        self.assertEqual(code,0)
        output=json.dumps(result)
        self.assertNotIn('ghp_'+'x'*36,output)
        self.assertNotIn('github_pat_'+'y'*36,output)
        self.assertNotIn('https://example.invalid/file?'+'token=',output)
        self.assertIn('[REDACTED',output)

    def test_receipt_metadata_is_also_redacted_without_breaking_json(self):
        request=dict(self.capability,backend_id='ghp_'+'z'*36)
        result,code=self.invoke(request=request)
        self.assertEqual(code,0);self.assertEqual(result['backend_id'],'[REDACTED]')

    def test_basic_headers_and_credential_urls_are_redacted(self):
        self.assertEqual(backend.scrub('Authorization: Basic dXNlcjpwYXNz'),'Authorization: [REDACTED]')
        self.assertEqual(backend.scrub('https://name:password@example.invalid/repo'),
                         'https://[REDACTED]@example.invalid/repo')

    def test_timeout_terminates_the_cli_and_its_tool_child(self):
        result,code=self.invoke('timeout',timeout=0.15)
        self.assertEqual(code,124);self.assertTrue(result['timed_out'])
        time.sleep(0.8)
        self.assertFalse((self.root/'late-child-output').exists())

    def test_inner_timeout_terminates_multiple_started_child_generations(self):
        result,code=self.invoke('tree',timeout=0.6)
        self.assertEqual(code,124)
        self.assertEqual(len(list(self.root.glob('grandchild-*'))),3)
        time.sleep(1.3)
        self.assertFalse(list(self.root.glob('late-grandchild-*')))

    def test_outer_supervisor_kill_closes_lifeline_and_terminates_cli_tools(self):
        argv=[sys.executable,str(Path(backend.__file__)), '--codex-command',
              json.dumps([sys.executable,str(self.fake),'timeout']),'--cwd',str(self.root),'--timeout','30']
        process=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                 text=True,start_new_session=True)
        process.stdin.write(json.dumps(self.capability));process.stdin.close();process.stdin=None
        deadline=time.monotonic()+5
        while not (self.root/'child-started').exists() and time.monotonic()<deadline:
            time.sleep(0.01)
        self.assertTrue((self.root/'child-started').exists())
        os.killpg(process.pid,signal.SIGKILL)
        process.communicate(timeout=3)
        time.sleep(0.8)
        self.assertFalse((self.root/'late-child-output').exists())

    def test_missing_executable_has_structured_failure(self):
        result,code=backend.run(self.capability,[str(self.root/'absent-codex')],1,self.root)
        self.assertEqual(code,127);self.assertFalse(result['passed'])
        self.assertEqual(result['failure'],'codex-process-failed')

    def test_command_json_and_invalid_requests(self):
        self.assertEqual(backend.command('["python3","fake.py"]'),['python3','fake.py'])
        self.assertEqual(backend.command('/path with spaces/codex'),['/path with spaces/codex'])
        for value in ('[]','[1]'):
            with self.assertRaises(ValueError):backend.command(value)
        for request in ({'kind':'other'},{'kind':'work'},{'kind':'capability','backend_id':'fixture'}):
            with self.assertRaises(ValueError):self.invoke(request=request)

    def test_cli_stdin_and_return_code_contract(self):
        argv=[sys.executable,str(Path(backend.__file__)), '--codex-command',
              json.dumps([sys.executable,str(self.fake),'good']),'--cwd',str(self.root),'--timeout','5']
        result=subprocess.run(argv,input=json.dumps(self.capability),text=True,capture_output=True)
        self.assertEqual(result.returncode,0);receipt=json.loads(result.stdout)
        self.assertTrue(receipt['passed']);self.assertEqual(receipt['backend_id'],'fixture-only')
        invalid=subprocess.run(argv,input='not-json',text=True,capture_output=True)
        self.assertEqual(invalid.returncode,64);self.assertEqual(json.loads(invalid.stdout)['failure'],'invalid-request')

    def test_parent_authentication_environment_is_unchanged(self):
        before={key:os.environ.get(key) for key in ('CODEX_HOME','OPENAI_API_KEY','GITHUB_PAT_TOKEN')}
        self.invoke()
        self.assertEqual(before,{key:os.environ.get(key) for key in before})


if __name__=='__main__':
    unittest.main()
