"""Install optional, pinned native services into a shared cloud runtime."""
from __future__ import annotations

import hashlib
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import re

from .core import atomic


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install_profile(name: str, runtime: Path) -> dict:
    if name not in ('memory', 'queue'):
        raise ValueError(f'Unknown profile: {name}')
    runtime = Path(runtime).absolute()
    if name == 'queue':
        for parent in (runtime, *runtime.parents):
            if parent.is_symlink():
                raise ValueError('The shared runtime is a user-managed link')
    if runtime.is_symlink():
        raise ValueError('The shared runtime is a user-managed link')
    if name == 'memory' and sys.version_info < (3, 12):
        raise ValueError('The memory profile requires Python 3.12 or newer; the core profile supports Python 3.11')
    runtime = runtime.resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    lock_path = runtime / ('.' + name + '-profile.lock')
    if lock_path.is_symlink():
        raise ValueError('The memory profile lock is a user-managed link')
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _install_memory(runtime) if name == 'memory' else _install_queue(runtime)


def _install_queue(runtime: Path) -> dict:
    assets = Path(__file__).parent / 'assets/queue'
    destination = runtime / 'queue'
    if destination.is_symlink():
        raise ValueError('The queue runtime path is a user-managed link')
    destination.mkdir(parents=True, exist_ok=True)
    receipt_file = destination / 'installation.json'
    owner_file = destination / '.environment-owner.json'
    marker_file = destination / '.dependencies-installed'
    for path in (receipt_file, owner_file, marker_file, destination / '.venv'):
        if path.is_symlink():
            raise ValueError('A queue service path is a user-managed link: ' + path.name)
    wanted_owner = {'schema_version': 1, 'managed_by': 'CodexCloudKit', 'profile': 'queue'}
    owner = json.loads(owner_file.read_text()) if owner_file.is_file() else {}
    previous = json.loads(receipt_file.read_text()) if receipt_file.is_file() else {}
    if owner and owner != wanted_owner:
        raise ValueError('An existing queue environment was preserved')
    if (destination / '.venv').exists() and not owner and not previous:
        raise ValueError('An existing queue environment was preserved')
    sources = {name: assets / name for name in ('requirements.in', 'requirements.lock')}
    for name, source in sources.items():
        target = destination / name
        if target.is_symlink():
            raise ValueError('A queue service file is a user-managed link: ' + name)
        if target.exists() and (not target.is_file() or _digest(target) not in (_digest(source), previous.get('managed', {}).get(name))):
            raise ValueError('A queue service file contains a local customization: ' + name)
    changes = []
    if not owner:
        atomic(owner_file, wanted_owner)
    for name, source in sources.items():
        target = destination / name
        if not target.exists() or _digest(target) != _digest(source):
            shutil.copyfile(source, target)
            changes.append(name)
    environment = os.environ.copy()
    environment['UV_CACHE_DIR'] = str(runtime / 'uv-cache')
    python = destination / '.venv/bin/python'
    if not python.is_file():
        result = subprocess.run([sys.executable, '-m', 'venv', str(destination / '.venv')], capture_output=True, text=True)
        if result.returncode:
            _failure_log(destination, 'queue-environment', result)
            raise RuntimeError('Queue environment creation failed')
    lock_hash = _digest(destination / 'requirements.lock')
    expected_versions = {name.lower().replace('_', '-').replace('.', '-'): version
                         for name, version in re.findall(r'^([A-Za-z0-9_.-]+)==([^\s\\]+)',
                            (destination / 'requirements.lock').read_text(), flags=re.M)}
    dependency_versions = _dependency_versions(python)
    dependencies_reused = (previous.get('requirements_sha256') == lock_hash
                           and marker_file.is_file() and marker_file.read_text().strip() == lock_hash
                           and all(dependency_versions.get(name) == version for name, version in expected_versions.items()))
    if not dependencies_reused:
        uv = shutil.which('uv')
        if uv:
            command = [uv, 'pip', 'sync', '--python', str(python), '--require-hashes', str(destination / 'requirements.lock')]
        else:
            command = [str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '--require-hashes', '-r', str(destination / 'requirements.lock')]
        result = subprocess.run(command, env=environment, capture_output=True, text=True)
        if result.returncode:
            _failure_log(destination, 'queue-dependencies', result)
            raise RuntimeError('Pinned queue dependency installation failed')
        marker_file.write_text(lock_hash + '\n')
        dependency_versions = _dependency_versions(python)
    if not all(dependency_versions.get(name) == version for name, version in expected_versions.items()):
        raise RuntimeError('The installed queue distributions do not match their pinned versions')
    probe = subprocess.run([str(python), '-c',
                            'import json,redis,rq; print(json.dumps({"rq":rq.__version__,"redis":redis.__version__}))'],
                           capture_output=True, text=True)
    if probe.returncode:
        _failure_log(destination, 'queue-imports', probe)
        raise RuntimeError('Installed queue client imports failed')
    bridge = Path(__file__).parent / 'runtime/queue_bridge.py'
    receipt = {'schema_version': 1, 'passed': True, 'profile': 'queue', 'python': str(python),
               'requirements_sha256': lock_hash, 'dependencies_reused': dependencies_reused,
               'dependency_install_performed': not dependencies_reused,
               'distribution_versions': dependency_versions, 'verified_distribution_count': len(expected_versions),
               'import_versions': json.loads(probe.stdout),
               'managed': {name: _digest(destination / name) for name in sources}, 'source_files_updated': changes,
               'bridge_script': str(bridge), 'job_import_strategy': 'script-directory queue_bridge.consume',
               'redis_service_started': False, 'redis_connection_tested': False}
    atomic(receipt_file, receipt)
    return receipt


