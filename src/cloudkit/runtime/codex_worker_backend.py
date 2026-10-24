#!/usr/bin/env python3
"""Stdin-JSON adapter for an authenticated Codex CLI and independent acceptance."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import uuid


TOKEN = re.compile(r'(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})')
SIGNED_URL = re.compile(r'https?://\S+[?&](?:X-Amz-Signature|sig|token|signature)=\S+', re.I)
BEARER = re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}')
AUTHORIZATION = re.compile(r'(?i)\b(?:proxy-)?authorization\s*[:=]\s*(?:bearer|basic)\s+\S+')
URL_CREDENTIAL = re.compile(r'(?i)(https?://)[^/@\s]+:[^/@\s]+@')


def scrub(text):
    text = AUTHORIZATION.sub('Authorization: [REDACTED]', str(text))
    text = URL_CREDENTIAL.sub(r'\1[REDACTED]@', text)
    return BEARER.sub('Bearer [REDACTED]', SIGNED_URL.sub('[REDACTED_SIGNED_URL]', TOKEN.sub('[REDACTED]', text)))


def scrub_values(value):
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {key: scrub_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_values(item) for item in value]
    return value


def command(value):
    if value.lstrip().startswith('['):
        argv = json.loads(value)
    else:
        argv = [value]
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
        raise ValueError('codex-command must be an executable or a nonempty JSON argv array')
    return argv


def kill_group(group):
    try:
        os.killpg(group, signal.SIGKILL)
    except ProcessLookupError:
        pass


def process_guardian(lifeline, result_pipe):
    """Own the isolated CLI group and terminate it when the adapter disappears."""
    # Only the adapter owns the pipe writer. A supervisor killing the adapter
    # closes it in the kernel, even when the adapter cannot execute cleanup.
    os.set_inheritable(lifeline, False)
    os.set_inheritable(result_pipe, False)

    def watch_parent():
        while os.read(lifeline, 1):
            pass
        kill_group(os.getpgrp())

    threading.Thread(target=watch_parent, daemon=True).start()
    code = 127
    try:
        config = json.load(sys.stdin)
        child = subprocess.Popen(config['argv'], stdin=subprocess.PIPE,
                                 text=True, errors='replace', close_fds=True)
        child.communicate(config['prompt'])
        code = child.returncode
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(type(error).__name__ + ': ' + str(error), file=sys.stderr, flush=True)
    try:
        os.write(result_pipe, str(code).encode('ascii') + b'\n')
        os.close(result_pipe)
    finally:
        # The result pipe preserves the CLI exit status before this atomic
        # group cleanup removes the guardian and any remaining attached tools.
        kill_group(os.getpgrp())


def invoke_cli(argv, prompt, root, timeout):
    lifeline_read, lifeline_write = os.pipe()
    result_read, result_write = os.pipe()
    process = None; timed_out = False
    try:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
            '--_process-guardian', str(lifeline_read), str(result_write)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=root, text=True, errors='replace', start_new_session=True,
            pass_fds=(lifeline_read, result_write))
        os.close(lifeline_read); lifeline_read = None
        os.close(result_write); result_write = None
        try:
            stdout, stderr = process.communicate(json.dumps({'argv': argv, 'prompt': prompt}), timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True; kill_group(process.pid)
            stdout, stderr = process.communicate()
        result = os.read(result_read, 128).decode('ascii').strip()
        code = 124 if timed_out else int(result) if re.fullmatch(r'-?\d+', result) else process.returncode or 127
        return code, stdout, stderr, timed_out
    except OSError as error:
        if process is not None:
            kill_group(process.pid); process.communicate()
        return 127, '', type(error).__name__ + ': ' + str(error), False
    finally:
        for descriptor in (lifeline_read, lifeline_write, result_read, result_write):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def parse_events(stdout, nonce):
    messages, event_count, invalid_lines = [], 0, 0
    completed, failed = False, False
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            invalid_lines += 1
            continue
        if not isinstance(event, dict):
            invalid_lines += 1
            continue
        event_count += 1
        kind = event.get('type')
        if kind == 'item.completed':
            item = event.get('item')
            if isinstance(item, dict) and item.get('type') == 'agent_message' and isinstance(item.get('text'), str):
                messages.append(item['text'])
        if kind == 'turn.completed':
            completed, failed = True, False
        if kind in ('turn.failed', 'error'):
            completed, failed = False, True
    proof = any(nonce in message for message in messages)
    return {'agent_messages': messages, 'agent_message_count': len(messages), 'event_count': event_count,
            'invalid_lines': invalid_lines, 'turn_completed': completed, 'turn_failed': failed,
            'nonce_found_in_agent_message': proof}


def prompt_for(request, nonce):
    if request['kind'] == 'capability':
        return ('Reply with the exact proof string below in your final assistant message. '
                'Do not use tools, modify files or print a version.\n' + nonce + '\n')
    context = {key: request.get(key) for key in ('request_id', 'goal', 'project_id', 'branch', 'source_sha',
               'round', 'acceptance_commands', 'previous_acceptance', 'checkpoint', 'instructions')}
    return ('Continue the project in the current working directory. Read its applicable AGENTS.md and '
            'persistent goal/checkpoint, implement or repair the requested work, and use the previous '
            'independent acceptance evidence to address failures. The supervisor will run acceptance '
            'commands independently after this worker exits. Report changes and remaining work. '
            'Include the proof string below in the final assistant message.\n'
            'PROOF: ' + nonce + '\nTASK JSON:\n' + json.dumps(context, ensure_ascii=False) + '\n')


def run(request, codex_command, timeout, cwd=None, model=None):
    if not isinstance(request, dict) or request.get('kind') not in ('capability', 'work'):
        raise ValueError('stdin must contain an object with kind capability or work')
    if timeout <= 0:
        raise ValueError('timeout must be positive')
    if request['kind'] == 'capability':
        for key in ('challenge', 'backend_id'):
            if not isinstance(request.get(key), str) or not request[key]:
                raise ValueError('capability request requires ' + key)
    elif not isinstance(request.get('request_id'), str) or not request['request_id']:
        raise ValueError('work request requires request_id')
    root = Path(cwd or Path.cwd()).resolve()
    if not root.is_dir():
        raise ValueError('project working directory does not exist')
    nonce = 'CLOUDKIT_MODEL_PROOF_' + uuid.uuid4().hex
    prompt = prompt_for(request, nonce)
    argv = list(codex_command) + ['exec', '--ephemeral', '--json', '-C', str(root),
        '-c', 'approval_policy="never"', '-c', 'sandbox_mode="workspace-write"']
    if model:
        argv += ['--model', model]
    argv += ['-']
    started = time.monotonic()
    code, stdout, stderr, timed_out = invoke_cli(argv, prompt, root, timeout)
    parsed = parse_events(stdout, nonce)
    proven = code == 0 and parsed['turn_completed'] and not parsed['turn_failed'] and parsed['nonce_found_in_agent_message']
    response = '\n\n'.join(scrub(message) for message in parsed.pop('agent_messages')) if proven else ''
    receipt = {'schema_version': 1, 'kind': request['kind'], 'passed': bool(proven),
        'model_response': response, 'model_output_received': bool(proven),
        'returncode': code, 'timed_out': timed_out, 'elapsed_seconds': round(time.monotonic() - started, 4),
        'project_root': str(root), 'proof': parsed,
        'stdout_sha256': hashlib.sha256(stdout.encode()).hexdigest(),
        'stderr': scrub(stderr)[-8000:], 'stderr_truncated': len(scrub(stderr)) > 8000,
        'authentication_configuration_preserved': True, 'acceptance_run_by_backend': False}
    if request['kind'] == 'capability':
        receipt.update(backend_id=request['backend_id'], challenge=request['challenge'])
        if proven:
            receipt['capability'] = 'model-worker'
    else:
        receipt['request_id'] = request['request_id']
    if not proven:
        receipt['failure'] = ('timeout' if timed_out else 'codex-process-failed' if code else
                              'codex-turn-failed' if parsed['turn_failed'] else 'model-nonce-proof-missing')
    return scrub_values(receipt), 0 if proven else code or 65


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex-command', default='codex', help='Executable or JSON argv array, without a shell')
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--cwd', type=Path, help='Project root; defaults to the current working directory')
    parser.add_argument('--model', help='Use an explicitly selected model on the existing configured provider')
    args = parser.parse_args(argv)
    try:
        request = json.load(sys.stdin)
        receipt, code = run(request, command(args.codex_command), args.timeout, args.cwd, args.model)
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        receipt, code = {'schema_version': 1, 'passed': False, 'failure': 'invalid-request',
                         'message': scrub(error), 'model_output_received': False}, 64
    print(json.dumps(receipt, ensure_ascii=False))
    return code


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--_process-guardian':
        process_guardian(int(sys.argv[2]), int(sys.argv[3]))
        raise SystemExit(127)
    raise SystemExit(main())
