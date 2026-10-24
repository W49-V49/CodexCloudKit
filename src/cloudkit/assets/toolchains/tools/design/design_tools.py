#!/usr/bin/env python3
"""Explicit, project-independent entry points for the pinned diagram helpers."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('status','skill-path','profile','check','import-mermaid','import-drawio','import-excalidraw','export-svg','export-png'))
    parser.add_argument('arguments',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    runtime=Path(os.environ.get('CLOUDKIT_DESIGN_ROOT',Path(__file__).resolve().parents[1]))
    os.environ['CLOUDKIT_DESIGN_ROOT']=str(runtime)
    os.environ.setdefault('CLOUDKIT_PLAYWRIGHT_MODULE',str(runtime/'browser/node_modules/playwright'))
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(runtime/'browser/cache'))
    metadata=json.loads((runtime/'installation.json').read_text())
    browser=metadata.get('browser',{})
    if browser.get('executable'):os.environ.setdefault('CLOUDKIT_CHROMIUM_PATH',browser['executable'])
    skill=Path(metadata['skill_path'])
    if args.action=='status':print(json.dumps(metadata,ensure_ascii=False,indent=2));return 0
    if args.action=='skill-path':print(skill);return 0
    if args.action=='profile':
        return subprocess.run([sys.executable,str(Path(metadata['repository_root'])/'tools/design/project_profile.py'),*args.arguments]).returncode
    if args.action=='export-png':
        if len(args.arguments) not in {2,3}:parser.error('export-png requires input.html output.png [scale]')
        admitted=subprocess.run([sys.executable,str(skill/'scripts/self_check.py'),args.arguments[0]],capture_output=True,text=True)
        if admitted.returncode:
            print(admitted.stdout+admitted.stderr,file=sys.stderr);return admitted.returncode
        return subprocess.run(['node',str(Path(metadata['repository_root'])/'tools/design/export_png.cjs'),*args.arguments]).returncode
    scripts={'check':'self_check.py','import-mermaid':'mermaid_extract.py','import-drawio':'drawio_extract.py','import-excalidraw':'excalidraw_extract.py','export-svg':'export_svg.py'}
    script=skill/'scripts'/scripts[args.action]
    return subprocess.run([sys.executable,str(script),*args.arguments]).returncode

if __name__=='__main__':raise SystemExit(main())
