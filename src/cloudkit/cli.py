"""Command-line entry points for reusable cloud development setup."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from . import __version__
from .core import DEFAULT_RUNTIME, DEFAULT_STATE, attach, doctor, index, load, restore_index, setup


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog='cloudkit', description='Reusable cloud development setup and task adapters')
    root.add_argument('--version', action='version', version=__version__)
    commands = root.add_subparsers(dest='command', required=True)
    install = commands.add_parser('setup')
    install.add_argument('--runtime-root', type=Path, default=DEFAULT_RUNTIME)
    install.add_argument('--memory-protocol', type=Path)
    install.add_argument('--profile', choices=('core', 'memory', 'queue', 'languages', 'reverse', 'design', 'code-intelligence'), default='core')
    health = commands.add_parser('doctor')
    health.add_argument('--runtime-root', type=Path, default=DEFAULT_RUNTIME)
    health.add_argument('--project-root', type=Path)
    connect = commands.add_parser('attach')
    connect.add_argument('--project-root', required=True, type=Path)
    connect.add_argument('--runtime-root', type=Path, default=DEFAULT_RUNTIME)
    connect.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    inventory = commands.add_parser('index')
    inventory.add_argument('--project-root', required=True, type=Path)
    inventory.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    inventory.add_argument('--fingerprint', default='content-inventory-v1')
    inventory.add_argument('--export', type=Path)
    recover = commands.add_parser('restore-index')
    recover.add_argument('--project-root', required=True, type=Path)
    recover.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    recover.add_argument('--archive', required=True, type=Path)
    recover.add_argument('--expected-sha256', required=True)
    memory = commands.add_parser('memory', description='Delegate to the configured existing formal-memory protocol')
    memory.add_argument('--project-id', required=True)
    memory.add_argument('--branch', default='main')
    memory.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    memory.add_argument('--runtime-root', type=Path, default=DEFAULT_RUNTIME)
    memory.add_argument('--protocol', type=Path)
    memory.add_argument('arguments', nargs=argparse.REMAINDER)
    mcp = commands.add_parser('mcp', description='Start the project-bound formal-memory MCP server over stdio')
    mcp.add_argument('--runtime-root', type=Path, default=DEFAULT_RUNTIME)
    mcp.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    mcp.add_argument('--project-root', type=Path, required=True)
    mcp.add_argument('--project-id')
    mcp.add_argument('--branch')
    run = commands.add_parser('run', description='Invoke a configured task supervisor')
    run.add_argument('--supervisor', type=Path, default=Path(__file__).parent / 'runtime/worker_supervisor.py')
    run.add_argument('arguments', nargs=argparse.REMAINDER)
    queue = commands.add_parser('queue', description='Deliver durable task wakeups through an optional Redis queue')
    queue.add_argument('--runtime-root', type=Path, default=DEFAULT_RUNTIME)
    queue.add_argument('arguments', nargs=argparse.REMAINDER)
    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == 'setup':
            result = setup(args.runtime_root, args.memory_protocol)
            if args.profile in ('memory', 'queue'):
                from .profiles import install_profile
                result['profile_installation'] = install_profile(args.profile, args.runtime_root)
                result['profile'] = args.profile
            elif args.profile != 'core':
                from .optional_tools import install_optional_profile
                result['profile_installation'] = install_optional_profile(args.profile, args.runtime_root)
                result['profile'] = args.profile
        elif args.command == 'doctor':
            result = doctor(args.runtime_root, args.project_root)
        elif args.command == 'attach':
            result = attach(args.project_root, args.runtime_root, args.state_dir)
        elif args.command == 'index':
            result = index(args.project_root, args.state_dir, args.fingerprint, args.export)
        elif args.command == 'restore-index':
            result = restore_index(args.project_root, args.state_dir, args.archive, args.expected_sha256)
        elif args.command == 'memory':
            configuration = load(args.runtime_root.resolve() / 'installation.json', {})
            protocol = args.protocol or configuration.get('memory_protocol') or Path(__file__).parent / 'runtime/memoryctl.py'
            if not protocol or not Path(protocol).is_file():
                raise ValueError('Formal memory protocol is not configured; pass --protocol or configure it with setup')
            arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
            if not arguments:
                raise ValueError('Specify a memory protocol command, such as status or sync')
            command = [sys.executable, str(Path(protocol).resolve()), '--project', args.project_id, '--branch', args.branch,
                       '--store-root', str(args.state_dir.resolve()), '--backend', str(args.runtime_root.resolve() / 'memory/backend.py'), *arguments]
            environment = os.environ.copy()
            environment['CLOUDKIT_RUNTIME_ROOT'] = str(args.runtime_root.resolve())
            return subprocess.run(command, env=environment).returncode
        elif args.command == 'mcp':
            command = [sys.executable, str(Path(__file__).parent / 'runtime/mcp_server.py'),
                       '--project-path', str(args.project_root.resolve()), '--store-root', str(args.state_dir.resolve()),
                       '--backend', str(args.runtime_root.resolve() / 'memory/backend.py')]
            if args.project_id:
                command.extend(['--project', args.project_id])
            if args.branch:
                command.extend(['--branch', args.branch])
            environment = os.environ.copy()
            environment['CLOUDKIT_RUNTIME_ROOT'] = str(args.runtime_root.resolve())
            return subprocess.run(command, env=environment).returncode
        elif args.command == 'queue':
            python = args.runtime_root.resolve() / 'queue/.venv/bin/python'
            bridge = Path(__file__).parent / 'runtime/queue_bridge.py'
            if not python.is_file() or not bridge.is_file():
                raise ValueError('Install the queue profile before starting the wakeup bridge')
            arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
            return subprocess.run([str(python), str(bridge), *arguments]).returncode
        else:
            supervisor = args.supervisor.resolve()
            if not supervisor.is_file():
                raise ValueError('The configured supervisor executable does not exist')
            arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
            return subprocess.run([sys.executable, str(supervisor), *arguments]).returncode
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get('passed') else 1
    except (ValueError, OSError, RuntimeError, ImportError, json.JSONDecodeError) as error:
        print(json.dumps({'passed': False, 'error': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
