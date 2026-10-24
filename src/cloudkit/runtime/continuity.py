#!/usr/bin/env python3
"""Event-driven continuation in the existing automation/state control branch.

This adapter runs explicit commands through the existing memory execution engine.
Model goals remain WAITING_CAPABILITY until a real subscription worker is verified.
It never creates a second queue/database, copies authentication or changes billing.
"""
from __future__ import annotations

import argparse
import base64
import copy
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
import uuid

BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('memory_execution', BASE / 'execution.py')
execution = importlib.util.module_from_spec(spec)
spec.loader.exec_module(execution)
WORKFLOW = 'autonomy.yml'
TERMINAL = {'DONE', 'CANCELLED', 'BLOCKED'}


def utc():
    return dt.datetime.now(dt.timezone.utc).timestamp()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode() + b'\n'


def safe_content(data):
    if execution.TOKEN.search(data.decode('utf-8', errors='replace')):
        raise ValueError('Credential-pattern material cannot enter durable continuity storage')
    if re.search(rb'https?://\S+[?&](?:X-Amz-Signature|sig|token|signature)=', data, re.I):
        raise ValueError('Signed URLs cannot enter durable continuity storage')
    return data


class Store:
    """Optimistic, fast-forward writes preserving the existing control tree."""
    def __init__(self, repository, branch='automation/state'):
        self.repository = Path(repository).resolve()
        self.branch = branch

    def git(self, *args, data=None, env=None):
        environment = os.environ.copy()
        if env:
            environment.update(env)
        # GitHub Actions token remains only in process memory, never in a URL/config file.
        token = environment.get('GITHUB_TOKEN') or environment.get('GITHUB_PAT_TOKEN') or environment.get('GH_TOKEN')
        if token:
            count = int(environment.get('GIT_CONFIG_COUNT', '0'))
            environment.update({f'GIT_CONFIG_KEY_{count}': 'http.https://github.com/.extraheader',
                                f'GIT_CONFIG_VALUE_{count}': 'AUTHORIZATION: basic ' + base64.b64encode(('x-access-token:' + token).encode()).decode(),
                                'GIT_CONFIG_COUNT': str(count + 1)})
        result = subprocess.run(['git', '-C', str(self.repository), *args], input=data,
                                capture_output=True, env=environment)
        if result.returncode:
            raise RuntimeError('Git continuity storage operation failed; credential-bearing diagnostics suppressed')
        return result.stdout

    def read(self):
        self.git('fetch', 'origin', '+refs/heads/' + self.branch + ':refs/remotes/origin/' + self.branch)
        parent = self.git('rev-parse', 'refs/remotes/origin/' + self.branch).decode().strip()
        state = json.loads(self.git('show', parent + ':STATE.json'))
        if state.get('schema_version') != 1 or state.get('execution_route') != 'chatgpt-private-runner':
            raise ValueError('Unsupported control state or changed subscription authentication route')
        return parent, state

    def blob(self, parent, name):
        return self.git('show', parent + ':' + name)

    def update(self, mutation, message='Persist cloud continuation checkpoint'):
        # Conflicting writers re-read and merge; a failed push never overwrites newer memory.
        for attempt in range(3):
            parent, state = self.read()
            updates, result = mutation(state)
            if updates is None:
                return result
            updates = dict(updates)
            updates['STATE.json'] = encoded(state)
            with tempfile.TemporaryDirectory(prefix='continuity-index-') as temporary:
                env = {'GIT_INDEX_FILE': str(Path(temporary) / 'index')}
                self.git('read-tree', parent, env=env)
                for path, content in updates.items():
                    safe_content(content)
                    blob = self.git('hash-object', '-w', '--stdin', data=content).decode().strip()
                    self.git('update-index', '--add', '--cacheinfo', '100644,' + blob + ',' + path, env=env)
                tree = self.git('write-tree', env=env).decode().strip()
                commit = self.git('-c', 'user.name=CloudKit', '-c', 'user.email=cloudkit@localhost',
                                  'commit-tree', tree, '-p', parent, data=(message + '\n').encode()).decode().strip()
                try:
                    self.git('push', 'origin', commit + ':refs/heads/' + self.branch)
                    return result
                except RuntimeError:
                    if attempt == 2:
                        raise
        raise AssertionError('unreachable')


