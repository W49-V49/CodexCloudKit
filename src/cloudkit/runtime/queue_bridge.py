#!/usr/bin/env python3
"""Optional Redis/RQ wakeups backed by the existing durable Git task state."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid
from urllib.parse import urlsplit

try:
    from . import continuity as c
except ImportError:
    import continuity as c


def _optional():
    from redis import Redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry
    from rq import Queue, Worker
    from rq.exceptions import DuplicateJobError, DequeueTimeout
    return Redis, Queue, Worker, DuplicateJobError, DequeueTimeout, Retry, NoBackoff


def _connection(worker=False):
    Redis, *_ = _optional()
    Retry, NoBackoff = _optional()[-2:]
    return Redis.from_url(os.environ.get('CLOUDKIT_REDIS_URL', 'redis://127.0.0.1:16379/0'),
        socket_connect_timeout=2, socket_timeout=75 if worker else 3,
        retry=Retry(NoBackoff(), 0), retry_on_timeout=False)


def _queue(name, connection):
    _, Queue, *_ = _optional()
    from rq.serializers import JSONSerializer
    return Queue(name, connection=connection, serializer=JSONSerializer)


def _task(store, identifier):
    c.task_id(identifier)
    _, state = store.read()
    item = state.get('autonomy_tasks', {}).get(identifier)
    if item is None:
        raise ValueError('The durable task does not exist')
    return item


def _job_id(store, identifier, event, generation=0):
    remote = store.git('remote', 'get-url', 'origin').decode().strip()
    parsed = urlsplit(remote)
    identity = ((parsed.hostname or '') + parsed.path) if parsed.scheme else remote
    return 'cloudkit-' + hashlib.sha256((identity + '\0' + identifier + '\0' + event + '\0' + str(generation)).encode()).hexdigest()


def _record_failure(store, identifier, event, error_type):
    fingerprint = c.execution.digest({'transport': 'rq', 'error_type': error_type})
    def mutation(state):
        item = state['autonomy_tasks'][identifier]
        box = item.get('outbox') or {}
        if box.get('event_id') != event or item['status'] in c.TERMINAL:
            return None, {'recorded': False, 'reason': 'superseded'}
        delivery = box.setdefault('rq', {})
        failures = delivery.setdefault('failures', {})
        failures[fingerprint] = failures.get(fingerprint, 0) + 1
        delivery.update(last_error_type=error_type, failure_count=failures[fingerprint],
                        tripped=failures[fingerprint] >= 2, failed_at=c.utc())
        evidence = {'transport': 'rq', 'event_id': event, 'error_type': error_type,
                    'failure_count': failures[fingerprint], 'tripped': delivery['tripped'],
                    'fallback': 'existing workflow_dispatch, repository_dispatch and hourly inspection',
                    'credential_or_connection_url_recorded': False}
        return {'autonomy/' + identifier + '/queue/' + event + '-failure.json': c.encoded(evidence)}, evidence
    return store.update(mutation, 'Preserve Git outbox after an optional queue transport failure')


def _fallback(store, identifier, ref, github_repository=None):
    repository = github_repository or c.github_identity(store.repository)
    if not repository:
        return {'dispatched': False, 'reason': 'github-route-unconfigured',
                'outbox_preserved': True, 'next_route': 'existing-hourly-inspection'}
    try:
        return c.flush_outbox(store, c.GitHub(repository), ref, identifier)
    except (ValueError, RuntimeError, OSError) as error:
        # Details and retry counters are owned by the existing dispatcher.
        return {'dispatched': False, 'reason': type(error).__name__, 'outbox_preserved': True}


def enqueue(repository, identifier, project_root=None, event_id=None, queue_name='cloudkit', ref='main', github_repository=None):
    c.task_id(queue_name)
    store = c.Store(repository);item = _task(store, identifier)
    box = item.get('outbox') or {}
    event = event_id or box.get('event_id')
    if item['status'] in c.TERMINAL or item['status'] == 'WAITING_CAPABILITY':
        return {'enqueued': False, 'reason': item['status'], 'canonical_state': 'Git'}
    if event in item.get('processed_events', []):
        return {'enqueued': False, 'reason': 'duplicate-event', 'canonical_state': 'Git'}
    if not event or box.get('event_id') != event:
        raise ValueError('The wakeup must use the current durable outbox event')
    c.task_id(event)
    if item.get('lease') and item['lease']['expires_at'] > c.utc():
        return {'enqueued': False, 'reason': 'live-lease', 'canonical_state': 'Git'}
    delivery = box.get('rq') or {}
    if delivery.get('tripped'):
        return {'enqueued': False, 'reason': 'rq-route-tripped', 'outbox_preserved': True,
                'fallback': _fallback(store, identifier, ref, github_repository)}
    try:
        _, Queue, _, DuplicateJobError, *_ = _optional()
        connection = _connection();connection.ping()
        generation = delivery.get('generation', 0)
        job_id = _job_id(store, identifier, event, generation)
        queue = _queue(queue_name, connection)
        existing = queue.fetch_job(job_id)
        if existing is not None:
            status = str(existing.get_status(refresh=True)).split('.')[-1].lower()
            if status in {'failed', 'stopped', 'canceled'}:
                raise RuntimeError('An earlier queue wakeup failed before durable completion')
            if status == 'finished':
                # A previous wakeup may have observed a live lease. Git owns the
                # retry intent; allocate a fresh transport attempt, not a new task.
                def next_attempt(state):
                    current = state['autonomy_tasks'][identifier]
                    current_box = current.get('outbox') or {}
                    if current_box.get('event_id') != event or current['status'] in c.TERMINAL:
                        return None, None
                    current_delivery = current_box.setdefault('rq', {})
                    current_delivery['generation'] = max(current_delivery.get('generation', 0), generation) + 1
                    return {}, current_delivery['generation']
                generation = store.update(next_attempt, 'Recover a finished queue wakeup from the unchanged Git outbox')
                if generation is None:
                    return {'enqueued': False, 'reason': 'superseded', 'canonical_state': 'Git'}
                job_id = _job_id(store, identifier, event, generation)
        worker_plan = item.get('worker_supervisor', {}).get('plan', {})
        timeout = sum(route.get('probe_timeout_seconds', 60) for route in worker_plan.get('backends', []))
        timeout += max([route.get('timeout_seconds', 600) for route in worker_plan.get('backends', [])] or [600])
        timeout += len(worker_plan.get('acceptance', [])) * worker_plan.get('acceptance_timeout_seconds', 300) + 180
        try:
            queue.enqueue_call('queue_bridge.consume', kwargs={'identifier': identifier,
                'event_id': event, 'queue_name': queue_name, 'ref': ref, 'github_repository': github_repository},
                job_id=job_id, unique=True, timeout=timeout, result_ttl=3600, failure_ttl=86400,
                description='Durable task wakeup ' + identifier)
            duplicate = False
        except DuplicateJobError:
            duplicate = True
        def delivered(state):
            current = state['autonomy_tasks'][identifier];current_box = current.get('outbox') or {}
            if current_box.get('event_id') != event or current['status'] in c.TERMINAL:
                return None, None
            current_box.setdefault('rq', {}).update(job_id=job_id, generation=generation,
                queue_name=queue_name, status='QUEUED', requested_at=c.utc())
            return {}, None
        store.update(delivered, 'Record queue delivery without replacing the durable Git outbox')
        return {'enqueued': not duplicate, 'duplicate_delivery': duplicate, 'job_id': job_id,
                'event_id': event, 'canonical_state': 'Git', 'model_authorization_supplied_by_bridge': False}
    except Exception as error:
        recorded = _record_failure(store, identifier, event, type(error).__name__)
        return {'enqueued': False, 'error_type': type(error).__name__, 'outbox_preserved': True,
                'transport_failure': recorded, 'fallback': _fallback(store, identifier, ref, github_repository)}


def consume(repository=None, identifier=None, project_root=None, event_id=None, queue_name='cloudkit', ref='main', github_repository=None):
    """Consume one wakeup; the established supervisor owns leases and acceptance."""
    repository = os.environ.get('CLOUDKIT_QUEUE_CONTROL_REPOSITORY') or repository
    if not repository:
        raise ValueError('Start the queue consumer with its current control repository')
    if os.environ.get('CLOUDKIT_QUEUE_CONTROL_REPOSITORY'):
        project_root = os.environ.get('CLOUDKIT_QUEUE_PROJECT_ROOT') or None
    store = c.Store(repository)
    result = c.run_round(store, identifier, event_id, 'rq-' + uuid.uuid4().hex, project_root)
    latest = _task(store, identifier)
    next_delivery = None
    if latest['status'] in {'READY', 'RETRY_WAIT'} and latest.get('outbox'):
        next_delivery = enqueue(repository, identifier, project_root, queue_name=queue_name,
                                ref=ref, github_repository=github_repository)
    return {'scope': 'Queue wakeup only; detailed results remain in the durable Git evidence',
            'task_id': identifier, 'event_id': event_id, 'status': latest['status'],
            'claimed': bool(result.get('claimed')), 'next_job_id': (next_delivery or {}).get('job_id'),
            'next_delivery_requested': bool(next_delivery and next_delivery.get('enqueued')),
            'model_execution_proven_by_queue': False}


def worker(repository, project_root=None, queue_name='cloudkit', burst=False, max_jobs=None, max_idle_time=None, ref='main', github_repository=None):
    # RQ imports callable references from this directory in both source-script
    # and installed-package operation. The optional queue venv needs no copy of
    # the toolkit package or business data.
    module_dir = str(Path(__file__).resolve().parent)
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)
    c.task_id(queue_name)
    store = c.Store(repository)
    context = {'CLOUDKIT_QUEUE_CONTROL_REPOSITORY': str(Path(repository).resolve()),
               'CLOUDKIT_QUEUE_PROJECT_ROOT': str(Path(project_root).resolve()) if project_root else ''}
    previous_context = {key: os.environ.get(key) for key in context}
    os.environ.update(context)
    try:
        _, Queue, Worker, _, DequeueTimeout, *_ = _optional()
        connection = _connection(worker=True);connection.ping()
        # Redis may have restarted or lost delivery data. Rebuild pending
        # wakeups from Git; a Redis job registry is never task authority.
        _, active = store.read()
        recovered = []
        for identifier, item in active.get('autonomy_tasks', {}).items():
            lease = item.get('lease') or {}
            if (item['status'] in {'READY', 'RETRY_WAIT', 'ACTIVE'} and item.get('outbox')
                    and lease.get('expires_at', 0) <= c.utc()
                    and (item.get('next_wakeup_at') or 0) <= c.utc()):
                delivered = enqueue(repository, identifier, project_root, queue_name=queue_name,
                                    ref=ref, github_repository=github_repository)
                if delivered.get('enqueued'):
                    recovered.append(identifier)
        class WakeWorker(Worker):
            transport_error = None
            def dequeue_job_and_maintain_ttl(self, timeout, max_idle_time=None):
                started = time.monotonic()
                while True:
                    try:
                        self.heartbeat()
                        if self.should_run_maintenance_tasks:
                            self.run_maintenance_tasks()
                        selected_timeout = min(timeout, max_idle_time) if timeout is not None and max_idle_time else timeout
                        result = self.queue_class.dequeue_any(self.queues, selected_timeout, connection=self.connection,
                            job_class=self.job_class, serializer=self.serializer,
                            death_penalty_class=self.death_penalty_class)
                        if result:
                            result[0].redis_server_version = self.get_redis_server_version()
                        self.heartbeat()
                        return result
                    except DequeueTimeout:
                        if max_idle_time is not None and time.monotonic() - started >= max_idle_time:
                            return None
                    except Exception as error:
                        self.transport_error = type(error).__name__
                        raise
        from rq.serializers import JSONSerializer
        instance = WakeWorker([_queue(queue_name, connection)], connection=connection,
                              log_job_description=False, worker_ttl=60, serializer=JSONSerializer)
        worked = instance.work(burst=burst, logging_level='WARNING', max_jobs=max_jobs, max_idle_time=max_idle_time)
        if instance.transport_error:
            raise RuntimeError('The optional queue transport stopped')
        return {'passed': True, 'scope': 'RQ consumer lifecycle only; inspect Git task acceptance separately',
                'processed_jobs': bool(worked), 'recovered_wakeups_from_git': recovered,
                'model_execution_proven_by_queue': False}
    except Exception as error:
        _, state = store.read();fallbacks = {}
        for identifier, item in state.get('autonomy_tasks', {}).items():
            box = item.get('outbox') or {}
            if item['status'] in {'READY', 'RETRY_WAIT'} and box.get('event_id'):
                _record_failure(store, identifier, box['event_id'], type(error).__name__)
                fallbacks[identifier] = _fallback(store, identifier, ref, github_repository)
        return {'passed': False, 'error_type': type(error).__name__, 'outboxes_preserved': True,
                'fallbacks': fallbacks, 'model_execution_proven_by_queue': False}
    finally:
        for key, value in previous_context.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def inspect(repository, identifier, queue_name='cloudkit'):
    store = c.Store(repository);item = _task(store, identifier);box = item.get('outbox') or {}
    result = {'task_id': identifier, 'status': item['status'], 'canonical_state': 'Git',
              'outbox': box, 'processed_event_count': len(item.get('processed_events', [])),
              'model_execution_proven_by_queue': False}
    try:
        _, Queue, *_ = _optional();connection = _connection();connection.ping()
        queue = _queue(queue_name, connection)
        result['redis'] = {'available': True, 'queued_jobs': queue.count}
    except Exception as error:
        result['redis'] = {'available': False, 'error_type': type(error).__name__}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--project-root')
    parser.add_argument('--queue', default='cloudkit')
    parser.add_argument('--ref', default='main')
    parser.add_argument('--github-repository')
    commands = parser.add_subparsers(dest='action', required=True)
    put = commands.add_parser('enqueue');put.add_argument('--task-id', required=True);put.add_argument('--event-id')
    take = commands.add_parser('worker')
    mode = take.add_mutually_exclusive_group()
    mode.add_argument('--burst', action='store_true')
    mode.add_argument('--poll', action='store_false', dest='burst')
    take.set_defaults(burst=False)
    take.add_argument('--max-jobs', type=int);take.add_argument('--max-idle-time', type=int)
    show = commands.add_parser('inspect');show.add_argument('--task-id', required=True)
    args = parser.parse_args(argv)
    common = {'repository': args.repository, 'queue_name': args.queue}
    if args.action == 'enqueue':
        result = enqueue(**common, identifier=args.task_id, project_root=args.project_root,
                         event_id=args.event_id, ref=args.ref, github_repository=args.github_repository)
    elif args.action == 'worker':
        result = worker(**common, project_root=args.project_root, burst=args.burst,
                        max_jobs=args.max_jobs, max_idle_time=args.max_idle_time,
                        ref=args.ref, github_repository=args.github_repository)
    else:
        result = inspect(**common, identifier=args.task_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get('passed') is False else 0


if __name__ == '__main__':
    raise SystemExit(main())
