#!/usr/bin/env python3
"""Durable worker/acceptance rounds stored in the existing cloud control state."""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

try:
    from . import continuity as c
except ImportError:
    import continuity as c

e = c.execution


def validate(plan):
    for key in ('project_id', 'branch', 'source_sha', 'goal', 'backends', 'acceptance'):
        if not plan.get(key):
            raise ValueError('Missing worker plan field: ' + key)
    if not re.fullmatch(r'[0-9a-f]{40}', plan['source_sha']):
        raise ValueError('A full source SHA is required')
    for field in ('progress_files', 'exclude_paths'):
        names = plan.get(field, [])
        if not isinstance(names, list) or any(not isinstance(name, str) or not name or Path(name).is_absolute() or '..' in Path(name).parts for name in names):
            raise ValueError(field + ' must list relative project file names')
    ids, independence, commands = set(), set(), set()
    for backend in plan['backends']:
        identifier = c.task_id(backend['id'])
        if identifier in ids or not backend.get('independence_key') or backend['independence_key'] in independence:
            raise ValueError('Worker routes need unique IDs and independent execution routes')
        if backend.get('kind') not in ('codex', 'command_fixture'):
            raise ValueError('Worker kind must identify a Codex adapter or explicit command fixture')
        for field in ('command', 'probe_command'):
            argv = backend.get(field)
            if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
                raise ValueError(field + ' must be a nonempty argv array')
        if e.digest(backend['command']) in commands:
            raise ValueError('Identical commands are not independent fallback routes')
        if backend.get('timeout_seconds', 600) <= 0:
            raise ValueError('Worker timeout must be positive')
        ids.add(identifier); independence.add(backend['independence_key']); commands.add(e.digest(backend['command']))
    for command in plan['acceptance']:
        if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
            raise ValueError('Acceptance commands must be argv arrays')
    if e.TOKEN.search(json.dumps(plan)):
        raise ValueError('Plan contains credential-shaped material')
    if plan.get('acceptance_timeout_seconds', 300) <= 0:
        raise ValueError('Acceptance timeout must be positive')


def register_worker(store, identifier, plan, estimate_seconds=1800, readiness=None):
    validate(plan)
    c.register(store, identifier, estimate_seconds, kind='model', readiness=readiness, goal=plan['goal'])
    def mutation(state):
        item = state['autonomy_tasks'][identifier]
        old = item.get('worker_supervisor')
        if old:
            if old['plan_fingerprint'] != e.digest(plan):
                raise ValueError('Replace worker goals through an explicit new task identity')
            return None, copy.deepcopy(item)
        item['worker_supervisor'] = {'schema_version': 1, 'plan': copy.deepcopy(plan),
            'plan_fingerprint': e.digest(plan), 'round': 0, 'routes': {}, 'events': [],
            'acceptance': None, 'inflight': None, 'snapshot_path': None}
        item.update(status='READY', capability_reason=None, next_wakeup_at=c.utc(),
                    outbox={'event_id': uuid.uuid4().hex, 'status': 'PENDING'})
        return {}, copy.deepcopy(item)
    return store.update(mutation, 'Register worker driver in the existing task checkpoint')


