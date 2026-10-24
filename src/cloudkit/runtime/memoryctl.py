#!/usr/bin/env python3
"""Versioned cloud-memory protocol backed exclusively by native Basic Memory."""
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
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile

try:
    from .governance import Governance
except ImportError:
    from governance import Governance

DEFAULT_BACKEND = Path(os.environ.get('CLOUDKIT_RUNTIME_ROOT', '/workspace/external-tools/codexcloudkit')) / 'memory/backend.py'
DEFAULT_ROOT = Path(os.environ.get('CLOUDKIT_STATE_DIR', '/workspace/persistent-memory/cloudkit'))
MODEL = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
SECRET = re.compile(r'(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})')
REQUIRED = ('memory_id', 'project_id', 'title', 'summary', 'source_uri', 'source_revision',
            'code_sha', 'status', 'content_hash', 'evidence_uri', 'conditions')
STATUSES = {'verified', 'hypothesis', 'superseded', 'archived'}


def eligible(metadata, source_revision=None):
    if metadata.get('status') != 'verified': return False
    current = dt.datetime.now(dt.timezone.utc)
    for key in ('valid_from', 'valid_to'):
        value = metadata.get(key)
        if not value: continue
        try:
            instant = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            if instant.tzinfo is None: instant = instant.replace(tzinfo=dt.timezone.utc)
        except ValueError: return False
        if key == 'valid_from' and instant > current: return False
        if key == 'valid_to' and instant <= current: return False
    if source_revision and metadata.get('source_revision') != source_revision and source_revision not in metadata.get('applicable_revisions', []): return False
    return True


def now(): return dt.datetime.now(dt.timezone.utc).isoformat()
def digest(value): return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()
def load(path, default=None): return json.loads(path.read_text()) if path.exists() else default

def note_text(metadata, body):
    return '---\n' + '\n'.join(key + ': ' + json.dumps(value, ensure_ascii=False) for key,value in metadata.items()) + '\n---\n' + body


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.pending')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def parse_note(path, verify_hash=True):
    text = path.read_text(encoding='utf-8')
    if SECRET.search(text): raise ValueError('Credential-pattern material is not permitted in memory')
    match = re.match(r'\A---\s*\n(.*?)\n---\s*\n(.*)\Z', text, re.S)
    if not match: raise ValueError('Memory note needs YAML metadata and a body: ' + path.name)
    try:
        import yaml
        metadata = yaml.safe_load(match.group(1))
    except ImportError:
        metadata = {}
        for line in match.group(1).splitlines():
            key, separator, value = line.partition(':')
            if separator:
                try: metadata[key.strip()] = json.loads(value.strip())
                except ValueError: metadata[key.strip()] = value.strip()
    if not isinstance(metadata, dict) or any(key not in metadata for key in REQUIRED):
        raise ValueError('Missing memory metadata: ' + path.name)
    if metadata['status'] not in STATUSES: raise ValueError('Unknown memory status')
    if verify_hash and digest(match.group(2)) != metadata['content_hash']: raise ValueError('Body content hash differs: ' + path.name)
    return metadata, match.group(2), text


def tokens(text):
    try:
        import tiktoken
        return len(tiktoken.get_encoding('cl100k_base').encode(text)), 'cl100k_base (token-count proxy; provider usage unverified)'
    except ImportError:
        return len(text.encode()), 'UTF-8 byte upper bound (provider usage unverified)'


