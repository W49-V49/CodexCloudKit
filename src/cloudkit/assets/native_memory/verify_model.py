#!/usr/bin/env python3
"""Verify the native Basic Memory provider selects locked weights offline."""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
ROOT=Path(__file__).resolve().parent
if Path(sys.executable).parent!=ROOT/'.venv/bin':
    os.execv(str(ROOT/'.venv/bin/python'),[str(ROOT/'.venv/bin/python'),__file__])
from ensure_model import configure_runtime, ensure_model, read_lock, fingerprint


def main():
    lock=read_lock()
    checks={}
    # Force a complete independent digest pass, then exercise the unchanged-artifact path.
    full=ensure_model(allow_download=False,force_verify=True)
    reuse=ensure_model(allow_download=False,force_verify=False)
    checks['all_artifact_digests']={'passed':full['verified_file_count']==5}
    checks['unchanged_reuse']={'passed':reuse['verification_mode']=='unchanged-verified-artifact-stats'}
    configure_runtime(os.environ)
    def reject_network(*args,**kwargs):raise RuntimeError('Network access prohibited during offline model verification')
    socket.socket.connect=reject_network
    from basic_memory.repository.fastembed_provider import FastEmbedEmbeddingProvider
    provider=FastEmbedEmbeddingProvider(model_name=lock['model'],dimensions=lock['dimensions'],cache_dir=full['cache_dir'],threads=2)
    native=provider._create_model()
    actual=Path(native.model._model_dir).resolve()
    checks['native_snapshot_selection']={'passed':actual==Path(full['snapshot_dir']).resolve(),'selected_revision':actual.name}
    vectors=list(native.embed(['云端开发记忆','持久化保存任务证据']))
    checks['offline_local_inference']={'passed':len(vectors)==2 and all(len(x)==lock['dimensions'] for x in vectors),'vectors':len(vectors),'dimensions':lock['dimensions'],'network_disabled_for_test':True}
    with tempfile.TemporaryDirectory(prefix='model-integrity-',dir=ROOT) as temporary:
        test_cache=Path(temporary)
        repo=test_cache/('models--'+lock['repository'].replace('/','--'))
        snapshot=repo/'snapshots'/lock['revision'];snapshot.mkdir(parents=True)
        original=Path(full['snapshot_dir'])
        for name in lock['files']:
            if name=='config.json':
                data=(original/name).read_bytes();(snapshot/name).write_bytes(b'X'+data[1:])
            else:(snapshot/name).symlink_to(original/name)
        rejected=False
        try:ensure_model(test_cache,allow_download=False,force_verify=True)
        except ValueError:rejected=True
        checks['same_size_corrupt_artifact_rejected']={'passed':rejected and not (test_cache/'verified-model.json').exists()}
    result={'schema_version':1,**fingerprint(),'checks':checks,'passed':all(x['passed'] for x in checks.values()),'native_package_modified':False,'paid_api_calls':0,'cache_dir':full['cache_dir']}
    (ROOT/'model-runtime-validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2));return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
