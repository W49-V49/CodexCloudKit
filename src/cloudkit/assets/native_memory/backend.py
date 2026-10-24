#!/usr/bin/env python3
"""Machine-readable bridge to the version-pinned native Basic Memory backend."""
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
VENV_PYTHON=ROOT/'.venv/bin/python'
if Path(sys.executable).parent != VENV_PYTHON.parent:
    if __name__ != '__main__':
        raise RuntimeError('Import this bridge using the Basic Memory isolated Python interpreter')
    os.execv(str(VENV_PYTHON),[str(VENV_PYTHON),__file__,*sys.argv[1:]])
import argparse
import asyncio
import json
import subprocess


async def _reconcile_path(project, target):
    from basic_memory import db
    from basic_memory.config import ConfigManager
    from basic_memory.repository import ProjectRepository
    from basic_memory.services.initialization import reconcile_projects_with_config
    from basic_memory.services.project_service import ProjectService
    cfg=ConfigManager().config
    if not await reconcile_projects_with_config(cfg):
        raise RuntimeError('Native project reconciliation failed')
    _,sessions=await db.get_or_create_db(db_path=cfg.database_path,db_type=db.DatabaseType.FILESYSTEM)
    service=ProjectService(repository=ProjectRepository(),session_maker=sessions)
    await service.update_project(project,updated_path=str(target))
    async with db.scoped_session(sessions) as session:
        item=await ProjectRepository().get_by_name(session,project)
    if item is None or Path(item.path).resolve()!=target:
        raise RuntimeError('Native project relocation not confirmed')


def relocate(project,path):
    from sync import synchronize
    target=Path(path).resolve()
    if not target.is_dir():raise ValueError('Restored knowledge directory must exist')
    asyncio.run(_reconcile_path(project,target))
    return {'success':True,'project':project,'path':str(target),'reconciled':True}


def register(project, path):
    from sync import synchronize  # Applies local-only environment defaults before imports.
    from basic_memory.config import ConfigManager
    from basic_memory.config_models import resolve_data_dir, ProjectEntry
    target=Path(path).resolve()
    if not target.is_dir():
        raise ValueError('Project directory must already exist')
    data_dir=resolve_data_dir()
    fresh=not (data_dir/'config.json').exists()
    if fresh and (data_dir/'memory.db').exists():
        raise RuntimeError('Database exists without configuration; restore its matching config first')
    manager=ConfigManager()
    cfg=manager.config
    if fresh:
        # Native model/config persistence avoids seeding an overlapping default main project.
        cfg=cfg.model_copy(update={'projects':{project:ProjectEntry(path=str(target))},'default_project':project})
        manager.save_config(cfg)
        asyncio.run(_reconcile_path(project,target))
        return {'success':True,'project':project,'path':str(target),'registered':True}
    existing=cfg.projects.get(project)
    if existing is not None:
        if Path(existing.path).resolve()!=target:
            raise ValueError('Existing project has a different path; use a new partition')
        asyncio.run(_reconcile_path(project,target))
        return {'success':True,'project':project,'path':str(target),'registered':False,'reconciled':True}
    run=subprocess.run([str(ROOT/'bm'),'project','add',project,str(target),'--local','--no-wait'],capture_output=True,text=True)
    if run.returncode:
        raise RuntimeError('Native project registration failed')
    asyncio.run(_reconcile_path(project,target))
    return {'success':True,'project':project,'path':str(target),'registered':True,'reconciled':True}


def index(project, full=False):
    from sync import synchronize
    result=asyncio.run(synchronize(project,full=full))
    result.update(result['changes'])
    result['error']=result['files']['failed']+result['embeddings']['errors']+result['embeddings']['deferred']
    return result


def search(project, query, limit=8, mode='hybrid', status='verified', page=1):
    if not 1<=limit<=50:
        raise ValueError('limit must be between 1 and 50')
    if page < 1:
        raise ValueError('page must be positive')
    from sync import synchronize  # Initialize the same isolated local environment.
    args=[str(ROOT/'bm'),'tool','search-notes',query,'--project',project,'--page-size',str(limit),'--page',str(page),'--json','--compact']
    if mode in ('hybrid','vector'):args.append('--'+mode)
    if status:args.extend(['--status',status])
    run=subprocess.run(args,capture_output=True,text=True)
    if run.returncode:raise RuntimeError('Native search failed')
    payload=json.loads(run.stdout)
    if any(item.get('content') for item in payload.get('results',[])):
        raise RuntimeError('Native compact discovery unexpectedly returned note bodies')
    candidates=[]
    for item in payload.get('results',[])[:limit]:
        candidates.append({'path':item.get('file_path'),'title':item.get('title'),'snippet':(item.get('content') or '')[:500],
                           'score':item.get('score'),'permalink':item.get('permalink'),'id':item.get('external_id'),
                           'updated_at':item.get('updated_at'),'metadata':item.get('metadata',{})})
    return {'success':True,'project':project,'mode':mode,'status_filter':status,'discovery_mode':'compact-metadata-only','semantic_min_similarity':float(os.environ.get('BASIC_MEMORY_SEMANTIC_MIN_SIMILARITY','0.30')),'candidates':candidates,'candidate_count':len(candidates),'has_more':payload.get('has_more',False),'page':payload.get('current_page',page),'page_size':payload.get('page_size',limit),'paid_api_calls':0}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-dir',help='Optional per-project/branch isolated config and SQLite directory')
    sub=parser.add_subparsers(dest='command',required=True)
    add=sub.add_parser('register');add.add_argument('project');add.add_argument('path')
    rel=sub.add_parser('relocate');rel.add_argument('project');rel.add_argument('path')
    idx=sub.add_parser('index');idx.add_argument('project');idx.add_argument('--full',action='store_true')
    sea=sub.add_parser('search');sea.add_argument('project');sea.add_argument('query');sea.add_argument('--limit',type=int,default=8);sea.add_argument('--mode',choices=('hybrid','vector','text'),default='hybrid');sea.add_argument('--status',default='verified');sea.add_argument('--page',type=int,default=1)
    args=parser.parse_args()
    if args.config_dir:os.environ['BASIC_MEMORY_CONFIG_DIR']=str(Path(args.config_dir).resolve())
    try:
        if args.command=='register':result=register(args.project,args.path)
        elif args.command=='relocate':result=relocate(args.project,args.path)
        elif args.command=='index':result=index(args.project,full=args.full)
        else:result=search(args.project,args.query,args.limit,args.mode,args.status,args.page)
    except Exception as error:result={'success':False,'error_type':type(error).__name__}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['success'] else 1
if __name__=='__main__':raise SystemExit(main())
