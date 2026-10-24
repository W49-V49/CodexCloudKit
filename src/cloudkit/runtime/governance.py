"""Opt-in classified, bounded storage; recall remains evidence-driven and unbounded."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import stat

CATEGORIES = ('foundation', 'environment', 'tools', 'workflows', 'memory', 'failures', 'verification', 'project')
MAINTENANCE = 'Export a verified snapshot to configured private control storage; review obsolete generation/log copies with the maintenance plan. Never discard facts to satisfy a quota.'


def read_json(path, default):
    return json.loads(path.read_text()) if path.is_file() else default


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.pending')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def logical_bytes(directory):
    """Do not follow links or count files outside the partition."""
    total = 0
    for path in directory.rglob('*'):
        if path.relative_to(directory).parts[0] in {'.rollback', '.restore-rollback'}:
            continue
        try:
            info = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            # SQLite removes transient shared-memory/WAL files as a connection closes.
            # A vanished file consumes no persistent bytes; other I/O errors remain fatal.
            continue
        if stat.S_ISREG(info.st_mode):
            total += info.st_size
    return total


def classify(metadata):
    category = metadata.get('category')
    if category in CATEGORIES:
        return category, 'explicit-category'
    # Legacy records are classified from previously verified metadata, never full sessions.
    text = ' '.join(str(metadata.get(key, '')) for key in ('memory_id', 'title', 'kind')).lower()
    for category, words in (
        ('foundation', ('foundation', 'personal', '个人基础', '自定义指令')),
        ('failures', ('failure', 'repair', 'root-cause', '故障', '修复', 'readonly')),
        ('memory', ('memory', 'retrieval', 'index', '记忆', '检索', '索引')),
        ('verification', ('acceptance', 'test', '验收', '测试')),
        ('workflows', ('workflow', 'supervisor', 'dispatch', 'idle', 'runner', '调度', '续跑')),
        ('tools', ('mcp', 'skill', 'context7', 'serena', '工具', '技能', 'superpowers')),
        ('environment', ('environment', 'cloud', 'auth', 'subscription', '环境', '认证', '云端'))):
        if any(word in text for word in words):
            return category, 'legacy-metadata-rule'
    return 'project', 'legacy-project-fallback'


def metadata_header(path):
    """Read only a new/changed pending note's header, not its conversation body."""
    with path.open(encoding='utf-8') as stream:
        if stream.readline().strip() != '---':
            raise ValueError('Pending knowledge must have a metadata header')
        lines = []
        for line in stream:
            if line.strip() == '---':
                break
            lines.append(line)
            if sum(map(len, lines)) > 32768:
                raise ValueError('Pending knowledge metadata is too large')
    try:
        import yaml
        return yaml.safe_load(''.join(lines))
    except ImportError:
        result = {}
        for line in lines:
            key, separator, value = line.partition(':')
            if separator:
                try: result[key.strip()] = json.loads(value.strip())
                except ValueError: result[key.strip()] = value.strip()
        return result


