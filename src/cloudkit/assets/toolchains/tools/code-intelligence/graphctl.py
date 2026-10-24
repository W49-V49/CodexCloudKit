#!/usr/bin/env python3
"""Project-bound CodeGraph adapter; indexes stay outside business repositories."""
import argparse,contextlib,fcntl,hashlib,json,os,re,shutil,sqlite3,stat,subprocess,sys,tempfile,time
from pathlib import Path
from urllib.parse import urlsplit
RUNTIME=Path(os.environ.get('CLOUDKIT_CODEGRAPH_ROOT',Path(__file__).resolve().parents[1]))
STORE=Path(os.environ.get('CLOUDKIT_CODEGRAPH_STORE',RUNTIME.parent/'state/codegraph'))
LIMIT=256*1024*1024

def git(project,*args):
    result=subprocess.run(['git','-C',str(project),*args],capture_output=True,text=True)
    return result.stdout.strip() if result.returncode==0 else None
def identity(project):
    project=Path(project).resolve()
    if not project.is_dir():raise ValueError('Select an existing project directory')
    toplevel=git(project,'rev-parse','--show-toplevel')
    if toplevel:
        project=Path(toplevel).resolve()
    remote=git(project,'remote','get-url','origin') or ''
    parsed=urlsplit(remote)
    origin=None
    if parsed.hostname=='github.com':origin='github.com/'+parsed.path.strip('/').removesuffix('.git')
    elif remote.startswith('git@github.com:'):origin='github.com/'+remote.partition(':')[2].removesuffix('.git')
    branch=git(project,'symbolic-ref','--short','HEAD') or git(project,'rev-parse','HEAD') or 'unversioned-fixture'
    key=hashlib.sha256(((origin or 'path:'+str(project))+'\0'+branch).encode()).hexdigest()[:24]
    folder=STORE/key
    if any(path.is_symlink() for path in (STORE.parent,STORE,folder)) or not folder.resolve().is_relative_to(STORE.resolve()):
        raise ValueError('Graph partition must stay inside its physical cloud store without symlinks')
    if folder.exists() and any(path.is_symlink() for path in folder.iterdir()):raise ValueError('Managed graph files must not be symlinks across partitions')
    folder.mkdir(parents=True,exist_ok=True)
    return {'project_path':str(project),'origin':origin,'branch':branch,'project_key':key,'source_sha':git(project,'rev-parse','HEAD'),'folder':str(folder),'database':str(folder/'graph.db')}
def environment(info):
    env=os.environ.copy();env['CODEGRAPH_REGISTRY_PATH']=str(Path(info['folder'])/'registry.json')
    return env
def _atomic(path,value):
    tmp=path.with_suffix(path.suffix+'.pending');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)
def folder_bytes(folder):
    total=0
    for path in folder.iterdir():
        try:info=path.stat(follow_symlinks=False)
        except FileNotFoundError:continue
        if stat.S_ISREG(info.st_mode):total+=info.st_size
    return total
def attach_journal(info):
    project=Path(info['project_path']);link=project/'.codegraph';target=Path(info['folder'])
    if link.is_symlink() and link.resolve()!=target:
        old=link.resolve()
        metadata=old/'ownership.json'
        if not metadata.exists():metadata=old/'index.json'
        prior=json.loads(metadata.read_text()) if old.is_relative_to(STORE) and metadata.is_file() and not metadata.is_symlink() else {}
        prior_key=hashlib.sha256(((prior.get('origin') or 'path:'+str(project))+'\0'+str(prior.get('branch'))).encode()).hexdigest()[:24]
        if prior.get('project_path')!=str(project) or prior.get('origin')!=info['origin'] or prior_key!=old.name:raise ValueError('Existing .codegraph link belongs to another project; preserve it and use Serena')
        temporary=project/'.codegraph.next';temporary.symlink_to(target,target_is_directory=True);temporary.replace(link)
    elif link.exists() and not link.is_symlink():raise ValueError('Existing user .codegraph directory preserved; migrate its derived data explicitly or use Serena')
    elif not link.exists():link.symlink_to(target,target_is_directory=True)
    _atomic(target/'ownership.json',{key:info[key] for key in ('project_path','origin','branch','project_key')})
    exclude=git(project,'rev-parse','--git-path','info/exclude')
    if exclude:
        path=Path(exclude);path=path if path.is_absolute() else project/path
        old=path.read_text() if path.exists() else ''
        if '/.codegraph' not in old.splitlines():path.parent.mkdir(parents=True,exist_ok=True);path.write_text(old+('' if not old or old.endswith('\n') else '\n')+'/.codegraph\n')
@contextlib.contextmanager
def lock(info,shared=False):
    with (Path(info['folder'])/'write.lock').open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        try:yield
        finally:fcntl.flock(handle,fcntl.LOCK_UN)
