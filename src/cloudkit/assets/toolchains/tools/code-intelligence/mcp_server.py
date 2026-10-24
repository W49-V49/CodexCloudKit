#!/usr/bin/env python3
"""Focused CodeGraph MCP, backed by the installed upstream CLI and offline model."""
import argparse,json,os
from pathlib import Path
try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError:
    from mcp.server.mcpserver import MCPServer as FastMCP
import graphctl

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project-root',default=os.environ.get('CLOUD_PROJECT_PATH') or os.getcwd())
    options=parser.parse_args();project=str(Path(options.project_root).resolve());graphctl.identity(project)
    server=FastMCP('codex-codegraph')
    @server.tool()
    def codegraph_status()->dict:
        """Describe this project's external structural and semantic graph partition."""
        return graphctl.status(project)
    @server.tool()
    def codegraph_index(semantic:bool=True)->dict:
        """Incrementally index current source; unchanged symbol vectors are reused."""
        return graphctl.index(project,semantic)
    @server.tool()
    def codegraph_symbol(name:str)->dict:
        """Find a symbol with callers/callees and source positions in the current project."""
        return graphctl.query(project,'query',name)
    @server.tool()
    def codegraph_dependencies(file:str)->dict:
        """Inspect definitions and file import dependencies without reading full source."""
        return graphctl.query(project,'deps',file)
    @server.tool()
    def codegraph_impact(name:str)->dict:
        """Inspect transitive caller impact before changing a function."""
        return graphctl.query(project,'fn-impact',name)
    @server.tool()
    def codegraph_semantic_search(query:str,limit:int|None=None)->dict:
        """Rank local code symbols using existing offline multilingual vectors; verify source before decisions. No default retrieval count or token budget."""
        return graphctl.semantic_search(project,query,limit)
    server.run(transport='stdio')
if __name__=='__main__':main()