class Governance:
    def __init__(self, directory):
        raw = Path(directory).absolute()
        for candidate in (raw.parent, raw, raw/'runtime', raw/'runtime/governance', raw/'runtime/governance/storage-policy.json', raw/'runtime/governance/catalog.json'):
            if candidate.is_symlink(): raise ValueError('Memory governance paths must remain inside their physical partition without symlinks')
        self.directory = raw.resolve()
        self.folder = self.directory / 'runtime/governance'
        self.policy_path = self.folder / 'storage-policy.json'
        self.catalog_path = self.folder / 'catalog.json'
        self.policy = read_json(self.policy_path, None)
        if self.policy is not None:
            self.validate_policy(self.policy)

    @staticmethod
    def validate_policy(policy):
        if policy.get('schema_version') != 1 or set(policy.get('categories', {})) != set(CATEGORIES):
            raise ValueError('Storage policy must define the eight versioned categories')
        limits = policy.get('limits', {})
        for key in ('partition_bytes', 'active_fact_bytes', 'max_active_facts', 'single_note_bytes'):
            if type(limits.get(key)) is not int or limits[key] <= 0:
                raise ValueError('Storage policy limits must be positive byte/count integers')
        for value in policy['categories'].values():
            if type(value.get('active_bytes')) is not int or value['active_bytes'] <= 0:
                raise ValueError('Each category needs a positive storage allocation')
        if limits['single_note_bytes'] > limits['active_fact_bytes'] or limits['active_fact_bytes'] > limits['partition_bytes']:
            raise ValueError('Storage allocations must fit within the partition')
        if sum(value['active_bytes'] for value in policy['categories'].values()) > limits['active_fact_bytes']:
            raise ValueError('Category allocations exceed the active-fact allocation')

    @property
    def enabled(self):
        return self.policy is not None

    def entries(self, manifest):
        entries = {}
        superseded = read_json(self.directory / 'runtime/superseded.json', {})
        for ident, record in manifest.get('records', {}).items():
            metadata = record['metadata']
            path = self.directory / 'notes' / record['path']
            category, reason = classify(metadata)
            entries[ident] = {'category': category, 'classification_reason': reason,
                             'status': 'superseded' if ident in superseded else metadata.get('status'), 'path': record['path'],
                             'bytes': path.stat().st_size if path.is_file() else 0,
                             'source_hash': record.get('raw_source_hash'), 'legacy': 'category' not in metadata,
                             'indexed': True, 'category_required': False}
        superseded = read_json(self.directory / 'runtime/superseded.json', {})
        # Pending writes count before indexing, preventing unsynced quota bypass.
        by_path = {record['path']: record for record in manifest.get('records', {}).values()}
        by_hash = {record.get('raw_source_hash'): record for record in manifest.get('records', {}).values() if record.get('raw_source_hash')}
        catalog = read_json(self.catalog_path, {}).get('records', {})
        pending_cache = {item['path']: (ident, item) for ident, item in catalog.items()}
        for path in sorted((self.directory / 'runtime').rglob('*.md')):
            if path.is_symlink():
                raise ValueError('Knowledge paths must not be symlinks')
            relative = 'runtime/' + path.relative_to(self.directory / 'runtime').as_posix()
            cached = by_path.get(relative)
            raw_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            cached = cached if cached and cached.get('raw_source_hash') == raw_hash else by_hash.get(raw_hash)
            if not cached and relative in pending_cache and pending_cache[relative][1].get('source_hash') == raw_hash:
                ident, item = pending_cache[relative]
                indexed = ident in manifest.get('records', {})
                entries[ident] = dict(item, status='superseded' if ident in superseded else item['status'],
                                      indexed=indexed, legacy=indexed and item.get('legacy', False),
                                      category_required=not indexed and item.get('classification_reason') != 'explicit-category')
                continue
            metadata = cached['metadata'] if cached and cached.get('raw_source_hash') == raw_hash else metadata_header(path)
            if not isinstance(metadata, dict) or not metadata.get('memory_id'):
                raise ValueError('Pending knowledge needs a stable memory ID')
            ident = metadata['memory_id']
            category, reason = classify(metadata)
            entries[ident] = {'category': category, 'classification_reason': reason,
                             'status': 'superseded' if ident in superseded else metadata.get('status'),
                             'path': relative, 'bytes': path.stat().st_size, 'source_hash': raw_hash,
                             'legacy': ident in manifest.get('records', {}) and 'category' not in metadata,
                             'indexed': ident in manifest.get('records', {}),
                             'category_required': ident not in manifest.get('records', {}) and 'category' not in metadata}
        return entries

    def stats(self, manifest):
        if not self.enabled:
            return {'enabled': False, 'note': 'Enable the project storage policy with memoryctl govern --policy FILE'}
        entries = self.entries(manifest)
        active = {key: item for key, item in entries.items() if item['status'] == 'verified'}
        categories = {category: {'active_bytes': sum(item['bytes'] for item in active.values() if item['category'] == category),
                                 'active_facts': sum(item['category'] == category for item in active.values()),
                                 'limit_bytes': self.policy['categories'][category]['active_bytes']} for category in CATEGORIES}
        usage = {'partition_bytes': logical_bytes(self.directory),
                 'active_fact_bytes': sum(item['bytes'] for item in active.values()), 'active_facts': len(active)}
        violations = self._violations(usage, categories)
        return {'enabled': True, 'policy_path': str(self.policy_path), 'catalog_path': str(self.catalog_path),
                'limits': self.policy['limits'], 'usage': usage, 'categories': categories,
                'violations': violations, 'recall_token_budget': None, 'recall_result_limit': None,
                'maintenance_plan': self.maintenance_plan(), 'over_limit_action': MAINTENANCE}

    def _violations(self, usage, categories):
        limits = self.policy['limits']; errors = []
        for field, limit in (('partition_bytes', 'partition_bytes'), ('active_fact_bytes', 'active_fact_bytes'), ('active_facts', 'max_active_facts')):
            if usage[field] > limits[limit]: errors.append({'scope': field, 'actual': usage[field], 'limit': limits[limit]})
        for category, item in categories.items():
            if item['active_bytes'] > item['limit_bytes']:
                errors.append({'scope': 'category:' + category, 'actual': item['active_bytes'], 'limit': item['limit_bytes']})
        return errors

    def maintenance_plan(self):
        generations = self.directory / 'generations'
        ordered = sorted((p for p in generations.iterdir() if p.is_dir() and p.name.isdigit()), key=lambda p: int(p.name)) if generations.exists() else []
        return {'action': 'plan-only; verify durable backup before removal',
                'retain_latest_generations': 2,
                'older_generation_candidates': [str(p.relative_to(self.directory)) for p in ordered[:-2]],
                'facts_deleted_automatically': False, 'logs_require_durable_inventory_before_cleanup': True}

    def refresh_catalog(self, manifest):
        if not self.enabled: return
        atomic(self.catalog_path, {'schema_version': 1, 'project_id': manifest['project_id'], 'branch': manifest['branch'],
                                  'categories': list(CATEGORIES), 'records': self.entries(manifest)})

    def configure(self, policy, manifest, migrate_runtime=False):
        self.validate_policy(policy)
        self.policy = policy
        atomic(self.policy_path, policy)
        self.refresh_catalog(manifest)
        migrated = []
        if migrate_runtime:
            for ident, record in manifest.get('records', {}).items():
                relative = record['path']
                if not relative.startswith('runtime/') or relative.startswith('runtime/knowledge/'):
                    continue
                source = self.directory / relative
                if not source.is_file(): continue
                if source.is_symlink(): raise ValueError('Legacy runtime source must not be a symlink')
                category = classify(record['metadata'])[0]
                target = self.target(category, ident)
                if target.exists(): raise ValueError('Classified migration target already exists; preserve both for diagnosis')
                if hashlib.sha256(source.read_bytes()).hexdigest() != record.get('raw_source_hash'):
                    raise ValueError('Legacy runtime fact changed since indexing; synchronize before migration')
                target.parent.mkdir(parents=True, exist_ok=True)
                source.rename(target)
                migrated.append({'memory_id': ident, 'from': relative, 'to': target.relative_to(self.directory).as_posix(),
                                 'source_hash_preserved': True})
        return {'configured': True, 'migrated_runtime': migrated, 'sync_required': bool(migrated), 'storage': self.stats(manifest)}

    def target(self, category, ident):
        if category not in CATEGORIES or not re.fullmatch(r'[A-Za-z0-9_-]+', str(ident)):
            raise ValueError('A registered category and safe stable memory ID are required')
        target = self.directory / 'runtime/knowledge' / category / (ident + '.md')
        for path in [target, *target.parents]:
            if path == self.directory: break
            if path.is_symlink(): raise ValueError('Classified knowledge paths must not be symlinks')
        if not target.resolve().is_relative_to(self.directory): raise ValueError('Classified knowledge path escapes partition')
        return target

    @staticmethod
    def validate_content(metadata, body):
        if len(str(metadata.get('summary', ''))) > 600:
            raise ValueError('Memory summaries must be concise (at most 600 characters), not whole context')
        try:
            structure = json.loads(body)
        except (ValueError, TypeError):
            structure = None
        messages = structure.get('messages') if isinstance(structure, dict) else structure
        if isinstance(messages, list) and any(isinstance(item, dict) and 'role' in item and 'content' in item for item in messages):
            raise ValueError('Structured conversation dumps must not enter the memory corpus')
        speakers = re.findall(r'(?mi)^\s*(user|assistant|system|用户|助手)[：:]', body)
        transcript = len(speakers) >= 3 or (len(speakers) >= 2 and len(set(value.lower() for value in speakers)) >= 2)
        if metadata.get('kind') in {'conversation', 'transcript', 'raw-log', 'session', 'full-context'} or transcript or re.search(r'<\|(?:im_start|start)\|>', body):
            raise ValueError('Whole conversations/raw sessions are not memory facts; write a verified concise conclusion')

    def check_record(self, metadata, body, serialized, manifest, source=None):
        if not self.enabled: return None
        category = metadata.get('category')
        if metadata['memory_id'] in self.entries(manifest):
            raise ValueError('Existing memory IDs are immutable across categories; use a new ID and supersedes')
        target = self.target(category, metadata['memory_id'])
        if source is not None and Path(source).resolve() != target.parent.resolve():
            raise ValueError('Classified writes use runtime/knowledge/<category>; arbitrary --source is not allowed')
        self.validate_content(metadata, body)
        size = len(serialized.encode())
        if size > self.policy['limits']['single_note_bytes']:
            raise ValueError('Single memory note exceeds storage allocation; summarize and link evidence')
        stats = self.stats(manifest)
        usage, categories = stats['usage'], stats['categories']
        usage['partition_bytes'] += size
        if metadata['status'] == 'verified':
            replaced = metadata.get('supersedes') or []
            replaced = [replaced] if isinstance(replaced, str) else replaced
            existing = self.entries(manifest)
            for ident in replaced:
                old = existing.get(ident)
                if old and old['status'] == 'verified':
                    usage['active_facts'] -= 1; usage['active_fact_bytes'] -= old['bytes']; categories[old['category']]['active_bytes'] -= old['bytes']
            usage['active_facts'] += 1; usage['active_fact_bytes'] += size
            categories[category]['active_bytes'] += size
        errors = self._violations(usage, categories)
        if errors: raise ValueError(json.dumps({'error': 'MEMORY_STORAGE_CAPACITY', 'violations': errors, 'maintenance': MAINTENANCE}, ensure_ascii=False))
        return target

    def check_write_projection(self, metadata, serialized, manifest, ledger):
        if not self.enabled: return
        entries = self.entries(manifest)
        for ident in ledger:
            if ident in entries: entries[ident] = dict(entries[ident], status='superseded')
        target = self.target(metadata['category'], metadata['memory_id'])
        entries[metadata['memory_id']] = {'category': metadata['category'], 'classification_reason': 'explicit-category',
            'status': metadata['status'], 'path': target.relative_to(self.directory).as_posix(),
            'bytes': len(serialized.encode()), 'source_hash': hashlib.sha256(serialized.encode()).hexdigest(), 'legacy': False,
            'indexed': False, 'category_required': False}
        catalog = {'schema_version': 1, 'project_id': manifest['project_id'], 'branch': manifest['branch'],
                   'categories': list(CATEGORIES), 'records': entries}
        encode = lambda value: (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
        projected = logical_bytes(self.directory) + len(serialized.encode())
        projected += len(encode(catalog)) - (self.catalog_path.stat().st_size if self.catalog_path.exists() else 0)
        ledger_path = self.directory/'runtime/superseded.json'
        if ledger:
            projected += len(encode(ledger)) - (ledger_path.stat().st_size if ledger_path.exists() else 0)
        if projected > self.policy['limits']['partition_bytes']:
            raise ValueError(json.dumps({'error': 'MEMORY_STORAGE_CAPACITY', 'projected_partition_bytes': projected,
                                        'limit': self.policy['limits']['partition_bytes'], 'maintenance': MAINTENANCE}))

    def check_sync(self, records, previous, source_texts=None):
        if not self.enabled: return
        usage = {'partition_bytes': logical_bytes(self.directory), 'active_fact_bytes': 0, 'active_facts': 0}
        categories = {category: {'active_bytes': 0, 'limit_bytes': self.policy['categories'][category]['active_bytes']} for category in CATEGORIES}
        for ident, item in records.items():
            metadata = item['metadata']; category = metadata.get('category')
            if category is not None and category not in CATEGORIES:
                raise ValueError('Synchronized knowledge has an invalid explicit category')
            if category not in CATEGORIES:
                old_record = previous.get('records', {}).get(ident)
                if old_record is None or 'category' in old_record.get('metadata', {}):
                    raise ValueError('New synchronized knowledge requires an explicit registered category; only indexed uncategorized legacy facts are exempt')
                category = classify(metadata)[0]
            path = Path(item['input_path']);size = path.stat().st_size
            if size > self.policy['limits']['single_note_bytes']:
                raise ValueError('Synchronized memory note exceeds single-note storage allocation')
            old = previous.get('records', {}).get(ident, {})
            if ident not in previous.get('records', {}) or item.get('source_hash') != old.get('source_hash'):
                text = (source_texts or {}).get(ident)
                if text is None: text = path.read_text()
                match = re.match(r'\A---\s*\n.*?\n---\s*\n(.*)\Z', text, re.S)
                body = match.group(1) if match else text
                self.validate_content(metadata, body)
            old_path = self.directory / 'notes' / item['path']
            usage['partition_bytes'] += max(0, size - (old_path.stat().st_size if old_path.is_file() else 0))
            if metadata['status'] == 'verified':
                usage['active_facts'] += 1; usage['active_fact_bytes'] += size;categories[category]['active_bytes'] += size
        errors = self._violations(usage, categories)
        if errors: raise ValueError(json.dumps({'error': 'MEMORY_STORAGE_CAPACITY', 'violations': errors, 'maintenance': MAINTENANCE}, ensure_ascii=False))

    def check_partition(self):
        if self.enabled and logical_bytes(self.directory) > self.policy['limits']['partition_bytes']:
            raise ValueError('Native index growth exceeded partition storage allocation; restore previous generation. ' + MAINTENANCE)