def init_control(store, project_id, source_sha, branch='main'):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', project_id) or not re.fullmatch(r'[0-9a-f]{40}', source_sha):
        raise ValueError('Project identity and a full source SHA are required')
    checked = subprocess.run(['git', 'check-ref-format', '--branch', branch], capture_output=True)
    if checked.returncode:
        raise ValueError('Invalid work branch')
    remote = store.git('ls-remote', '--heads', 'origin', 'refs/heads/' + store.branch).strip()
    if remote:
        commit, state = store.read()
        return {'initialized': False, 'existing_state_preserved': True, 'control_commit': commit, 'status': state['status']}
    state = {'schema_version': 1, 'status': 'IDLE', 'execution_route': 'chatgpt-private-runner',
        'continuous_execution_enabled': True, 'goal_id': None, 'goal_revision': 1, 'code_commit_sha': source_sha,
        'work_branch': branch, 'control_project_id': project_id, 'memory_partitions': {}, 'autonomy_tasks': {},
        'index_generation': 0, 'memory_revision': 0, 'created_at': e.now()}
    with tempfile.TemporaryDirectory(prefix='worker-control-init-') as directory:
        env = {'GIT_INDEX_FILE': str(Path(directory)/'index')}
        store.git('read-tree', '--empty', env=env)
        blob = store.git('hash-object', '-w', '--stdin', data=c.encoded(state)).decode().strip()
        store.git('update-index', '--add', '--cacheinfo', '100644,' + blob + ',STATE.json', env=env)
        tree = store.git('write-tree', env=env).decode().strip()
        commit = store.git('-c', 'user.name=Cloud automation', '-c', 'user.email=automation@users.noreply.github.com',
            'commit-tree', tree, data=b'Initialize durable cloud task state\n').decode().strip()
        try:
            store.git('push', 'origin', commit + ':refs/heads/' + store.branch)
        except RuntimeError:
            if store.git('ls-remote', '--heads', 'origin', 'refs/heads/' + store.branch).strip():
                winner, saved = store.read()
                return {'initialized': False, 'existing_state_preserved': True, 'control_commit': winner, 'status': saved['status']}
            raise
    return {'initialized': True, 'control_commit': commit, 'status': state['status']}


def _git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True)
    if result.returncode:
        raise ValueError('Project Git identity could not be observed')
    return result.stdout.strip()


def _engine(plan, identifier, root, checkpoint):
    head = _git(root, 'rev-parse', 'HEAD')
    steps = [{'id': 'accept-' + str(i), 'kind': 'acceptance', 'required': True,
        'inputs': plan.get('progress_files', []), 'routes': [{'id': 'verify', 'independence_key': 'independent-acceptance-' + str(i),
        'command': command, 'idempotent': True, 'timeout_seconds': plan.get('acceptance_timeout_seconds', 300)}]}
        for i, command in enumerate(plan['acceptance'])]
    acceptance_plan = {'schema_version': 1, 'project_id': plan['project_id'], 'branch': plan['branch'],
        'source_sha': head, 'goal_id': identifier, 'snapshot_base': plan['source_sha'], 'acceptance_version': plan.get('acceptance_version', 1), 'steps': steps}
    return e.Execution(acceptance_plan, root, checkpoint), acceptance_plan


def _inventory(root, plan):
    names = set(_git(root, 'ls-files', '-z').split('\0')) | set(_git(root, 'ls-files', '--others', '--exclude-standard', '-z').split('\0')) | set(plan.get('progress_files', []))
    names.discard('')
    names = {name for name in names if Path(name).name not in {'auth.json', '.env', 'credentials'}
        and not any(part in {'.git', '.aws', '.ssh', '.codex'} for part in Path(name).parts)}
    names -= set(plan.get('exclude_paths', []))
    _regular_paths(root, names)
    content = e.file_hashes(root, sorted(names))
    return {'head': _git(root, 'rev-parse', 'HEAD'), 'files': content, 'fingerprint': e.digest(content)}


def _regular_paths(root, names):
    for name in names:
        candidate = root/name
        for index in range(1, len(Path(name).parts) + 1):
            if root.joinpath(*Path(name).parts[:index]).is_symlink():
                raise ValueError('Worker evidence paths must not traverse symbolic links')
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root) or (resolved.exists() and not resolved.is_file()):
            raise ValueError('Worker evidence must identify regular project files')


def _snapshot(root, checkpoint, acceptance_plan):
    untracked = _git(root, 'ls-files', '--others', '--exclude-standard', '-z').split('\0')
    changed = subprocess.run(['git', '-C', str(root), 'diff', acceptance_plan.get('snapshot_base', acceptance_plan['source_sha']), '--name-only', '-z'], capture_output=True, text=True)
    tracked_delta = changed.stdout.split('\0') if changed.returncode == 0 else _git(root, 'ls-files', '-z').split('\0')
    names = []
    for name in untracked + tracked_delta:
        if not name:
            continue
        parts = Path(name).parts
        if any(part in {'.git', '.aws', '.ssh', '.codex'} for part in parts) or Path(name).name in {'auth.json', '.env', 'credentials'}:
            continue
        names.append(name)
    changed_current = _git(root, 'diff', 'HEAD', '--name-only', '-z').split('\0')
    _regular_paths(root, [name for name in names + changed_current if name])
    copied = copy.deepcopy(acceptance_plan)
    copied['steps'][0]['outputs'] = list(dict.fromkeys(copied['steps'][0].get('outputs', []) + names))
    return c.snapshot(root, checkpoint, copied)