def _install_memory(runtime: Path) -> dict:
    assets = Path(__file__).parent / 'assets/native_memory'
    destination = runtime / 'memory'
    if destination.is_symlink():
        raise ValueError('The memory runtime path is a user-managed link')
    destination.mkdir(parents=True, exist_ok=True)
    receipt_file = destination / 'installation.json'
    owner_file = destination / '.environment-owner.json'
    marker_file = destination / '.dependencies-installed'
    for path in (receipt_file, owner_file, marker_file, destination / '.venv'):
        if path.is_symlink():
            raise ValueError('A memory service path is a user-managed link: ' + path.name)
    owner = json.loads(owner_file.read_text()) if owner_file.is_file() else {}
    if owner and owner != {'schema_version': 1, 'managed_by': 'CodexCloudKit'}:
        raise ValueError('An existing memory environment was preserved')
    if (destination / '.venv').exists() and not owner and not receipt_file.exists():
        raise ValueError('An existing memory environment was preserved')
    previous = json.loads(receipt_file.read_text()) if receipt_file.is_file() else {}
    sources = {p.name: p for p in assets.iterdir() if p.is_file()}
    changes = []
    # Check every managed file before making any change.
    for name, source in sources.items():
        target = destination / name
        if target.is_symlink():
            raise ValueError(f'Memory service file is a user-managed link: {name}')
        if target.exists() and _digest(target) not in (_digest(source), previous.get('managed', {}).get(name)):
            raise ValueError(f'Memory service file contains a local customization: {name}')
    for name, source in sources.items():
        target = destination / name
        if not target.exists() or _digest(target) != _digest(source):
            shutil.copyfile(source, target)
            changes.append(name)
        if name == 'bm':
            target.chmod(0o755)
    environment = os.environ.copy()
    environment['UV_CACHE_DIR'] = str(runtime / 'uv-cache')
    python = destination / '.venv/bin/python'
    lock_hash = _digest(destination / 'requirements.lock')
    expected_versions = {name.lower().replace('_', '-').replace('.', '-'): version
                         for name, version in re.findall(r'^([A-Za-z0-9_.-]+)==([^\s\\]+)',
                            (destination / 'requirements.lock').read_text(), flags=re.M)}
    if not owner:
        atomic(owner_file, {'schema_version': 1, 'managed_by': 'CodexCloudKit'})
    if not python.is_file():
        subprocess.run([sys.executable, '-m', 'venv', str(destination / '.venv')], check=True, capture_output=True)
    dependency_versions = _dependency_versions(python)
    dependencies_reused = (previous.get('requirements_sha256') == lock_hash
                           and marker_file.is_file() and marker_file.read_text().strip() == lock_hash
                           and all(dependency_versions.get(name) == version for name, version in expected_versions.items()))
    if not dependencies_reused:
        if shutil.which('uv'):
            command = ['uv', 'pip', 'sync', '--python', str(python), '--require-hashes', str(destination / 'requirements.lock')]
        else:
            command = [str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '--require-hashes', '-r', str(destination / 'requirements.lock')]
        result = subprocess.run(command, env=environment, capture_output=True, text=True)
        if result.returncode:
            _failure_log(destination, 'dependencies', result)
            raise RuntimeError('Pinned memory dependency installation failed')
        marker_file.write_text(lock_hash + '\n')
        dependency_versions = _dependency_versions(python)
    if not all(dependency_versions.get(name) == version for name, version in expected_versions.items()):
        raise RuntimeError('The installed memory distributions do not match their pinned versions')
    result = subprocess.run([str(python), str(destination / 'ensure_model.py'), '--reuse-verified'],
                            env=environment, capture_output=True, text=True)
    if result.returncode:
        _failure_log(destination, 'model', result)
        raise RuntimeError('Pinned memory model preparation failed')
    model = json.loads(result.stdout)
    receipt = {'schema_version': 1, 'passed': True, 'profile': 'memory',
               'backend': str(destination / 'backend.py'), 'python': str(python),
               'requirements_sha256': lock_hash,
               'dependencies_reused': dependencies_reused,
               'distribution_versions': dependency_versions,
               'verified_distribution_count': len(expected_versions),
               'managed': {name: _digest(destination / name) for name in sources},
               'model': model, 'source_files_updated': changes}
    atomic(receipt_file, receipt)
    return receipt


def _dependency_versions(python: Path) -> dict:
    if not python.is_file():
        return {}
    result = subprocess.run([str(python), '-c',
        'import importlib.metadata as m,json; '
        'print(json.dumps({item.metadata["Name"].lower().replace("_","-").replace(".","-"):item.version '
        'for item in m.distributions() if item.metadata.get("Name")}))'], capture_output=True, text=True)
    if result.returncode:
        return {}
    try:
        return json.loads(result.stdout)
    except ValueError:
        return {}


def _failure_log(destination: Path, stage: str, result) -> None:
    text = result.stdout + '\n' + result.stderr
    text = re.sub(r'(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})', '[REDACTED]', text)
    text = re.sub(r'https?://\S+[?&](?:X-Amz-Signature|sig|token|signature)=\S+', '[REDACTED_URL]', text, flags=re.I)
    path = destination / ('install-' + stage + '-errors.log')
    if not path.is_symlink():
        path.write_text(text)