class GitHub:
    def __init__(self, repository, token=None, opener=None):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise ValueError('Expected owner/repository identity')
        self.repository = repository
        self.token = token or os.environ.get('GITHUB_TOKEN') or os.environ.get('GITHUB_PAT_TOKEN') or os.environ.get('GH_TOKEN')
        self.opener = opener or urllib.request.urlopen

    def request(self, method, path, value=None):
        if not self.token:
            raise ValueError('GitHub dispatch capability unavailable: no process token')
        request = urllib.request.Request('https://api.github.com/repos/' + self.repository + path,
            data=encoded(value) if value is not None else None, method=method,
            headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                     'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': 'application/json'})
        try:
            with self.opener(request, timeout=30) as response:
                content = response.read()
                return json.loads(content) if content else {'accepted': response.status == 204}
        except urllib.error.HTTPError as error:
            raise RuntimeError('GitHub continuity API HTTP ' + str(error.code)) from None

    def readiness(self):
        workflow = self.request('GET', '/actions/workflows/' + WORKFLOW)
        if workflow.get('state') != 'active':
            raise ValueError('Hourly/dispatch workflow is not active on GitHub')
        content = self.request('GET', '/contents/.github/workflows/' + WORKFLOW)
        source = base64.b64decode(content.get('content', '')).decode()
        if not re.search(r"cron:\s*['\"]17 \* \* \* \*['\"]", source) or 'workflow_dispatch:' not in source:
            raise ValueError('Published hourly and event triggers were not verified')
        return {'workflow_active': True, 'hourly_trigger_published': True,
                'dispatch_api_configured': True, 'dispatch_permission_verified_by_acceptance': False}

    def dispatch(self, ref, task_id, event_id):
        # The API request is issued immediately. GitHub queue start latency is external.
        return self.request('POST', '/actions/workflows/' + WORKFLOW + '/dispatches',
                            {'ref': ref, 'inputs': {'action': 'tick', 'task_id': task_id, 'event_id': event_id}})

    def dispatch_alternative(self, ref, task_id, event_id):
        return self.request('POST', '/dispatches', {'event_type': 'autonomy_tick',
                            'client_payload': {'ref': ref, 'task_id': task_id, 'event_id': event_id}})


def task_id(value):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('Task ID must contain 1–100 safe identifier characters')
    return value


def register(store, identifier, estimate_seconds, plan=None, kind='model', readiness=None, goal=None):
    task_id(identifier)
    if estimate_seconds < 0:
        raise ValueError('A nonnegative duration estimate is required')
    if estimate_seconds > 3600 and not (readiness and readiness.get('hourly_trigger_published') and readiness.get('initial_dispatch_accepted')):
        raise ValueError('Long tasks require published hourly trigger and an accepted dispatch probe before work')
    if kind == 'command':
        execution.validate(plan)
    elif kind != 'model':
        raise ValueError('Only explicit command plans or capability-gated model goals are supported')
    def mutation(state):
        tasks = state.setdefault('autonomy_tasks', {})
        if identifier in tasks:
            old = tasks[identifier]
            if old.get('plan') != plan or old.get('kind') != kind:
                raise ValueError('An existing task cannot silently change its execution plan/kind')
            return None, copy.deepcopy(old)
        item = {'schema_version': 1, 'task_id': identifier, 'kind': kind,
                'goal': goal, 'estimated_seconds': estimate_seconds, 'plan': plan,
                'status': 'READY' if kind == 'command' else 'WAITING_CAPABILITY',
                'capability_reason': None if kind == 'command' else 'No verified persistent subscription model worker registered',
                'created_at': utc(), 'updated_at': utc(), 'revision': 1,
                'preflight': readiness or {}, 'lease': None, 'processed_events': [],
                'outbox': {'event_id': uuid.uuid4().hex, 'status': 'PENDING'} if kind == 'command' else None,
                'next_wakeup_at': utc(), 'run_evidence': [], 'execution_task_key': None}
        tasks[identifier] = item
        return {}, copy.deepcopy(item)
    return store.update(mutation, 'Register duration-aware cloud continuation task')


