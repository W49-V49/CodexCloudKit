#!/usr/bin/env python3
"""Real Redis/RQ acceptance with disposable Git stores and explicit command fixtures."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


q = None
w = None
SERVER = None
SCRIPT = None
SERVER_STARTS = 0


def git(root, *args):
    return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL,text=True).strip()


class RealQueueAcceptance(unittest.TestCase):
    def setUp(self):
        global SERVER_STARTS
        self.temporary = tempfile.TemporaryDirectory(prefix='cloudkit-rq-accept-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.port_socket = socket.socket();self.port_socket.bind(('127.0.0.1',0));port = self.port_socket.getsockname()[1];self.port_socket.close()
        self.url = 'redis://127.0.0.1:' + str(port) + '/0'
        self.environment = patch.dict(os.environ, {'CLOUDKIT_REDIS_URL':self.url});self.environment.start()
        self.addCleanup(self.environment.stop)
        self.server_stdout = self.base/'redis-process.stdout.log'
        self.server_stderr = self.base/'redis-process.stderr.log'
        stdout = self.server_stdout.open('w');stderr = self.server_stderr.open('w')
        self.addCleanup(stdout.close);self.addCleanup(stderr.close)
        self.server = subprocess.Popen([SERVER,'--port',str(port),'--bind','127.0.0.1','--save','',
            '--appendonly','no','--dir',str(self.base),'--logfile',str(self.base/'redis.log')],
            stdout=stdout,stderr=stderr)
        self.addCleanup(self._stop_server)
        self.connection = q._connection()
        deadline = time.monotonic()+10
        while True:
            if self.server.poll() is not None:
                raise RuntimeError(self._startup_diagnostics('Redis exited before becoming ready'))
            try:
                self.connection.ping();SERVER_STARTS+=1;break
            except Exception:
                if time.monotonic()>=deadline:
                    raise RuntimeError(self._startup_diagnostics('Redis did not become ready before the startup deadline')) from None
                time.sleep(.05)
        remote = self.base/'control.git';self.control = self.base/'control'
        subprocess.run(['git','init','--bare','-q',str(remote)],check=True)
        subprocess.run(['git','clone','-q',str(remote),str(self.control)],check=True,capture_output=True)
        self.project = self.base/'runner'/'project';self.project.mkdir(parents=True)
        git(self.project,'init','-q','-b','main');git(self.project,'remote','add','origin','https://github.com/example/queue-project.git')
        (self.project/'result.txt').write_text('initial')
        (self.project/'worker.py').write_text('''import json,sys
from pathlib import Path
r=json.load(sys.stdin)
if r['kind']=='capability':
 print(json.dumps({'capability':'model-worker','challenge':r['challenge'],'backend_id':r['backend_id'],'model_response':'Explicit command fixture, not a real model'}));sys.exit()
p=Path('calls.txt');p.write_text(str(int(p.read_text())+1) if p.exists() else '1')
Path('result.txt').write_text('good' if r.get('previous_acceptance') else 'almost')
print(json.dumps({'fixture':True,'request_id':r['request_id']}))
''')
        git(self.project,'add','.');git(self.project,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','project')
        self.store = q.c.Store(self.control)
        sha = git(self.project,'rev-parse','HEAD')
        w.init_control(self.store,'example/control',sha)
        self.plan = {'project_id':'example/queue-project','branch':'main','source_sha':sha,
            'goal':'Synthetic command fixture: repair until independent acceptance passes',
            'progress_files':['result.txt'],'backends':[{'id':'fixture','independence_key':'explicit-command-fixture',
            'kind':'command_fixture','idempotent':True,'command':[sys.executable,'worker.py'],
            'probe_command':[sys.executable,'worker.py'],'timeout_seconds':10,'probe_timeout_seconds':10}],
            'acceptance':[[sys.executable,'-c',"from pathlib import Path; assert Path('result.txt').read_text()=='good'"]],
            'acceptance_timeout_seconds':10}
        w.register_worker(self.store,'goal',self.plan)

    def _stop_server(self):
        if self.server.poll() is None:
            self.server.terminate()
            try:self.server.wait(timeout=5)
            except subprocess.TimeoutExpired:self.server.kill();self.server.wait(timeout=5)

    def _startup_diagnostics(self, reason):
        details=[reason, 'invoked_path='+str(SERVER), 'exit_code='+str(self.server.poll())]
        for label,path in [('stdout',self.server_stdout),('stderr',self.server_stderr),('redis_log',self.base/'redis.log')]:
            details.append(label+':\n'+(path.read_text(errors='replace')[-6000:] if path.exists() else '(not created)'))
        return '\n'.join(details)

    def state(self):return self.store.read()[1]['autonomy_tasks']['goal']

    def queue(self):
        return q._queue('cloudkit',self.connection)

    def enqueue(self, **kwargs):return q.enqueue(str(self.control),'goal',str(self.project),**kwargs)

    def consume_process(self, max_jobs=10):
        command=[sys.executable,str(SCRIPT),'--repository',str(self.control),'--project-root',str(self.project),
                 'worker','--burst','--max-jobs',str(max_jobs)]
        environment={key:value for key,value in os.environ.items() if key not in {'GITHUB_REPOSITORY','GITHUB_TOKEN','GITHUB_PAT_TOKEN','GH_TOKEN'}}
        run=subprocess.run(command,capture_output=True,text=True,timeout=120,env=environment)
        self.assertEqual(run.returncode,0,run.stderr[-2000:]+run.stdout[-2000:])
        self.assertTrue(json.loads(run.stdout)['passed'])
        self.assertFalse(json.loads(run.stdout)['model_execution_proven_by_queue'])

    def test_real_queue_failure_repair_independent_acceptance_done(self):
        queued=self.enqueue();self.assertTrue(queued['enqueued'])
        job=self.queue().fetch_job(queued['job_id'])
        self.assertEqual(job.serializer.__name__,'JSONSerializer')
        self.assertNotIn('goal',job.kwargs);self.assertNotIn('plan',job.kwargs);self.assertNotIn('memory',job.kwargs)
        self.assertNotIn('repository',job.kwargs);self.assertNotIn('project_root',job.kwargs)
        self.consume_process()
        job.refresh();transport_result=job.return_value()
        self.assertNotIn('result',transport_result);self.assertNotIn('acceptance',transport_result)
        self.assertNotIn('goal',transport_result);self.assertNotIn('memory',transport_result)
        current=self.state();self.assertEqual(current['status'],'DONE')
        self.assertEqual((self.project/'calls.txt').read_text(),'2')
        self.assertEqual((self.project/'result.txt').read_text(),'good')
        self.assertEqual(len(current['processed_events']),2)
        reports=[json.loads(self.store.blob(self.store.read()[0],item['report_path'])) for item in current['run_evidence']]
        self.assertFalse(reports[0]['acceptance']['passed']);self.assertTrue(reports[1]['acceptance']['passed'])
        self.assertTrue(all(not item['actual_model_success_verified'] for item in reports))
        self.assertIsNone(current['outbox'])

    def test_duplicate_wakeup_is_unique_and_done_is_not_requeued(self):
        first=self.enqueue();second=self.enqueue()
        self.assertTrue(first['enqueued']);self.assertTrue(second['duplicate_delivery'])
        self.assertEqual(self.queue().count,1)
        self.consume_process()
        self.assertEqual(self.enqueue()['reason'],'DONE')
        self.assertEqual(self.queue().count,0)
        self.assertEqual((self.project/'calls.txt').read_text(),'2')

    def test_consumer_process_restart_resumes_persisted_round(self):
        self.enqueue();self.consume_process(max_jobs=1)
        self.assertEqual(self.state()['status'],'READY');self.assertEqual(self.queue().count,1)
        new_control=self.base/'runner-b-control'
        remote=git(self.control,'remote','get-url','origin')
        subprocess.run(['git','clone','-q',remote,str(new_control)],check=True,capture_output=True)
        new_project=self.base/'runner-b'/'project';new_project.parent.mkdir()
        subprocess.run(['git','clone','-q',str(self.project),str(new_project)],check=True,capture_output=True)
        git(new_project,'remote','set-url','origin','https://github.com/example/queue-project.git')
        shutil.rmtree(self.project.parent);shutil.rmtree(self.control)
        self.control=new_control;self.project=new_project;self.store=q.c.Store(new_control)
        self.consume_process()
        self.assertEqual(self.state()['status'],'DONE')
        self.assertEqual((self.project/'calls.txt').read_text(),'2')

    def test_disconnected_redis_preserves_outbox_and_switches_existing_routes(self):
        event=self.state()['outbox']['event_id']
        self.server.terminate();self.server.wait(timeout=5)
        class UnavailableDispatch:
            def __init__(self,*args,**kwargs):pass
            def dispatch(self,*args):raise RuntimeError('Synthetic dispatch endpoint unavailable')
            def dispatch_alternative(self,*args):raise RuntimeError('Synthetic independent endpoint unavailable')
        with patch.object(q.c,'GitHub',UnavailableDispatch):
            first=self.enqueue(github_repository='example/control')
            second=self.enqueue(github_repository='example/control')
            third=self.enqueue(github_repository='example/control')
        current=self.state();self.assertEqual(current['outbox']['event_id'],event)
        self.assertEqual(current['outbox']['status'],'PENDING')
        self.assertTrue(current['outbox']['rq']['tripped'])
        self.assertEqual(current['outbox']['rq']['failure_count'],2)
        self.assertEqual(current['outbox']['route'],'hourly_watchdog')
        self.assertTrue(first['outbox_preserved']);self.assertTrue(second['outbox_preserved'])
        self.assertEqual(third['reason'],'rq-route-tripped')

    def test_polling_consumer_stops_redis_retry_and_retains_git_wakeup(self):
        event=self.state()['outbox']['event_id']
        def future(state):
            state['autonomy_tasks']['goal']['next_wakeup_at']=q.c.utc()+3600
            return {},None
        self.store.update(future,'Keep a future task pending while testing consumer disconnect')
        environment={key:value for key,value in os.environ.items() if key not in {'GITHUB_REPOSITORY','GITHUB_TOKEN','GITHUB_PAT_TOKEN','GH_TOKEN'}}
        process=subprocess.Popen([sys.executable,str(SCRIPT),'--repository',str(self.control),
            '--project-root',str(self.project),'worker','--max-idle-time','30'],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=environment)
        try:
            deadline=time.monotonic()+10
            while not self.connection.smembers('rq:workers'):
                if time.monotonic()>=deadline:raise AssertionError('The real RQ consumer did not start')
                time.sleep(.05)
            self.server.terminate();self.server.wait(timeout=5)
            stdout,stderr=process.communicate(timeout=15)
            self.assertEqual(process.returncode,1,stderr[-1000:]+stdout[-1000:])
            result=json.loads(stdout)
            self.assertFalse(result['passed']);self.assertTrue(result['outboxes_preserved'])
            self.assertEqual(self.state()['outbox']['event_id'],event)
            self.assertEqual(self.state()['outbox']['status'],'PENDING')
        finally:
            if process.poll() is None:
                process.kill();process.communicate(timeout=5)

    def test_lost_redis_jobs_are_rebuilt_from_the_git_outbox(self):
        self.enqueue();self.assertEqual(self.queue().count,1)
        self.connection.flushdb();self.assertEqual(self.queue().count,0)
        self.assertEqual(self.state()['status'],'READY')
        self.consume_process()
        self.assertEqual(self.state()['status'],'DONE')
        self.assertEqual((self.project/'calls.txt').read_text(),'2')


def main():
    global q,w,SERVER,SCRIPT
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bridge-script',type=Path)
    parser.add_argument('--redis-server',default=os.environ.get('CLOUDKIT_REDIS_SERVER') or shutil.which('redis-server'))
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not args.redis_server or not Path(args.redis_server).is_file():parser.error('A real redis-server executable is required')
    try:
        import rq,redis
    except ImportError:parser.error('Install the optional rq/redis profile before running this acceptance')
    if args.bridge_script:
        SCRIPT=args.bridge_script.resolve()
    else:
        candidates=[Path(__file__).with_name('queue_bridge.py'),
                    Path(__file__).resolve().parents[1]/'src/cloudkit/runtime/queue_bridge.py']
        SCRIPT=next((path.resolve() for path in candidates if path.is_file()),None)
        if SCRIPT is None:
            try:
                from cloudkit.runtime import queue_bridge as selected
                SCRIPT=Path(selected.__file__).resolve()
            except ImportError:
                parser.error('Pass --bridge-script for the installed queue bridge location')
    sys.path.insert(0,str(SCRIPT.parent))
    import queue_bridge as bridge
    import worker_supervisor as supervisor
    # Preserve argv[0]: distro redis-server may be a symlink to a multicall
    # redis-check-rdb executable whose behavior depends on the invoked name.
    q,w,SERVER=bridge,supervisor,os.path.abspath(args.redis_server)
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(RealQueueAcceptance)
    result=unittest.TextTestRunner(verbosity=2,failfast=True).run(suite)
    version=subprocess.run([SERVER,'--version'],capture_output=True,text=True,timeout=5)
    report={'passed':result.wasSuccessful(),'tests_run':result.testsRun,'real_redis_service':SERVER_STARTS>0,
        'redis_service_starts':SERVER_STARTS,'redis_server_invocation':SERVER,'server_invocation_preserved':True,
        'redis_server_version':(version.stdout or version.stderr).strip(),
        'distributions':{'rq':importlib.metadata.version('rq'),'redis':importlib.metadata.version('redis')},
        'bridge_script_sha256':hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
        'canonical_task_state':'Git continuity.Store/autonomy_tasks','formal_memory_stored_in_redis':False,
        'serializer':'rq.serializers.JSONSerializer',
        'model_fixture_scope':'Explicit command fixture; no real model success or authorization asserted',
        'github_fallback_scope':'Controlled protocol fixture; real GitHub dispatch acceptance is a separate test',
        'consumer_restarted_in_separate_process':True,'consumer_relocated_to_new_control_and_project_directories':True,
        'queue_payload_contains_only_wakeup_routing':True,'failures':len(result.failures),'errors':len(result.errors)}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n')
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':raise SystemExit(main())