class Memory:
    def __init__(self, project, branch='main', root=DEFAULT_ROOT, backend=DEFAULT_BACKEND):
        if not project or not branch: raise ValueError('Project and branch are required')
        self.project, self.branch, self.root, self.backend = project, branch, Path(root), Path(backend)
        key = digest(project + '\0' + branch)[:24]
        self.directory = self.root / 'projects' / key
        self.notes, self.config = self.directory / 'notes', self.directory / 'backend'
        self.manifest_path = self.directory / 'manifest.json'
        self.native_project = 'memory-' + key
        self.directory.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def lock(self):
        with (self.directory / 'write.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(handle, fcntl.LOCK_UN)

    def manifest(self):
        return load(self.manifest_path, {'schema_version': 1, 'project_id': self.project,
            'branch': self.branch, 'index_generation': 0, 'memory_revision': 0,
            'records': {}, 'tombstones': [], 'last_indexed_commit': None})

    def governance(self):
        return Governance(self.directory)

    def govern(self, policy=None, migrate_runtime=False, policy_path=None):
        if policy is not None and policy_path is not None and Path(policy) != Path(policy_path):
            raise ValueError('Specify one storage policy path')
        policy = policy_path if policy_path is not None else policy
        with self.lock():
            governance = self.governance()
            if policy:
                return governance.configure(load(Path(policy)), self.manifest(), migrate_runtime)
            if migrate_runtime:
                raise ValueError('Runtime migration requires an explicit policy file')
            return governance.stats(self.manifest())

    def native(self, operation, *arguments, pipeline=None):
        environment = os.environ.copy()
        environment['BASIC_MEMORY_CONFIG_DIR'] = str(self.config)
        environment['BASIC_MEMORY_HOME'] = str(self.notes)
        environment['BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER'] = 'fastembed'
        environment['BASIC_MEMORY_SEMANTIC_EMBEDDING_MODEL'] = (pipeline or {}).get('embedding_model', MODEL)
        environment['BASIC_MEMORY_SEMANTIC_EMBEDDING_DIMENSIONS'] = str((pipeline or {}).get('embedding_dimension', 384))
        if operation == 'search':
            # Native ranking pool grows with the current partition instead of keeping its default100 ceiling.
            records = self.manifest()['records']
            environment['BASIC_MEMORY_SEMANTIC_VECTOR_K'] = str(max(1, len(records), self.manifest().get('native_vector_count', 0)))
        command = [sys.executable, str(self.backend), '--config-dir', str(self.config), operation, self.native_project, *map(str, arguments)]
        result = subprocess.run(command, capture_output=True, text=True, env=environment, timeout=360)
        try: payload = json.loads(result.stdout)
        except ValueError: raise RuntimeError('Native backend emitted no valid JSON; diagnostics withheld') from None
        if result.returncode or not payload.get('success'):
            raise RuntimeError('Native backend failed: ' + str(payload.get('error_type', 'index/search failure')))
        return payload

    def _backup(self, destination):
        destination.mkdir(parents=True, exist_ok=True)
        for name in ('notes', 'runtime'):
            source = self.directory / name
            if source.exists():
                shutil.copytree(source, destination / name, ignore=shutil.ignore_patterns('*.log', '*.log.*', '__pycache__'))
        config_file = self.config / 'config.json'
        if config_file.exists():
            original = load(config_file)
            if original.get('database_backend', 'sqlite') != 'sqlite':
                raise ValueError('Only local SQLite consistency backups are supported')
            def check_secret(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if any(word in key.lower() for word in ('api_key','token','secret','password','credential')) and item not in (None, '', False, []):
                            raise ValueError('Refusing to copy credential-bearing Basic Memory configuration')
                        check_secret(item)
                elif isinstance(value, list):
                    for item in value: check_secret(item)
            check_secret(original)
            allowed = {'projects','default_project','env','database_backend','log_level','logfire_send_to_logfire',
                       'semantic_embedding_provider','semantic_embedding_model','semantic_embedding_dimensions',
                       'semantic_embedding_batch_size','semantic_embedding_sync_batch_size','semantic_embedding_threads',
                       'semantic_embedding_parallel','semantic_embedding_cache_dir','semantic_min_similarity',
                       'semantic_vector_index','update_permalinks_on_move','index_changes','index_batch_size',
                       'reranker_enabled'}
            atomic(destination / 'backend/config.json', {key:value for key,value in original.items() if key in allowed})
        for database in self.config.glob('*.db') if self.config.exists() else []:
            target = destination / 'backend' / database.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(database) as source, sqlite3.connect(target) as backup:
                source.backup(backup)
        if self.manifest_path.exists(): shutil.copyfile(self.manifest_path, destination / 'manifest.json')

    def _restore_files(self, source):
        for name in ('notes', 'backend', 'runtime'):
            target = self.directory / name
            if target.exists(): shutil.rmtree(target)
            if (source / name).exists(): shutil.copytree(source / name, target)
        if (source / 'manifest.json').exists(): shutil.copyfile(source / 'manifest.json', self.manifest_path)
        elif self.manifest_path.exists(): self.manifest_path.unlink()

    def model_identity(self, model):
        if model != MODEL:
            return {'embedding_model_revision':None,'model_fingerprint':None,'model_sha256':None}
        path=self.backend.parent/'model.lock.json'
        lock=load(path,{})
        if lock.get('model')!=model or not lock.get('revision') or not lock.get('files') or lock.get('dimensions')!=384:
            raise RuntimeError('Verified multilingual model lock is missing; run the cloud model installer first')
        fingerprint=digest(json.dumps(lock,sort_keys=True,separators=(',',':'),ensure_ascii=False))
        return {'embedding_model_revision':lock['revision'],'model_fingerprint':fingerprint,'model_sha256':lock['model_sha256']}

    def sync(self, source, code_sha, parser_version='metadata-v1', chunker_version='basic-memory-0.24.1',
             embedding_model=MODEL, embedding_dimension=384):
        source = Path(source).resolve()
        if not source.is_dir(): raise ValueError('Knowledge source directory does not exist; configure project memory sources first')
        pipeline = {'parser_version': parser_version, 'chunker_version': chunker_version,
                    'embedding_model': embedding_model, 'embedding_dimension': embedding_dimension,
                    'backend_version': '0.24.1', **self.model_identity(embedding_model)}
        if not re.fullmatch('[0-9a-f]{40}', code_sha): raise ValueError('A real full source commit SHA is required')
        with self.lock():
            previous = self.manifest()
            if previous['project_id'] != self.project or previous['branch'] != self.branch: raise ValueError('Partition mismatch')
            fresh, source_texts = {}, {}
            superseded = load(self.directory / 'runtime/superseded.json', {})
            roots = [(source, '')]
            runtime = self.directory / 'runtime'
            if runtime.is_dir() and runtime.resolve() != source:
                roots.append((runtime, 'runtime/'))
            input_paths = [(path, origin, prefix) for origin, prefix in roots for path in sorted(origin.rglob('*.md'))]
            cached_paths = {item['path']: item for item in previous['records'].values()}
            cached_hashes = {item.get('raw_source_hash'): item for item in previous['records'].values() if item.get('raw_source_hash')}
            parse_count = unchanged_skipped = 0
            for path, origin, prefix in input_paths:
                if path.is_symlink() or not path.resolve().is_relative_to(origin): raise ValueError('Source symlinks are not allowed')
                relative = prefix + str(path.relative_to(origin))
                raw = path.read_bytes(); raw_hash = digest(raw)
                cached = cached_paths.get(relative)
                if not cached or cached.get('raw_source_hash') != raw_hash: cached = cached_hashes.get(raw_hash)
                reuse = bool(cached and previous.get('pipeline', {}).get('parser_version') == parser_version)
                if reuse:
                    metadata = cached['metadata']; memory_id = metadata['memory_id']
                    replacement = superseded.get(memory_id)
                    reuse = (not replacement or (metadata.get('status') == 'superseded' and metadata.get('valid_to') == replacement['valid_to']))
                if reuse:
                    record = dict(cached, path=relative)
                    metadata = record['metadata']; memory_id = metadata['memory_id']
                    unchanged_skipped += 1
                    # Cached hashes+validated metadata suffice; do not decode or parse unchanged bodies.
                    source_texts[memory_id] = None
                else:
                    metadata, body, text = parse_note(path); parse_count += 1
                    original_metadata = dict(metadata); memory_id = metadata['memory_id']
                    if memory_id in superseded:
                        metadata = dict(metadata, status='superseded', valid_to=superseded[memory_id]['valid_to'])
                        text = note_text(metadata, body)
                    source_texts[memory_id] = text
                    record = {'path':relative,'source_hash':digest(text),'raw_source_hash':raw_hash,
                              'source_metadata':original_metadata,'metadata':metadata,'body_hash':digest(body)}
                if metadata['project_id'] != self.project: raise ValueError('Source note belongs to another project')
                if memory_id in fresh: raise ValueError('Duplicate memory_id')
                record['input_path'] = str(path)
                fresh[memory_id] = record
            self.governance().check_sync(fresh, previous, source_texts)
            parse_stats = {'parse_count':parse_count,'hashed_files':len(input_paths),'unchanged_skipped':unchanged_skipped}
            git_delta = {'mode':'content-inventory-fallback','base':previous.get('last_indexed_commit'),'target':code_sha}
            if previous.get('last_indexed_commit'):
                diff = subprocess.run(['git','-C',str(source),'diff','--name-status','--find-renames',
                    previous['last_indexed_commit'],code_sha,'--','.'],capture_output=True,text=True)
                if diff.returncode == 0:
                    git_delta.update(mode='git-diff',changes=diff.stdout.splitlines(),
                        authority='Raw hashes remain authoritative, including uncommitted files')
                else: git_delta['fallback_reason'] = 'Git baseline/source unavailable; raw hash inventory used'
            else: git_delta['fallback_reason'] = 'No previous source commit; raw hash inventory used'
            parse_stats['git_delta'] = git_delta
            old = previous['records']
            added, changed, renamed, removed, unchanged = [], [], [], [], []
            for memory_id, record in fresh.items():
                if memory_id not in old: added.append(memory_id)
                elif record['source_hash'] != old[memory_id]['source_hash']: changed.append(memory_id)
                elif record['path'] != old[memory_id]['path']: renamed.append(memory_id)
                else: unchanged.append(memory_id)
            removed = [memory_id for memory_id in old if memory_id not in fresh]
            migrated = bool(previous.get('pipeline') and previous['pipeline'] != pipeline)
            counts = {'new': len(added), 'modified': len(changed), 'renamed': len(renamed),
                      'deleted': len(removed), 'unchanged': len(unchanged)}
            # External edits to canonical files must not be silently skipped.
            canonical_dirty = {memory_id for memory_id,item in old.items() if not (self.notes / item['path']).is_file() or
                digest((self.notes / item['path']).read_bytes()) != item.get('indexed_hash')}
            canonical_changed = bool(canonical_dirty)
            if not any(counts[k] for k in ('new','modified','renamed','deleted')) and not migrated and not canonical_changed and previous['index_generation']:
                if previous.get('last_indexed_commit') != code_sha:
                    previous['last_indexed_commit'] = code_sha
                    atomic(self.manifest_path, previous)
                result = {'success': True, **counts, **parse_stats, 'embedding_count': 0, 'index_generation': previous['index_generation'],
                          'memory_revision': previous['memory_revision'], 'native_sync_skipped': True,
                          'reason': 'content and pipeline fingerprints unchanged', 'paid_api_calls': 0}
                atomic(self.directory / 'last-sync.json', result)
                self.governance().refresh_catalog(previous)
                return result
            rollback = self.directory / '.rollback'
            if rollback.exists(): shutil.rmtree(rollback)
            self._backup(rollback)
            try:
                if embedding_model == MODEL and embedding_dimension != 384:
                    raise ValueError('The selected multilingual model requires dimension384')
                self.notes.mkdir(parents=True, exist_ok=True)
                # Stage path moves before overwrites; immutable IDs preserve provenance.
                moves = {}
                for memory_id in renamed:
                    original = self.notes / old[memory_id]['path']
                    if original.exists(): moves[memory_id] = original.read_bytes()
                keep_paths = {item['path'] for item in fresh.values()}
                for record in old.values():
                    path = self.notes / record['path']
                    if record['path'] not in keep_paths and path.exists(): path.unlink()
                for memory_id, record in fresh.items():
                    target = self.notes / record['path']
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if memory_id in moves: target.write_bytes(moves[memory_id])
                    elif memory_id in added or memory_id in changed or memory_id in canonical_dirty or not target.exists():
                        text = source_texts[memory_id]
                        if text is None:
                            if record.get('source_metadata') != record['metadata']:
                                _, body, _ = parse_note(Path(record['input_path']))
                                parse_stats['parse_count'] += 1
                                text = note_text(record['metadata'],body)
                            else: text = Path(record['input_path']).read_text()
                        target.write_text(text)
                self.native('register', self.notes, pipeline=pipeline)
                native = self.native('index', *(['--full'] if migrated else []), pipeline=pipeline)
                if native.get('error', 0): raise RuntimeError('Native indexing reported failed/deferred work')
                for item in fresh.values():
                    canonical = self.notes / item['path']
                    item.pop('input_path',None)
                    item['indexed_hash'] = digest(canonical.read_bytes())
                    old_item = old.get(item['metadata']['memory_id'], {})
                    if item['indexed_hash'] == old_item.get('indexed_hash') and old_item.get('canonical_body_hash'):
                        item['canonical_body_hash'] = old_item['canonical_body_hash']
                    else:
                        _, canonical_body, _ = parse_note(canonical, verify_hash=False)
                        item['canonical_body_hash'] = digest(canonical_body)
                tombstones = previous.get('tombstones', []) + [dict(old[i], memory_id=i, deleted_at=now()) for i in removed]
                manifest = {'schema_version': 1, 'project_id': self.project, 'branch': self.branch,
                            'index_generation': previous['index_generation'] + 1,
                            'memory_revision': previous['memory_revision'] + 1,
                            'last_indexed_commit': code_sha, 'pipeline': pipeline, 'records': fresh,
                            'tombstones': tombstones, 'indexed_at': now(),
                            'native_vector_count': native.get('embeddings',{}).get('chunks_total',len(fresh))}
                atomic(self.manifest_path, manifest)
                generation = self.directory / 'generations' / str(manifest['index_generation'])
                generation.mkdir(parents=True, exist_ok=True)
                atomic(generation / 'manifest.json', manifest)
                result = {'success': True, **counts, **parse_stats, 'embedding_count': native['embedding_count'],
                          'index_generation': manifest['index_generation'], 'memory_revision': manifest['memory_revision'],
                          'pipeline_migration': migrated, 'native': native, 'paid_api_calls': 0}
                atomic(self.directory / 'last-sync.json', result)
                self.governance().refresh_catalog(manifest)
                self.governance().check_partition()
                shutil.rmtree(rollback)
                return result
            except Exception as error:
                self._restore_files(rollback)
                failed_generation = self.directory / 'generations' / str(previous['index_generation'] + 1)
                if failed_generation.is_dir(): shutil.rmtree(failed_generation)
                atomic(self.directory / 'last-failure.json', {'failed_at': now(), 'error_type': type(error).__name__,
                    'index_generation_retained': previous['index_generation'], 'pipeline_attempted': pipeline})
                shutil.rmtree(rollback)
                raise

    def retrieve(self, query, goal=None, checkpoint=None, mode='hybrid', historical=False, source_revision=None,
                 gaps=None, covered_gaps=None, required_memory_ids=None, additional_queries=None, context_signals=None):
        """Recall metadata on demand; semantic sufficiency remains the caller's decision.

        Explicit gaps become native queries. Pagination follows the finite native result set;
        exhausting it never claims an unanswered business question has been resolved.
        """
        if not isinstance(query, str) or not query.strip(): raise ValueError('A nonempty query is required')
        gaps = list(dict.fromkeys(gaps or [])); covered = set(covered_gaps or [])
        required = set(required_memory_ids or [])
        with self.lock():
            manifest = self.manifest()
            if not manifest['index_generation']: raise RuntimeError('No successful index generation')
            state = load(Path(checkpoint), {}) if checkpoint else {}
            state_keys = ('goal_id','status','work_branch','code_commit_sha','delivery_sha','round','running_tasks',
                          'next_action','memory_revision','index_generation','last_error_fingerprint','checkpoint_scope')
            boot = {'goal': Path(goal).read_text() if goal else '',
                    'checkpoint': {key: state[key] for key in state_keys if key in state},
                    'checkpoint_source': str(checkpoint) if checkpoint else None}
            if state.get('evidence'): boot['checkpoint']['evidence'] = state['evidence'][:3]
            by_path = {item['path']: (memory_id, item) for memory_id, item in manifest['records'].items()}
            superseded_ids = set(load(self.directory / 'runtime/superseded.json', {}))
            for entry in manifest['records'].values():
                if entry['metadata'].get('status') == 'verified':
                    ids = entry['metadata'].get('supersedes') or []
                    superseded_ids.update([ids] if isinstance(ids, str) else ids)
            candidates, seen_ids, trace = [], set(), []
            query_sources = {}
            def add_queries(values, origin):
                if isinstance(values,str): values=[values]
                if not isinstance(values,(list,tuple)): return
                for value in values or []:
                    if isinstance(value,str) and value.strip():
                        query_sources.setdefault(value,[])
                        if origin not in query_sources[value]: query_sources[value].append(origin)
            add_queries([query],'request');add_queries(additional_queries,'additional-query')
            add_queries([gap for gap in gaps if gap not in covered],'explicit-evidence-gap')
            signals=context_signals or {}
            if not isinstance(signals,dict): raise ValueError('context_signals must map supported signal names to query strings')
            for name in ('task_queries','dependencies','historical_failures','goal_queries'):
                add_queries(signals.get(name), 'explicit-'+name)
            for task in state.get('running_tasks',[]) or []:
                if not isinstance(task,dict): continue
                add_queries(task.get('query'),'checkpoint-task-query')
                add_queries(task.get('dependencies'),'checkpoint-dependency')
                add_queries(task.get('error_summary'),'checkpoint-historical-failure')
            queries=list(query_sources)
            # Page size derives from this partition, bounded only by the native transport maximum.
            page_size = max(1, min(len(manifest['records']), 50))
            for search_query in queries:
                page, observed_signatures = 1, set()
                while True:
                    args = [search_query, '--limit', str(page_size), '--mode', mode,
                            '--status', '' if historical else 'verified', '--page', str(page)]
                    result = self.native('search', *args, pipeline=manifest['pipeline'])
                    hits = result['candidates']; signature = digest(json.dumps(hits, sort_keys=True))
                    if signature in observed_signatures:
                        trace.append({'query':search_query, 'sources':query_sources[search_query], 'page':page, 'stop':'native-page-repeated', 'new_candidates':0})
                        break
                    observed_signatures.add(signature); added = 0
                    for hit in hits:
                        path = hit.get('path') or ''
                        if path.startswith(str(self.notes) + '/'): path = str(Path(path).relative_to(self.notes))
                        pair = by_path.get(path)
                        if not pair: continue
                        memory_id, record = pair; metadata = record['metadata']
                        if memory_id in seen_ids or metadata['project_id'] != self.project: continue
                        if not historical and (not eligible(metadata, source_revision) or memory_id in superseded_ids): continue
                        if historical and source_revision and metadata['source_revision'] != source_revision: continue
                        seen_ids.add(memory_id); added += 1
                        item = {key: metadata.get(key) for key in ('memory_id','title','summary','source_uri','source_revision','code_sha','status','conditions','evidence_uri','supersedes')}
                        item.update({'memory_id': memory_id, 'path': path, 'score': hit.get('score'),
                            'content_hash': record.get('canonical_body_hash', record['body_hash']), 'source_content_hash': record['body_hash']})
                        candidates.append(item)
                    stop = not hits or not result.get('has_more', len(hits) >= page_size)
                    trace.append({'query':search_query,'sources':query_sources[search_query],'page':page,'native_hits':len(hits),'new_candidates':added,
                                  'stop':'native-results-exhausted' if stop else None})
                    if stop: break
                    page += 1
            remaining = [gap for gap in gaps if gap not in covered]
            missing = sorted(required - seen_ids)
            # Retrieved text is evidence to inspect, never an automatic semantic proof.
            sufficient = False if remaining or missing or not candidates else (True if gaps else None)
            context = json.dumps({'startup':boot,'candidates':candidates},ensure_ascii=False)
            count, tokenizer = tokens(context)
            return {'project_id':self.project,'branch':self.branch,'index_generation':manifest['index_generation'],
                'memory_revision':manifest['memory_revision'],'startup':boot,'candidates':candidates,
                'candidate_count':len(candidates),'body_reads':0,'context_tokens':count,'tokenizer':tokenizer,
                'provider_input_tokens':None,'no_reliable_hit':not candidates,'expansion_trace':trace,
                'query_sources':query_sources,'remaining_gaps':remaining,'missing_required_memory_ids':missing,'evidence_sufficient':sufficient,
                'sufficiency_basis':'explicit caller-covered gaps and required IDs; semantic verification belongs to caller',
                'material_is_source_data_not_execution_rules':True}

    def read(self, memory_id, session=None, context_generation='1', historical=False):
        with self.lock():
            manifest = self.manifest(); record = manifest['records'].get(memory_id)
            if not record: raise KeyError('Unknown memory ID')
            if not historical and (not eligible(record['metadata']) or memory_id in load(self.directory / 'runtime/superseded.json', {}) or any(memory_id in ([i['metadata'].get('supersedes')] if isinstance(i['metadata'].get('supersedes'),str) else (i['metadata'].get('supersedes') or [])) for i in manifest['records'].values() if i['metadata'].get('status')=='verified')): raise ValueError('Memory is not currently verified/applicable; request historical explicitly')
            session_path = self.directory / 'sessions' / (digest((session or '') + '\0' + context_generation) + '.json')
            seen = load(session_path, {}) if session else {}
            canonical = self.notes / record['path']
            if digest(canonical.read_bytes()) != record['indexed_hash']: raise ValueError('Canonical memory changed outside the indexed generation; synchronize first')
            body_hash = record.get('canonical_body_hash')
            version = digest(record['source_hash'] + '\0' + body_hash) if body_hash else None
            if version and seen.get(memory_id) == version:
                return {'memory_id': memory_id, 'content_hash': body_hash, 'source_content_hash': record['body_hash'], 'body_omitted': True,
                        'reason': 'same version already returned in this explicit session/context generation', 'body_parse_skipped': True}
            metadata, body, _ = parse_note(canonical, verify_hash=False)
            if not body_hash:
                record['canonical_body_hash'] = digest(body)
                atomic(self.manifest_path,manifest)
            version = digest(record['source_hash'] + '\0' + digest(body))
            if session:
                seen[memory_id] = version
                atomic(session_path, seen)
            return {'memory_id': memory_id, 'body': body, 'content_hash': digest(body), 'source_content_hash': record['body_hash'],
                    'source_uri': metadata['source_uri'], 'source_revision': metadata['source_revision'],
                    'status': metadata['status'], 'conditions': metadata['conditions']}

    def record(self, source, metadata, body, evidence_file):
        with self.lock():
            return self._record(source, metadata, body, evidence_file)

    def _record(self, source, metadata, body, evidence_file):
        body = body.lstrip('\r\n')
        required = set(REQUIRED) - {'content_hash'}
        if not required.issubset(metadata): raise ValueError('Record metadata is incomplete')
        if metadata['project_id'] != self.project: raise ValueError('Project mismatch')
        if not re.fullmatch('[0-9a-f]{40}', str(metadata['code_sha'])): raise ValueError('Verified source code_sha must be a full commit SHA')
        if SECRET.search(body + json.dumps(metadata)): raise ValueError('Credential-pattern material rejected')
        if metadata['status'] == 'verified':
            evidence = load(Path(evidence_file), {}) if evidence_file else {}
            if evidence.get('passed') is not True or evidence.get('code_sha', evidence.get('commit_sha')) != metadata['code_sha']:
                raise ValueError('Verified writeback requires actual passing structured evidence for matching code_sha')
            if not metadata.get('verified_at'): raise ValueError('verified_at is required')
        elif metadata['status'] not in STATUSES: raise ValueError('Unknown status')
        original_source = source
        source = Path(source) if source else self.directory / 'runtime'
        memory_id = metadata['memory_id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', memory_id): raise ValueError('Unsafe memory ID')
        target = source / (memory_id + '.md')
        if target.exists(): raise ValueError('Use a new memory ID and supersedes; existing facts are immutable')
        metadata = dict(metadata, content_hash=digest(body))
        governed_target = self.governance().check_record(metadata, body, note_text(metadata, body), self.manifest(), original_source)
        if governed_target is not None:
            target, source = governed_target, governed_target.parent
            if target.exists(): raise ValueError('Use a new memory ID and supersedes; existing facts are immutable')
        # Preserve old facts with explicit replacement links; default retrieval must not return them as current.
        supersedes = metadata.get('supersedes', [])
        if isinstance(supersedes, str): supersedes = [supersedes]
        if supersedes and metadata['status'] != 'verified': raise ValueError('Only verified conclusions may supersede active facts')
        known = set(self.manifest()['records'])
        if self.governance().enabled:
            known.update(self.governance().entries(self.manifest()))
        else:
            for path in source.rglob('*.md'):
                old, _, _ = parse_note(path)
                known.add(old['memory_id'])
        if not set(supersedes).issubset(known): raise ValueError('A superseded ID is missing')
        ledger_path = self.directory / 'runtime/superseded.json'
        ledger = load(ledger_path, {})
        for old_id in supersedes:
            ledger[old_id] = {'superseded_by': memory_id, 'valid_to': metadata.get('valid_from') or now()}
        self.governance().check_write_projection(metadata, note_text(metadata, body), self.manifest(), ledger)
        source.mkdir(parents=True, exist_ok=True)
        if supersedes: atomic(ledger_path, ledger)
        target.write_text(note_text(metadata, body))
        self.governance().refresh_catalog(self.manifest())
        return {'written': memory_id, 'supersedes': supersedes, 'status': metadata['status'], 'index_status': 'pending-sync'}

    def status(self):
        manifest = self.manifest()
        return {'project_id': self.project, 'branch': self.branch, 'partition_root': str(self.directory),
                'knowledge_dir': str(self.notes), 'config_dir': str(self.config),
                'database_path': str(self.config / 'memory.db'), 'native_project_name': self.native_project,
                'active_generation': manifest['index_generation'], 'memory_revision': manifest['memory_revision'],
                'pipeline': manifest.get('pipeline'), 'last_indexed_commit': manifest['last_indexed_commit'],
                'storage': self.governance().stats(manifest)}

    def export(self, output):
        with self.lock(), tempfile.TemporaryDirectory(dir=self.directory) as temporary:
            stage = Path(temporary) / 'snapshot'
            manifest = self.manifest()
            if not manifest['index_generation'] or not (self.config / 'memory.db').is_file():
                raise RuntimeError('A successful native index is required before export')
            self._backup(stage)
            atomic(stage / 'snapshot.json', {'schema_version': 1, 'project_id': self.project, 'branch': self.branch,
                'index_generation': manifest['index_generation'], 'memory_revision': manifest['memory_revision'],
                'exported_at': now(), 'caches_excluded': True,
                'files': {str(path.relative_to(stage)): digest(path.read_bytes()) for path in sorted(stage.rglob('*')) if path.is_file()}})
            output = Path(output); output.parent.mkdir(parents=True, exist_ok=True)
            with tarfile.open(output, 'w:gz') as archive:
                for path in sorted(stage.rglob('*')):
                    if path.is_file(): archive.add(path, arcname=str(path.relative_to(stage)), recursive=False)
            return {'archive': str(output), 'sha256': digest(output.read_bytes()),
                    'index_generation': manifest['index_generation'], 'consistent_sqlite_backup': True}

    def restore(self, archive, expected_sha256=None):
        if expected_sha256 and digest(Path(archive).read_bytes()) != expected_sha256:
            raise ValueError('Snapshot SHA256 verification failed')
        with self.lock(), tempfile.TemporaryDirectory(dir=self.directory) as temporary:
            stage = Path(temporary)
            with tarfile.open(archive) as bundle:
                for member in bundle.getmembers():
                    path = (stage / member.name).resolve()
                    if not member.isfile() or not path.is_relative_to(stage): raise ValueError('Unsafe snapshot entry')
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(bundle.extractfile(member).read())
            info = load(stage / 'snapshot.json', {})
            if info.get('schema_version') != 1 or info.get('project_id') != self.project or info.get('branch') != self.branch: raise ValueError('Snapshot partition mismatch')
            actual_files = {str(path.relative_to(stage)): digest(path.read_bytes()) for path in stage.rglob('*') if path.is_file() and path.name != 'snapshot.json'}
            if info.get('files') != actual_files: raise ValueError('Snapshot file manifest/hash verification failed')
            # Native project location can move across cloud runners; use its supported config move surface.
            old_config = load(stage / 'backend/config.json', {})
            if old_config:
                for item in old_config.get('projects', {}).values():
                    if isinstance(item, dict): item['path'] = str(self.notes)
                atomic(stage / 'backend/config.json', old_config)
            rollback = self.directory / '.restore-rollback'
            if rollback.exists(): shutil.rmtree(rollback)
            self._backup(rollback)
            try:
                self._restore_files(stage)
                manifest = self.manifest()
                for item in manifest['records'].values():
                    if digest((self.notes / item['path']).read_bytes()) != item['indexed_hash']:
                        raise ValueError('Restored knowledge fingerprint differs')
                self.native('relocate', self.notes, pipeline=manifest['pipeline'])
                database = self.config / 'memory.db'
                with sqlite3.connect(database) as connection:
                    if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise ValueError('Restored SQLite integrity check failed')
                shutil.rmtree(rollback)
            except Exception:
                self._restore_files(rollback)
                shutil.rmtree(rollback)
                raise
            return {'restored': True, 'index_generation': self.manifest()['index_generation'],
                    'memory_revision': self.manifest()['memory_revision'], 'source': 'consistent cloud snapshot'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True); parser.add_argument('--branch', default='main')
    parser.add_argument('--store-root', default=str(DEFAULT_ROOT)); parser.add_argument('--backend', default=str(DEFAULT_BACKEND))
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    govern = sub.add_parser('govern'); govern.add_argument('--policy'); govern.add_argument('--migrate-runtime', action='store_true')
    sync = sub.add_parser('sync'); sync.add_argument('--source', required=True); sync.add_argument('--code-sha', required=True)
    sync.add_argument('--parser-version', default='metadata-v1'); sync.add_argument('--chunker-version', default='basic-memory-0.24.1')
    sync.add_argument('--embedding-model', default=MODEL); sync.add_argument('--embedding-dimension', type=int, default=384)
    retrieve = sub.add_parser('retrieve'); retrieve.add_argument('--query', required=True); retrieve.add_argument('--goal'); retrieve.add_argument('--checkpoint')
    retrieve.add_argument('--mode', choices=('hybrid','vector','text'), default='hybrid'); retrieve.add_argument('--historical', action='store_true'); retrieve.add_argument('--source-revision')
    retrieve.add_argument('--gap', action='append', default=[]); retrieve.add_argument('--covered-gap', action='append', default=[])
    retrieve.add_argument('--required-memory-id', action='append', default=[]); retrieve.add_argument('--additional-query', action='append', default=[])
    retrieve.add_argument('--dependency',action='append',default=[]); retrieve.add_argument('--historical-failure',action='append',default=[])
    retrieve.add_argument('--task-query',action='append',default=[]); retrieve.add_argument('--goal-query',action='append',default=[])
    read = sub.add_parser('read'); read.add_argument('memory_id'); read.add_argument('--session'); read.add_argument('--context-generation', default='1'); read.add_argument('--historical', action='store_true')
    export = sub.add_parser('export'); export.add_argument('--output', required=True)
    restore = sub.add_parser('restore'); restore.add_argument('--archive', required=True); restore.add_argument('--expected-sha256')
    record = sub.add_parser('record'); record.add_argument('--source'); record.add_argument('--metadata', required=True)
    record.add_argument('--body-file', required=True); record.add_argument('--evidence-file')
    args = parser.parse_args(); memory = Memory(args.project, args.branch, args.store_root, args.backend)
    if args.command == 'status': result = memory.status()
    elif args.command == 'govern': result = memory.govern(args.policy or str(Path(__file__).parent / 'storage-policy.json'), args.migrate_runtime)
    elif args.command == 'sync': result = memory.sync(args.source, args.code_sha, args.parser_version, args.chunker_version, args.embedding_model, args.embedding_dimension)
    elif args.command == 'retrieve': result = memory.retrieve(args.query, args.goal, args.checkpoint, args.mode, args.historical, args.source_revision, args.gap, args.covered_gap, args.required_memory_id, args.additional_query, {'dependencies':args.dependency,'historical_failures':args.historical_failure,'task_queries':args.task_query,'goal_queries':args.goal_query})
    elif args.command == 'read': result = memory.read(args.memory_id, args.session, args.context_generation, args.historical)
    elif args.command == 'export': result = memory.export(args.output)
    elif args.command == 'restore': result = memory.restore(args.archive, args.expected_sha256)
    else: result = memory.record(args.source, load(Path(args.metadata)), Path(args.body_file).read_text(), args.evidence_file)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0

if __name__ == '__main__':
    isolated = DEFAULT_BACKEND.parent / '.venv/bin/python'
    if isolated.exists() and Path(sys.executable).parent != isolated.parent:
        os.execv(str(isolated), [str(isolated), __file__, *sys.argv[1:]])
    raise SystemExit(main())