def claim(store, identifier, event_id, owner, lease_seconds=3300, clock=utc):
    def mutation(state):
        item = state.get('autonomy_tasks', {}).get(identifier)
        if not item:
            return None, {'claimed': False, 'reason': 'task-not-found'}
        if item['status'] in TERMINAL:
            return None, {'claimed': False, 'reason': item['status']}
        if item['status'] == 'WAITING_CAPABILITY' and item['kind'] != 'model':
            return None, {'claimed': False, 'reason': 'WAITING_CAPABILITY'}
        if event_id in item['processed_events']:
            return None, {'claimed': False, 'reason': 'duplicate-event'}
        if item.get('lease') and item['lease']['expires_at'] > clock():
            return None, {'claimed': False, 'reason': 'live-lease'}
        if item['kind'] == 'model':
            # A script/role label is never accepted as a live model capability.
            item['status'] = 'WAITING_CAPABILITY'
            item['updated_at'] = clock()
            item['next_wakeup_at'] = clock() + 3600
            item['run_evidence'].append({'event_id': event_id, 'at': clock(), 'status': 'WAITING_CAPABILITY', 'model_called': False})
            item['processed_events'].append(event_id)
            return {}, {'claimed': False, 'reason': 'WAITING_CAPABILITY'}
        item['lease'] = {'owner': owner, 'event_id': event_id, 'expires_at': clock() + lease_seconds}
        item['status'] = 'ACTIVE'
        item['revision'] += 1
        item['updated_at'] = clock()
        return {}, {'claimed': True, 'task': copy.deepcopy(item)}
    return store.update(mutation, 'Lease existing cloud continuation checkpoint')


def pending(store, clock=utc):
    _, state = store.read()
    return [identifier for identifier, item in sorted(state.get('autonomy_tasks', {}).items(), key=lambda pair: pair[1].get('updated_at', 0))
            if item['status'] not in TERMINAL and
            (not item.get('lease') or item['lease']['expires_at'] <= clock()) and
            item.get('next_wakeup_at', 0) <= clock()]


def flush_outbox(store, github, ref, identifier):
    _, state = store.read()
    item = state['autonomy_tasks'][identifier]
    box = item.get('outbox')
    if item['status'] in TERMINAL or item['status'] == 'WAITING_CAPABILITY' or not box or box['status'] != 'PENDING':
        return {'dispatched': False, 'reason': item['status']}
    route = box.get('route', 'workflow_dispatch')
    if route == 'hourly_watchdog':
        return {'dispatched': False, 'route': route, 'reason': 'Both event API paths tripped; hourly schedule is the independent wakeup'}
    try:
        # One shared wakeup picks the oldest due task; GitHub may coalesce queued runs.
        if route == 'repository_dispatch':
            github.dispatch_alternative(ref, '', box['event_id'])
        else:
            github.dispatch(ref, '', box['event_id'])
    except (RuntimeError, ValueError, OSError) as error:
        fingerprint = execution.digest({'route': route, 'failure': execution.scrub(str(error))})
        def record_failure(latest):
            current = latest['autonomy_tasks'][identifier]
            current_box = current.get('outbox') or {}
            if current_box.get('event_id') != box['event_id'] or current['status'] in TERMINAL:
                return None, {'dispatched': False, 'reason': 'superseded'}
            failures = current_box.setdefault('failures', {})
            failures[fingerprint] = failures.get(fingerprint, 0) + 1
            current_box.setdefault('logs', []).append({'at': utc(), 'route': route, 'fingerprint': fingerprint,
                                                     'failure': execution.scrub(str(error))})
            count = failures[fingerprint]
            if count >= 2:
                current_box['route'] = 'repository_dispatch' if route == 'workflow_dispatch' else 'hourly_watchdog'
                current_box['circuit_tripped'] = route
            return {}, {'dispatched': False, 'route': route, 'failure_count': count,
                        'next_route': current_box.get('route', route)}
        failure = store.update(record_failure, 'Archive dispatch failure and select independent wakeup route')
        if failure.get('next_route') == 'repository_dispatch' and route != 'repository_dispatch':
            return flush_outbox(store, github, ref, identifier)
        return failure
    def mutation(latest):
        current = latest['autonomy_tasks'][identifier]
        if (current.get('outbox') or {}).get('event_id') == box['event_id']:
            current['outbox'].update(status='SENT', requested_at=utc())
        return {}, {'dispatched': True, 'event_id': box['event_id'], 'queue_start_time_guaranteed': False}
    return store.update(mutation, 'Record immediate next-round dispatch acceptance')