def _backend_fingerprint(root, backend):
    files = {}
    for field in ('command', 'probe_command'):
        for index, value in enumerate(backend[field]):
            if index and Path(value).suffix not in {'.py', '.sh', '.js', '.mjs', '.cjs', '.ts'}:
                continue
            candidate = Path(value) if Path(value).is_absolute() else root/value
            if index == 0 and not candidate.is_file():
                executable = shutil.which(value)
                if executable:
                    candidate = Path(executable)
            try:
                if candidate.is_file():
                    files[field + ':' + str(index)] = e.digest(candidate.read_bytes())
            except OSError:
                continue
    return e.digest({'backend': backend, 'files': files})


def _acceptance_signature(engine):
    return e.digest([(name, sorted({fingerprint for route in step.get('routes', {}).values()
        for fingerprint in route.get('errors', {})})) for name, step in sorted(engine.state['steps'].items())
        if step['status'] != 'PASSED'])


def _process(engine, argv, request, timeout):
    # The established execution engine owns process-group timeouts and redaction.
    with tempfile.TemporaryDirectory(prefix='worker-request-', dir=engine.root.parent) as directory:
        request_file = Path(directory) / 'request.json'
        request_file.write_text(json.dumps(request, ensure_ascii=False))
        launcher = [sys.executable, '-c',
            'import json,subprocess,sys; p=json.load(open(sys.argv[1])); '
            'r=subprocess.run(p["argv"],input=p["stdin"],text=True); sys.exit(r.returncode)',
            str(request_file)]
        request_file.write_text(json.dumps({'argv': argv, 'stdin': json.dumps(request, ensure_ascii=False)}))
        return engine.run_process({'command': launcher, 'timeout_seconds': timeout})


def _error(code, stdout, stderr, exception):
    message = exception or (stderr.strip().splitlines()[-1] if stderr.strip() else stdout.strip().splitlines()[-1] if stdout.strip() else 'nonzero exit')
    message = re.sub(r'\b[0-9a-f]{12,}\b|\b\d+(?:\.\d+)?\b', '<value>', message)[:1500]
    return e.digest({'error': message}), message


