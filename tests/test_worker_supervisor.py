#!/usr/bin/env python3
"""Real Git and subprocess tests for the worker adapter; no model is simulated as real."""
import concurrent.futures
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from cloudkit.runtime import continuity as c
from cloudkit.runtime import worker_supervisor as w


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL, text=True).strip()


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='worker-supervisor-', dir='/tmp')
        self.base = Path(self.temporary.name)
        self.bare = self.base/'remote.git'
        subprocess.run(['git','init','--bare','-q',str(self.bare)],check=True)
        self.control = self.base/'control'
        subprocess.run(['git','clone','-q',str(self.bare),str(self.control)],check=True,capture_output=True)
        git(self.control,'switch','-c','automation/state')
        self.original = {'schema_version':1,'status':'IDLE','goal_id':'original',
            'execution_route':'chatgpt-private-runner','memory_partitions':{'untouched':{'revision':4}}}
        (self.control/'STATE.json').write_text(json.dumps(self.original))
        git(self.control,'add','STATE.json');git(self.control,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','control')
        git(self.control,'push','origin','automation/state')
        self.store = c.Store(self.control)
        self.project = self.base/'runner-a'/'project';self.project.mkdir(parents=True)
        git(self.project,'init','-q','-b','main');git(self.project,'remote','add','origin','https://github.com/fixture/project.git')
        (self.project/'result.txt').write_text('initial')
        self.worker = self.project/'worker.py'
        self.worker.write_text('''import json,sys,time
from pathlib import Path
r=json.load(sys.stdin)
if r['kind']=='capability':
 print(json.dumps({'capability':'model-worker','challenge':r['challenge'],'backend_id':r['backend_id'],'model_response':'Explicit command fixture, not a model'}));sys.exit()
p=Path('calls.txt');n=int(p.read_text())+1 if p.exists() else 1;p.write_text(str(n))
mode=sys.argv[1]
if mode=='repair':Path('result.txt').write_text('good' if r.get('previous_acceptance') else 'almost')
elif mode=='good':Path('result.txt').write_text('good')
elif mode=='sleep-good':time.sleep(1);Path('result.txt').write_text('good')
elif mode=='vary':print(['alpha','beta','gamma','delta'][(n-1)%4],file=sys.stderr);sys.exit(7)
elif mode=='timeout':time.sleep(5);Path('late.txt').write_text('should not exist')
print(json.dumps({'fixture':True,'request_id':r['request_id']}))
''')
        git(self.project,'add','.');git(self.project,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','project')
        self.plan = {'project_id':'fixture/project','branch':'main','source_sha':git(self.project,'rev-parse','HEAD'),
            'goal':'Produce good result via independent validation','progress_files':['result.txt'],
            'backends':[self.backend('primary','repair')],
            'acceptance':[[sys.executable,'-c',"from pathlib import Path; assert Path('result.txt').read_text()=='good', 'result is not good'"]],
            'acceptance_timeout_seconds':10}

    def tearDown(self):self.temporary.cleanup()

    def backend(self, name, mode):
        return {'id':name,'independence_key':name,'kind':'command_fixture','idempotent':True,
            'command':[sys.executable,'worker.py',mode], 'probe_command':[sys.executable,'worker.py','probe'],
            'timeout_seconds':10,'probe_timeout_seconds':10}

    def register(self):return w.register_worker(self.store,'goal',self.plan)
    def tick(self,event='one',root=None,store=None):return w.run_round(store or self.store,'goal',event,'owner-'+event,root or self.project)
    def fresh(self):
        new=self.base/'runner-b'/'project';new.parent.mkdir(exist_ok=True)
        subprocess.run(['git','clone','-q',str(self.project),str(new)],check=True,capture_output=True)
        git(new,'remote','set-url','origin','https://github.com/fixture/project.git')
        return new

    def test_failed_acceptance_drives_real_repair_and_done_stops(self):
        self.register();first=self.tick();self.assertEqual(first['status'],'READY',first)
        self.assertFalse(first['passed']);self.assertEqual(first['worker']['returncode'],0)
        second=self.tick('two');self.assertEqual(second['status'],'DONE',second)
        self.assertTrue(second['passed']);self.assertFalse(second['actual_model_success_verified'])
        self.assertEqual((self.project/'calls.txt').read_text(),'2')
        self.assertEqual(self.tick('three')['reason'],'DONE')
        _,state=self.store.read()
        for key,value in self.original.items():self.assertEqual(state[key],value)
        self.assertIsNone(state['autonomy_tasks']['goal']['outbox'])

    def test_existing_continuity_entrypoint_dispatches_worker_and_acceptance(self):
        self.plan['backends'] = [self.backend('primary', 'good')]
        self.register()
        result = c.run_round(self.store, 'goal', 'dispatcher-event', 'dispatcher-owner', self.project)
        self.assertEqual(result['status'], 'DONE', result)
        self.assertTrue(result['passed'])
        self.assertFalse(result['actual_model_success_verified'])
        self.assertEqual((self.project/'calls.txt').read_text(), '1')
        self.assertEqual(c.run_round(self.store, 'goal', 'dispatcher-again', 'dispatcher-owner', self.project)['reason'], 'DONE')

    def test_cross_process_and_cross_directory_restore(self):
        self.register();self.tick('before')
        new=self.fresh();shutil.rmtree(self.project.parent)
        result=subprocess.run([sys.executable,str(Path(w.__file__)),'--repository',str(self.control),'tick','--task-id','goal','--project-root',str(new),'--event-id','after'],check=True,capture_output=True,text=True)
        report=json.loads(result.stdout);self.assertEqual(report['status'],'DONE',report)
        self.assertEqual((new/'calls.txt').read_text(),'2')
        self.assertEqual((new/'result.txt').read_text(),'good')

    def test_two_equivalent_acceptance_failures_trip_and_fallback(self):
        self.plan['backends']=[self.backend('bad','noop'),self.backend('independent','good')]
        self.register();self.assertEqual(self.tick('one')['status'],'READY');self.tick('two')
        third=self.tick('three');self.assertEqual(third['status'],'DONE',third)
        parent,state=self.store.read();routes=state['autonomy_tasks']['goal']['worker_supervisor']['routes']
        self.assertEqual(routes['bad']['attempts'],2);self.assertTrue(routes['bad']['tripped'])
        archive=json.loads(self.store.blob(parent,'autonomy/goal/circuits/bad.json'))
        self.assertEqual(archive['trip_reason'],'equivalent-error-twice')

    def test_three_distinct_errors_without_progress_switch_route(self):
        self.plan['backends']=[self.backend('varying','vary'),self.backend('fallback','good')]
        self.register()
        for i in range(3):self.assertEqual(self.tick('fail-'+str(i))['status'],'READY')
        _,state=self.store.read();stats=state['autonomy_tasks']['goal']['worker_supervisor']['routes']['varying']
        self.assertEqual(stats['attempts'],3);self.assertEqual(stats['no_progress'],3);self.assertTrue(stats['tripped'])
        self.assertEqual(self.tick('recover')['status'],'DONE')

    def test_duplicate_event_does_not_execute_worker(self):
        self.register();self.tick('same');again=self.tick('same')
        self.assertEqual(again['reason'],'duplicate-event');self.assertEqual((self.project/'calls.txt').read_text(),'1')

    def test_two_control_clones_observe_one_live_lease(self):
        self.plan['backends']=[self.backend('primary','sleep-good')];self.register()
        other=self.base/'other-control';subprocess.run(['git','clone','-q','--branch','automation/state',str(self.bare),str(other)],check=True,capture_output=True)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            a=pool.submit(self.tick,'first');b=pool.submit(self.tick,'second',None,c.Store(other));results=[a.result(),b.result()]
        self.assertEqual(sum(x.get('status')=='DONE' for x in results),1,results)
        self.assertEqual(sum(x.get('reason')=='live-lease' for x in results),1,results)
        self.assertEqual((self.project/'calls.txt').read_text(),'1')

    def test_completed_worker_result_resumes_acceptance_without_rerun(self):
        self.plan['backends']=[self.backend('primary','good')];self.register()
        with patch.object(w.e.Execution,'run',side_effect=RuntimeError('synthetic verifier infrastructure outage')):
            first=self.tick('interrupted')
        self.assertEqual(first['status'],'RETRY_WAIT',first)
        new=self.fresh();shutil.rmtree(self.project.parent)
        second=self.tick('resume',new)
        self.assertEqual(second['status'],'DONE',second)
        self.assertTrue(second['worker_result_resumed_without_reexecution'])
        self.assertEqual((new/'calls.txt').read_text(),'1')
        self.assertFalse(second['model_invocation_attempted'])
        _,state=self.store.read()
        self.assertEqual(state['autonomy_tasks']['goal']['worker_supervisor']['routes']['primary']['attempts'],1)

    def test_version_output_is_not_a_model_capability(self):
        self.plan['backends'][0]['probe_command']=[sys.executable,'--version'];self.register()
        result=self.tick();self.assertEqual(result['status'],'WAITING_CAPABILITY')
        self.assertFalse((self.project/'calls.txt').exists());self.assertFalse(result['actual_model_success_verified'])
        self.assertEqual(c.pending(self.store),[])
        process=subprocess.run([sys.executable,str(Path(w.__file__)),'--repository',str(self.control),'tick','--task-id','goal','--project-root',str(self.project),'--event-id','cli-unavailable'],capture_output=True,text=True)
        self.assertEqual(process.returncode,1)
        self.assertEqual(json.loads(process.stdout)['status'],'WAITING_CAPABILITY')

    def test_bad_project_identity_releases_lease_and_archives_failure(self):
        self.register();git(self.project,'remote','set-url','origin','https://github.com/fixture/wrong.git')
        result=self.tick();self.assertEqual(result['status'],'BLOCKED',result)
        parent,state=self.store.read();self.assertIsNone(state['autonomy_tasks']['goal']['lease'])
        proof=json.loads(self.store.blob(parent,'autonomy/goal/worker-runs/one/setup-failure.json'))
        self.assertEqual(proof['status'],'BLOCKED')

    def test_direct_run_advances_without_round_confirmation(self):
        self.register();result=w.run_until_terminal(self.store,'goal',self.project,max_rounds=4)
        self.assertEqual(result['status'],'DONE',result);self.assertEqual((self.project/'calls.txt').read_text(),'2')

    def test_init_control_without_source_history_and_preserve_existing(self):
        bare=self.base/'new-private.git';subprocess.run(['git','init','--bare','-q',str(bare)],check=True)
        checkout=self.base/'new-private';subprocess.run(['git','clone','-q',str(bare),str(checkout)],check=True,capture_output=True)
        store=c.Store(checkout)
        result=w.init_control(store,'fixture/toolbox',self.plan['source_sha'])
        self.assertTrue(result['initialized'])
        parent,state=store.read();self.assertTrue(state['continuous_execution_enabled'])
        again=w.init_control(store,'fixture/different','a'*40,'other')
        self.assertFalse(again['initialized']);self.assertTrue(again['existing_state_preserved'])
        other_parent,unchanged=store.read();self.assertEqual(parent,other_parent);self.assertEqual(state,unchanged)

    def test_initial_source_sha_drift_is_not_silently_accepted(self):
        self.register();(self.project/'extra.txt').write_text('changed')
        git(self.project,'add','extra.txt');git(self.project,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','new head')
        result=self.tick();self.assertEqual(result['status'],'BLOCKED',result)
        self.assertFalse((self.project/'calls.txt').exists())

    def test_capability_cache_rechecks_changed_executable_script(self):
        self.plan['backends']=[self.backend('primary','good')]
        self.plan['backends'][0]['probe_command']=[sys.executable,'probe.py']
        probe=self.project/'probe.py';probe.write_text("print('version only')")
        self.register();first=self.tick('bad-probe');self.assertEqual(first['status'],'WAITING_CAPABILITY',first)
        probe.write_text("import json,sys;r=json.load(sys.stdin);print(json.dumps({'capability':'model-worker','challenge':r['challenge'],'backend_id':r['backend_id'],'model_response':'Explicit fixture'}))")
        second=self.tick('changed-probe');self.assertEqual(second['status'],'DONE',second)
        _,state=self.store.read();stats=state['autonomy_tasks']['goal']['worker_supervisor']['routes']['primary']
        self.assertEqual(len(stats['history']),1);self.assertTrue(stats['history'][0]['tripped'])

    def test_changed_done_inputs_revalidate_before_calling_worker(self):
        self.plan['backends']=[self.backend('primary','good')];self.register()
        self.assertEqual(self.tick('complete')['status'],'DONE')
        (self.project/'result.txt').write_text('regressed')
        changed=self.tick('changed');self.assertEqual(changed['status'],'READY',changed)
        self.assertFalse(changed['model_invocation_attempted']);self.assertEqual((self.project/'calls.txt').read_text(),'1')
        fixed=self.tick('repair');self.assertEqual(fixed['status'],'DONE',fixed)
        self.assertEqual((self.project/'calls.txt').read_text(),'2')

    def test_internal_symlink_cannot_alias_excluded_file(self):
        self.plan['backends']=[self.backend('primary','good')]
        (self.project/'.env').write_text('SYNTHETIC_DO_NOT_ARCHIVE=plain_fixture_password')
        (self.project/'settings.py').symlink_to('.env')
        self.register();result=self.tick();self.assertEqual(result['status'],'RETRY_WAIT',result)
        self.assertFalse((self.project/'calls.txt').exists())
        parent,state=self.store.read()
        material=self.store.blob(parent,'STATE.json')
        self.assertNotIn(b'plain_fixture_password',material)

    def test_malformed_probe_json_switches_independent_route(self):
        bad=self.backend('bad','noop');bad['probe_command']=[sys.executable,'-c',"print('[]')"]
        self.plan['backends']=[bad,self.backend('good','good')];self.register()
        result=self.tick();self.assertEqual(result['status'],'DONE',result)
        _,state=self.store.read();stats=state['autonomy_tasks']['goal']['worker_supervisor']['routes']['bad']
        self.assertTrue(stats['tripped']);self.assertEqual(stats['attempts'],0)

    def test_repeated_verifier_infrastructure_failure_trips_and_stops(self):
        self.plan['backends']=[self.backend('primary','good')];self.register()
        with patch.object(w.e.Execution,'run',side_effect=OSError('synthetic persistent verifier filesystem failure')):
            first=self.tick('failure-one');second=self.tick('failure-two')
        self.assertEqual(first['status'],'RETRY_WAIT',first)
        self.assertEqual(second['status'],'BLOCKED',second)
        self.assertTrue(second['infrastructure_circuit_tripped'])
        self.assertEqual(self.tick('not-again')['reason'],'BLOCKED')
        self.assertEqual((self.project/'calls.txt').read_text(),'1')
        parent,state=self.store.read()
        circuit=json.loads(self.store.blob(parent,'autonomy/goal/circuits/infrastructure.json'))
        self.assertEqual(circuit['attempts'],2);self.assertTrue(circuit['pending_result_preserved'])

    def test_different_goals_cannot_write_the_same_workspace_concurrently(self):
        self.plan['backends']=[self.backend('primary','sleep-good')];self.register()
        w.register_worker(self.store,'other-goal',self.plan)
        other=self.base/'other-control';subprocess.run(['git','clone','-q','--branch','automation/state',str(self.bare),str(other)],check=True,capture_output=True)
        started=threading.Event();original=w._process
        def observed(engine,argv,request,timeout):
            if request['kind']=='work':started.set()
            return original(engine,argv,request,timeout)
        with patch.object(w,'_process',side_effect=observed),concurrent.futures.ThreadPoolExecutor(2) as pool:
            first=pool.submit(self.tick,'write-one')
            self.assertTrue(started.wait(5))
            second=pool.submit(w.run_round,c.Store(other),'other-goal','write-two','other-owner',self.project)
            blocked=second.result();finished=first.result()
        self.assertEqual(blocked['reason'],'workspace-busy',blocked)
        self.assertEqual(finished['status'],'DONE',finished)
        self.assertEqual((self.project/'calls.txt').read_text(),'1')

    def test_automatic_runner_clone_and_checkpoint_resume(self):
        base=Path('/workspace/autonomy-runs');base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='worker-auto-',dir=base) as directory:
            identifier=Path(directory).name
            w.register_worker(self.store,identifier,self.plan)
            original=self.store.git;clones=[]
            def local_fixture_clone(*args,**kwargs):
                if args and args[0]=='clone':
                    clones.append(args[-2])
                    return subprocess.check_output(['git','clone','--no-hardlinks',str(self.project),args[-1]],stderr=subprocess.DEVNULL)
                return original(*args,**kwargs)
            with patch.object(self.store,'git',side_effect=local_fixture_clone):
                result=w.run_until_terminal(self.store,identifier,None,max_rounds=4)
            root=Path(directory)/'project'
            self.assertEqual(result['status'],'DONE',result)
            self.assertEqual(clones,['https://github.com/fixture/project.git'])
            self.assertEqual((root/'calls.txt').read_text(),'2')
            self.assertEqual(git(root,'remote','get-url','origin'),'https://github.com/fixture/project.git')


if __name__=='__main__':unittest.main(verbosity=2)
