#!/usr/bin/env python3
"""Persist memory snapshots using the existing independent control branch."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tarfile
from urllib.parse import urlsplit

try:
    from .continuity import Store
except ImportError:
    from continuity import Store


def command(repository, *arguments, data=None, environment=None):
    return Store(repository).git(*arguments, data=data, env=environment)


def partition(project, branch):
    return hashlib.sha256((project + '\0' + branch).encode()).hexdigest()[:24]


def control_project(repository):
    """Derive only sanitized identity; never persist a credential-bearing remote."""
    remote=command(repository,'remote','get-url','origin').decode().strip()
    if remote.startswith('git@github.com:'):
        value=remote.partition(':')[2]
    else:
        parsed=urlsplit(remote)
        if parsed.hostname!='github.com':return None
        value=parsed.path.strip('/')
    value=value.removesuffix('.git')
    return value if re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',value) else None


def publish(args):
    repository = Path(args.repository).resolve()
    archive = Path(args.snapshot).read_bytes()
    if not re.fullmatch(r'[0-9a-f]{40}', args.code_sha):
        raise ValueError('A full source code SHA is required.')
    status = json.loads(Path(args.status).read_text())
    if status['project_id'] != args.project or status['branch'] != args.branch:
        raise ValueError('Snapshot status belongs to another project/branch.')
    if status.get('last_indexed_commit') != args.code_sha:
        raise ValueError('Snapshot status does not match the exact indexed source SHA.')
    with tarfile.open(args.snapshot) as snapshot:
        info = json.load(snapshot.extractfile('snapshot.json'))
        manifest = json.load(snapshot.extractfile('manifest.json'))
    for content in (info, manifest):
        if content.get('project_id') != args.project or content.get('branch') != args.branch:
            raise ValueError('Archive partition differs from its claimed status.')
    if manifest.get('last_indexed_commit') != args.code_sha or info.get('index_generation') != status['active_generation']:
        raise ValueError('Archive generation/source differs from its claimed status.')
    key = partition(args.project, args.branch)
    digest = hashlib.sha256(archive).hexdigest()
    command(repository, 'fetch', 'origin', 'refs/heads/automation/state:refs/remotes/origin/automation/state')
    parent = command(repository, 'rev-parse', 'refs/remotes/origin/automation/state').decode().strip()
    state = json.loads(command(repository, 'show', parent + ':STATE.json'))
    existing_path = 'memory/' + key + '/metadata.json'
    existing = command(repository, 'ls-tree', parent, '--', existing_path).strip()
    actual_digest = json.loads(command(repository, 'show', parent + ':' + existing_path))['snapshot_sha256'] if existing else 'absent'
    if actual_digest != args.expected_snapshot_sha256:
        raise ValueError('Durable memory changed since recovery; fetch, merge via native sync and revalidate before publishing.')
    state.setdefault('goal_revision',1)
    identity=control_project(repository)
    partitions=state.get('memory_partitions',{})
    primary=(identity==args.project if identity else not partitions or (len(partitions)==1 and key in partitions))
    # Foreign project snapshots update their own partition, never the toolbox's
    # legacy top-level counters. Each project's partition metadata is authoritative.
    if primary:
        state.update(index_generation=status['active_generation'],memory_revision=status['memory_revision'])
    metadata = {'schema_version': 1, 'project_id': args.project, 'branch': args.branch,
                'source_code_sha': args.code_sha, 'index_generation': status['active_generation'],
                'memory_revision': status['memory_revision'], 'pipeline': status['pipeline'],
                'snapshot_sha256': digest, 'snapshot_path': 'memory/' + key + '/snapshot.tar.gz'}
    state.setdefault('memory_partitions', {})[key] = metadata
    evidence = state.setdefault('evidence', [])
    marker = 'memory/' + key + '/metadata.json'
    if marker not in evidence:
        evidence.append(marker)
    updates = {'STATE.json': json.dumps(state, ensure_ascii=False, indent=2).encode() + b'\n',
               metadata['snapshot_path']: archive,
               marker: json.dumps(metadata, ensure_ascii=False, indent=2).encode() + b'\n'}
    if args.evaluation:
        report = Path(args.evaluation).read_bytes()
        result = json.loads(report)
        if result.get('passed') is not True or result.get('source_revision') != args.code_sha:
            raise ValueError('Publication requires actual passing retrieval evidence for this source SHA.')
        if (result.get('project_id') != args.project or result.get('branch') != args.branch
                or result.get('index_generation') != status['active_generation']
                or result.get('memory_revision') != status['memory_revision']):
            raise ValueError('Evaluation does not match this project, branch and memory generation.')
        updates['memory/' + key + '/evaluation.json'] = report
    if args.evidence_dir:
        source = Path(args.evidence_dir).resolve(strict=True)
        evidence_base = 'memory/' + key + '/evidence/' + args.code_sha
        inventory = {}
        secret = re.compile(rb'(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,})')
        for path in sorted(source.rglob('*')):
            if path.is_symlink():
                raise ValueError('Evidence symlinks are not permitted.')
            if not path.is_file() or path.name == 'publication.json' or path.suffix not in ('.json', '.log'):
                continue
            content = path.read_bytes()
            if secret.search(content):
                raise ValueError('Refusing credential-pattern material in durable evidence.')
            relative = path.relative_to(source).as_posix()
            updates[evidence_base + '/' + relative] = content
            inventory[relative] = hashlib.sha256(content).hexdigest()
        updates[evidence_base + '/inventory.json'] = json.dumps(inventory, indent=2).encode() + b'\n'
        metadata['evidence_path'] = evidence_base
        # Metadata is shared with the state mapping; serialize after adding the durable evidence reference.
        updates[marker] = json.dumps(metadata, ensure_ascii=False, indent=2).encode() + b'\n'
        updates['STATE.json'] = json.dumps(state, ensure_ascii=False, indent=2).encode() + b'\n'
    with tempfile.TemporaryDirectory(prefix='memory-control-') as temporary:
        environment = os.environ.copy()
        environment['GIT_INDEX_FILE'] = str(Path(temporary) / 'index')
        command(repository, 'read-tree', parent, environment=environment)
        for path, content in updates.items():
            blob = command(repository, 'hash-object', '-w', '--stdin', data=content).decode().strip()
            command(repository, 'update-index', '--add', '--cacheinfo', '100644,' + blob + ',' + path,
                    environment=environment)
        tree = command(repository, 'write-tree', environment=environment).decode().strip()
        commit = command(repository, '-c', 'user.name=CloudKit',
                         '-c', 'user.email=cloudkit@localhost',
                         'commit-tree', tree, '-p', parent,
                         data=b'Persist verified incremental memory checkpoint\n').decode().strip()
        command(repository, 'push', 'origin', commit + ':refs/heads/automation/state')
    return {'published': True, 'control_commit': commit, **metadata,
            'scheduler_status_preserved': state.get('status'), 'authentication_route_preserved': state.get('execution_route')}


def fetch(args):
    repository = Path(args.repository).resolve()
    key = partition(args.project, args.branch)
    if args.local_ref:
        ref = command(repository, 'rev-parse', '--verify', args.local_ref + '^{commit}').decode().strip()
    else:
        command(repository, 'fetch', 'origin', 'refs/heads/automation/state:refs/remotes/origin/automation/state')
        ref = 'refs/remotes/origin/automation/state'
    if not command(repository, 'ls-tree', ref, '--', 'memory/' + key + '/metadata.json').strip():
        if args.allow_absent:
            return {'fetched': False, 'reason': 'partition-not-initialized', 'project_id': args.project, 'branch': args.branch}
        raise ValueError('No durable memory partition exists on the selected control ref.')
    metadata = json.loads(command(repository, 'show', ref + ':memory/' + key + '/metadata.json'))
    if metadata['project_id'] != args.project or metadata['branch'] != args.branch:
        raise ValueError('Stored partition metadata mismatch.')
    content = command(repository, 'show', ref + ':' + metadata['snapshot_path'])
    if hashlib.sha256(content).hexdigest() != metadata['snapshot_sha256']:
        raise ValueError('Stored snapshot digest mismatch.')
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    destination.with_suffix(destination.suffix + '.metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    return {'fetched': True, 'archive': str(destination), **metadata}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', default='.')
    parser.add_argument('--project', required=True)
    parser.add_argument('--branch', default='main')
    commands = parser.add_subparsers(dest='command', required=True)
    writer = commands.add_parser('publish')
    writer.add_argument('--snapshot', required=True)
    writer.add_argument('--status', required=True)
    writer.add_argument('--code-sha', required=True)
    writer.add_argument('--evaluation')
    writer.add_argument('--expected-snapshot-sha256', required=True,
                        help='Digest fetched before work, or absent for a positively uninitialized partition')
    writer.add_argument('--evidence-dir', help='Persist redacted JSON/log evidence with a file digest inventory on the existing control branch')
    reader = commands.add_parser('fetch')
    reader.add_argument('--output', required=True)
    reader.add_argument('--local-ref', help='Read an already fetched control commit without network mutation')
    reader.add_argument('--allow-absent', action='store_true', help='Permit only a positively absent, not yet initialized partition')
    args = parser.parse_args()
    result = publish(args) if args.command == 'publish' else fetch(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
