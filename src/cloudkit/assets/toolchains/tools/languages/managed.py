#!/usr/bin/env python3
"""Preserve custom files and link targets when updating the owned tool prefix."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil


class Managed:
    def __init__(self, root):
        self.root=Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        self.receipt=self.root/'managed-installation.json'
        if self.receipt.is_symlink():raise ValueError('Preserve custom managed receipt symlink')
        self.data=json.loads(self.receipt.read_text()) if self.receipt.exists() else {'schema_version':1,'files':{},'links':{}}
        if self.data.get('schema_version')!=1:raise ValueError('Unsupported managed installation receipt')

    def key(self,path):
        if '..' in Path(path).parts:raise ValueError('Managed target must not contain parent traversals')
        path=Path(os.path.abspath(path))
        relative=path.relative_to(self.root)
        for parent in path.parents:
            if parent==self.root:break
            if parent.is_symlink():raise ValueError('Preserve custom symlink in managed target parent: '+str(parent))
        return str(relative)

    def save(self):
        pending=self.receipt.with_suffix('.pending')
        if pending.exists() or pending.is_symlink():raise ValueError('Preserve pre-existing managed pending file')
        pending.write_text(json.dumps(self.data,indent=2)+'\n')
        pending.replace(self.receipt)

    def check_file(self,source,target):
        target=Path(target);key=self.key(target)
        desired=hashlib.sha256(Path(source).read_bytes()).hexdigest()
        if target.is_symlink():raise ValueError('Preserve custom managed file symlink: '+str(target))
        if target.exists():
            if not target.is_file():raise ValueError('Preserve custom non-file target: '+str(target))
            current=hashlib.sha256(target.read_bytes()).hexdigest()
            if current not in {desired,self.data['files'].get(key)}:
                raise ValueError('Preserve customized file; original bytes retained: '+str(target))
        return key,desired

    def file(self,source,target,executable=False):
        target=Path(target);key,desired=self.check_file(source,target)
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest()!=desired:
            temporary=target.with_name(target.name+'.managed-pending')
            if temporary.exists() or temporary.is_symlink():raise ValueError('Preserve custom pending target: '+str(temporary))
            shutil.copyfile(source,temporary)
            if executable:temporary.chmod(0o755)
            temporary.replace(target)
        elif executable:target.chmod(0o755)
        self.data['files'][key]=desired;self.save()

    def link(self,path,target):
        path=Path(path);key=self.key(path);desired=os.path.abspath(target)
        if not Path(desired).is_relative_to(self.root):raise ValueError('Managed launcher must target the isolated tool prefix')
        if path.is_symlink():
            current=os.readlink(path)
            if current not in {desired,self.data['links'].get(key)}:
                raise ValueError('Preserve customized launcher target; link retained: '+str(path))
            if current!=desired:path.unlink()
        elif path.exists():raise ValueError('Preserve user-owned non-link launcher: '+str(path))
        path.parent.mkdir(parents=True,exist_ok=True)
        if not path.is_symlink():path.symlink_to(desired,target_is_directory=Path(target).is_dir())
        self.data['links'][key]=desired;self.save()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--preflight',action='store_true')
    parser.add_argument('--file',nargs=2,action='append',default=[],metavar=('SOURCE','TARGET'))
    parser.add_argument('--executable-file',nargs=2,action='append',default=[],metavar=('SOURCE','TARGET'))
    args=parser.parse_args();managed=Managed(args.root)
    for source,target in args.file+args.executable_file:managed.check_file(source,target)
    if not args.preflight:
        for source,target in args.file:managed.file(source,target)
        for source,target in args.executable_file:managed.file(source,target,True)
    print(json.dumps({'preflight':args.preflight,'managed_files':len(args.file)+len(args.executable_file)}))


if __name__=='__main__':main()
