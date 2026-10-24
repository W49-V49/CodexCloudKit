"""Install optional shared toolchains from pinned, externally downloaded artifacts."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys

from .core import atomic

PROFILES = ('languages', 'reverse', 'design', 'code-intelligence')
ASSETS = Path(__file__).parent / 'assets/toolchains'


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _claim(destination: Path, name: str) -> None:
    if destination.is_symlink():
        raise ValueError('The selected toolchain directory is a user-managed link')
    destination.mkdir(parents=True, exist_ok=True)
    owner = destination / '.cloudkit-profile-owner.json'
    if owner.is_symlink():
        raise ValueError('The toolchain ownership receipt is a user-managed link')
    wanted = {'schema_version': 1, 'managed_by': 'CodexCloudKit', 'profile': name}
    if owner.exists():
        if json.loads(owner.read_text()) != wanted:
            raise ValueError('An existing toolchain directory was preserved')
    elif any(destination.iterdir()):
        raise ValueError('An existing toolchain directory was preserved; select an empty runtime')
    else:
        atomic(owner, wanted)


def _run(command, environment: dict, output: Path, cwd: Path | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(command, env=environment, cwd=cwd, capture_output=True, text=True)
    text = result.stdout + '\n' + result.stderr
    text = re.sub(r'(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})', '[REDACTED]', text)
    text = re.sub(r'https?://\S+[?&](?:X-Amz-Signature|sig|token|signature)=\S+', '[REDACTED_URL]', text, flags=re.I)
    if output.is_symlink():
        raise ValueError('An installation log is a user-managed link')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text)
    if result.returncode:
        raise RuntimeError('Optional tool installation failed; see ' + str(output))
    return result


def install_optional_profile(name: str, runtime: Path) -> dict:
    if name not in PROFILES:
        raise ValueError('Unknown optional toolchain: ' + name)
    if sys.version_info < (3, 12):
        raise ValueError('Optional portable toolchains require Python 3.12 or newer')
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise ValueError('The pinned toolchain artifacts target Linux x86_64')
    runtime = Path(runtime).absolute()
    for path in (runtime, *runtime.parents):
        if path.is_symlink():
            raise ValueError('The shared runtime cannot traverse a user-managed link')
    runtime.mkdir(parents=True, exist_ok=True)
    lock = runtime / ('.' + name + '-profile.lock')
    if lock.is_symlink():
        raise ValueError('The profile lock is a user-managed link')
    with lock.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        return _install(name, runtime.resolve())


def _install(name: str, runtime: Path) -> dict:
    environment = os.environ.copy()
    environment.update(CLOUDKIT_RUNTIME_ROOT=str(runtime), CLOUDKIT_LANGUAGE_ROOT=str(runtime / 'languages'),
                       REVERSE_TOOLS_ROOT=str(runtime / 'reverse'), CLOUDKIT_DESIGN_ROOT=str(runtime / 'design'),
                       CLOUDKIT_DESIGN_STATE_ROOT=str(runtime / 'state/design/projects'),
                       CLOUDKIT_CODEGRAPH_ROOT=str(runtime / 'code-intelligence'),
                       CLOUDKIT_CODEGRAPH_STORE=str(runtime / 'state/codegraph'), CLOUDKIT_MEMORY_ROOT=str(runtime / 'memory'))
    for required in ('node', 'npm', 'uv') if name in ('languages', 'code-intelligence') else ('uv',) if name == 'reverse' else ('node', 'npm'):
        if shutil.which(required) is None:
            raise ValueError('Install the required executable before this profile: ' + required)
    destination = runtime / name
    _claim(destination, name)
    log = destination / 'profile-install.log'
    if name == 'languages':
        _run(['bash', str(ASSETS / 'environment/languages/install.sh')], environment, log)
        receipt = {'archives': json.loads((destination / 'archive-installation.json').read_text()),
                   'entrypoint': str(destination / 'language-tools'), 'editor_config': str(destination / 'nvim-init.lua')}
    elif name == 'reverse':
        jdk = runtime / 'languages/jdk/bin/javac'
        if not jdk.is_file():
            _claim(runtime / 'languages', 'languages')
            _run([sys.executable, str(ASSETS / 'tools/languages/install.py'), '--root', str(runtime / 'languages'), '--component', 'jdk'], environment, destination / 'jdk-install.log')
        environment['REVERSE_JAVA_HOME'] = str(runtime / 'languages/jdk')
        _run([sys.executable, str(ASSETS / 'environment/reverse/install.py'), '--runtime', str(destination)], environment, log)
        receipt = json.loads((destination / 'installation.json').read_text())
    elif name == 'design':
        _run([sys.executable, str(ASSETS / 'environment/design/install.py')], environment, log)
        receipt = json.loads((destination / 'installation.json').read_text())
        receipt['browser'] = _install_design_browser(destination, environment)
        atomic(destination / 'installation.json', receipt)
    else:
        receipt = _install_codegraph(runtime, environment, log)
    result = {'schema_version': 1, 'passed': True, 'profile': name, 'runtime_root': str(runtime),
              'installation': receipt, 'functional_acceptance_performed': False,
              'source_sha256': {str(path.relative_to(ASSETS)): _hash(path)
                                for base in ('tools', 'environment') for path in sorted((ASSETS / base / name).rglob('*'))
                                if path.is_file() and '__pycache__' not in path.parts}}
    atomic(destination / 'profile.json', result)
    return result


def _install_design_browser(destination: Path, environment: dict) -> dict:
    browser = destination / 'browser'
    if browser.is_symlink():
        raise ValueError('The diagram browser directory is a user-managed link')
    browser.mkdir(exist_ok=True)
    receipt_file = browser / 'installation.json'
    if receipt_file.is_symlink():
        raise ValueError('The diagram browser receipt is a user-managed link')
    previous = json.loads(receipt_file.read_text()) if receipt_file.is_file() else {}
    for name in ('package.json', 'package-lock.json'):
        original = ASSETS / 'environment/design' / name
        target = browser / name
        if target.is_symlink() or target.exists() and _hash(target) not in (_hash(original), previous.get('managed', {}).get(name)):
            raise ValueError('A customized diagram browser manifest was preserved: ' + name)
    for name in ('package.json', 'package-lock.json'):
        shutil.copyfile(ASSETS / 'environment/design' / name, browser / name)
    lock_hash = _hash(browser / 'package-lock.json')
    if previous.get('lock_sha256') != lock_hash or not (browser / 'node_modules/playwright/package.json').is_file():
        _run(['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund', '--cache', str(browser / 'npm-cache')], environment, browser / 'dependencies.log', browser)
    chromium = shutil.which('chromium') or shutil.which('chromium-browser')
    environment = dict(environment, PLAYWRIGHT_BROWSERS_PATH=str(browser / 'cache'))
    if chromium is None:
        _run([str(browser / 'node_modules/.bin/playwright'), 'install', 'chromium'], environment, browser / 'chromium-install.log', browser)
    receipt = {'schema_version': 1, 'lock_sha256': lock_hash,
               'managed': {name: _hash(browser / name) for name in ('package.json', 'package-lock.json')},
               'module': str(browser / 'node_modules/playwright'), 'cache': str(browser / 'cache'),
               'executable': chromium, 'uses_platform_chromium': chromium is not None, 'playwright': '1.64.1'}
    atomic(receipt_file, receipt)
    return receipt


def _install_codegraph(runtime: Path, environment: dict, log: Path) -> dict:
    from .profiles import install_profile
    memory = install_profile('memory', runtime)
    destination = runtime / 'code-intelligence'
    receipt_path = destination / 'installation.json'
    previous = json.loads(receipt_path.read_text()) if receipt_path.is_file() else {}
    pairs = {destination / name: ASSETS / 'environment/code-intelligence' / name for name in ('package.json', 'package-lock.json')}
    pairs.update({destination / 'protocol' / name: ASSETS / 'tools/code-intelligence' / name
                  for name in ('graphctl.py', 'semantic.py', 'mcp_server.py', 'context_output.py')})
    for target, source in pairs.items():
        if target.is_symlink():
            raise ValueError('A code intelligence file is a user-managed link')
        expected = _hash(source)
        if target.exists() and _hash(target) not in (expected, previous.get('managed', {}).get(str(target.relative_to(destination)))):
            raise ValueError('A customized code intelligence file was preserved: ' + target.name)
    for target, source in pairs.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    lock_hash = _hash(destination / 'package-lock.json')
    healthy = subprocess.run(['node', '-e', 'require("better-sqlite3") (":memory:").close()'], cwd=destination, capture_output=True).returncode == 0
    if not healthy or previous.get('lock_sha256') != lock_hash:
        _run(['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund', '--cache', str(destination / 'cache/npm')], environment, log, destination)
        probe = subprocess.run(['node', '-e', 'require("better-sqlite3") (":memory:").close()'], cwd=destination, capture_output=True)
        if probe.returncode:
            _run(['npm', 'rebuild', 'better-sqlite3', '--cache', str(destination / 'cache/npm')], environment, destination / 'sqlite-build.log', destination)
    _run(['node', '-e', 'require("better-sqlite3") (":memory:").close()'], environment, destination / 'sqlite-smoke.log', destination)
    binaries = destination / 'bin'
    binaries.mkdir(exist_ok=True)
    commands = {'codegraph-tools': [memory['python'], str(destination / 'protocol/graphctl.py')],
                'context-output': [memory['python'], str(destination / 'protocol/context_output.py')],
                'codegraph-native': [str(destination / 'node_modules/.bin/codegraph')]}
    exported = {key: environment[key] for key in ('CLOUDKIT_RUNTIME_ROOT', 'CLOUDKIT_LANGUAGE_ROOT',
                'CLOUDKIT_DESIGN_ROOT', 'CLOUDKIT_DESIGN_STATE_ROOT', 'CLOUDKIT_CODEGRAPH_ROOT',
                'CLOUDKIT_CODEGRAPH_STORE', 'CLOUDKIT_MEMORY_ROOT')}
    exported['CODEGRAPH_REGISTRY_PATH'] = str(destination / 'registry.json')
    for name, command in commands.items():
        target = binaries / name
        text = '#!/usr/bin/env bash\nset -euo pipefail\n' + ''.join('export ' + key + '=' + shlex.quote(value) + '\n' for key, value in exported.items())
        text += 'exec ' + shlex.join(command) + ' "$@"\n'
        expected = hashlib.sha256(text.encode()).hexdigest()
        if target.is_symlink() or target.exists() and _hash(target) not in (expected, previous.get('managed', {}).get('bin/' + name)):
            raise ValueError('A customized code intelligence launcher was preserved: ' + name)
        target.write_text(text)
        target.chmod(0o755)
    managed = {str(target.relative_to(destination)): _hash(target) for target in pairs}
    managed.update({'bin/' + name: _hash(binaries / name) for name in commands})
    receipt = {'schema_version': 1, 'lock_sha256': lock_hash, 'managed': managed,
               'packages': {'@optave/codegraph': '3.17.0', 'context-mode': '1.0.169'},
               'memory_root': str(runtime / 'memory'), 'canonical_memory_replaced': False,
               'postinstall_hooks_executed': False, 'entrypoint': str(binaries / 'codegraph-tools'),
               'mcp_servers': {'codegraph': {'command': memory['python'], 'args': [str(destination / 'protocol/mcp_server.py')]}},
               'optional_mcp_servers': {'context-output': {'command': memory['python'], 'args': [str(destination / 'protocol/context_output.py')]}}}
    atomic(receipt_path, receipt)
    return receipt
