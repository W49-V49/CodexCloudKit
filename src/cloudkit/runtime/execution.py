#!/usr/bin/env python3
"""Finite, resumable execution of explicit plans inside the existing checkpoint.

This is a command/checkpoint adapter, not another scheduler or model runner.
It never enables a runner, changes authentication or calls a paid model API.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import uuid


FOUNDATION = (
    'Cloud only. Read the current goal and persistent checkpoint at task start. '
    'Reuse effective GitHub, Codex Cloud, MCP, Skills and scheduler components. '
    'Use hashes, Git changes and version fingerprints; do not parse, index or read '
    'unchanged bodies again while their version remains in the current context. '
    'Recall incrementally from goal, state, code dependencies, historical failures '
    'and evidence gaps; expand recursively when evidence is insufficient, without '
    'a fixed memory token budget or fixed retrieval count. Preserve sources, status, '
    'versions, checkpoints and real test evidence. Use independent agents, assistant '
    'agents and child agents where useful, with explicit ownership and serialized '
    'shared writes; do not invent agents or claim they survive task termination. '
    'Diagnose failures. Trip a route after two equivalent errors or three consecutive '
    'attempts without verified progress, archive redacted logs, and select an '
    'applicable independent alternative. Resume from durable checkpoints. Only '
    'reproducible required acceptance may establish completion; otherwise repair '
    'or report an exhausted external blocker. Never change the billing route.'
)
TOKEN = re.compile(r'(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})')


def scrub(text):
    text = TOKEN.sub('[REDACTED]', str(text))
    text = re.sub(r'(?i)(authorization\s*[:=]\s*)(?:bearer|basic)\s+\S+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)(https?://)[^/@\s]+:[^/@\s]+@', r'\1[REDACTED]@', text)
    return text


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(value).hexdigest()


def read_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def file_hashes(root, names):
    results = {}
    for name in names:
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Plan file paths must remain inside the cloud project')
        if path.is_symlink() or path.is_dir():
            raise ValueError('Plan evidence paths must be regular files, not directories')
        results[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    return results


def validate(plan):
    for key in ('project_id', 'branch', 'goal_id', 'source_sha', 'acceptance_version', 'steps'):
        if key not in plan:
            raise ValueError('Missing plan field: ' + key)
    if not re.fullmatch(r'[0-9a-f]{40}', str(plan['source_sha'])):
        raise ValueError('source_sha must be a full Git commit SHA')
    if not isinstance(plan['steps'], list) or not plan['steps']:
        raise ValueError('A nonempty explicit step plan is required')
    if plan.get('execution_route', 'chatgpt-private-runner') != 'chatgpt-private-runner':
        raise ValueError('Changing the configured authentication or billing route is not supported')
    ids = [step['id'] for step in plan['steps']]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r'[A-Za-z0-9_-]+', x) for x in ids):
        raise ValueError('Step IDs must be unique safe identifiers')
    acceptances = [step for step in plan['steps'] if step.get('kind') == 'acceptance' and step.get('required', True)]
    if not acceptances:
        raise ValueError('At least one explicit required acceptance command is necessary')
    seen = set()
    for step in plan['steps']:
        if not set(step.get('depends_on', [])).issubset(seen):
            raise ValueError('Steps must be topologically ordered with existing dependencies')
        if not set(step.get('repair_invalidates', [])).issubset(seen):
            raise ValueError('A repair can invalidate only earlier build/test steps')
        seen.add(step['id'])
        if not step.get('routes'):
            raise ValueError('Every step needs an explicit command route')
        for family in ('routes', 'repair_routes'):
            keys, commands, route_ids = set(), set(), set()
            for route in step.get(family, []):
                command = route.get('command')
                if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
                    raise ValueError('Commands must be nonempty argv arrays; no implicit shell')
                if TOKEN.search(json.dumps(route)):
                    raise ValueError('Credentials may not be embedded in plans')
                key = route.get('independence_key')
                if not key or key in keys or digest(command) in commands:
                    raise ValueError('Alternatives need unique independence keys and genuinely different commands')
                if not re.fullmatch(r'[A-Za-z0-9_-]+', route.get('id', '')) or route['id'] in route_ids:
                    raise ValueError('Route IDs must be unique safe identifiers')
                if route.get('requires_model') or route.get('paid_api'):
                    raise ValueError('An unverified model runner or a billing-route switch cannot be enabled here')
                if route.get('max_attempts', 3) < 1 or route.get('timeout_seconds', 300) <= 0:
                    raise ValueError('Finite positive attempt and timeout policies are required')
                keys.add(key); commands.add(digest(command)); route_ids.add(route['id'])


class Execution:
    def __init__(self, plan, project_root, checkpoint):
        validate(plan)
        self.plan = plan
        self.root = Path(project_root).resolve(strict=True)
        if not self.root.is_relative_to(Path('/workspace')) and not self.root.is_relative_to(Path('/tmp')):
            raise ValueError('Execution is restricted to the cloud workspace')
        self.checkpoint = Path(checkpoint).resolve()
        if not self.checkpoint.is_relative_to(Path('/workspace')) and not self.checkpoint.is_relative_to(Path('/tmp')):
            raise ValueError('Checkpoint must be stored in the cloud workspace')
        self.key = digest([plan['project_id'], plan['branch'], plan['goal_id']])[:24]
        self.logs = self.checkpoint.parent / 'execution-logs' / self.key
        self.document = None
        self.state = None
        self.expected_resume_fingerprint = None
        self.repository = self.observe_repository()

    def git(self, *arguments):
        result = subprocess.run(['git', '-C', str(self.root), *arguments], capture_output=True)
        if result.returncode:
            raise ValueError('Actual Git repository identity/branch/HEAD could not be verified')
        return result.stdout

    def observe_repository(self):
        actual_root = Path(self.git('rev-parse', '--show-toplevel').decode().strip()).resolve()
        if actual_root != self.root:
            raise ValueError('project_root must be the actual Git checkout root')
        remote = self.git('remote', 'get-url', 'origin').decode().strip()
        if '://' in remote:
            parsed = urlsplit(remote)
            host, identity = parsed.hostname, parsed.path.lstrip('/')
        else:
            matched = re.fullmatch(r'(?:[^@]+@)?([^:]+):(.+)', remote)
            if not matched:
                raise ValueError('A supported actual GitHub origin is required')
            host, identity = matched.groups()
        identity = identity.removesuffix('.git').rstrip('/')
        if host != 'github.com' or identity.lower() != self.plan['project_id'].lower():
            raise ValueError('Actual Git origin does not match the plan project identity')
        branch = self.git('symbolic-ref', '--quiet', '--short', 'HEAD').decode().strip()
        head = self.git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip()
        if branch != self.plan['branch']:
            raise ValueError('Actual Git branch does not match the plan branch')
        if head != self.plan['source_sha']:
            raise ValueError('Actual Git HEAD does not match the plan source_sha')
        return {'host': host, 'project_id': identity, 'branch': branch, 'observed_head_sha': head}

    def source_fingerprint(self):
        outputs = {name for step in self.plan['steps'] for name in step.get('outputs', [])}
        tracked = self.git('ls-files', '-z').decode().split('\0')
        tracked = [name for name in tracked if name and name not in outputs]
        sources = sorted(set(tracked).union(name for step in self.plan['steps'] for name in step.get('inputs', []) if name not in outputs))
        # Runtime checkpoint/logs are control storage, not source, even if placed inside the checkout.
        sources = [name for name in sources if (self.root / name).resolve() != self.checkpoint and not (self.root / name).resolve().is_relative_to(self.logs)]
        content = file_hashes(self.root, sources)
        changes = self.git('status', '--porcelain', '-z', '--untracked-files=no')
        diff = self.git('diff', 'HEAD', '--binary')
        return {'source_files': content, 'source_fingerprint': digest(content),
                'tracked_worktree_dirty': bool(changes), 'tracked_diff_sha256': digest(diff)}

    def save(self):
        self.state['updated_at'] = now()
        self.document.setdefault('execution_tasks', {})[self.key] = self.state
        atomic(self.checkpoint, self.document)

    def event(self, kind, **data):
        self.state.setdefault('events', []).append({'at': now(), 'kind': kind, **data})

    def instructions(self):
        result = {'personal_foundation': FOUNDATION, 'agents': [], 'goal': None}
        for name in ('AGENTS.md', 'docs/GOAL.md'):
            path = self.root / name
            if path.is_file():
                result['agents' if name == 'AGENTS.md' else 'goal'] = {
                    'path': name, 'sha256': digest(path.read_bytes())}
        result['delegation'] = self.plan.get('delegation', [])
        result['delegation_scope'] = 'Hints for the active cloud orchestrator; not invented running agents'
        return result

    def fingerprint(self, step):
        dependencies = {name: self.state['steps'].get(name, {}).get('completion_fingerprint')
                        for name in step.get('depends_on', [])}
        return digest({'step': step, 'inputs': file_hashes(self.root, step.get('inputs', [])),
                       'dependencies': dependencies, 'source_sha': self.plan['source_sha'],
                       'acceptance_version': self.plan['acceptance_version'],
                       'actual_source': self.source_fingerprint()['source_fingerprint'],
                       'instructions': self.state['startup_instructions']})

    def initialize_step(self, step):
        entry = self.state['steps'].get(step['id'])
        # Resolve an old uncertain command before considering any changed plan, source,
        # goal or instruction fingerprint. A version change must not erase side effects.
        if entry and entry.get('inflight') and not self.interrupted(step, entry):
            return entry
        fingerprint = self.fingerprint(step)
        if entry and entry['input_fingerprint'] != fingerprint:
            pending_repair = entry['status'] == 'REPAIR_NEEDED' and entry.get('source_sha') == self.plan['source_sha']
            self.event('step-invalidated', step=step['id'], previous_status=entry['status'], reason='input/version/dependency fingerprint changed')
            self.state.setdefault('history', []).append({'step': step['id'], **entry})
            entry = None
        else:
            pending_repair = False
        if entry is None:
            entry = {'status': 'REPAIR_NEEDED' if pending_repair else 'PENDING',
                     'input_fingerprint': fingerprint, 'routes': {}, 'source_sha': self.plan['source_sha'],
                     'attempts': [], 'completion_fingerprint': None}
            self.state['steps'][step['id']] = entry
            self.save()
        if entry['status'] == 'PASSED':
            current_outputs = file_hashes(self.root, step.get('outputs', []))
            if current_outputs != entry.get('outputs', {}):
                self.event('step-invalidated', step=step['id'], reason='passed output evidence changed or disappeared')
                self.state.setdefault('history', []).append({'step': step['id'], **entry})
                entry = {'status': 'PENDING', 'input_fingerprint': fingerprint, 'routes': {}, 'source_sha': self.plan['source_sha'],
                         'attempts': [], 'completion_fingerprint': None}
                self.state['steps'][step['id']] = entry
                self.save()
        return entry

    def route_state(self, step, entry, route, repair=False):
        if repair:
            key = step['id'] + ':' + digest(route)
            return self.state.setdefault('repairs', {}).setdefault(key, {
                'attempt_count': 0, 'errors': {}, 'no_progress': 0, 'tripped': False,
                'route': route['id'], 'independence_key': route['independence_key']})
        # A failed command may mutate tracked files. Such a mutation invalidates
        # PASS evidence, but must not erase its own circuit/attempt history.
        key = digest([step['id'], route, self.plan['source_sha'], self.plan['acceptance_version']])
        stats = self.state.setdefault('route_lifetimes', {}).setdefault(key, {
            'attempt_count': 0, 'errors': {}, 'no_progress': 0, 'tripped': False,
            'independence_key': route['independence_key']})
        entry['routes'][route['id']] = stats
        return stats

    def run_process(self, route, command=None):
        cwd = (self.root / route.get('cwd', '.')).resolve()
        if not cwd.is_relative_to(self.root):
            raise ValueError('Command working directory must remain inside the project')
        try:
            process = subprocess.Popen(command or route['command'], cwd=cwd, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, errors='replace', start_new_session=True)
            try:
                stdout, stderr = process.communicate(timeout=route.get('timeout_seconds', 300))
                return process.returncode, scrub(stdout), scrub(stderr), None
            except subprocess.TimeoutExpired:
                # Stop the entire attached process group before starting a fallback.
                # A descendant deliberately daemonizing into another session is outside
                # this adapter's process-group guarantee and needs service-specific cleanup.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = process.communicate()
                return 124, scrub(stdout), scrub(stderr) + '\nCommand timed out; attached process group terminated', 'timeout-process-group-terminated'
        except OSError as error:
            return 127, '', scrub(type(error).__name__ + ': ' + str(error)), type(error).__name__

    def error_fingerprint(self, route, code, stdout, stderr, exception):
        material = stderr.strip() or stdout.strip()
        category = exception
        for pattern in route.get('error_patterns', []):
            if re.search(pattern['pattern'], material):
                category = pattern['category']
                break
        if not category:
            line = material.splitlines()[-1] if material else 'nonzero exit'
            line = re.sub(r'\b[0-9a-f]{12,}\b|\b\d+(?:\.\d+)?\b', '<volatile>', line)
            category = line[:1000]
        # Different wrappers may return different exit codes for the same root
        # failure. Preserve the code as evidence, not as a different error class.
        return digest({'tool': route['command'][0], 'category': category}), category

    def trip(self, step, route, stats, reason, repair=False):
        if stats['tripped']:
            return
        stats['tripped'] = True
        stats['trip_reason'] = reason
        stats['tripped_at'] = now()
        self.logs.mkdir(parents=True, exist_ok=True)
        archive = self.logs / (step['id'] + '-' + route['id'] + ('-repair' if repair else '') + '-archive.json')
        entry = self.state['steps'].get(step['id'], {})
        entries = [old for old in self.state.get('history', []) if old.get('step') == step['id']] + [entry]
        attempts = [item for old in entries for item in old.get('attempts', [])
                    if item['route'] == route['id'] and item['repair'] == repair
                    and item.get('route_definition_fingerprint') == digest(route)]
        atomic(archive, {'archived_at': now(), 'reason': reason,
                        'route': route['id'], 'source_sha': self.plan['source_sha'],
                        'attempts': attempts,
                        'circuit': stats.copy()})
        stats['archive'] = str(archive)
        self.event('route-tripped', step=step['id'], route=route['id'], repair=repair,
                   reason=reason, logs=str(self.logs))
        self.save()

    def attempt(self, step, entry, route, repair=False):
        stats = self.route_state(step, entry, route, repair)
        names = list(dict.fromkeys(route.get('progress_files', []) + step.get('outputs', [])))
        before = file_hashes(self.root, names)
        attempt_id = uuid.uuid4().hex
        entry['status'] = 'RUNNING_REPAIR' if repair else 'RUNNING'
        entry['inflight'] = {'id': attempt_id, 'route': route['id'], 'repair': repair,
                             'started_at': now(), 'idempotent': route.get('idempotent', False),
                             'route_definition': route, 'step_outputs': step.get('outputs', [])}
        stats['attempt_count'] += 1
        self.event('command-dispatched', step=step['id'], route=route['id'], repair=repair,
                   attempt=stats['attempt_count'], instruction_fingerprint=digest(self.state['startup_instructions']))
        self.save()  # Durable before the command can produce side effects.
        code, stdout, stderr, exception = self.run_process(route)
        self.repository = self.observe_repository()
        after = file_hashes(self.root, names)
        progressed = before != after
        self.logs.mkdir(parents=True, exist_ok=True)
        log = self.logs / (attempt_id + '.log')
        log.write_text('stdout:\n' + stdout + '\nstderr:\n' + stderr + '\n')
        evidence = {'attempt_id': attempt_id, 'step': step['id'], 'route': route['id'],
                    'route_definition_fingerprint': digest(route),
                    'repair': repair, 'returncode': code, 'passed': code == 0,
                    'source_sha': self.plan['source_sha'], 'acceptance_version': self.plan['acceptance_version'],
                    'progressed': progressed, 'outputs': after, 'log': str(log),
                    'log_sha256': digest(log.read_bytes()), 'finished_at': now()}
        entry['attempts'].append(evidence)
        entry.pop('inflight', None)
        stats['no_progress'] = 0 if progressed else stats['no_progress'] + 1
        if code != 0:
            fingerprint, category = self.error_fingerprint(route, code, stdout, stderr, exception)
            evidence['error_fingerprint'] = fingerprint
            evidence['error_category'] = category
            stats['errors'][fingerprint] = stats['errors'].get(fingerprint, 0) + 1
            if stats['errors'][fingerprint] >= 2:
                self.trip(step, route, stats, 'equivalent-error-twice', repair)
        # A repair must change declared business evidence; exit 0 alone is insufficient.
        successful = code == 0 and (not repair or progressed)
        if not successful and stats['no_progress'] >= 3:
            self.trip(step, route, stats, 'three-consecutive-attempts-without-progress', repair)
        if not successful and stats['attempt_count'] >= route.get('max_attempts', 3):
            self.trip(step, route, stats, 'finite-attempt-policy-exhausted', repair)
        if successful:
            if repair:
                entry['status'] = 'PENDING'
                self.event('repair-passed', step=step['id'], route=route['id'], evidence=evidence)
            else:
                entry['status'] = 'PASSED'
                entry['outputs'] = file_hashes(self.root, step.get('outputs', []))
                entry['completion_fingerprint'] = digest({'inputs': entry['input_fingerprint'],
                    'outputs': entry['outputs'], 'route': route['id'], 'evidence': evidence['log_sha256']})
                entry['passed_at'] = now()
                self.event('step-passed', step=step['id'], route=route['id'], evidence=evidence)
        else:
            entry['status'] = 'REPAIR_NEEDED' if step.get('kind') == 'acceptance' and step.get('repair_routes') and not repair else 'PENDING'
            self.event('failure-diagnosed', step=step['id'], route=route['id'], repair=repair,
                       error=evidence.get('error_category', 'successful-exit-without-business-progress'),
                       progressed=progressed, next_action='dispatch an applicable repair or independent route')
        self.save()
        return successful

    def eligible_route(self, step, entry, repair=False):
        for route in step.get('repair_routes' if repair else 'routes', []):
            stats = self.route_state(step, entry, route, repair)
            if route.get('applicable', True) is not True:
                if not stats.get('inapplicable_recorded'):
                    self.event('route-inapplicable', step=step['id'], route=route['id'],
                               reason=route.get('inapplicable_reason', 'plan applicability condition is false'))
                    stats['inapplicable_recorded'] = True
                continue
            if stats['attempt_count'] >= route.get('max_attempts', 3) and not stats['tripped']:
                self.trip(step, route, stats, 'finite-attempt-policy-exhausted', repair)
            if not stats['tripped']:
                return route
        return None

    def interrupted(self, step, entry):
        inflight = entry.get('inflight')
        if not inflight:
            return True
        # Use the durable ORIGINAL route, never a new plan's relaxed replay flag.
        route = inflight.get('route_definition')
        if route and route.get('resume_probe'):
            code, stdout, stderr, _ = self.run_process(route, route['resume_probe'])
            self.logs.mkdir(parents=True, exist_ok=True)
            log = self.logs / (inflight['id'] + '-resume-probe.log')
            log.write_text(stdout + '\n' + stderr)
            self.event('resume-probe', step=step['id'], route=route['id'], passed=code == 0, log=str(log))
            if code == 0 and not inflight['repair']:
                entry['status'] = 'PASSED'
                entry['outputs'] = file_hashes(self.root, step.get('outputs', []))
                entry['completion_fingerprint'] = digest({'inputs': entry['input_fingerprint'], 'outputs': entry['outputs'], 'resume_probe': digest(log.read_bytes())})
                entry['attempts'].append({'attempt_id': inflight['id'], 'step': step['id'],
                    'route': route['id'], 'repair': False, 'kind': 'resume-probe',
                    'returncode': 0, 'passed': True, 'source_sha': self.repository['observed_head_sha'],
                    'acceptance_version': self.plan['acceptance_version'], 'outputs': entry['outputs'],
                    'log': str(log), 'log_sha256': digest(log.read_bytes()), 'finished_at': now()})
                entry.pop('inflight', None)
                self.save()
                return True
        if not route or route.get('idempotent') is not True:
            entry['status'] = 'UNCERTAIN'
            self.event('interrupted-non-idempotent-command', step=step['id'], route=inflight['route'],
                       next_action='provide a successful resume probe or an explicit reviewed replacement plan')
            self.save()
            return False
        self.event('interrupted-command-recovered', step=step['id'], route=route['id'],
                   note='side effects may have occurred; retry allowed because the explicit route is idempotent')
        entry.pop('inflight', None)
        entry['status'] = 'REPAIR_NEEDED' if inflight['repair'] else 'PENDING'
        self.save()
        return True

    def block(self, step, reason):
        self.state['status'] = 'BLOCKED'
        self.state['blocked_step'] = step
        self.state['next_action'] = reason
        self.event('task-blocked', step=step, reason=reason)
        self.save()
        return self.report()

    def report(self):
        self.repository = self.observe_repository()
        actual_source = self.source_fingerprint()
        return {'schema_version': 1, 'project_id': self.plan['project_id'], 'branch': self.plan['branch'],
                'goal_id': self.plan['goal_id'], 'code_sha': self.repository['observed_head_sha'],
                'actual_repository': self.repository, **actual_source,
                'acceptance_binding': 'observed HEAD plus recorded source-file fingerprints',
                'acceptance_version': self.plan['acceptance_version'], 'task_status': self.state['status'],
                'passed': self.state['status'] == 'DONE', 'checkpoint': str(self.checkpoint),
                'task_key': self.key, 'plan_fingerprint': self.state['plan_fingerprint'],
                'namespace': 'execution_tasks.' + self.key, 'evidence': self.state['steps'],
                'events': self.state['events'], 'root_status_preserved': self.document.get('status'),
                'authentication_route_preserved': self.document.get('execution_route'),
                'continuous_execution_enabled': self.document.get('continuous_execution_enabled', False),
                'adapter_model_calls': 0, 'adapter_scheduler_created': False,
                'subprocess_model_usage_verified': False,
                'subprocess_scheduler_usage_verified': False}

    def run(self, pause_after=None):
        self.checkpoint.parent.mkdir(parents=True, exist_ok=True)
        with self.checkpoint.with_name(self.checkpoint.name + '.lock').open('a') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            self.repository = self.observe_repository()
            self.document = read_json(self.checkpoint, {'schema_version': 1, 'status': 'IDLE',
                'execution_route': 'chatgpt-private-runner', 'continuous_execution_enabled': False,
                'runner_verified': False})
            if self.document.get('execution_route') != 'chatgpt-private-runner':
                raise ValueError('Existing checkpoint authentication route differs; refusing to change it')
            self.state = self.document.get('execution_tasks', {}).get(self.key, {
                'schema_version': 1, 'status': 'PENDING', 'steps': {}, 'events': [], 'repairs': {}})
            previous_plan = self.state.get('plan_snapshot')
            previous_fingerprint = self.state.get('plan_fingerprint')
            if previous_plan is not None and digest(previous_plan) != previous_fingerprint:
                raise ValueError('Persisted plan fingerprint verification failed')
            current_fingerprint = digest(self.plan)
            if self.expected_resume_fingerprint is not None and (
                    previous_plan is None or previous_fingerprint != self.expected_resume_fingerprint):
                raise ValueError('Persisted resume plan changed before checkpoint lock acquisition')
            if previous_plan is not None and previous_fingerprint != current_fingerprint:
                self.state.setdefault('plan_history', []).append({
                    'replaced_at': now(), 'plan_fingerprint': previous_fingerprint,
                    'plan_snapshot': previous_plan})
            # Store the complete validated plan, including pending routes. A durable
            # checkpoint must not depend on a separate original plan file surviving.
            self.state['plan_snapshot'] = json.loads(json.dumps(self.plan))
            self.state['startup_instructions'] = self.instructions()
            self.state['source_sha'] = self.plan['source_sha']
            self.state['plan_fingerprint'] = current_fingerprint
            self.state['acceptance_version'] = self.plan['acceptance_version']
            self.state['status'] = 'ACTIVE'
            self.event('task-started-or-resumed', source_sha=self.plan['source_sha'],
                       startup_instruction_fingerprint=digest(self.state['startup_instructions']))
            self.save()
            planned_ids = {step['id'] for step in self.plan['steps']}
            for step_id, entry in self.state['steps'].items():
                if entry.get('inflight') and step_id not in planned_ids:
                    return self.block(step_id, 'An interrupted step was removed from the plan; its uncertain side effects cannot be discarded')
            commands = 0
            while True:
                all_passed = True
                repaired = False
                for step in self.plan['steps']:
                    entry = self.initialize_step(step)
                    if not self.interrupted(step, entry):
                        return self.block(step['id'], 'Interrupted side effects remain uncertain; no unsafe automatic replay')
                    if entry['status'] == 'PASSED':
                        continue
                    all_passed = False
                    if not all(self.state['steps'].get(x, {}).get('status') == 'PASSED' for x in step.get('depends_on', [])):
                        break
                    if entry['status'] == 'REPAIR_NEEDED':
                        route = self.eligible_route(step, entry, repair=True)
                        if route is None:
                            return self.block(step['id'], 'Acceptance failed and all explicit independent repair routes are unavailable or exhausted')
                        self.event('repair-dispatched', step=step['id'], route=route['id'],
                                   delegation=self.plan.get('delegation', []))
                        if self.attempt(step, entry, route, repair=True):
                            invalid = set(step.get('repair_invalidates', [])) | {step['id']}
                            for item in self.plan['steps']:
                                if set(item.get('depends_on', [])).intersection(invalid):
                                    invalid.add(item['id'])
                            for step_id in invalid:
                                previous = self.state['steps'].pop(step_id, None)
                                if previous:
                                    self.state.setdefault('history', []).append({'step': step_id, **previous})
                            self.event('repair-invalidated-checks', step=step['id'], invalidated=sorted(invalid))
                            self.save()
                            repaired = True
                        else:
                            entry['status'] = 'REPAIR_NEEDED'
                            self.save()
                        commands += 1
                    else:
                        route = self.eligible_route(step, entry)
                        if route is None:
                            return self.block(step['id'], 'No applicable independent execution route remains; archived logs and fingerprints are in the checkpoint')
                        self.attempt(step, entry, route)
                        commands += 1
                    if pause_after and commands >= pause_after:
                        self.state['status'] = 'PAUSED'
                        self.state['next_action'] = 'Resume this same persistent checkpoint'
                        self.save()
                        return self.report()
                    break
                if all_passed:
                    self.state['status'] = 'DONE'
                    self.state['next_action'] = 'Required explicit acceptance passed; retain reproducible evidence'
                    self.event('required-acceptance-completed',
                               acceptances=[s['id'] for s in self.plan['steps'] if s.get('kind') == 'acceptance'])
                    self.save()
                    return self.report()
                if repaired:
                    continue


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--plan')
    selection.add_argument('--resume-task', help='Task key from a persistent execution checkpoint')
    parser.add_argument('--project-root', default='.')
    parser.add_argument('--checkpoint')
    parser.add_argument('--store-root', default='/workspace/persistent-memory/cloudkit')
    parser.add_argument('--pause-after', type=int, help='Optional explicit safe scheduling boundary, useful for handover')
    parser.add_argument('--output')
    args = parser.parse_args()
    if args.resume_task:
        if not args.checkpoint:
            parser.error('--resume-task requires an explicit --checkpoint')
        if not re.fullmatch(r'[0-9a-f]{24}', args.resume_task):
            parser.error('--resume-task must be the persisted 24-character task key')
        document = read_json(Path(args.checkpoint), {})
        state = document.get('execution_tasks', {}).get(args.resume_task, {})
        plan = state.get('plan_snapshot')
        if not isinstance(plan, dict) or digest(plan) != state.get('plan_fingerprint'):
            raise ValueError('Persisted plan fingerprint verification failed or plan snapshot unavailable')
        validate(plan)
        if digest([plan['project_id'], plan['branch'], plan['goal_id']])[:24] != args.resume_task:
            raise ValueError('Persisted plan does not match the requested task namespace')
    else:
        plan = read_json(Path(args.plan), {})
    partition = hashlib.sha256((plan.get('project_id', '') + '\0' + plan.get('branch', '')).encode()).hexdigest()[:24]
    checkpoint = args.checkpoint or str(Path(args.store_root) / 'projects' / partition / 'runtime/execution/STATE.json')
    runner = Execution(plan, args.project_root, checkpoint)
    if args.resume_task:
        runner.expected_resume_fingerprint = digest(plan)
    report = runner.run(args.pause_after)
    if args.output:
        atomic(Path(args.output), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['passed'] or report['task_status'] == 'PAUSED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
