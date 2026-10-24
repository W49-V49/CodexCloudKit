#!/usr/bin/env python3
"""Install hash-pinned portable language tools without changing platform runtimes."""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from managed import Managed

SOURCE = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, default=Path(os.environ.get('CLOUDKIT_LANGUAGE_ROOT',Path.cwd()/'languages')))
parser.add_argument('--component', action='append', help='Only install these pinned archive components')
args = parser.parse_args()
root = args.root.resolve()
if platform.system() != 'Linux' or platform.machine() != 'x86_64':
    raise SystemExit('This lock targets Linux x86_64; select matching official artifacts first.')
lock = json.loads((SOURCE/'environment/languages/toolchain.lock.json').read_text())
selected = args.component or list(lock['artifacts'])
if any(name not in lock['artifacts'] for name in selected):
    raise SystemExit('Unknown pinned component')
cache = root/'downloads'
cache.mkdir(parents=True, exist_ok=True)
versions = root/'versions'
versions.mkdir(exist_ok=True)
managed=Managed(root)
records = {}

def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()

def download(name):
    item = lock['artifacts'][name]
    suffix = '.tar.xz' if item['url'].endswith('.xz') else '.tar.gz'
    target = cache/(name+'-'+item['sha256'][:16]+suffix)
    if not target.exists() or sha(target) != item['sha256']:
        temporary = target.with_suffix(target.suffix+'.pending')
        request = urllib.request.Request(item['url'], headers={'User-Agent':'Codex-cloud-language-installer'})
        with urllib.request.urlopen(request, timeout=180) as response, temporary.open('wb') as output:
            shutil.copyfileobj(response, output, length=1024*1024)
        if sha(temporary) != item['sha256']:
            temporary.unlink()
            raise ValueError('Official artifact digest mismatch: '+name)
        temporary.replace(target)
    return name, target

downloads = dict(concurrent.futures.ThreadPoolExecutor(max_workers=4).map(download, selected))
for name in selected:
    item = lock['artifacts'][name]
    destination = versions/(name+'-'+item['sha256'][:16])
    marker = destination/'installed.json'
    if not marker.exists():
        with tempfile.TemporaryDirectory(prefix='language-extract-', dir=root) as temporary:
            stage = Path(temporary)
            with tarfile.open(downloads[name]) as archive:
                archive.extractall(stage, filter='data')
            if item['kind'] == 'rust-component':
                installers = list(stage.glob('*/install.sh'))
                if len(installers) != 1:
                    raise ValueError('Unexpected Rust distribution layout')
                subprocess.run(['bash',str(installers[0]),'--prefix='+str(root/'rust'),'--disable-ldconfig'],check=True)
                destination.mkdir(exist_ok=True)
            else:
                stage.rename(destination)
            marker.write_text(json.dumps({'name':name,'sha256':item['sha256'],'version':item['version']},indent=2)+'\n')
    if name == 'jdk':
        candidates = [p.parent.parent for p in destination.glob('*/bin/javac')]
        if len(candidates) != 1:
            raise ValueError('Unexpected official JDK layout')
        home = candidates[0]
        managed.link(root/'jdk',home)
    records[name] = {'version':item['version'],'official_sha256':item['sha256'],'archive':str(downloads[name]),'installed_dir':str(destination)}

(root/'bin').mkdir(exist_ok=True)
def link(name, target):
    managed.link(root/'bin'/name,target)

for name in selected:
    destination=Path(records[name]['installed_dir'])
    if name=='go':
        for binary in ('go','gofmt'): link(binary,destination/'go/bin'/binary)
    elif name=='jdk':
        for binary in ('java','javac','jar','javadoc','jlink','jpackage'): link(binary,root/'jdk/bin'/binary)
    elif name=='neovim': link('nvim',destination/'nvim-linux-x86_64/bin/nvim')
    elif name=='shellcheck': link('shellcheck',next(destination.glob('*/shellcheck')))
if (root/'rust/bin').exists():
    for name in ('rustc','rustdoc','cargo','rustfmt','cargo-fmt','clippy-driver','cargo-clippy','rust-analyzer','rust-gdb','rust-gdbgui','rust-lldb'):
        path=root/'rust/bin'/name
        if path.is_file():link(name,path)
record=root/'archive-installation.json'
old=json.loads(record.read_text()) if record.exists() else {}
old.update(records)
record.write_text(json.dumps(old,indent=2)+'\n')
print(json.dumps({'installed':list(records),'root':str(root),'java_home':str(root/'jdk') if (root/'jdk').exists() else None}))
