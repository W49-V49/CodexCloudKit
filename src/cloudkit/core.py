"""Project attachment and derived content inventories using the standard library."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

from . import __version__

DEFAULT_RUNTIME = Path('/workspace/external-tools/codexcloudkit')
DEFAULT_STATE = Path('/workspace/persistent-memory/cloudkit')
SKILLS = {
    'memory-context': 'Read the short goal and active checkpoint first. Retrieve relevant verified memory progressively from the task, dependencies, prior failures and evidence gaps. Read original bodies and code symbols only as needed. Record concise conclusions with sources and applicability.',
    'incremental-index': 'Use content hashes, Git changes and parser/model fingerprints to reuse unchanged content. Handle changes, removals and renames explicitly. Keep code inventories separate from the formal memory backend. Verify restore and repeated synchronization.',
    'goal-supervisor': 'Persist the goal, acceptance commands, source revision and next action. Run the configured model backend, then run independent acceptance commands. If acceptance fails, provide its real output to the next repair step. Resume the durable checkpoint after interruption.',
    'qa-repair-release': 'Exercise actual behavior, build and tests. A successful model process does not prove project completion. Diagnose failures, preserve logs and switch an independently applicable route after repeated errors or no progress. Stop only after required acceptance passes.',
    'agent-orchestration': 'Delegate independent work to real agents and collect their results. Assign one writer per shared file. Use child agents when the task benefits from them. Persist handoffs, checkpoints, test evidence and the next action.',
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load(path: Path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def atomic(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.cloudkit-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def git(root: Path, *arguments: str, check=True) -> str:
    run = subprocess.run(['git', '-C', str(root), *arguments], capture_output=True, text=True)
    if check and run.returncode:
        raise ValueError('Git project operation failed')
    return run.stdout.strip() if run.returncode == 0 else ''


def project_identity(path: Path) -> dict:
    path = path.resolve()
    root = git(path, 'rev-parse', '--show-toplevel')
    if not root:
        raise ValueError('The project must be an existing Git checkout')
    root = Path(root).resolve()
    remote = git(root, 'remote', 'get-url', 'origin', check=False)
    if remote.startswith('git@') and ':' in remote:
        host, _, tail = remote.partition(':')
        origin = host.split('@', 1)[-1] + '/' + tail
    elif remote:
        parsed = urlsplit(remote)
        origin = (parsed.hostname or '') + parsed.path if parsed.hostname else str(Path(remote).resolve())
    else:
        origin = str(root)
    origin = origin.rstrip('/').removesuffix('.git')
    branch = git(root, 'symbolic-ref', '--short', 'HEAD', check=False) or 'detached-' + git(root, 'rev-parse', 'HEAD')[:12]
    project_key = digest((origin + '\0' + branch).encode())[:24]
    return {'root': str(root), 'origin': origin, 'branch': branch, 'project_key': project_key,
            'source_sha': git(root, 'rev-parse', 'HEAD', check=False) or None}


def state_partition(project: Path, state: Path) -> tuple[dict, Path]:
    identity = project_identity(project)
    state = state.resolve()
    root = Path(identity['root'])
    if state == root or state.is_relative_to(root):
        raise ValueError('Choose a state directory outside the project checkout')
    return identity, state / 'projects' / identity['project_key']


def setup(runtime: Path, memory_protocol: Path | None = None) -> dict:
    if runtime.is_symlink():
        raise ValueError('Runtime root must be a directory')
    runtime = runtime.resolve()
    receipt_file = runtime / 'installation.json'
    previous = load(receipt_file, {})
    previous_files = previous.get('files', {})
    writes = {}
    for name, body in SKILLS.items():
        relative = '.agents/skills/cloudkit-' + name + '/SKILL.md'
        text = '---\nname: cloudkit-' + name + '\ndescription: ' + body.split('.')[0] + '\n---\n\n' + body + '\n'
        writes[relative] = text.encode()
    config = {'schema_version': 1, 'version': __version__, 'profile': 'core',
              'memory_protocol': str(memory_protocol.resolve()) if memory_protocol else previous.get('memory_protocol'),
              'skills': sorted('cloudkit-' + name for name in SKILLS)}
    for relative, content in writes.items():
        destination = runtime / relative
        for parent in [destination, *destination.parents]:
            if parent == runtime.parent:
                break
            if parent.is_symlink():
                raise ValueError('Managed runtime paths must not be symlinks')
        if destination.exists() and destination.read_bytes() != content:
            if previous_files.get(relative) != digest(destination.read_bytes()):
                raise ValueError('A customized runtime file was preserved: ' + relative)
    reused = 0
    for relative, content in writes.items():
        destination = runtime / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.read_bytes() == content:
            reused += 1
        else:
            destination.write_bytes(content)
    config['files'] = {relative: digest(content) for relative, content in writes.items()}
    atomic(receipt_file, config)
    return {'passed': True, 'runtime_root': str(runtime), 'profile': 'core', 'version': __version__,
            'skills': config['skills'], 'reused_files': reused, 'written_files': len(writes) - reused,
            'memory_protocol_configured': bool(config['memory_protocol']), 'model_worker_started': False}


def doctor(runtime: Path, project: Path | None = None) -> dict:
    checks = {}
    for name, arguments in {'git': ['--version'], 'python3': ['--version'], 'codex': ['--version'], 'node': ['--version']}.items():
        executable = shutil.which(name)
        if not executable:
            checks[name] = {'available': False, 'reason': 'executable-not-found'}
            continue
        try:
            run = subprocess.run([executable, *arguments], capture_output=True, text=True, timeout=20)
            value = (run.stdout or run.stderr).strip().splitlines()
            version = value[0][:180] if value else ''
            version = re.sub(r'(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,})', '[REDACTED]', version)
            checks[name] = {'available': run.returncode == 0, 'path': executable, 'returncode': run.returncode, 'version': version}
        except subprocess.TimeoutExpired:
            checks[name] = {'available': False, 'reason': 'version-probe-timeout', 'path': executable}
    installation = load(runtime.resolve() / 'installation.json', {})
    protocol = installation.get('memory_protocol') or str(Path(__file__).parent / 'runtime/memoryctl.py')
    result = {'passed': checks['git']['available'] and checks['python3']['available'],
              'scope': 'Executable and setup probes only; no model request has been made',
              'checks': checks, 'setup_installed': bool(installation),
              'memory_protocol_available': bool(protocol and Path(protocol).is_file()),
              'memory_backend_installed': (runtime.resolve() / 'memory/.venv/bin/python').is_file() and (runtime.resolve() / 'memory/backend.py').is_file(),
              'model_worker_verified': False}
    if project:
        result['project'] = project_identity(project)
    return result


def attach(project: Path, runtime: Path, state: Path) -> dict:
    identity, partition = state_partition(project, state)
    root = Path(identity['root']); runtime = runtime.resolve()
    installed = load(runtime / 'installation.json', {})
    if not installed.get('skills'):
        raise ValueError('Run cloudkit setup before attaching a project')
    planned = []
    for name in installed['skills']:
        source = runtime / '.agents/skills' / name
        if not (source / 'SKILL.md').is_file():
            raise ValueError('The shared skill installation is incomplete')
        planned.append((name, source, root / '.agents/skills' / name))
    for _, _, destination in planned:
        for parent in destination.parents:
            if parent == root:
                break
            if parent.is_symlink():
                raise ValueError('Project skill directory is a symlink')
    generated = root / 'AGENTS.cloudkit.md'
    rules = ('# Shared cloud task startup\n\nRead the project AGENTS.md, short goal and active checkpoint first. '
             'Restore this project and branch from its configured private state. Use relevant shared skills and progressively retrieve verified memory. '
             'Use real agents for independent tasks. Run actual project acceptance, preserve failures and resume durable steps. '
             'Write concise verified conclusions with sources; keep raw logs in the configured evidence store.\n')
    managed_config = root / '.codexcloudkit.json'
    if generated.is_symlink() or managed_config.is_symlink():
        raise ValueError('Generated project files must not be symlinks')
    existing_config = load(managed_config, {})
    if existing_config and existing_config.get('managed_by') != 'CodexCloudKit':
        raise ValueError('An existing project configuration was preserved')
    if generated.exists() and existing_config.get('rules_sha256') != digest(generated.read_bytes()) and generated.read_text() != rules:
        raise ValueError('Customized shared rules were preserved')
    linked = []; reused = []; overrides = []
    for name, source, destination in planned:
        if destination.is_symlink() and destination.resolve() == source:
            reused.append(name)
        elif destination.exists() or destination.is_symlink():
            overrides.append(name)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(source, target_is_directory=True)
            linked.append(name)
    generated.write_text(rules)
    config = {'schema_version': 1, 'managed_by': 'CodexCloudKit', 'runtime_root': str(runtime), 'state_dir': str(state.resolve()),
              'partition_root': str(partition), 'project': identity, 'rules_sha256': digest(rules.encode())}
    atomic(managed_config, config)
    exclude = Path(git(root, 'rev-parse', '--git-path', 'info/exclude'))
    if not exclude.is_absolute():
        exclude = root / exclude
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text() if exclude.exists() else ''
    lines = ['/.codexcloudkit.json', '/AGENTS.cloudkit.md', *['/.agents/skills/' + name for name in sorted(linked + reused)]]
    missing = [line for line in lines if line not in text.splitlines()]
    if missing:
        exclude.write_text(text.rstrip('\n') + '\n' + '\n'.join(missing) + '\n')
    return {'passed': True, 'project': identity, 'partition_root': str(partition), 'linked': linked,
            'already_linked': reused, 'project_overrides_preserved': overrides, 'existing_AGENTS_modified': False,
            'read_at_startup': ['AGENTS.md', 'AGENTS.cloudkit.md']}


def index(project: Path, state: Path, fingerprint: str = 'content-inventory-v1', export: Path | None = None) -> dict:
    identity, partition = state_partition(project, state)
    root = Path(identity['root']); path = partition / 'code-inventory.json'
    old = load(path, {})
    raw = subprocess.check_output(['git', '-C', str(root), 'ls-files', '--cached', '--others', '--exclude-standard', '-z'])
    files = {}
    for encoded in raw.split(b'\0'):
        if not encoded:
            continue
        relative = os.fsdecode(encoded); source = root / relative
        if not source.is_file() or source.is_symlink():
            continue
        resolved = source.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError('Source path leaves the project')
        hasher = hashlib.sha256()
        with source.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                hasher.update(block)
        files[relative] = {'sha256': hasher.hexdigest(), 'bytes': source.stat().st_size}
    previous = old.get('files', {})
    added = set(files) - set(previous); deleted = set(previous) - set(files)
    modified = {name for name in set(files) & set(previous) if files[name] != previous[name]}
    renamed = []
    for before in sorted(deleted):
        matches = [name for name in sorted(added) if files[name] == previous[before]]
        reverse = [name for name in deleted if previous[name] == previous[before]]
        if len(matches) == 1 and len(reverse) == 1:
            after = matches[0]; renamed.append({'from': before, 'to': after}); added.remove(after)
    deleted -= {item['from'] for item in renamed}
    changed_fingerprint = bool(old) and old.get('fingerprint') != fingerprint
    changed = not old or bool(added or deleted or modified or renamed or changed_fingerprint)
    generation = old.get('generation', 0) + int(changed)
    payload = {'schema_version': 1, 'project': identity, 'fingerprint': fingerprint, 'generation': generation, 'files': files}
    if changed or old.get('project', {}).get('source_sha') != identity['source_sha']:
        atomic(path, payload)
    if export:
        atomic(export, payload)
    return {'passed': True, 'scope': 'Derived content-hash inventory; not a formal memory store, parser, embedding model or semantic index',
            'project': identity, 'generation': generation, 'files': len(files), 'hash_reads': len(files),
            'added': sorted(added), 'modified': sorted(modified), 'deleted': sorted(deleted), 'renamed': renamed,
            'fingerprint_changed': changed_fingerprint, 'work_items': len(files) if changed_fingerprint else len(added) + len(modified),
            'unchanged_content_reused': sum(name in previous and files[name] == previous[name] for name in files),
            'inventory_path': str(path), 'export': str(export) if export else None,
            'export_sha256': digest(export.read_bytes()) if export else None}


def restore_index(project: Path, state: Path, archive: Path, expected_sha256: str) -> dict:
    data = archive.read_bytes()
    if digest(data) != expected_sha256:
        raise ValueError('Inventory snapshot SHA256 mismatch')
    payload = json.loads(data)
    identity, partition = state_partition(project, state)
    if payload.get('schema_version') != 1 or payload.get('project', {}).get('project_key') != identity['project_key']:
        raise ValueError('Inventory snapshot belongs to another project or branch')
    if not isinstance(payload.get('files'), dict):
        raise ValueError('Inventory snapshot is incomplete')
    for name, item in payload['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts or not re.fullmatch(r'[0-9a-f]{64}', item['sha256']):
            raise ValueError('Inventory snapshot contains an invalid entry')
    atomic(partition / 'code-inventory.json', payload)
    return {'passed': True, 'project': identity, 'generation': payload['generation'], 'files': len(payload['files']),
            'scope': 'Derived inventory restored; source checkout and formal memory restore are separate operations'}
