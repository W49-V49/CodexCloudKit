#!/usr/bin/env python3
"""Optional context-mode output tools with disposable cache, without session hooks.

No model proxy, hook installation, upgrade, timeline/history analytics or fetch
tool is exposed. This is output processing, not canonical project memory.
"""
import argparse,asyncio,json,os,shutil,signal,stat,sys,tempfile,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
from mcp.types import Tool

RUNTIME=Path(os.environ.get('CLOUDKIT_CODEGRAPH_ROOT',Path(__file__).resolve().parents[1]))
CACHE=RUNTIME/'cache/context-mode'
SESSION_LIMIT=64*1024*1024
TOTAL_LIMIT=256*1024*1024
TTL=24*60*60
ALLOWED={'ctx_execute','ctx_execute_file','ctx_batch_execute','ctx_index','ctx_search'}
ACTIVE_CACHE=None
def usage(folder):
    total=0
    if not folder.exists():return 0
    for path in folder.rglob('*'):
        try:item=path.stat(follow_symlinks=False)
        except FileNotFoundError:continue
        if stat.S_ISREG(item.st_mode):total+=item.st_size
    return total
def cleanup():
    CACHE.mkdir(parents=True,exist_ok=True);removed=[]
    for folder in CACHE.iterdir():
        if not folder.is_dir() or folder.is_symlink() or not folder.name.startswith('session-'):continue
        marker=folder/'owner.json'
        try:owner=json.loads(marker.read_text())
        except (FileNotFoundError,ValueError):continue
        try:os.kill(owner['pid'],0);alive=True
        except ProcessLookupError:alive=False
        except PermissionError:alive=True
        if not alive and time.time()-owner['created_at']>TTL:shutil.rmtree(folder);removed.append(folder.name)
    return {'removed':removed,'bytes':usage(CACHE),'session_limit_bytes':SESSION_LIMIT,'total_limit_bytes':TOTAL_LIMIT,'stale_ttl_seconds':TTL}
async def serve(project):
    global ACTIVE_CACHE
    policy=cleanup()
    if policy['bytes']>=TOTAL_LIMIT:raise ValueError('Transient context-output cache is full; run cleanup or use direct cloud tools')
    with tempfile.TemporaryDirectory(prefix='session-',dir=CACHE) as temporary:
        folder=Path(temporary);ACTIVE_CACHE=folder;(folder/'owner.json').write_text(json.dumps({'pid':os.getpid(),'created_at':time.time(),'project_path':project}))
        env=os.environ.copy();env.update(CONTEXT_MODE_DIR=str(folder),CONTEXT_MODE_PLATFORM='codex',CONTEXT_MODE_PROJECT_DIR=project,CLAUDE_PROJECT_DIR=project)
        params=StdioServerParameters(command='node',args=[str(RUNTIME/'node_modules/context-mode/server.bundle.mjs')],cwd=project,env=env)
        async with stdio_client(params,errlog=sys.stderr) as (reader,writer):
            async with ClientSession(reader,writer) as upstream:
                await upstream.initialize();listed=await upstream.list_tools()
                definitions=[]
                for tool in listed.tools:
                    if tool.name not in ALLOWED:continue
                    schema=json.loads(json.dumps(getattr(tool,'input_schema',getattr(tool,'inputSchema',{}))))
                    if tool.name=='ctx_search':
                        schema.setdefault('properties',{})['sort']={'type':'string','enum':['relevance'],'default':'relevance'}
                        schema['properties'].pop('project',None)
                    definitions.append(Tool.model_validate({'name':tool.name,'inputSchema':schema,'description':'Optional temporary tool-output processor. No durable memory or whole-session capture. Upstream output/search caps apply; use Basic Memory for uncapped evidence-driven recall. '+tool.name}))
                server=Server('codex-context-output')
                async def call_tool(name,arguments):
                    if name not in ALLOWED:raise ValueError('This adapter only exposes temporary local-output tools')
                    if usage(folder)>SESSION_LIMIT:raise ValueError('Transient output cache exceeds 64 MiB; restart this optional MCP and use direct tools meanwhile')
                    arguments=dict(arguments or {})
                    if name=='ctx_search':arguments['sort']='relevance';arguments.pop('project',None)
                    result=await upstream.call_tool(name,arguments)
                    if usage(folder)>SESSION_LIMIT:raise ValueError('Transient output cache exceeded 64 MiB; session will be cleaned at disconnect')
                    return result
                if hasattr(server,'add_request_handler'):
                    async def list_handler(context,params):return types.ListToolsResult(tools=definitions)
                    async def call_handler(context,params):return await call_tool(params.name,params.arguments)
                    server.add_request_handler('tools/list',types.PaginatedRequestParams,list_handler)
                    server.add_request_handler('tools/call',types.CallToolRequestParams,call_handler)
                else:
                    async def legacy_list():return definitions
                    server.list_tools()(legacy_list)
                    server.call_tool()(call_tool)
                async with stdio_server() as (incoming,outgoing):
                    serving=asyncio.create_task(server.run(incoming,outgoing,server.create_initialization_options()))
                    async def monitor():
                        while True:
                            await asyncio.sleep(1)
                            if usage(folder)>SESSION_LIMIT or usage(CACHE)>TOTAL_LIMIT:raise RuntimeError('Transient output cache storage limit reached')
                    monitoring=asyncio.create_task(monitor())
                    done,pending=await asyncio.wait({serving,monitoring},return_when=asyncio.FIRST_COMPLETED)
                    for task in pending:task.cancel()
                    await asyncio.gather(*pending,return_exceptions=True)
                    for task in done:task.result()
async def run_with_signals(project):
    task=asyncio.create_task(serve(project));loop=asyncio.get_running_loop()
    def stop():
        # MCP clients commonly terminate the server instead of closing stdin.
        # Clear this process's disposable output cache before bounded teardown.
        if ACTIVE_CACHE is not None:shutil.rmtree(ACTIVE_CACHE,ignore_errors=True)
        task.cancel()
    for sig in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,stop)
    try:await task
    except asyncio.CancelledError:pass
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project-root',default=os.environ.get('CLOUD_PROJECT_PATH') or os.getcwd());parser.add_argument('--cleanup',action='store_true')
    args=parser.parse_args()
    if args.cleanup:print(json.dumps(cleanup()));return
    project=Path(args.project_root).resolve()
    if not project.is_dir():raise ValueError('Select an existing project directory')
    asyncio.run(run_with_signals(str(project)))
if __name__=='__main__':main()
