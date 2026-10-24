#!/usr/bin/env python3
"""Real native graph, offline semantic, incremental and MCP acceptance fixtures."""
import argparse,asyncio,concurrent.futures,hashlib,json,os,shutil,sqlite3,subprocess,sys,tempfile,threading,time
from pathlib import Path
from unittest.mock import patch
import graphctl,semantic
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

SOURCE=Path(__file__).resolve().parent
PYTHON=str(Path(os.environ['CLOUDKIT_MEMORY_ROOT'])/'.venv/bin/python')
def require(value,message):
    if not value:raise AssertionError(message)
async def mcp_checks(project,output):
    from context_output import CACHE
    prior={path.name for path in CACHE.glob('session-*')}
    reports={}
    for name,script in [('codegraph','mcp_server.py'),('context-output','context_output.py')]:
        params=StdioServerParameters(command=PYTHON,args=[str(graphctl.RUNTIME/'protocol'/script),'--project-root',str(project)])
        with (output/(name+'-mcp.log')).open('w') as err:
            async with stdio_client(params,errlog=err) as (read,write):
                async with ClientSession(read,write) as session:
                    await session.initialize();listed=await session.list_tools();names={tool.name for tool in listed.tools}
                    if name=='codegraph':
                        require(len(names)==6,'Six focused graph tools must be available')
                        result=await session.call_tool('codegraph_symbol',{'name':'invoice_total'})
                        require(not result.is_error and 'add_numbers' in str(result.content),'Actual graph caller/callee tool failed')
                        result=await session.call_tool('codegraph_semantic_search',{'query':'计算发票数量和单价的总金额','limit':1})
                        require(not result.is_error and 'invoice_total' in str(result.content),'Actual semantic MCP query failed')
                    else:
                        require(names=={'ctx_execute','ctx_execute_file','ctx_batch_execute','ctx_index','ctx_search'},'Optional processor exposed unexpected host mutation/history/fetch tools')
                        result=await session.call_tool('ctx_execute',{'language':'python','code':'print(sum(range(1000)))'})
                        require(not result.is_error and '499500' in str(result.content),'Real output execution failed')
                        result=await session.call_tool('ctx_index',{'content':'Invoice billing uses quantity multiplied by price. Transient acceptance fixture.','source':'verify-billing'})
                        require(not result.is_error,'Explicit output indexing failed')
                        result=await session.call_tool('ctx_search',{'queries':['quantity price'],'source':'verify-billing'})
                        require(not result.is_error and 'Invoice billing' in str(result.content),'Transient output search failed')
                    reports[name]={'passed':True,'tools':sorted(names),'upstream':'@optave/codegraph 3.17.0' if name=='codegraph' else 'context-mode 1.0.169','scope':'Actual stdio initialize, tools/list and tool calls; not UI activation'}
    left=[]
    for path in CACHE.glob('session-*'):
        if path.name not in prior:
            try:owner=json.loads((path/'owner.json').read_text())
            except (FileNotFoundError,ValueError):continue
            if owner.get('project_path')==str(project):left.append(path.name)
    require(not left,'This test project temporary output cache survived normal disconnect')
    reports['context-output']['cache_deleted_on_disconnect']=True
    return reports
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True);evidence=args.output.parent
    report={'passed':False,'scope':'Synthetic local Python/TypeScript native graph + offline multilingual symbol vectors + real MCP; no business source or model API','checks':{}}
    folders=[]
    try:
        with tempfile.TemporaryDirectory(prefix='codegraph-accept-',dir=graphctl.RUNTIME) as temporary:
            project=Path(temporary)/'source';project.mkdir()
            python=project/'maths.py';typescript=project/'app.ts'
            python.write_text('def add_numbers(left, right):\n    """Add two numbers and return the sum."""\n    return left + right\n\ndef invoice_total(quantity, price):\n    """Calculate invoice total from quantity and unit price."""\n    return add_numbers(quantity * price, 0)\n')
            typescript.write_text('export function greeting(name: string): string { return `Hello ${name}`; }\nexport function welcomeUser(): string { return greeting("world"); }\n')
            first=graphctl.index(project);info=graphctl.identity(project);folders.append(Path(info['folder']))
            require(first['semantic']['embedded']==4,'Initial graph must embed four actual symbols')
            query=graphctl.query(project,'query','invoice_total')
            require('add_numbers' in json.dumps(query),'Native cross-function call edge missing')
            repeated=graphctl.index(project)
            require(repeated['native_incremental_no_changes'] and repeated['semantic']['embedded']==0 and repeated['semantic']['source_files_read']==0,'Unchanged graph or semantic content was repeated')
            ranked=graphctl.semantic_search(project,'计算发票商品数量和单价的总金额')
            require(ranked['candidates'][0]['name']=='invoice_total','Chinese semantic query did not rank English invoice function first')
            report['checks']['native_and_semantic']={'passed':True,'first':first,'repeated':repeated,'semantic_candidates':ranked['candidates']}
            report['checks']['mcp']=asyncio.run(mcp_checks(project,evidence))
            python.write_text(python.read_text().replace('return left + right','return (left + right) * 2'))
            modified=graphctl.index(project);require(modified['semantic']['embedded']==1,'Single function change must re-embed only changed symbol')
            python.rename(project/'billing.py');renamed=graphctl.index(project)
            require(renamed['semantic']['embedded']==0 and renamed['semantic']['source_files_read']==0,'Rename must reuse vectors without rereading unchanged source')
            (project/'billing.py').unlink();deleted=graphctl.index(project)
            require(deleted['semantic']['symbols']==2 and deleted['semantic']['embedded']==0,'Deleted symbols remained in semantic index')
            report['checks']['modify_rename_delete']={'passed':True,'modified_embedded':modified['semantic']['embedded'],'renamed_embedded':renamed['semantic']['embedded'],'deleted_current_symbols':deleted['semantic']['symbols']}
            with patch.object(semantic,'FP_VERSION','codegraph-symbol-format-acceptance-migration'):
                migration=graphctl.index(project)
                require(migration['semantic']['embedded']==2,'Changed pipeline fingerprint must rebuild incompatible derived vectors')
            graphctl.index(project)
            report['checks']['version_fingerprint_migration']={'passed':True,'scope':'Actual adapter fingerprint drift using the same locked model, not a different downloaded model','embedded':migration['semantic']['embedded']}
            before={name:(Path(info['folder'])/name).read_bytes() for name in ('semantic.json','semantic.npz','index.json','registry.json')}
            with patch.object(graphctl,'LIMIT',1):
                rejected=False
                try:graphctl.index(project)
                except ValueError:rejected=True
                require(rejected,'Capacity overflow must reject')
            require(all((Path(info['folder'])/name).read_bytes()==value for name,value in before.items()),'Capacity rejection lost old semantic/catalog files')
            require(graphctl.query(project,'query','greeting')['results'],'Previous native graph was not restored')
            report['checks']['capacity_rollback']={'passed':True,'previous_graph_and_semantic_preserved':True}
            entered=threading.Event();native_called=threading.Event();native=graphctl.native
            def observed(*a,**kw):native_called.set();return native(*a,**kw)
            def reader():entered.set();return graphctl.query(project,'query','greeting')
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool,patch.object(graphctl,'native',observed):
                with graphctl.lock(info):
                    future=pool.submit(reader);require(entered.wait(2),'Reader did not start');time.sleep(.05);require(not native_called.is_set(),'Query ran during exclusive index transaction')
                require(future.result(timeout=10)['results'],'Reader did not resume after writer released lock')
            report['checks']['concurrent_reader_waits_for_writer']={'passed':True}
            sentinel=Path(temporary)/'sentinel';sentinel.write_text('not a graph')
            database=Path(info['database']);backup=database.with_suffix('.saved');database.rename(backup);database.symlink_to(sentinel)
            try:
                rejected=False
                try:graphctl.query(project,'query','greeting')
                except ValueError:rejected=True
                require(rejected and sentinel.read_text()=='not a graph','Managed graph symlink crossed the project partition')
            finally:database.unlink();backup.rename(database)
            report['checks']['managed_symlink_rejected']={'passed':True}
            require((project/'.codegraph').is_symlink() and (project/'.codegraph').resolve()==Path(info['folder']),'Native journal data was not directed into external managed partition')
            report['checks']['business_data_external']={'passed':True,'managed_link_only':'.codegraph','journal_data_external':True,'source_entries':sorted(p.name for p in project.iterdir())}
            require(Path(graphctl.environment(info)['CODEGRAPH_REGISTRY_PATH']).parent==Path(info['folder']),'Registry is not project-partitioned')
            report['checks']['registry_project_partition']={'passed':True}
            # Real unborn Git branches exercise managed link ownership without
            # contacting GitHub or touching the selected business repository.
            branch_project=Path(temporary)/'branch-source';branch_project.mkdir()
            (branch_project/'sample.py').write_text('def branch_value():\n    return 1\n')
            subprocess.run(['git','init','-b','main',str(branch_project)],check=True,capture_output=True)
            subprocess.run(['git','-C',str(branch_project),'remote','add','origin','https://github.com/fixture/codegraph-'+Path(temporary).name+'.git'],check=True,capture_output=True)
            main_index=graphctl.index(branch_project,semantic=False);folders.append(Path(main_index['folder']))
            nested=branch_project/'nested';nested.mkdir()
            nested_identity=graphctl.identity(nested)
            require(nested_identity['folder']==main_index['folder'] and nested_identity['project_path']==str(branch_project),'Nested cwd did not canonicalize to the Git project root')
            subprocess.run(['git','-C',str(branch_project),'symbolic-ref','HEAD','refs/heads/feature'],check=True,capture_output=True)
            feature=graphctl.index(branch_project,semantic=False);folders.append(Path(feature['folder']))
            require(main_index['folder']!=feature['folder'] and (branch_project/'.codegraph').resolve()==Path(feature['folder']),'Branch change did not rebind its owned external partition')
            subprocess.run(['git','-C',str(branch_project),'symbolic-ref','HEAD','refs/heads/main'],check=True,capture_output=True)
            graphctl.index(branch_project,semantic=False)
            require((branch_project/'.codegraph').resolve()==Path(main_index['folder']),'Switching back lost the original partition')
            require(not subprocess.check_output(['git','-C',str(branch_project),'status','--porcelain','--untracked-files=all'],text=True).count('.codegraph'),'Managed journal link appeared in git status')
            target=branch_project/'.codegraph';target.unlink();unknown=Path(temporary)/'user-owned';unknown.mkdir();target.symlink_to(unknown,target_is_directory=True)
            rejected=False
            try:graphctl.index(branch_project,semantic=False)
            except ValueError:rejected=True
            require(rejected and target.resolve()==unknown,'Unknown user link was overwritten')
            report['checks']['branch_owned_link_rebind']={'passed':True,'real_git_branches':True,'unknown_link_preserved':True,'git_link_locally_excluded':True,'nested_cwd_uses_git_root':True}
            report['passed']=True
    finally:
        for folder in folders:
            if folder.is_relative_to(graphctl.STORE) and folder.exists():shutil.rmtree(folder)
        report['tested_source_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCE.glob('*.py')}
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'passed':report['passed'],'checks':list(report['checks']),'report':str(args.output)}))
    return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
