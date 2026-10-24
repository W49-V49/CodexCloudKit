#!/usr/bin/env python3
"""Resolve a cloud project/branch style profile without writing HOME or vendors."""
from __future__ import annotations
import os
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

def git(project,*arguments,optional=False):
    result=subprocess.run(['git','-C',str(project),*arguments],capture_output=True,text=True)
    if result.returncode and not optional:raise ValueError('Project Git identity is unavailable; raw remotes were withheld')
    return result.stdout.strip() if result.returncode==0 else None

def profile(project,store_root=None,name='cloud-auto',source=None):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}',name) or name=='default':raise ValueError('Use a safe nonreserved profile slug')
    project=Path(git(Path(project).resolve(),'rev-parse','--show-toplevel')).resolve()
    remote=git(project,'remote','get-url','origin')
    if remote.startswith('git@github.com:'):identity=remote.split(':',1)[1].removesuffix('.git')
    else:
        parsed=urlsplit(remote)
        if parsed.scheme not in {'https','ssh'} or parsed.hostname!='github.com':raise ValueError('Bind the actual authorized GitHub project origin first')
        identity=parsed.path.strip('/').removesuffix('.git')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',identity):raise ValueError('Invalid project identity')
    branch=git(project,'symbolic-ref','--quiet','--short','HEAD')
    if not branch:raise ValueError('A project branch is required for an isolated profile')
    key=hashlib.sha256((identity+'\0'+branch).encode()).hexdigest()[:24]
    root=Path(store_root or os.environ.get('CLOUDKIT_DESIGN_STATE_ROOT',str(Path(os.environ.get('CLOUDKIT_DESIGN_ROOT',Path.cwd()/'design')).parent/'state/design/projects'))).absolute()
    directory=root/key;target=directory/'profiles'/(name+'.md')
    for candidate in [root,*root.parents,directory,directory/'profiles',target,directory/'project.json']:
        if candidate.is_symlink():raise ValueError('Cloud profile paths must not use symlinks')
    record=directory/'project.json'
    old=json.loads(record.read_text()) if record.exists() else {}
    if old.get('project_id',identity)!=identity or old.get('branch',branch)!=branch:raise ValueError('Existing profile index belongs to a different project; preserved')
    installation=json.loads((Path(os.environ.get('CLOUDKIT_DESIGN_ROOT',Path.cwd()/'design'))/'installation.json').read_text())
    pristine=Path(installation['skill_path'])/'references/style-guide.md'
    lock=json.loads((Path(installation['repository_root'])/'environment/design/upstreams.lock.json').read_text())
    expected=lock['diagram_design']['file_sha256']['skills/diagram-design/references/style-guide.md']
    if hashlib.sha256(pristine.read_bytes()).hexdigest()!=expected:raise ValueError('Installed pristine profile template differs; preserve it and revalidate')
    if target.exists():
        body=target.read_text();created=False
    else:
        body=Path(source).read_text() if source else pristine.read_text().replace('Font source: `web`','Font source: `system`')
        if len(body.encode())>65536:raise ValueError('Style profiles must fit within 64 KiB; do not store sessions or logs')
        if any(marker not in body for marker in ('### Semantic roles','## Typography','Font source:')):raise ValueError('A complete style guide schema is required')
        if re.search(r'(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,})',body):raise ValueError('Credential-pattern material rejected')
        stamp=datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        header=f'<!-- diagram-design-profile\nname: Cloud project profile\nslug: {name}\nsource-url: none\ncreated: {stamp}\nupdated: {stamp}\nnotes: Explicit cloud profile; existing project branding takes priority; system fonts avoid network dependency\n-->\n\n'
        body=re.sub(r'\A<!-- diagram-design-profile\n.*?-->\n\n','',body,flags=re.S)
        content=header+body
        if len(content.encode())>65536:raise ValueError('Serialized style profiles must fit within 64 KiB')
        directory.mkdir(parents=True,exist_ok=True);target.parent.mkdir(exist_ok=True)
        target.write_text(content);created=True
    if len(body.encode())>65536 or any(marker not in body for marker in ('### Semantic roles','## Typography','Font source:')):
        raise ValueError('Existing profile was preserved but its full style schema/size is invalid')
    metadata={'schema_version':1,'project_id':identity,'branch':branch,'project_path':str(project),'partition_key':key,'profile':str(target),'profile_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'created':created,'profile_existing_preserved':not created,'upstream_source_unchanged':hashlib.sha256(pristine.read_bytes()).hexdigest()==expected,'resolution':'Explicit cloud adapter profile; read this complete guide alongside SKILL.md, do not use upstream HOME-based marker lookup or mutate the managed style guide.'}
    directory.mkdir(parents=True,exist_ok=True)
    record.write_text(json.dumps(dict(old,**metadata),ensure_ascii=False,indent=2)+'\n')
    return metadata

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root',required=True);parser.add_argument('--name',default='cloud-auto')
    parser.add_argument('--store-root');parser.add_argument('--source',help='Optional full project brand style guide; existing profiles are preserved')
    args=parser.parse_args();print(json.dumps(profile(args.project_root,args.store_root,args.name,args.source),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
