#!/usr/bin/env python3
"""Use centrally installed static binary-analysis tools without executing the input."""
import argparse,hashlib,json,os,re,shutil,subprocess,sys,tempfile
from pathlib import Path

RUNTIME=Path(os.environ.get('REVERSE_TOOLS_ROOT',str(Path(__file__).resolve().parents[1]))).resolve()
def java_environment():
    candidates=[os.environ.get('REVERSE_JAVA_HOME'),str(RUNTIME.parent/'languages/jdk'),os.environ.get('JAVA_HOME')]
    selected=None
    for raw in candidates:
        if not raw:continue
        folder=Path(raw)
        if not (folder/'bin/javac').is_file():continue
        probe=subprocess.run([str(folder/'bin/java'),'-version'],capture_output=True,text=True)
        match=re.search(r'version "(\d+)',probe.stderr+probe.stdout)
        if probe.returncode==0 and match and int(match[1])>=25:selected=folder;break
    if selected is None:raise RuntimeError('Ghidra12.2 needs JDK25+; install the central language toolchain or set REVERSE_JAVA_HOME')
    environment=os.environ.copy()
    environment['JAVA_HOME']=str(selected)
    environment['PATH']=str(selected/'bin')+os.pathsep+environment.get('PATH','')
    for variable,relative in [('XDG_CONFIG_HOME','state/config'),('XDG_CACHE_HOME','state/cache'),('TMPDIR','state/tmp')]:
        path=RUNTIME/relative;path.mkdir(parents=True,exist_ok=True);environment[variable]=str(path)
    return environment

def headless(arguments):
    launcher=RUNTIME/'ghidra/ghidra_12.2_PUBLIC/support/analyzeHeadless'
    return subprocess.call([str(launcher),*arguments],env=java_environment())

def analyze(binary,output,symbol=None):
    binary=Path(binary).resolve(strict=True);output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=True)
    data=binary.read_bytes()
    report={'input':str(binary),'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'input_executed':False}
    commands={
       'format':['file','-b',str(binary)],
       'binary_info':[str(RUNTIME/'rizin/bin/rz-bin'),'-Ij',str(binary)],
       'symbols':[str(RUNTIME/'rizin/bin/rz-bin'),'-sj',str(binary)],
       'sections':[str(RUNTIME/'rizin/bin/rz-bin'),'-Sj',str(binary)]}
    for name,command in commands.items():
        result=subprocess.run(command,capture_output=True,text=True,timeout=60)
        (output/(name+'.log')).write_text(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError(name+' failed; see evidence output')
        if name=='format':report[name]=result.stdout.strip()
        else:
            value=json.loads(result.stdout);(output/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')
            if name=='binary_info':report[name]=value
    if symbol:
        script=Path(__file__).resolve().with_name('ExportFunctions.java')
        with tempfile.TemporaryDirectory(prefix='headless-project-',dir=output) as project:
            generated=Path(project)/'current-decompilation.json'
            command=[str(RUNTIME/'ghidra/ghidra_12.2_PUBLIC/support/analyzeHeadless'),project,'fixture-analysis',
               '-import',str(binary),'-analysisTimeoutPerFile','120','-max-cpu','2',
               '-scriptPath',str(script.parent),'-postScript',script.name,str(generated),symbol,'-deleteProject']
            result=subprocess.run(command,capture_output=True,text=True,env=java_environment(),timeout=240)
            (output/'ghidra-headless.log').write_text(result.stdout+result.stderr)
            if result.returncode or not generated.exists():raise RuntimeError('Ghidra decompilation failed; see ghidra-headless.log')
            decompiled=json.loads(generated.read_text())
            if decompiled.get('program')!=binary.name or not decompiled.get('functions') or any(item.get('name')!=symbol for item in decompiled['functions']):
                raise RuntimeError('Ghidra output belongs to a different input or symbol')
            generated.replace(output/'decompilation.json')
        report['decompilation']=decompiled
    if hashlib.sha256(binary.read_bytes()).hexdigest()!=report['sha256']:raise RuntimeError('Input changed during static analysis; preserve evidence and retry the new version')
    report['passed']=True
    (output/'analysis.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('inventory')
    item=sub.add_parser('analyze');item.add_argument('--binary',required=True);item.add_argument('--output',required=True)
    item.add_argument('--decompile',action='store_true');item.add_argument('--symbol',default='main')
    args=parser.parse_args()
    if args.action=='inventory':result=json.loads((RUNTIME/'installation.json').read_text())
    else:result=analyze(args.binary,args.output,args.symbol if args.decompile else None)
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='headless':raise SystemExit(headless(sys.argv[2:]))
    raise SystemExit(main())
