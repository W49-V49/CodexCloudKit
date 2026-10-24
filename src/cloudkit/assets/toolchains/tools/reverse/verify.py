#!/usr/bin/env python3
"""Real benign-fixture acceptance for the pinned reverse-tool installation."""
import argparse,hashlib,json,subprocess,sys
from pathlib import Path
from reverse_tools import RUNTIME,analyze

parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
options=parser.parse_args();out=options.output.resolve();out.mkdir(parents=True,exist_ok=True)
report={'schema_version':1,'passed':False,'scope':'Benign compiled fixture static analysis, actual Ghidra decompilation and pinned upstream synthetic AnyPS5 tests; no external target or game files','checks':{}}
source=out/'fixture.c';source.write_text('#include <stdio.h>\nint fixture_sum(int a,int b){return a+b+7;}\nint main(void){printf("fixture=%d\\n",fixture_sum(2,3));return 0;}\n')
binary=out/'fixture';subprocess.run(['gcc','-g','-O0','-fno-inline',str(source),'-o',str(binary)],check=True)
report['fixture_source_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
result=analyze(binary,out/'analysis',symbol='fixture_sum')
functions=result['decompilation']['functions']
assert any(item['name']=='fixture_sum' and '7' in item['c'] and '+' in item['c'] for item in functions)
report['checks']['ghidra_real_decompilation']={'passed':True,'version':'12.2','functions':[f['name'] for f in functions],'input_executed':False}
hexyl=subprocess.run([str(RUNTIME/'bin/hexyl'),'--color','never','--length','16',str(binary)],capture_output=True,text=True,check=True)
(out/'hexyl.log').write_text(hexyl.stdout);assert '45 4c 46' in hexyl.stdout.lower()
report['checks']['hexyl_elf_magic']={'passed':True,'version':'0.17.0'}
disasm=subprocess.run([str(RUNTIME/'bin/rizin'),'-q','-c','aaa;pdf @ sym.fixture_sum',str(binary)],capture_output=True,text=True,check=True,timeout=60)
(out/'rizin-disassembly.log').write_text(disasm.stdout+disasm.stderr);assert 'fixture_sum' in disasm.stdout
report['checks']['rizin_disassembly']={'passed':True,'version':'0.9.1'}
base=RUNTIME/'anyps5/source/AnyPS5-3fd1cc393b8512c2e989860053f30dee5a8cd1aa'
cases=('core/relinker/cli/tests/test_help.py','core/relinker/relinker/tests/test_input_magic.py','core/relinker/relinker/tests/test_dynamic_segment.py')
results=[]
for relative in cases:
    script=base/relative
    run=subprocess.run([sys.executable,str(script),str(RUNTIME/'anyps5/relinker')],capture_output=True,text=True,timeout=90)
    (out/('anyps5-'+script.stem+'.log')).write_text(run.stdout+run.stderr)
    assert run.returncode==0,(relative,run.stderr)
    results.append({'test':relative,'source_sha256':hashlib.sha256(script.read_bytes()).hexdigest(),'passed':True})
report['checks']['anyps5_synthetic_regressions']={'passed':True,'version':'0.1.3','suite_count':3,'cases':results,'converted_output_executed':False}
report['checks']['file_elf_format']={'passed':'ELF 64-bit' in result['format'],'format':result['format']}
assert report['checks']['file_elf_format']['passed']
python_check=out/'python-libraries.json'
subprocess.run([str(RUNTIME/'python/.venv/bin/python'),str(Path(__file__).with_name('verify_python.py')),'--binary',str(binary),'--output',str(python_check)],check=True,capture_output=True,text=True)
report['checks']['python_binary_libraries']=json.loads(python_check.read_text())
report['passed']=True
(out/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False,indent=2))
