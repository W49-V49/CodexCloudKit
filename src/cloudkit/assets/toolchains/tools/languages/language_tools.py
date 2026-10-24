#!/usr/bin/env python3
"""Use the isolated shared language toolbox without changing platform settings."""
import argparse
import json
import os
from pathlib import Path
import subprocess

ROOT=Path(os.environ.get('CLOUDKIT_LANGUAGE_ROOT',Path(__file__).resolve().parent))
def environment():
    env=os.environ.copy()
    env['PATH']=os.pathsep.join(str(p) for p in (ROOT/'bin',ROOT/'node/node_modules/.bin',ROOT/'python/bin'))+os.pathsep+env.get('PATH','')
    paths={'JAVA_HOME':ROOT/'jdk','UV_CACHE_DIR':ROOT/'uv-cache','GOPATH':ROOT/'go-work',
       'GOCACHE':ROOT/'go-cache','GOMODCACHE':ROOT/'go-mod','CARGO_HOME':ROOT/'cargo-home',
       'XDG_CACHE_HOME':ROOT/'cache','XDG_CONFIG_HOME':ROOT/'config','XDG_DATA_HOME':ROOT/'data',
       'XDG_STATE_HOME':ROOT/'state'}
    for key,value in paths.items():
        value.mkdir(parents=True,exist_ok=True)
        env[key]=str(value)
    env['GOTOOLCHAIN']='local'
    return env

def assess(project):
    root=Path(project).resolve()
    if not root.is_dir():raise ValueError('Selected project does not exist')
    definitions=[
      ('package.json','JavaScript/TypeScript','ready','npm/node; install application lockfile in its own repository'),
      ('pyproject.toml','Python','ready','Select its own isolated environment and dependency lock'),
      ('requirements.txt','Python','ready','Select its own isolated environment and dependency lock'),
      ('go.mod','Go','ready','Use pinned Go; resolve module dependencies with sum.golang.org'),
      ('Cargo.toml','Rust','ready','Use pinned Rust; honor Cargo.lock in business repository'),
      ('pom.xml','Java/Maven','jdk-ready-manager-on-demand','JDK25 exists; pin and verify Maven when project requires it'),
      ('build.gradle*','Java/Kotlin/Gradle','jdk-ready-manager-on-demand','Prefer existing checked-in Gradle wrapper after integrity review'),
      ('composer.json','PHP','on-demand','https://www.php.net/downloads.php'),
      ('Gemfile','Ruby','on-demand','https://www.ruby-lang.org/en/downloads/'),
      ('*.csproj','.NET','on-demand','https://dotnet.microsoft.com/download/dotnet'),
      ('pubspec.yaml','Dart/Flutter','on-demand','https://dart.dev/get-dart / https://docs.flutter.dev/get-started/install'),
      ('Package.swift','Swift','on-demand','https://www.swift.org/install/'),
    ]
    return {'project':str(root),'scope':'Top-level dependency-manifest assessment; not installation or application acceptance',
       'requirements':[{'manifest':str(path.relative_to(root)),'language':language,'status':status,'next_action':action}
          for pattern,language,status,action in definitions for path in root.glob(pattern)],
       'notes':'No language ecosystem includes all project dependencies. Keep application manifests, lockfiles and application environments in the selected project.'}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    run=sub.add_parser('run');run.add_argument('arguments',nargs=argparse.REMAINDER)
    inspect=sub.add_parser('assess');inspect.add_argument('--project',required=True)
    sub.add_parser('paths')
    args=parser.parse_args()
    if args.command=='run':
        command=args.arguments[1:] if args.arguments[:1]==['--'] else args.arguments
        if not command:parser.error('run requires a command after --')
        return subprocess.run(command,env=environment()).returncode
    if args.command=='assess':print(json.dumps(assess(args.project),ensure_ascii=False,indent=2))
    else:print(json.dumps({'root':str(ROOT),'bin':str(ROOT/'bin'),'jdk':str(ROOT/'jdk'),'node_bin':str(ROOT/'node/node_modules/.bin'),'python':str(ROOT/'python/bin/python'),'editor_config':str(ROOT/'nvim-init.lua')},indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