def run_round(store, identifier, event_id, owner, project_root=None, clock=c.utc):
    c.task_id(identifier); c.task_id(event_id)
    root = Path(project_root).resolve(strict=True) if project_root else Path(os.environ.get('CLOUDKIT_RUNS_ROOT', '/workspace/autonomy-runs'))/identifier/'project'
    if project_root is None:
        workspace = Path(os.environ.get('CLOUDKIT_RUNS_ROOT', '/workspace/autonomy-runs')).resolve()
        if root.is_symlink() or not root.resolve().is_relative_to(workspace):
            raise ValueError('Default worker clone path is not a regular workspace destination')
    checkpoint = root.parent / ('.cloudkit-worker-' + identifier) / 'STATE.json'
    snapshot_path = 'autonomy/' + identifier + '/worker-snapshot.tar.gz'
    def claim(state):
        item = state.get('autonomy_tasks', {}).get(identifier)
        if not item or 'worker_supervisor' not in item:
            return None, {'claimed': False, 'reason': 'worker-task-not-found'}
        worker = item['worker_supervisor']; plan = worker['plan']
        validate(plan)
        if worker['plan_fingerprint'] != e.digest(plan):
            raise ValueError('Persisted worker plan fingerprint differs')
        if item['status'] in c.TERMINAL:
            if item['status'] != 'DONE' or not checkpoint.exists() or _inventory(root, plan)['fingerprint'] == worker.get('last_source_fingerprint'):
                return None, {'claimed': False, 'reason': item['status']}
            current_inventory = _inventory(root, plan)
            backend_id = worker.get('last_backend', plan['backends'][0]['id'])
            worker['pending_result'] = {'backend': backend_id, 'request_id': uuid.uuid4().hex,
                'result': [0, '{}', '', None], 'before': e.file_hashes(root, plan.get('progress_files', [])),
                'before_inventory': current_inventory, 'acceptance_only': True,
                'worker_evidence': {'backend': backend_id, 'kind': 'acceptance-revalidation', 'returncode': 0}}
            worker['events'].append({'kind': 'source-change-invalidated-done', 'at': e.now()})
        if event_id in item['processed_events']:
            return None, {'claimed': False, 'reason': 'duplicate-event'}
        if item.get('lease') and item['lease']['expires_at'] > clock():
            return None, {'claimed': False, 'reason': 'live-lease'}
        seconds = sum(x.get('probe_timeout_seconds', 60) for x in plan['backends']) + max(x.get('timeout_seconds', 600) for x in plan['backends']) + len(plan['acceptance']) * plan.get('acceptance_timeout_seconds', 300) + 120
        item.update(status='ACTIVE', lease={'owner': owner, 'event_id': event_id, 'expires_at': clock() + seconds}, updated_at=clock())
        return {}, {'claimed': True, 'task': copy.deepcopy(item)}
    acquired = store.update(claim, 'Lease one actual worker and acceptance round')
    if not acquired['claimed']:
        return acquired
    item = acquired['task']; worker = item['worker_supervisor']; plan = worker['plan']
    workspace_lock = None
    try:
        root.parent.mkdir(parents=True, exist_ok=True)
        workspace_lock = (root.parent/('.cloudkit-workspace-' + e.digest(str(root).encode())[:24] + '.lock')).open('a')
        try:
            fcntl.flock(workspace_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            workspace_lock.close()
            def busy(state):
                current = state['autonomy_tasks'][identifier]
                lease = current.get('lease') or {}
                if lease.get('owner') != owner or lease.get('event_id') != event_id:
                    return None, {'claimed': False, 'reason': 'lease-lost'}
                current.update(status='READY', lease=None, next_wakeup_at=clock(),
                    outbox={'event_id': uuid.uuid4().hex, 'status': 'PENDING'})
                return {}, {'claimed': False, 'reason': 'workspace-busy', 'status': 'READY'}
            return store.update(busy, 'Keep another goal from writing an active project workspace')
        if project_root is None and not root.exists():
            root.parent.mkdir(parents=True, exist_ok=True)
            source = str(store.repository) if plan['project_id'] == c.github_identity(store.repository) else 'https://github.com/' + plan['project_id'] + '.git'
            store.git('clone', '--no-hardlinks', source, str(root))
            subprocess.run(['git', '-C', str(root), 'remote', 'set-url', 'origin', 'https://github.com/' + plan['project_id'] + '.git'], check=True, capture_output=True)
            subprocess.run(['git', '-C', str(root), 'switch', '-C', plan['branch'], plan['source_sha']], check=True, capture_output=True)
        parent, _ = store.read()
        if not checkpoint.exists() and worker.get('snapshot_path'):
            data = store.blob(parent, worker['snapshot_path'])
            if e.digest(data) != worker['snapshot_sha256']:
                raise ValueError('Worker recovery snapshot digest differs')
            c.restore(data, root, checkpoint)
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        if worker['round'] == 0 and not worker.get('pending_result') and not worker.get('inflight') and _git(root, 'rev-parse', 'HEAD') != plan['source_sha']:
            raise ValueError('Initial worker checkout differs from the registered source SHA')
        engine, acceptance_plan = _engine(plan, identifier, root, checkpoint)
        if not checkpoint.exists():
            e.atomic(checkpoint, {'schema_version': 1, 'status': 'IDLE', 'execution_route': 'chatgpt-private-runner'})
    except Exception as error:
        reason = e.scrub(type(error).__name__ + ': ' + str(error))
        def setup_failed(state):
            current = state['autonomy_tasks'][identifier]
            lease = current.get('lease') or {}
            if lease.get('owner') != owner or lease.get('event_id') != event_id:
                return None, {'claimed': True, 'status': 'LEASE_LOST'}
            failed = {'claimed': True, 'status': 'BLOCKED', 'reason': reason, 'passed': False, 'model_invocation_attempted': False}
            current.update(status='BLOCKED', lease=None, outbox=None, next_wakeup_at=None)
            current['processed_events'].append(event_id)
            return {'autonomy/' + identifier + '/worker-runs/' + event_id + '/setup-failure.json': c.encoded(failed)}, failed
        try:
            return store.update(setup_failed, 'Archive failed worker recovery before releasing its lease')
        finally:
            if workspace_lock:
                workspace_lock.close()
    round_number = worker['round'] + 1
    updates = {}; report = {'schema_version': 1, 'task_id': identifier, 'event_id': event_id, 'round': round_number,
        'scope': 'Worker subprocess and independent project acceptance', 'model_invocation_attempted': False,
        'actual_model_success_verified': False, 'passed': False, 'started_at': e.now()}
    def persist():
        archive = _snapshot(root, checkpoint, acceptance_plan)
        worker.update(snapshot_path=snapshot_path, snapshot_sha256=e.digest(archive))
        def mutation(state):
            current = state['autonomy_tasks'][identifier]
            lease = current.get('lease')
            if not lease or lease['owner'] != owner or lease['event_id'] != event_id:
                raise ValueError('Worker lease was lost')
            current['worker_supervisor'] = copy.deepcopy(worker)
            return {snapshot_path: archive, **updates}, None
        store.update(mutation, 'Persist worker checkpoint before next side effect')
    def logged(name, result):
        code, stdout, stderr, exception = result
        text = 'stdout:\n' + stdout + '\nstderr:\n' + stderr + '\n'
        path = 'autonomy/' + identifier + '/worker-runs/' + event_id + '/' + name + '.log'
        updates[path] = text.encode()
        return {'returncode': code, 'exception': exception, 'log': path, 'log_sha256': e.digest(text.encode())}
    try:
        if worker.get('inflight') and not worker['inflight'].get('idempotent'):
            report.update(status='WAITING_CAPABILITY', reason='interrupted-worker-needs-supported-resume-route')
        else:
            pending_result = worker.get('pending_result')
            selected = next((route for route in plan['backends'] if pending_result and route['id'] == pending_result['backend']), None)
            for backend in ([] if selected else plan['backends']):
                stats = worker['routes'].setdefault(backend['id'], {'attempts': 0, 'errors': {}, 'no_progress': 0, 'tripped': False})
                receipt_key = _backend_fingerprint(root, backend)
                if stats.get('observed_fingerprint') != receipt_key:
                    if stats.get('observed_fingerprint'):
                        history = {key: value for key, value in stats.items() if key != 'history'}
                        stats.setdefault('history', []).append(history)
                        stats.update(attempts=0, errors={}, no_progress=0, tripped=False)
                    stats['observed_fingerprint'] = receipt_key
                if stats['tripped']:
                    continue
                if stats.get('capability_fingerprint') != receipt_key:
                    challenge = uuid.uuid4().hex
                    probe = _process(engine, backend['probe_command'], {'kind': 'capability', 'challenge': challenge, 'backend_id': backend['id']}, backend.get('probe_timeout_seconds', 60))
                    probe_evidence = logged('probe-' + backend['id'], probe)
                    try:
                        receipt = json.loads(probe[1])
                    except (TypeError, ValueError):
                        receipt = {}
                    if not isinstance(receipt, dict):
                        receipt = {}
                    good = probe[0] == 0 and receipt.get('capability') == 'model-worker' and receipt.get('challenge') == challenge and receipt.get('backend_id') == backend['id'] and bool(receipt.get('model_response'))
                    stats['capability_probe'] = {**probe_evidence, 'passed': good, 'kind': backend['kind']}
                    if not good:
                        stats.update(tripped=True, trip_reason='capability-probe-failed', archived_at=e.now())
                        updates['autonomy/' + identifier + '/circuits/' + backend['id'] + '.json'] = c.encoded(stats)
                        persist()
                        continue
                    stats['capability_fingerprint'] = receipt_key
                selected = backend
                break
            if selected is None:
                report.update(status='WAITING_CAPABILITY', reason='all-independent-worker-routes-exhausted')
            else:
                stats = worker['routes'][selected['id']]
                if pending_result:
                    before = pending_result['before']
                    before_inventory = pending_result['before_inventory']
                    result = tuple(pending_result['result'])
                    request_id = pending_result['request_id']
                    report['worker'] = pending_result['worker_evidence']
                    report['worker_result_resumed_without_reexecution'] = True
                else:
                    before = e.file_hashes(root, plan.get('progress_files', []))
                    before_inventory = _inventory(root, plan)
                    request_id = worker.get('inflight', {}).get('request_id') if worker.get('inflight') else uuid.uuid4().hex
                    request = {'kind': 'work', 'request_id': request_id, 'goal': plan['goal'], 'project_id': plan['project_id'],
                        'branch': plan['branch'], 'source_sha': before_inventory['head'], 'round': round_number,
                        'acceptance_commands': plan['acceptance'], 'previous_acceptance': worker.get('acceptance'),
                        'checkpoint': {'round': worker['round'], 'previous_failure': worker.get('last_failure')},
                        'instructions': e.FOUNDATION}
                    worker['inflight'] = {'request_id': request_id, 'backend': selected['id'], 'idempotent': selected.get('idempotent', False), 'started_at': e.now()}
                    persist()
                    result = _process(engine, selected['command'], request, selected.get('timeout_seconds', 600))
                    stats['attempts'] += 1
                    report['worker'] = {'backend': selected['id'], 'kind': selected['kind'], **logged('worker', result)}
                    worker['inflight'] = None
                    worker['pending_result'] = {'backend': selected['id'], 'request_id': request_id,
                        'result': list(result), 'before': before, 'before_inventory': before_inventory,
                        'worker_evidence': report['worker']}
                    persist()
                report['model_invocation_attempted'] = selected['kind'] == 'codex' and not pending_result
                try:
                    worker_receipt = json.loads(result[1])
                except (ValueError, TypeError):
                    worker_receipt = {}
                if not isinstance(worker_receipt, dict):
                    worker_receipt = {}
                model_receipt_good = selected['kind'] == 'codex' and result[0] == 0 and worker_receipt.get('request_id') == request_id and bool(worker_receipt.get('model_response'))
                report['actual_model_success_verified'] = bool(model_receipt_good and not pending_result)
                if pending_result:
                    report['resumed_worker_model_receipt_verified'] = bool(model_receipt_good)
                worker['inflight'] = None
                engine, acceptance_plan = _engine(plan, identifier, root, checkpoint)
                after_inventory = _inventory(root, plan)
                after = e.file_hashes(root, plan.get('progress_files', []))
                acceptance = []
                if result[0] == 0:
                    # Fresh acceptance goal per round forces real checks, not a cached PASS.
                    engine.plan['goal_id'] = identifier + '-accept-' + str(round_number)
                    engine.key = e.digest([plan['project_id'], plan['branch'], engine.plan['goal_id']])[:24]
                    accepted = engine.run()
                    acceptance = accepted['evidence']
                    worker['acceptance'] = {'passed': accepted['passed'], 'source_sha': accepted['code_sha'], 'evidence': acceptance}
                    # Execution logs are included in the same durable archive.
                    report['acceptance'] = worker['acceptance']
                    report['passed'] = accepted['passed']
                else:
                    worker['acceptance'] = {'passed': False, 'not_run': 'worker-process-failed'}
                progress = before != after
                stats['no_progress'] = 0 if progress or report['passed'] else stats['no_progress'] + 1
                if not report['passed']:
                    fingerprint, message = _error(*result) if result[0] else (_acceptance_signature(engine), 'independent acceptance failed')
                    stats['errors'][fingerprint] = stats['errors'].get(fingerprint, 0) + 1
                    worker['last_failure'] = {'fingerprint': fingerprint, 'message': message, 'evidence': worker['acceptance']}
                    if stats['errors'][fingerprint] >= 2 or stats['no_progress'] >= 3:
                        stats.update(tripped=True, trip_reason='equivalent-error-twice' if stats['errors'][fingerprint] >= 2 else 'three-rounds-without-declared-progress', archived_at=e.now())
                        updates['autonomy/' + identifier + '/circuits/' + selected['id'] + '.json'] = c.encoded(stats)
                report.update(status='DONE' if report['passed'] else 'READY', progress={'declared_files_changed': progress,
                    'before_fingerprint': before_inventory['fingerprint'], 'after_fingerprint': after_inventory['fingerprint']})
                worker['last_source_fingerprint'] = after_inventory['fingerprint']
                worker['last_backend'] = selected['id']
                worker['pending_result'] = None
        worker['round'] = round_number
        worker['events'].append({'event_id': event_id, 'at': e.now(), 'status': report['status']})
        persist()
    except Exception as error:
        reason = e.scrub(type(error).__name__ + ': ' + str(error))
        fingerprint, _ = _error(1, '', reason, type(error).__name__ + ':' + reason)
        failures = worker.setdefault('infrastructure_failures', {})
        failures[fingerprint] = failures.get(fingerprint, 0) + 1
        tripped = failures[fingerprint] >= 2
        report.update(status='BLOCKED' if tripped else 'RETRY_WAIT', reason=reason,
            infrastructure_failure_count=failures[fingerprint], infrastructure_circuit_tripped=tripped)
        if tripped:
            updates['autonomy/' + identifier + '/circuits/infrastructure.json'] = c.encoded({
                'reason': 'equivalent-infrastructure-error-twice', 'error': reason,
                'fingerprint': fingerprint, 'attempts': failures[fingerprint], 'pending_result_preserved': bool(worker.get('pending_result'))})
    report['completed_at'] = e.now()
    report_path = 'autonomy/' + identifier + '/worker-runs/' + event_id + '/report.json'
    updates[report_path] = c.encoded(report)
    def finish(state):
        current = state['autonomy_tasks'][identifier]
        lease = current.get('lease')
        if not lease or lease['owner'] != owner or lease['event_id'] != event_id:
            return None, {'claimed': True, 'status': 'LEASE_LOST'}
        current.update(worker_supervisor=copy.deepcopy(worker), status=report['status'], lease=None, updated_at=clock(),
            next_wakeup_at=clock() if report['status'] in {'READY', 'RETRY_WAIT'} else None if report['status'] in c.TERMINAL else clock() + 3600,
            outbox={'event_id': uuid.uuid4().hex, 'status': 'PENDING'} if report['status'] in {'READY', 'RETRY_WAIT'} else None)
        current['processed_events'].append(event_id)
        current['run_evidence'].append({'event_id': event_id, 'report_path': report_path, 'status': report['status'],
            'model_called': report['model_invocation_attempted'], 'at': clock()})
        return updates, {'claimed': True, **report}
    try:
        return store.update(finish, 'Save independent acceptance before scheduling another worker round')
    finally:
        workspace_lock.close()


def run_until_terminal(store, identifier, project_root, max_rounds=50, owner=None):
    if max_rounds < 1:
        raise ValueError('A positive process round bound is required')
    owner = owner or ('worker-' + uuid.uuid4().hex)
    result = None
    for _ in range(max_rounds):
        result = run_round(store, identifier, uuid.uuid4().hex, owner, project_root)
        if not result.get('claimed') or result.get('status') not in {'READY', 'RETRY_WAIT'}:
            break
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True)
    commands = parser.add_subparsers(dest='action', required=True)
    init = commands.add_parser('init')
    init.add_argument('--project-id', required=True); init.add_argument('--source-sha', required=True)
    init.add_argument('--branch', default='main')
    register = commands.add_parser('register')
    register.add_argument('--task-id', required=True); register.add_argument('--plan', required=True)
    tick = commands.add_parser('tick')
    tick.add_argument('--task-id', required=True); tick.add_argument('--project-root', required=True)
    tick.add_argument('--event-id', default=None); tick.add_argument('--owner', default=None)
    run = commands.add_parser('run')
    run.add_argument('--task-id', required=True); run.add_argument('--project-root', required=True)
    run.add_argument('--max-rounds', type=int, default=50)
    args = parser.parse_args(); store = c.Store(args.repository)
    if args.action == 'init':
        result = init_control(store, args.project_id, args.source_sha, args.branch)
    elif args.action == 'register':
        result = register_worker(store, args.task_id, json.loads(Path(args.plan).read_text()))
    elif args.action == 'tick':
        result = run_round(store, args.task_id, args.event_id or uuid.uuid4().hex, args.owner or uuid.uuid4().hex, args.project_root)
    else:
        result = run_until_terminal(store, args.task_id, args.project_root, args.max_rounds)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get('status', result.get('reason')) in {'BLOCKED', 'WAITING_CAPABILITY', 'worker-task-not-found'} else 0


if __name__ == '__main__':
    raise SystemExit(main())
