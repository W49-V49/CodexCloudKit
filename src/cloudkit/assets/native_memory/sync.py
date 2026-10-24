#!/usr/bin/env python3
"""Run Basic Memory 0.24.1 native incremental indexing and emit bounded counters."""
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / '.venv/bin/python'
if Path(sys.executable).parent != PYTHON.parent:
    if __name__ != '__main__':
        raise RuntimeError('Import this bridge using the Basic Memory isolated Python interpreter')
    os.execv(str(PYTHON), [str(PYTHON), __file__, *sys.argv[1:]])
_defaults = {
    'BASIC_MEMORY_CONFIG_DIR': str(ROOT.parent / 'memory-data/config'),
    'BASIC_MEMORY_HOME': str(ROOT.parent / 'memory-data/knowledge'),
    'FASTEMBED_CACHE_PATH': str(ROOT.parent / 'models/fastembed'),
    'HF_HOME': str(ROOT.parent / 'models/huggingface'),
    'BASIC_MEMORY_FORCE_LOCAL': 'true', 'BASIC_MEMORY_AUTO_UPDATE': 'false',
    'BASIC_MEMORY_NO_PROMOS': '1', 'BASIC_MEMORY_LOGFIRE_ENABLED': 'false',
    'BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER': 'fastembed',
    'BASIC_MEMORY_SEMANTIC_EMBEDDING_MODEL': 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
    'BASIC_MEMORY_SEMANTIC_EMBEDDING_DIMENSIONS': '384',
    'BASIC_MEMORY_SEMANTIC_MIN_SIMILARITY': '0.30',
}
for _key, _value in _defaults.items():
    os.environ.setdefault(_key, _value)
os.environ['BASIC_MEMORY_FORCE_LOCAL']='true'
os.environ['BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER']='fastembed'
os.environ.setdefault('LOGFIRE_IGNORE_NO_CONFIG','1')
from ensure_model import configure_runtime
configure_runtime(os.environ)
import argparse
import asyncio
import datetime
import json
import time
from loguru import logger
logger.remove()

async def synchronize(project_name, full=False):
    from basic_memory import db
    from basic_memory.config import ConfigManager
    from basic_memory.services.initialization import reconcile_projects_with_config, recover_project_materializations
    from basic_memory.index.local_project import LocalProjectIndexRuntimeFactory, run_local_project_index_for_project
    from basic_memory.repository import EntityRepository, ProjectRepository
    from basic_memory.repository.search_repository import create_search_repository
    from basic_memory.repository.search_repository_base import purge_stale_search_index_rows
    from basic_memory.services.search_service import SearchService
    from basic_memory.services.file_service import FileService
    from basic_memory.markdown.markdown_processor import MarkdownProcessor
    from basic_memory.markdown.entity_parser import EntityParser
    from basic_memory.indexing.models import IndexFileJobStatus
    cfg = ConfigManager().config
    if cfg.semantic_embedding_provider != 'fastembed':
        raise RuntimeError('Only local FastEmbed is authorized')
    await reconcile_projects_with_config(cfg)
    _, sessions = await db.get_or_create_db(db_path=cfg.database_path, db_type=db.DatabaseType.FILESYSTEM)
    async with db.scoped_session(sessions) as session:
        projects = await ProjectRepository().get_active_projects(session)
    matches = [p for p in projects if p.name == project_name or p.permalink == project_name]
    if len(matches) != 1:
        raise RuntimeError('Project must resolve uniquely')
    project = matches[0]
    await recover_project_materializations(project, sessions)
    started = time.monotonic()
    from dataclasses import replace
    change_counts = {}
    class ObservedDetector:
        def __init__(self, original): self.original = original
        async def detect_all_changes(self, storage_files):
            report = await self.original.detect_all_changes(storage_files)
            for out, field in [('new','new_files'),('modified','modified_files'),('deleted','deleted_files'),('renamed','moved_files'),('unchanged','unchanged_files')]:
                change_counts[out] = len(getattr(report, field))
            return report
    class ObservedFactory(LocalProjectIndexRuntimeFactory):
        async def runtime_for_project(self, project):
            runtime = await super().runtime_for_project(project)
            return replace(runtime, change_detector=ObservedDetector(runtime.change_detector))
    result = await run_local_project_index_for_project(project, runtime_factory=ObservedFactory(), force_full=full, embeddings=False)
    failed = sum(f.status is IndexFileJobStatus.failed for batch in result.batch_results for f in batch.file_results)
    purged = await purge_stale_search_index_rows(sessions, project.id)
    repo = create_search_repository(sessions, project_id=project.id, app_config=cfg)
    files = FileService(Path(project.path), MarkdownProcessor(EntityParser(Path(project.path)), app_config=cfg), app_config=cfg)
    service = SearchService(repo, EntityRepository(project_id=project.id), files, session_maker=sessions)
    # Observe native batch counters without replacing the planner, chunker or vector logic.
    original = service.sync_entity_vectors_batch
    counts = {'chunks_total': 0, 'chunks_skipped': 0, 'embedding_jobs_total': 0}
    async def counted(*args, **kwargs):
        batch = await original(*args, **kwargs)
        for key in counts:
            counts[key] += getattr(batch, key)
        return batch
    service.sync_entity_vectors_batch = counted
    stats = await service.reindex_vectors(force_full=full)
    return {
        'schema_version': 1, 'tool': 'basic-memory', 'tool_version': '0.24.1',
        'project': project_name, 'sync_mode': 'native-full' if full else 'native-incremental',
        'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'changes': change_counts,
        'files': {'observed': result.total_files, 'indexed': result.enqueued_files - failed,
                  'failed': failed, 'deleted': result.deleted_files, 'moved': result.moved_files,
                  'batches': result.enqueued_batches, 'indexed_bytes': result.indexed_bytes,
                  'stale_search_rows_purged': purged},
        'embeddings': {**{k: stats[k] for k in ('total_entities','embedded','skipped','errors','deferred','vector_index','embedding_model')}, **counts},
        'duration_seconds': round(time.monotonic() - started, 4),
        'embedding_count': counts['embedding_jobs_total'],
        'paid_api_calls': 0, 'success': failed == 0 and stats['errors'] == 0 and stats['deferred'] == 0 and stats['vector_index'] is not None,
    }

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',default='main')
    parser.add_argument('--output')
    args=parser.parse_args()
    try:
        report=asyncio.run(synchronize(args.project))
    except Exception as error:
        report={'success':False,'tool_version':'0.24.1','error_type':type(error).__name__}
    if args.output:
        Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['success'] else 1
if __name__=='__main__':raise SystemExit(main())
