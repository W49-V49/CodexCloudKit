#!/usr/bin/env python3
"""Benign executable and language-server acceptance for the isolated toolchain."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
import traceback
from language_tools import ROOT, environment

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
env=environment();checks={}
def call(command,cwd,success=True,input=None):
    result=subprocess.run(command,cwd=cwd,env=env,input=input,capture_output=True,text=True,timeout=120)
    if success and result.returncode:raise ValueError('Command failed: '+command[0]+'\n'+result.stderr[-3000:])
    if not success and not result.returncode:raise ValueError('Invalid fixture was not rejected: '+command[0])
    return result

class LSP:
    def __init__(self,command,cwd):
        self.process=subprocess.Popen(command,cwd=cwd,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.queue=queue.Queue();self.counter=0
        def read():
            try:
                while True:
                    headers={}
                    while True:
                        line=self.process.stdout.readline()
                        if not line:return
                        if line in (b'\r\n',b'\n'):break
                        key,_,value=line.decode().partition(':');headers[key.lower()]=value.strip()
                    size=int(headers['content-length']);data=json.loads(self.process.stdout.read(size));self.queue.put(data)
            except Exception as error:self.queue.put(error)
        threading.Thread(target=read,daemon=True).start()
    def send(self,value):
        data=json.dumps(value).encode();self.process.stdin.write(('Content-Length: '+str(len(data))+'\r\n\r\n').encode()+data);self.process.stdin.flush()
    def notify(self,method,params):self.send({'jsonrpc':'2.0','method':method,'params':params})
    def request(self,method,params):
        self.counter+=1;ident=self.counter;self.send({'jsonrpc':'2.0','id':ident,'method':method,'params':params})
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            data=self.queue.get(timeout=max(.01,deadline-time.monotonic()))
            if isinstance(data,Exception):raise data
            if data.get('id')==ident and 'method' not in data:
                if data.get('error'):raise ValueError(json.dumps(data['error']))
                return data.get('result')
            if 'id' in data and 'method' in data:
                result=[] if data['method']=='workspace/configuration' else None
                self.send({'jsonrpc':'2.0','id':data['id'],'result':result})
        raise TimeoutError('LSP response timeout')
    def close(self):
        try:self.request('shutdown',None);self.notify('exit',None);self.process.wait(timeout=3)
        except Exception:self.process.kill();self.process.wait(timeout=3)
        self.process.stdin.close();self.process.stdout.close();self.process.stderr.close()

try:
 with tempfile.TemporaryDirectory(prefix='language-acceptance-',dir=ROOT) as temporary:
    work=Path(temporary)
    (work/'test_example.py').write_text('def test_arithmetic():\n    assert sum([1, 2, 3]) == 6\n')
    result=call(['pytest','-q','test_example.py'],work);checks['python_test']={'passed':True,'result':result.stdout.strip()}
    (work/'bad.py').write_text('print(undefined_fixture_variable)\n')
    result=call(['ruff','check','bad.py'],work,success=False);assert 'F821' in result.stdout
    checks['python_lint_rejects_error']={'passed':True,'rule':'F821'}
    (work/'hello.ts').write_text('const answer: number = 42;\nconsole.log(answer);\n')
    call(['tsc','--strict','--noEmit','--target','ES2022','--skipLibCheck','hello.ts'],work)
    (work/'bad.ts').write_text('const answer: number = "wrong";\n')
    result=call(['tsc','--strict','--noEmit','--skipLibCheck','bad.ts'],work,success=False);assert 'TS2322' in result.stdout
    checks['typescript_semantics']={'passed':True,'invalid_assignment_rejected':'TS2322'}
    (work/'eslint.config.mjs').write_text('export default [{rules:{"no-unused-vars":"error"}}];\n')
    (work/'bad.js').write_text('const unusedFixture = 1;\n')
    result=call(['eslint','bad.js'],work,success=False);assert 'no-unused-vars' in result.stdout
    checks['javascript_lint']={'passed':True,'unused_variable_rejected':True}
    (work/'format.js').write_text('const fixture={answer:42}\n')
    call(['prettier','--check','format.js'],work,success=False);call(['prettier','--write','format.js'],work);call(['prettier','--check','format.js'],work)
    checks['prettier_roundtrip']={'passed':True}
    result=call(['biome','format','--stdin-file-path','example.js'],work,input='const fixture={answer:42};')
    assert 'answer: 42' in result.stdout;checks['biome_formatter']={'passed':True}
    (work/'hello.c').write_text('#include <stdio.h>\nint main(void) { puts("C_OK"); return 0; }\n')
    call(['gcc','-std=c17','-Wall','-Werror','hello.c','-o','hello-c'],work);assert call([str(work/'hello-c')],work).stdout.strip()=='C_OK'
    checks['c_compile_run']={'passed':True,'compiler':'platform GCC'}
    (work/'hello.cpp').write_text('#include <iostream>\nint main() { std::cout << "CPP_OK"; }\n')
    (work/'CMakeLists.txt').write_text('cmake_minimum_required(VERSION 3.20)\nproject(cloud_fixture LANGUAGES CXX)\nset(CMAKE_CXX_STANDARD 20)\nadd_executable(hello-cpp hello.cpp)\n')
    call(['cmake','-S','.', '-B','build'],work);call(['cmake','--build','build'],work);assert call([str(work/'build/hello-cpp')],work).stdout.strip()=='CPP_OK'
    checks['cpp_cmake_compile_run']={'passed':True,'standard':'C++20'}
    (work/'Hello.java').write_text('public class Hello { public static void main(String[] args) { System.out.print("JAVA_OK"); } }\n')
    call(['javac','--release','25','Hello.java'],work);assert call(['java','-cp',str(work),'Hello'],work).stdout=='JAVA_OK'
    checks['java_compile_run']={'passed':True,'jdk_major':25}
    (work/'hello.go').write_text('package main\nimport "fmt"\nfunc main() { fmt.Print("GO_OK") }\n')
    assert call(['go','run','hello.go'],work).stdout=='GO_OK';checks['go_compile_run']={'passed':True,'real_language_toolchain_not_platform_Go_board_game':True}
    rust=work/'rust';(rust/'src').mkdir(parents=True)
    (rust/'Cargo.toml').write_text('[package]\nname="cloud_fixture"\nversion="0.1.0"\nedition="2024"\n')
    (rust/'src/main.rs').write_text('fn main() { println!("RUST_OK"); }\n#[test]\nfn arithmetic() { assert_eq!(2 + 2, 4); }\n')
    call(['cargo','fmt'],rust);call(['cargo','test','--offline'],rust);call(['cargo','clippy','--offline','--','-D','warnings'],rust)
    assert call(['cargo','run','--offline','--quiet'],rust).stdout.strip()=='RUST_OK'
    checks['rust_offline_cargo_test_clippy_run']={'passed':True,'edition':'2024'}
    (work/'good.sh').write_text('#!/usr/bin/env bash\nvalue=fixture\nprintf "%s\\n" "$value"\n')
    (work/'bad.sh').write_text('#!/usr/bin/env bash\nvalue="${1:-fixture}"\nprintf "%s\\n" $value\n')
    call(['shellcheck','good.sh'],work);result=call(['shellcheck','bad.sh'],work,success=False);assert 'SC2086' in result.stdout
    checks['shell_lint']={'passed':True,'unquoted_expansion_rejected':'SC2086'}
    result=call(['python','-m','sqlite3',':memory:','select 40 + 2;'],work);assert '42' in result.stdout
    formatted=call(['sql-formatter','--language','sqlite'],work,input='select id from users where id=42;').stdout
    assert 'select' in formatted.lower() and 'from' in formatted.lower() and formatted.count('\n')>=2;checks['sqlite_and_sql_format']={'passed':True,'engine':'Python standard-library sqlite3; no database service installed'}
    result=call(['nvim','--headless','-u',str(ROOT/'nvim-init.lua'),'+lua assert(vim.lsp.config.ts_ls); print("NVIM_OK")','+qa'],work)
    assert 'NVIM_OK' in result.stdout+result.stderr;checks['neovim_headless_lsp_configuration']={'passed':True,'lsp_configurations':9}
    node=ROOT/'node/node_modules/.bin'
    servers={'typescript':[str(node/'typescript-language-server'),'--stdio'],
      'python':[str(node/'pyright-langserver'),'--stdio'],
      'html':[str(node/'vscode-html-language-server'),'--stdio'],
      'css':[str(node/'vscode-css-language-server'),'--stdio'],
      'json':[str(node/'vscode-json-language-server'),'--stdio'],
      'yaml':[str(node/'yaml-language-server'),'--stdio'],
      'bash':[str(node/'bash-language-server'),'start'],
      'go':[str(ROOT/'bin/gopls')], 'rust':[str(ROOT/'bin/rust-analyzer')]}
    lsp_checks={}
    (work/'tsconfig.json').write_text('{"compilerOptions":{"strict":true},"include":["hello.ts"]}')
    for name,command in servers.items():
        client=LSP(command,work)
        try:
            parameters={'processId':os.getpid(),'rootUri':work.as_uri(),'capabilities':{},'workspaceFolders':[{'uri':work.as_uri(),'name':'benign-fixture'}]}
            if name=='typescript':parameters['initializationOptions']={'tsserver':{'path':str(ROOT/'node/node_modules/typescript/lib/tsserver.js')}}
            result=client.request('initialize',parameters)
            assert isinstance(result,dict) and result.get('capabilities')
            client.notify('initialized',{})
            item={'passed':True,'scope':'Real LSP initialize/capability exchange, not all language semantics'}
            if name=='typescript':
                client.notify('textDocument/didOpen',{'textDocument':{'uri':(work/'hello.ts').as_uri(),'languageId':'typescript','version':1,'text':(work/'hello.ts').read_text()}})
                hover=client.request('textDocument/hover',{'textDocument':{'uri':(work/'hello.ts').as_uri()},'position':{'line':0,'character':7}})
                assert 'number' in json.dumps(hover),hover;item['semantic_hover_number_verified']=True
            lsp_checks[name]=item
        finally:client.close()
    checks['language_servers']={'passed':True,'servers':lsp_checks}
except Exception:
    checks['failure']={'passed':False,'diagnostics':traceback.format_exc()}
report={'schema_version':1,'scope':'Benign shared-toolchain fixtures only, no business-project acceptance','passed':bool(checks) and all(item.get('passed') for item in checks.values()),'checks':checks,
  'source_sha256':{str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in [Path(__file__),Path(__file__).with_name('language_tools.py')]},
  'not_installed':['PHP interpreter','Ruby interpreter','.NET SDK','Kotlin compiler','Dart/Flutter','Swift','database servers','GUI/web IDE daemon'],
  'notes':'Node/Python/C/C++/Java/Go/Rust/Shell/SQL/Web are tested capabilities; additional runtimes and application dependencies are selected and verified when a real project requires them.'}
args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'passed':report['passed'],'checks_completed':len(checks),'report':str(args.output)}))
if not report['passed']:raise SystemExit(1)
