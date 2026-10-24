#!/usr/bin/env python3
"""Install a pinned, integrity-checked Diagram Design skill outside business repos."""
from __future__ import annotations
import hashlib
import os
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import urllib.request

SOURCE=Path(__file__).resolve().parent
REPOSITORY=SOURCE.parents[1]
RUNTIME=Path(os.environ.get('CLOUDKIT_DESIGN_ROOT',Path.cwd()/'design'))
LOCK=json.loads((SOURCE/'upstreams.lock.json').read_text())
PIN=LOCK['diagram_design']
DEST=RUNTIME/'diagram-design'/PIN['source_commit']

def verify(directory):
    bad=[]
    for name,wanted in PIN['file_sha256'].items():
        path=directory/name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=wanted:bad.append(name)
    if bad:raise ValueError('Pinned Diagram Design source differs; preserve and inspect: '+', '.join(bad[:8]))

def main():
    for path in (RUNTIME,RUNTIME.parent,RUNTIME/'bin',RUNTIME/'downloads',RUNTIME/'installation.json',DEST,DEST.parent):
        if path.is_symlink():raise ValueError('Managed design installation paths must not be symlinks')
    RUNTIME.mkdir(parents=True,exist_ok=True)
    record=RUNTIME/'installation.json'
    previous=json.loads(record.read_text()) if record.is_file() else {}
    reused=DEST.exists()
    if reused:
        verify(DEST)
    else:
        cache=RUNTIME/'downloads';cache.mkdir(exist_ok=True)
        archive=cache/(PIN['source_commit']+'.tar.gz')
        if not archive.exists():
            temporary=archive.with_suffix('.download')
            request=urllib.request.Request(PIN['source_url'],headers={'User-Agent':'Codex-cloud-design-installer'})
            with urllib.request.urlopen(request,timeout=60) as response,temporary.open('wb') as out:shutil.copyfileobj(response,out)
            if hashlib.sha256(temporary.read_bytes()).hexdigest()!=PIN['archive_sha256']:
                temporary.unlink();raise ValueError('Official Diagram Design archive integrity mismatch')
            temporary.replace(archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest()!=PIN['archive_sha256']:raise ValueError('Cached Diagram Design archive integrity mismatch')
        DEST.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='diagram-design-',dir=DEST.parent) as temporary:
            stage=Path(temporary)/'source';stage.mkdir()
            with tarfile.open(archive) as package:
                for member in package:
                    parts=PurePosixPath(member.name).parts
                    if not parts or PurePosixPath(member.name).is_absolute() or '..' in parts:raise ValueError('Unsafe upstream archive path')
                    relative=Path(*parts[1:])
                    if member.isdir():continue
                    if not member.isfile() or relative.as_posix() not in PIN['file_sha256']:raise ValueError('Unexpected upstream archive member')
                    target=stage/relative;target.parent.mkdir(parents=True,exist_ok=True)
                    with package.extractfile(member) as incoming,target.open('wb') as out:shutil.copyfileobj(incoming,out)
            verify(stage);stage.replace(DEST)
    tools=RUNTIME/'bin';tools.mkdir(exist_ok=True)
    for name,source in [('design-tools',REPOSITORY/'tools/design/design_tools.py')]:
        target=tools/name
        if target.is_symlink():raise ValueError('Managed launcher must not be a symlink')
        wanted=hashlib.sha256(source.read_bytes()).hexdigest()
        current=hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
        if current is not None and current not in {wanted,previous.get('managed_launcher_sha256')}:
            raise ValueError('Existing custom design launcher was preserved; inspect it before managed replacement')
        shutil.copyfile(source,target);target.chmod(0o755)
    metadata={'schema_version':1,'repository_root':str(REPOSITORY),'source_commit':PIN['source_commit'],'version':PIN['version'],'upstream_source':str(DEST),'skill_path':str(DEST/PIN['skill_relative_path']),'skills':[{'name':'diagram-design','path':str(DEST/PIN['skill_relative_path'])}],'bin_dirs':[str(tools)],'profile_command':[str(tools/'design-tools'),'profile','--project-root','<actual-project-root>'],'skill_activation':'filesystem install; read SKILL.md explicitly or attach via central adapter; UI marketplace activation not claimed','source_files_verified':len(PIN['file_sha256']),'unchanged_source_reused':reused,'managed_launcher_sha256':wanted}
    record.write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(metadata,ensure_ascii=False))

if __name__=='__main__':main()
