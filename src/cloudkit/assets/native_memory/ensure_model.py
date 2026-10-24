#!/usr/bin/env python3
"""Fetch one immutable official model revision and verify every required artifact.

Basic Memory0.24.1 exposes FastEmbed cache_dir, not specific_model_path/revision.
We therefore maintain an isolated, verified HF cache reference and force the
supported HF_HUB_OFFLINE runtime path. No package monkeypatch is used.
"""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parent
LOCK_PATH = ROOT / 'model.lock.json'
DEFAULT_CACHE_ROOT = ROOT.parent / 'models/basic-memory'


def read_lock():
    lock = json.loads(LOCK_PATH.read_text(encoding='utf-8'))
    if lock.get('schema_version') != 1 or len(lock.get('revision','')) != 40:
        raise ValueError('Unsupported immutable model lock')
    return lock


def fingerprint():
    lock = read_lock()
    canonical = json.dumps(lock, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return {'embedding_model_revision': lock['revision'],
            'model_fingerprint': hashlib.sha256(canonical).hexdigest(),
            'model_sha256': lock['model_sha256'], 'embedding_dimension': lock['dimensions']}


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name+'-', dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as handle:
            handle.write(text); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def layout(cache_dir, lock):
    repo = Path(cache_dir) / ('models--' + lock['repository'].replace('/','--'))
    return repo, repo/'snapshots'/lock['revision']


def file_stats(snapshot, lock):
    stats = {}
    for name in lock['files']:
        relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe artifact path in model lock')
        file=snapshot/name
        s=file.stat()
        if not file.is_file():raise ValueError('Missing regular model artifact')
        stats[name]={'bytes':s.st_size,'mtime_ns':s.st_mtime_ns,'inode':s.st_ino}
    return stats


def verify_files(snapshot, lock):
    for name, entry in lock['files'].items():
        file=snapshot/name
        size=file.stat().st_size
        if size != entry['bytes']:raise ValueError('Locked model artifact size mismatch')
        algorithm=entry['algorithm']
        if algorithm=='sha256':digest=hashlib.sha256()
        elif algorithm=='git-blob-sha1':
            digest=hashlib.sha1();digest.update(f'blob {size}\0'.encode())
        else:raise ValueError('Unsupported locked artifact digest')
        with file.open('rb') as handle:
            for block in iter(lambda:handle.read(1024*1024),b''):digest.update(block)
        if digest.hexdigest()!=entry['digest']:
            raise ValueError('Locked model artifact digest mismatch')


def ensure_model(cache_dir=None, allow_download=True, force_verify=True):
    lock=read_lock();cache=Path(cache_dir or os.environ.get('BASIC_MEMORY_PINNED_MODEL_CACHE') or (DEFAULT_CACHE_ROOT/lock['revision'])).resolve()
    repo,snapshot=layout(cache,lock);marker=cache/'verified-model.json';identity=fingerprint()
    try:stats=file_stats(snapshot,lock)
    except (OSError,ValueError):stats=None
    previous=None
    try:previous=json.loads(marker.read_text())
    except (OSError,ValueError):pass
    if not force_verify and stats is not None and previous and previous.get('identity')==identity and previous.get('files')==stats:
        mode='unchanged-verified-artifact-stats'
    else:
        if stats is None:
            if not allow_download:raise RuntimeError('Pinned model is missing; run the hashed installation script')
            # Only the installation process is allowed online; runtime always stays offline.
            os.environ['HF_HUB_OFFLINE']='0'
            os.environ['HF_HUB_DISABLE_PROGRESS_BARS']='1'
            os.environ.setdefault('HF_HOME',str(cache.parent/'hub-runtime'))
            os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN']='1'
            from huggingface_hub import snapshot_download
            with contextlib.redirect_stderr(io.StringIO()),contextlib.redirect_stdout(io.StringIO()):
                selected=snapshot_download(repo_id=lock['repository'],revision=lock['revision'],
                    cache_dir=str(cache),allow_patterns=list(lock['files']),local_files_only=False,token=False)
            if Path(selected).resolve()!=snapshot.resolve():raise RuntimeError('Hub selected an unexpected model revision')
        verify_files(snapshot,lock)
        stats=file_stats(snapshot,lock)
        atomic_text(marker,json.dumps({'schema_version':1,'identity':identity,'files':stats},sort_keys=True,indent=2)+'\n')
        mode='all-locked-artifact-digests'
    # This is a truthful local alias to the verified immutable snapshot, not a claim
    # about the remote main branch. Runtime offline mode prevents re-resolution.
    atomic_text(repo/'refs/main',lock['revision'])
    return {'success':True,'repository':lock['repository'],'model':lock['model'],**identity,
            'cache_dir':str(cache),'snapshot_dir':str(snapshot),'verified_file_count':len(lock['files']),
            'verification_mode':mode,'runtime_offline':True}


def configure_runtime(environment):
    lock=read_lock()
    if environment.get('BASIC_MEMORY_SEMANTIC_EMBEDDING_MODEL',lock['model'])!=lock['model']:
        raise ValueError('Embedding model changes require an explicit immutable model lock migration')
    if int(environment.get('BASIC_MEMORY_SEMANTIC_EMBEDDING_DIMENSIONS',lock['dimensions']))!=lock['dimensions']:
        raise ValueError('Embedding dimensions differ from the immutable model lock')
    result=ensure_model(environment.get('BASIC_MEMORY_PINNED_MODEL_CACHE'),allow_download=False,force_verify=False)
    environment['FASTEMBED_CACHE_PATH']=result['cache_dir']
    environment['BASIC_MEMORY_SEMANTIC_EMBEDDING_CACHE_DIR']=result['cache_dir']
    environment['HF_HUB_OFFLINE']='1'
    environment['HF_HUB_DISABLE_PROGRESS_BARS']='1'
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir');parser.add_argument('--fingerprint',action='store_true')
    parser.add_argument('--offline',action='store_true');parser.add_argument('--reuse-verified',action='store_true')
    args=parser.parse_args()
    try:result=fingerprint() if args.fingerprint else ensure_model(args.cache_dir,allow_download=not args.offline,force_verify=not args.reuse_verified)
    except Exception as error:
        print(json.dumps({'success':False,'error_type':type(error).__name__}));return 1
    print(json.dumps(result,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
