#!/usr/bin/env python3
"""Build one pinned Redis server into a selected external cloud directory."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

COMMIT = '335554f18caf7bbf6b0ac2b3548133d750f00a1b'
VERSION = '7.2.16'
REPOSITORY = 'https://github.com/redis/redis.git'


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: dict) -> None:
    pending = path.with_suffix(path.suffix + '.pending')
    if path.is_symlink() or pending.is_symlink():
        raise ValueError('A managed receipt is a user-owned link')
    pending.write_text(json.dumps(value, indent=2) + '\n')
    pending.replace(path)


def physical(path: Path) -> Path:
    path = path.absolute()
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError('The runtime path traverses a user-owned link')
    return path


def redacted(text: str) -> str:
    text = re.sub(r'(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})', '[REDACTED]', text)
    return re.sub(r'https?://\S+[?&](?:X-Amz-Signature|sig|token|signature)=\S+', '[REDACTED_URL]', text, flags=re.I)


def command(argv: list[str], log: Path, cwd: Path | None = None, timeout: int = 180) -> str:
    if log.is_symlink():
        raise ValueError('An installation log is a user-owned link')
    try:
        result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                                env=dict(os.environ, GIT_TERMINAL_PROMPT='0'))
    except subprocess.TimeoutExpired as error:
        log.write_text(redacted(str(error)) + '\n')
        raise RuntimeError('A Redis installation command timed out; see ' + str(log)) from None
    if log.is_symlink():
        raise ValueError('An installation log is a user-owned link')
    log.write_text(redacted(result.stdout + '\n' + result.stderr))
    if result.returncode:
        raise RuntimeError('A Redis installation command failed; see ' + str(log))
    return result.stdout.strip()


def verify_source(source: Path, logs: Path) -> None:
    actual = command(['git', '-C', str(source), 'rev-parse', 'HEAD'], logs / 'source-identity.log')
    if actual != COMMIT:
        raise ValueError('The Redis source commit differs from the pinned version')
    changed = command(['git', '-C', str(source), 'status', '--porcelain', '--untracked-files=no'], logs / 'source-status.log')
    if changed:
        raise ValueError('Changed Redis source files were preserved')


def install(runtime: Path, reuse_source: Path | None = None, build_timeout: int = 600) -> dict:
    runtime = physical(runtime)
    runtime.mkdir(parents=True, exist_ok=True)
    owner = runtime / '.redis-installer-owner.json'
    source = runtime / 'redis-source'
    receipt_file = runtime / 'redis-installation.json'
    for path in (owner, source, receipt_file, runtime / 'bin', runtime / 'logs', runtime / '.redis-install.lock'):
        physical(path)
    expected_owner = {'schema_version': 1, 'managed_by': 'CodexCloudKit', 'component': 'redis-server'}
    if owner.exists() and json.loads(owner.read_text()) != expected_owner:
        raise ValueError('An existing Redis runtime was preserved')
    if not owner.exists() and (source.exists() or receipt_file.exists()):
        raise ValueError('An unowned Redis runtime was preserved')
    with (runtime / '.redis-install.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not owner.exists():
            atomic(owner, expected_owner)
        logs = runtime / 'logs'
        logs.mkdir(exist_ok=True)
        previous = json.loads(receipt_file.read_text()) if receipt_file.exists() else {}
        binary = source / 'src/redis-server'
        reused_from = None
        if not source.exists():
            if reuse_source is not None:
                reuse_source = physical(reuse_source).resolve()
                verify_source(reuse_source, logs)
                shutil.copytree(reuse_source, source)
                reused_from = str(reuse_source)
            else:
                source.mkdir()
                command(['git', 'init', '--quiet', str(source)], logs / 'source-init.log')
                command(['git', '-C', str(source), 'remote', 'add', 'origin', REPOSITORY], logs / 'source-remote.log')
                command(['git', '-C', str(source), 'fetch', '--depth', '1', 'origin', COMMIT], logs / 'source-fetch.log')
                command(['git', '-C', str(source), 'checkout', '--detach', COMMIT], logs / 'source-checkout.log')
        else:
            head = subprocess.run(['git', '-C', str(source), 'rev-parse', 'HEAD'], capture_output=True, text=True)
            if head.returncode and (source / '.git').is_dir() and not any(item.name != '.git' for item in source.iterdir()):
                command(['git', '-C', str(source), 'fetch', '--depth', '1', 'origin', COMMIT], logs / 'source-fetch.log')
                command(['git', '-C', str(source), 'checkout', '--detach', COMMIT], logs / 'source-checkout.log')
        verify_source(source, logs)
        cached = (previous.get('official_source_commit') == COMMIT and binary.is_file()
                  and previous.get('binary_sha256') == digest(binary))
        if not cached:
            if previous.get('binary_sha256') and binary.is_file() and previous['binary_sha256'] != digest(binary):
                raise ValueError('A changed Redis executable was preserved')
            command(['make', '-j2', 'MALLOC=libc', 'BUILD_TLS=no', 'redis-server'], logs / 'build.log', source,
                    timeout=build_timeout)
        version = command([str(binary), '--version'], logs / 'version.log', timeout=20)
        if f'v={VERSION} ' not in version or COMMIT[:8] not in version or 'malloc=libc' not in version:
            raise RuntimeError('The built Redis server does not match the pinned version and build mode')
        binaries = runtime / 'bin'
        binaries.mkdir(exist_ok=True)
        launcher = binaries / 'redis-server'
        text = '#!/usr/bin/env bash\nset -euo pipefail\nexec ' + shlex.quote(str(binary)) + ' "$@"\n'
        wanted = hashlib.sha256(text.encode()).hexdigest()
        if launcher.is_symlink() or launcher.exists() and digest(launcher) not in (wanted, previous.get('launcher_sha256')):
            raise ValueError('A custom Redis launcher was preserved')
        launcher.write_text(text)
        launcher.chmod(0o755)
        receipt = {'schema_version': 1, 'passed': True, 'version': VERSION, 'official_source_commit': COMMIT,
                   'source_root': str(source), 'binary': str(binary), 'binary_sha256': digest(binary),
                   'launcher': str(launcher), 'launcher_sha256': digest(launcher), 'version_output': version,
                   'build_command': ['make', '-j2', 'MALLOC=libc', 'BUILD_TLS=no', 'redis-server'],
                   'existing_build_reused': cached, 'source_reused_from': reused_from,
                   'system_service_modified': False, 'server_started': False, 'completed_at': time.time()}
        atomic(receipt_file, receipt)
        return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, default=Path('/workspace/external-tools/codexcloudkit/queue-runtime'))
    parser.add_argument('--reuse-source', type=Path)
    parser.add_argument('--build-timeout', type=int, default=600)
    args = parser.parse_args()
    if not 1 <= args.build_timeout <= 3600:
        parser.error('build-timeout must be between 1 and 3600 seconds')
    try:
        print(json.dumps(install(args.runtime_root, args.reuse_source, args.build_timeout), indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({'passed': False, 'error_type': type(error).__name__, 'message': redacted(str(error))}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
