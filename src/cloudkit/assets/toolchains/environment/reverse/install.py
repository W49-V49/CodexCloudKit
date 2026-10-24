#!/usr/bin/env python3
"""Pinned headless reverse tools; source and runtime remain separate."""
import argparse,hashlib,json,os,platform,shutil,stat,subprocess,tarfile,zipfile
from pathlib import Path

SOURCE=Path(__file__).resolve().parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--runtime',type=Path,default=Path(os.environ.get('REVERSE_TOOLS_ROOT',Path.cwd()/'reverse')))
args=parser.parse_args();raw_root=args.runtime.absolute()
for candidate in (raw_root,*raw_root.parents):
    if candidate.is_symlink():raise SystemExit('Reverse runtime cannot traverse a symlink')
root=raw_root.resolve()
if platform.system()!='Linux' or platform.machine() not in {'x86_64','AMD64'}:raise SystemExit('These verified archives require Linux x86_64')
lock=json.loads((SOURCE/'lock.json').read_text());root.mkdir(parents=True,exist_ok=True)
def inside(path):
    if not path.resolve().is_relative_to(root):raise SystemExit('Reverse runtime path escapes its root')
    for candidate in (path,*path.parents):
        if candidate==root:break
        if candidate.is_symlink():raise SystemExit('Reverse runtime target cannot traverse a symlink')
for relative in ('downloads','bin','managed','python','python/.venv','python/.venv/bin','downloads/uv-cache','installation.json','lock.json'):
    inside(root/relative)
(root/'downloads').mkdir(exist_ok=True);(root/'bin').mkdir(exist_ok=True)

def sha(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()

tool_sources=SOURCE.parents[1]/'tools/reverse'
managed=root/'managed'
pairs={managed/path.name:path.read_bytes() for path in tool_sources.iterdir() if path.is_file() and path.suffix in {'.py','.java'} and not path.name.startswith('test_')}
pairs[root/'lock.json']=(SOURCE/'lock.json').read_bytes()
pairs[root/'python/requirements.lock']=(SOURCE/'requirements.lock').read_bytes()
import shlex
for name,command in {
    'reverse-tools':['python3',str(managed/'reverse_tools.py')],
    'ghidra-headless':['python3',str(managed/'reverse_tools.py'),'headless'],
    'hexyl':[str(root/'hexyl/hexyl-v0.17.0-x86_64-unknown-linux-musl/hexyl')],
    'anyps5-relinker':[str(root/'anyps5/relinker')],
    **{name:[str(root/'rizin/bin'/name)] for name in ('rizin','rz-bin','rz-diff','rz-hash','rz-asm')}
}.items():
    pairs[root/'bin'/name]=('#!/usr/bin/env bash\nset -euo pipefail\nexport REVERSE_TOOLS_ROOT='+shlex.quote(str(root))+'\nexec '+shlex.join(command)+' "$@"\n').encode()
previous_report=json.loads((root/'installation.json').read_text()) if (root/'installation.json').exists() else {}
old_hashes=previous_report.get('managed_sha256',{})
for path,content in pairs.items():
    inside(path)
    wanted=hashlib.sha256(content).hexdigest();relative=str(path.relative_to(root))
    if path.exists() and (not path.is_file() or sha(path) not in {wanted,old_hashes.get(relative)}):
        raise SystemExit('Differing user-owned file preserved: '+relative)

checks=[]
for item in lock['assets']:
    archive=root/'downloads'/item['cache_name']
    inside(archive);inside(root/item['target'])
    if not archive.exists():
        pending=archive.with_suffix(archive.suffix+'.partial')
        inside(pending)
        subprocess.run(['curl','--fail','--location','--connect-timeout','20','--max-time','600','--silent','--show-error',item['url'],'-o',str(pending)],check=True)
        if sha(pending)!=item['sha256']:
            pending.unlink();raise SystemExit('Official archive SHA256 mismatch: '+item['name'])
        pending.replace(archive)
    if sha(archive)!=item['sha256']:raise SystemExit('Cached archive SHA256 mismatch: '+item['name'])
    marker=root/('.installed-'+item['name']+'.json');target=root/item['target'];inside(marker)
    previous=json.loads(marker.read_text()) if marker.exists() else {}
    executable=root/item['executable'] if item.get('executable') else None
    if executable:
        inside(executable)
        if executable.exists() and previous.get('executable_sha256') and sha(executable)!=previous['executable_sha256']:
            raise SystemExit('Differing installed executable preserved: '+item['name'])
    if previous.get('archive_sha256')!=item['sha256'] or not target.exists():
        if item['archive']=='binary':
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(archive,target);target.chmod(0o755)
        else:
            target.mkdir(parents=True,exist_ok=True)
            if item['archive']=='tar':
                with tarfile.open(archive) as package:package.extractall(target,filter='data')
            else:
                with zipfile.ZipFile(archive) as package:
                    for entry in package.infolist():
                        destination=target/entry.filename
                        if not destination.resolve().is_relative_to(target.resolve()):raise ValueError('Unsafe archive path')
                        mode=entry.external_attr>>16
                        if stat.S_ISLNK(mode):raise ValueError('Unexpected ZIP symlink')
                        package.extract(entry,target)
                        if destination.is_file() and mode:destination.chmod(mode&0o777)
    if item.get('executable') and not (root/item['executable']).is_file():raise SystemExit('Installed launcher missing: '+item['name'])
    marker.write_text(json.dumps({'archive_sha256':item['sha256'],'version':item['version'],'executable_sha256':sha(executable) if executable else None},indent=2)+'\n')
    checks.append({'name':item['name'],'version':item['version'],'archive_sha256':item['sha256'],'verified':True})

python_root=root/'python';python_root.mkdir(exist_ok=True)
python_executable=python_root/'.venv/bin/python'
uv=shutil.which('uv')
if uv is None:raise SystemExit('The central cloud bootstrap must provide uv before installing hashed binary libraries')
environment=os.environ.copy();environment['UV_CACHE_DIR']=str(root/'downloads/uv-cache')
if not python_executable.exists():
    import sys
    subprocess.run([uv,'venv','--python',sys.executable,str(python_root/'.venv')],check=True,env=environment)
subprocess.run([uv,'pip','install','--python',str(python_executable),'--require-hashes','-r',str(SOURCE/'requirements.lock')],check=True,env=environment)
managed.mkdir(exist_ok=True)
fingerprints={}
for target,content in pairs.items():
    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
    if target.parent.name=='bin':target.chmod(0o755)
    fingerprints[str(target.relative_to(root))]=hashlib.sha256(content).hexdigest()
report={'schema_version':1,'runtime':str(root),'assets':checks,'managed_sha256':fingerprints,'python_requirements_sha256':sha(SOURCE/'requirements.lock'),'python_executable':str(python_executable),'source_root':str(SOURCE.parents[1]),'java_requirement':'JDK25+; reused from the central language toolchain','platform_packages_reused':['file','GNU binutils','gcc'],'scope':'Headless Ghidra, Rizin CLI, hexyl, AnyPS5 stable relinker-only, isolated LIEF/Capstone/YARA; not full GUI or a game runtime'}
(root/'installation.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
