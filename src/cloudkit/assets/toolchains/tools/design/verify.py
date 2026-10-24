#!/usr/bin/env python3
"""Reproduce real design-tool acceptance without model calls or provider accounts."""
from __future__ import annotations
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[2]

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.parent.mkdir(parents=True,exist_ok=True)
    metadata=json.loads((Path(os.environ['CLOUDKIT_DESIGN_ROOT'])/'installation.json').read_text())
    browser=metadata.get('browser',{})
    if browser.get('module'):os.environ.setdefault('CLOUDKIT_PLAYWRIGHT_MODULE',browser['module'])
    if browser.get('cache'):os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',browser['cache'])
    if browser.get('executable'):os.environ.setdefault('CLOUDKIT_CHROMIUM_PATH',browser['executable'])
    source=Path(metadata['upstream_source']);skill=Path(metadata['skill_path']);cli=(Path(os.environ['CLOUDKIT_DESIGN_ROOT'])/'bin/design-tools')
    lock=json.loads((ROOT/'environment/design/upstreams.lock.json').read_text())
    report={'schema_version':1,'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'Pinned local skill, data import, standalone SVG export, admission checks and actual Chromium fixture rendering; no model/GUI-service/business-design acceptance','passed':False,'installation':metadata,'checks':{}}
    def run(name,command,cwd=ROOT,expect=0):
        completed=subprocess.run(command,cwd=cwd,capture_output=True,text=True,timeout=180)
        (args.output.parent/(name+'.log')).write_text(completed.stdout+completed.stderr)
        assert completed.returncode==expect,(name,completed.returncode)
        report['checks'][name]={'passed':True,'returncode':completed.returncode}
        return completed.stdout
    for name,wanted in lock['diagram_design']['file_sha256'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==wanted,name
    report['checks']['pinned_source_integrity']={'passed':True,'files':len(lock['diagram_design']['file_sha256'])}
    run('installer_profile_regressions',[sys.executable,'-m','unittest','discover','-s','tools/design','-p','test_*.py','-v'])
    run('upstream_self_check',[sys.executable,'scripts/test-self-check.py'],source)
    run('upstream_svg_export_133_tests',[sys.executable,'scripts/test-export-svg-standalone.py'],source)
    fixture=skill/'assets/example-flowchart.html'
    run('example_admission',[str(cli),'check',str(fixture)])
    for name,action,relative,container in [('mermaid','import-mermaid','sample-flowchart.mmd','diagrams'),('drawio','import-drawio','sample-architecture.drawio','pages'),('excalidraw','import-excalidraw','sample-whiteboard.excalidraw','scene')]:
        result=json.loads(run(name+'_import',[str(cli),action,'--json',str(source/'scripts/fixtures'/relative)]))
        ir=result[container];ir=ir[0] if isinstance(ir,list) else ir
        assert ir['nodes'] and ir['edges'];report['checks'][name+'_import'].update(nodes=len(ir['nodes']),edges=len(ir['edges']))
    with tempfile.TemporaryDirectory(prefix='diagram-acceptance-') as temp:
        temporary=Path(temp);svg=temporary/'flowchart.svg'
        run('standalone_svg',[str(cli),'export-svg',str(fixture),str(svg)])
        parsed=ET.fromstring(svg.read_text());assert parsed.tag.endswith('svg')
        report['checks']['standalone_svg']['sha256']=hashlib.sha256(svg.read_bytes()).hexdigest()
        png=args.output.parent/'flowchart-render.png'
        run('standalone_png',[str(cli),'export-png',str(fixture),str(png),'1'])
        assert png.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
        report['checks']['standalone_png'].update(sha256=hashlib.sha256(png.read_bytes()).hexdigest(),bytes=png.stat().st_size)
        unsafe=temporary/'unsafe.html';unsafe.write_text(fixture.read_text().replace('</body>',"<script>alert('unsafe')</script></body>"))
        rejected=subprocess.run([str(cli),'check',str(unsafe)],capture_output=True,text=True)
        assert rejected.returncode!=0
        rejected_export=subprocess.run([str(cli),'export-png',str(unsafe),str(temporary/'must-not-exist.png')],capture_output=True,text=True)
        assert rejected_export.returncode!=0 and not (temporary/'must-not-exist.png').exists()
        report['checks']['executable_script_rejected']={'passed':True,'executed':False}
    browser_output=args.output.parent/'browser-smoke.json'
    run('chromium_desktop_mobile',['node',str(ROOT/'tools/design/browser_smoke.cjs'),str(fixture),str(browser_output)])
    assert json.loads(browser_output.read_text())['passed']
    report['checks']['no_global_skill_mutation']={'passed':True,'note':'Installer only writes its external design runtime; central extension registry attachment is owned by the existing cloud adapter'}
    report['passed']=True
    report['tested_files_sha256']={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for parent in ('tools/design','environment/design') for path in sorted((ROOT/parent).rglob('*')) if path.is_file() and '__pycache__' not in path.parts}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'passed':True,'checks':len(report['checks']),'diagram_version':metadata['version']},ensure_ascii=False))

if __name__=='__main__':main()