def native(info,command,*args):
    result=subprocess.run([str(RUNTIME/'node_modules/.bin/codegraph'),command,*map(str,args),'-d',info['database']],cwd=info['project_path'],env=environment(info),capture_output=True,text=True,timeout=600)
    if result.returncode:
        folder=RUNTIME/'diagnostics';folder.mkdir(parents=True,exist_ok=True)
        text=re.sub(r'(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,})','[REDACTED]',result.stderr).encode()[-131072:].decode(errors='ignore')
        path=folder/(str(time.time_ns())+'.log')
        if folder_bytes(folder)+len(text.encode())<=16*1024*1024:path.write_text(text)
        raise RuntimeError('CodeGraph operation failed; diagnostic '+str(path if path.exists() else folder)+'; use existing Serena fallback')
    return result
def index(project,semantic=True):
    info=identity(project);folder=Path(info['folder']);db=Path(info['database'])
    with lock(info),tempfile.TemporaryDirectory(prefix='graph-rollback-',dir=RUNTIME) as temporary:
        backup=Path(temporary)/'graph.db'
        preserved=('semantic.npz','semantic.json','index.json','registry.json','changes.journal')
        for name in preserved:
            if (folder/name).exists():shutil.copyfile(folder/name,Path(temporary)/name)
        if db.exists():
            with sqlite3.connect(db) as src,sqlite3.connect(backup) as dest:src.backup(dest)
        try:
            attach_journal(info)
            result=native(info,'build',info['project_path'])
            used=folder_bytes(folder)
            if used>LIMIT:raise ValueError('CodeGraph partition exceeds 256 MiB; keep previous graph and use Serena')
            if semantic:
                from semantic import build
                vectors=build(info['project_path'],db)
            else:vectors={'enabled':False}
            with sqlite3.connect(db) as connection:
                nodes=connection.execute('SELECT count(*) FROM nodes').fetchone()[0]
                edges=connection.execute('SELECT count(*) FROM edges').fetchone()[0]
            record={**info,'passed':True,'tool':'@optave/codegraph','version':'3.17.0','nodes':nodes,'edges':edges,'native_incremental_no_changes':'No changes detected' in (result.stdout+result.stderr),'semantic':vectors,'storage_limit_bytes':LIMIT,'canonical_memory_modified':False,'journal_link':str(Path(info['project_path'])/'.codegraph'),'journal_data_external':True}
            if folder_bytes(folder)+len(json.dumps(record).encode())>LIMIT:raise ValueError('CodeGraph partition exceeds 256 MiB; previous graph and semantic index retained')
            _atomic(folder/'index.json',record)
            # Upstream auto-registration assumes an in-project DB; correct only
            # this adapter's external registry entry after successful build.
            registry=folder/'registry.json'
            value=json.loads(registry.read_text()) if registry.exists() else {'repos':{}}
            for item in value.get('repos',{}).values():
                if item.get('path')==info['project_path']:item['dbPath']=info['database']
            if len(json.dumps(value).encode())>16*1024*1024:raise ValueError('CodeGraph registry exceeds 16 MiB')
            _atomic(registry,value)
            return record
        except Exception:
            if db.exists():
                for suffix in ('-wal','-shm'):db.with_name(db.name+suffix).unlink(missing_ok=True)
                db.unlink()
            if backup.exists():backup.replace(db)
            for name in preserved:
                (folder/name).unlink(missing_ok=True)
                if (Path(temporary)/name).exists():shutil.copyfile(Path(temporary)/name,folder/name)
            raise
def query(project,command,*args):
    info=identity(project)
    if command not in {'where','query','deps','context','fn-impact','stats','map','cycles','diff-impact','dataflow','path'}:raise ValueError('Unsupported query; use the native CLI explicitly if needed')
    with lock(info,shared=True):
        if not Path(info['database']).is_file():raise ValueError('Build the external project graph first')
        result=native(info,command,*args,'--json')
        return json.loads(result.stdout)
def semantic_search(project,text,limit=None):
    from semantic import search
    info=identity(project)
    with lock(info,shared=True):return search(info['database'],text,limit)
def status(project):
    info=identity(project);record=Path(info['folder'])/'index.json'
    return {'identity':info,'indexed':record.exists(),'last_index':json.loads(record.read_text()) if record.exists() else None,'storage_limit_bytes':LIMIT}
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project-root',default=os.environ.get('CLOUD_PROJECT_PATH') or os.getcwd())
    sub=p.add_subparsers(dest='command',required=True)
    build=sub.add_parser('build');build.add_argument('--no-semantic',action='store_true')
    sub.add_parser('status')
    search=sub.add_parser('search');search.add_argument('--query',required=True);search.add_argument('--limit',type=int)
    call=sub.add_parser('query');call.add_argument('native_command');call.add_argument('arguments',nargs=argparse.REMAINDER)
    args=p.parse_args()
    if args.command=='build':result=index(args.project_root,not args.no_semantic)
    elif args.command=='status':result=status(args.project_root)
    elif args.command=='search':
        result=semantic_search(args.project_root,args.query,args.limit)
    else:result=query(args.project_root,args.native_command,*args.arguments)
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':
    python=Path(os.environ.get('CLOUDKIT_MEMORY_ROOT',RUNTIME.parent/'memory'))/'.venv/bin/python'
    if python.exists() and Path(sys.executable).parent!=python.parent:os.execv(str(python),[str(python),__file__,*sys.argv[1:]])
    main()