def snapshot(root, checkpoint, plan):
    names = set()
    for step in plan['steps']:
        names.update(step.get('outputs', []))
        for route in step.get('routes', []) + step.get('repair_routes', []):
            names.update(route.get('progress_files', []))
    # Preserve tracked repairs, including deletions, across fresh runners.
    changed = subprocess.check_output(['git', '-C', str(root), 'diff', 'HEAD', '--name-only', '-z']).decode().split('\0')
    names.update(x for x in changed if x)
    files, deleted = {}, []
    for name in sorted(names):
        parts = Path(name).parts
        if any(part in {'.git', '.aws', '.ssh'} for part in parts) or Path(name).name in {'auth.json', '.env', 'credentials'}:
            raise ValueError('Authentication/configuration secrets cannot enter execution snapshots')
        path = (root / name).resolve()
        if not path.is_relative_to(root) or path.is_symlink():
            raise ValueError('Snapshot path escapes project root')
        if path.is_file():
            files['project/' + name] = safe_content(path.read_bytes())
        elif not path.exists():
            deleted.append(name)
        else:
            raise ValueError('Declared snapshot outputs must be regular files')
    files['runtime/STATE.json'] = safe_content(checkpoint.read_bytes())
    for path in checkpoint.parent.rglob('*'):
        if path.is_file() and path != checkpoint and not path.name.endswith('.lock'):
            files['runtime/' + path.relative_to(checkpoint.parent).as_posix()] = safe_content(path.read_bytes())
    files['manifest.json'] = encoded({'deleted': deleted, 'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}})
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode='w:gz') as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return target.getvalue()


def restore(content, root, checkpoint):
    with tarfile.open(fileobj=io.BytesIO(content), mode='r:gz') as archive:
        manifest = json.load(archive.extractfile('manifest.json'))
        members = {member.name: member for member in archive.getmembers()}
        for name, digest in manifest['files'].items():
            member = members[name]
            if not member.isfile():
                raise ValueError('Only regular snapshot members are allowed')
            data = archive.extractfile(member).read()
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('Cross-runner snapshot fingerprint mismatch')
            prefix, relative = name.split('/', 1)
            base = root if prefix == 'project' else checkpoint.parent if prefix == 'runtime' else None
            if base is None:
                raise ValueError('Unknown snapshot namespace')
            target = (base / relative).resolve()
            if not target.is_relative_to(base) or target.is_symlink():
                raise ValueError('Unsafe snapshot member')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        for name in manifest['deleted']:
            target = (root / name).resolve()
            if not target.is_relative_to(root) or target.is_symlink():
                raise ValueError('Unsafe deleted snapshot path')
            target.unlink(missing_ok=True)


def run_round(store, identifier, event_id, owner, project_root=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,220}', event_id):
        raise ValueError('Event ID must be a safe durable evidence identifier')
    _, active = store.read()
    if active.get('autonomy_tasks', {}).get(identifier, {}).get('worker_supervisor'):
        try:
            from . import worker_supervisor
        except ImportError:
            import worker_supervisor
        return worker_supervisor.run_round(store, identifier, event_id, owner, project_root)
    acquired = claim(store, identifier, event_id, owner)
    if not acquired['claimed']:
        return acquired
    item = acquired['task']
    plan = item['plan']
    root = Path(project_root).resolve() if project_root else Path(os.environ.get('CLOUDKIT_RUNS_ROOT', '/workspace/autonomy-runs')) / identifier / 'project'
    checkpoint = root.parent / 'runtime' / 'STATE.json'
    try:
        if project_root is None:
            if root.exists():
                raise ValueError('Fresh isolated runner project path already exists; refusing to overwrite it')
            root.parent.mkdir(parents=True, exist_ok=True)
            # Local object transfer avoids credential URLs; actual origin still proves GitHub identity.
            source = str(store.repository) if plan['project_id'] == github_identity(store.repository) else 'https://github.com/' + plan['project_id'] + '.git'
            store.git('clone', '--no-hardlinks', source, str(root))
            subprocess.run(['git', '-C', str(root), 'remote', 'set-url', 'origin', 'https://github.com/' + plan['project_id'] + '.git'], check=True, capture_output=True)
            subprocess.run(['git', '-C', str(root), 'switch', '-C', plan['branch'], plan['source_sha']], check=True, capture_output=True)
        parent, _ = store.read()
        if item.get('snapshot_path'):
            content = store.blob(parent, item['snapshot_path'])
            if hashlib.sha256(content).hexdigest() != item['snapshot_sha256']:
                raise ValueError('Durable continuation snapshot fingerprint mismatch')
            restore(content, root, checkpoint)
        if item.get('execution_task_key'):
            persisted = json.loads(checkpoint.read_text())['execution_tasks'][item['execution_task_key']]
            plan = persisted['plan_snapshot']
            if execution.digest(plan) != persisted['plan_fingerprint']:
                raise ValueError('Persisted execution plan changed without a versioned replacement')
        snapshot_path = 'autonomy/' + identifier + '/snapshot.tar.gz'
        class DurableExecution(execution.Execution):
            def save(self):
                super().save()
                archive = snapshot(root, checkpoint, plan)
                def persist_inflight(state):
                    current = state['autonomy_tasks'][identifier]
                    lease = current.get('lease')
                    if not lease or lease['owner'] != owner or lease['event_id'] != event_id:
                        raise ValueError('Lease lost before durable execution checkpoint')
                    current.update(snapshot_path=snapshot_path,
                                   snapshot_sha256=hashlib.sha256(archive).hexdigest(),
                                   execution_task_key=self.key, updated_at=utc())
                    return {snapshot_path: archive}, None
                store.update(persist_inflight, 'Persist execution boundary before command side effects')
        runner = DurableExecution(plan, root, checkpoint)
        if item.get('execution_task_key'):
            runner.expected_resume_fingerprint = execution.digest(plan)
        report = runner.run(pause_after=1)
        archive = snapshot(root, checkpoint, plan)
        status = 'DONE' if report['passed'] else 'READY' if report['task_status'] == 'PAUSED' else 'BLOCKED'
        evidence = {'event_id': event_id, 'owner': owner, 'at': utc(), 'status': status,
                    'execution_task_key': report['task_key'], 'model_called': False,
                    'report_path': 'autonomy/' + identifier + '/runs/' + event_id + '.json'}
        def finish(state):
            latest = state['autonomy_tasks'][identifier]
            if not latest.get('lease') or latest['lease']['owner'] != owner or latest['lease']['event_id'] != event_id:
                raise ValueError('Continuation lease was lost; rejecting stale completion')
            latest.update(status=status, lease=None, updated_at=utc(), execution_task_key=report['task_key'],
                          snapshot_path=snapshot_path, snapshot_sha256=hashlib.sha256(archive).hexdigest(), next_wakeup_at=utc())
            latest['processed_events'].append(event_id)
            latest['run_evidence'].append(evidence)
            latest['outbox'] = {'event_id': uuid.uuid4().hex, 'status': 'PENDING'} if status == 'READY' else None
            return {snapshot_path: archive, evidence['report_path']: encoded(report)}, {'claimed': True, **evidence, 'passed': report['passed']}
        return store.update(finish, 'Persist actual execution evidence before dispatching next round')
    except Exception as error:
        # No blind replay: the durable execution checkpoint resolves uncertain side
        # effects. Recoverable infrastructure retries are finite and separately logged.
        error_text = execution.scrub(type(error).__name__ + ': ' + str(error))
        def failed(state):
            latest = state['autonomy_tasks'][identifier]
            if latest.get('lease', {}).get('owner') != owner:
                return None, {'claimed': True, 'status': 'LEASE_LOST'}
            recoverable = isinstance(error, (RuntimeError, OSError, subprocess.SubprocessError))
            fingerprint = execution.digest(error_text)
            failures = latest.setdefault('infrastructure_failures', {})
            failures[fingerprint] = failures.get(fingerprint, 0) + 1
            status = 'RETRY_WAIT' if recoverable and failures[fingerprint] < 2 else 'WAITING_CAPABILITY' if recoverable else 'BLOCKED'
            latest.update(status=status, lease=None, updated_at=utc(), failure=error_text,
                          next_wakeup_at=utc() if status == 'RETRY_WAIT' else utc() + 3600,
                          outbox={'event_id': uuid.uuid4().hex, 'status': 'PENDING'} if status == 'RETRY_WAIT' else None)
            if status == 'WAITING_CAPABILITY':
                latest['circuit_tripped'] = {'fingerprint': fingerprint, 'reason': 'equivalent-infrastructure-error-twice',
                                            'next_action': 'Independent transport/worker capability required; no repeated failed command'}
            latest['run_evidence'].append({'event_id': event_id, 'at': utc(), 'status': status, 'failure': error_text, 'model_called': False})
            return {}, {'claimed': True, 'status': status, 'failure': error_text}
        return store.update(failed, 'Archive cloud continuation infrastructure failure')


def github_identity(repository):
    origin = subprocess.check_output(['git', '-C', str(repository), 'remote', 'get-url', 'origin'], text=True).strip()
    match = re.search(r'github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$', origin)
    if not match:
        return os.environ.get('GITHUB_REPOSITORY', '')
    return match.group(1)


def verify_expected_sha(store, expected=None):
    """Bind optional legacy state SHA and the actual controller checkout together."""
    parent, state = store.read()
    actual = store.git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip()
    if expected:
        if not re.fullmatch(r'[0-9a-f]{40}', expected):
            raise ValueError('expected_sha must be a full lowercase Git commit SHA')
        if state.get('code_commit_sha') != expected:
            raise ValueError('Control state code SHA differs from expected_sha')
        if actual != expected:
            raise ValueError('Actual controller checkout HEAD differs from expected_sha')
    return parent, state, actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', default='.')
    parser.add_argument('--github-repository', default=os.environ.get('GITHUB_REPOSITORY'))
    parser.add_argument('--ref', default='main')
    parser.add_argument('--output')
    sub = parser.add_subparsers(dest='command', required=True)
    preflight = sub.add_parser('preflight')
    preflight.add_argument('--expected-sha')
    reg = sub.add_parser('register')
    reg.add_argument('--task-id', required=True)
    reg.add_argument('--estimate-seconds', type=int, required=True)
    reg.add_argument('--kind', choices=['model', 'command'], default='model')
    reg.add_argument('--plan')
    reg.add_argument('--goal')
    tick = sub.add_parser('tick')
    tick.add_argument('--task-id')
    tick.add_argument('--expected-sha')
    tick.add_argument('--event-id', default=uuid.uuid4().hex)
    tick.add_argument('--owner', default=os.environ.get('GITHUB_RUN_ID', 'cloud-session') + '-' + os.environ.get('GITHUB_RUN_ATTEMPT', '1'))
    sub.add_parser('inspect')
    args = parser.parse_args()
    store = Store(args.repository)
    github = GitHub(args.github_repository or github_identity(args.repository))
    if args.command == 'register':
        readiness = github.readiness()
        if args.estimate_seconds > 3600:
            probe = 'readiness-' + uuid.uuid4().hex
            github.dispatch(args.ref, args.task_id, probe)
            readiness['initial_dispatch_accepted'] = True
            readiness['dispatch_permission_verified_by_acceptance'] = True
        plan = json.loads(Path(args.plan).read_text()) if args.plan else None
        result = register(store, args.task_id, args.estimate_seconds, plan, args.kind, readiness, args.goal)
        if result['kind'] == 'command':
            result = {'task': result, 'dispatch': flush_outbox(store, github, args.ref, args.task_id)}
    elif args.command == 'tick':
        verify_expected_sha(store, args.expected_sha)
        identifiers = [task_id(args.task_id)] if args.task_id else pending(store)
        results = []
        command_ran = False
        worker_selected = False
        for identifier in identifiers:
            _, control = store.read()
            item = control.get('autonomy_tasks', {}).get(identifier, {})
            is_worker = bool(item.get('worker_supervisor'))
            is_execution = item.get('kind') == 'command' or is_worker
            if is_execution and command_ran:
                continue
            if item.get('status') == 'WAITING_CAPABILITY' and item.get('kind') != 'model':
                continue
            worker_selected = worker_selected or is_worker
            event = args.event_id + '-' + identifier
            result = run_round(store, identifier, event, args.owner)
            command_ran = command_ran or (is_execution and result.get('claimed', False))
            if result.get('status') in ('READY', 'RETRY_WAIT') or result.get('reason') == 'duplicate-event':
                result['next_round'] = flush_outbox(store, github, args.ref, identifier)
            results.append(result)
        # DONE may leave other tasks ready; request a shared sweep without extending
        # this Runner past one command boundary. Only one wakeup request is needed.
        remaining = [key for key in pending(store) if store.read()[1]['autonomy_tasks'][key]['status'] in ('READY', 'RETRY_WAIT')]
        if remaining and not any(item.get('next_round', {}).get('dispatched') for item in results):
            def ensure_wakeup(state):
                item = state['autonomy_tasks'][remaining[0]]
                if item.get('status') not in ('READY', 'RETRY_WAIT'):
                    return None, None
                if not item.get('outbox') or item['outbox'].get('status') == 'SENT':
                    item['outbox'] = {'event_id': uuid.uuid4().hex, 'status': 'PENDING'}
                    return {}, None
                return None, None
            store.update(ensure_wakeup, 'Reconcile shared wakeup after coalesced GitHub events')
            results.append({'pending_task_wakeup': flush_outbox(store, github, args.ref, remaining[0])})
        result = {'scope': 'worker-and-command-continuation' if worker_selected else 'actual-command-continuation',
                  'model_called': any(item.get('model_invocation_attempted') or item.get('model_called') for item in results),
                  'results': results}
    elif args.command == 'preflight':
        parent, state, actual = verify_expected_sha(store, args.expected_sha)
        result = {'schema_version': 1, 'verification_scope': 'dispatch-preflight-only', 'model_called': False,
                  'model_repair_loop_verified': False, 'passed': True, 'control_commit': parent,
                  'code_commit_sha': actual, 'control_code_commit_sha': state.get('code_commit_sha'),
                  'status': state.get('status'), 'continuous_execution_enabled': state.get('continuous_execution_enabled', False),
                  'capability_gated_tasks': {key: value['status'] for key, value in state.get('autonomy_tasks', {}).items()}}
    else:
        parent, state = store.read()
        result = {'control_commit': parent, 'autonomy_tasks': state.get('autonomy_tasks', {})}
    if args.output:
        execution.atomic(Path(args.output), result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
