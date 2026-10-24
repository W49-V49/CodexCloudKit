"""Derived CodeGraph symbol vectors using the existing locked offline BM model.

This does not write canonical memory or call a paid model. Only changed symbol
content is embedded. Returned hits are candidates with source positions.
"""
import hashlib,json,os,sqlite3,sys
from pathlib import Path

MODEL_ROOT=Path(os.environ.get('CLOUDKIT_MEMORY_ROOT',Path(__file__).resolve().parents[2]/'memory'))
FP_VERSION='codegraph-symbol-content-v1'
MAX_BYTES=256*1024*1024
SYMBOL_SQL="SELECT id,name,kind,file,line,end_line,qualified_name,content_hash FROM nodes WHERE kind IN ('function','method','class','interface','constructor') ORDER BY file,line,name"

def _hash(value):return hashlib.sha256(value.encode()).hexdigest()
def _json(path,value):
    tmp=path.with_suffix(path.suffix+'.pending');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)
def _model():
    # Reuse immutable model files and the exact configured native embedding model.
    sys.path.insert(0,str(MODEL_ROOT))
    from ensure_model import configure_runtime,ensure_model,read_lock,fingerprint
    locked=read_lock();verified=ensure_model(allow_download=False)
    configure_runtime(os.environ)
    from basic_memory.repository.fastembed_provider import FastEmbedEmbeddingProvider
    provider=FastEmbedEmbeddingProvider(model_name=locked['model'],dimensions=locked['dimensions'],cache_dir=verified['cache_dir'],threads=2)
    return provider._create_model(),{**fingerprint(),'adapter':FP_VERSION}
def build(project,db_path):
    import numpy as np
    project=Path(project).resolve();db_path=Path(db_path)
    metadata_path=db_path.with_name('semantic.json');vectors_path=db_path.with_name('semantic.npz')
    model,fp=_model()
    previous=json.loads(metadata_path.read_text()) if metadata_path.exists() and vectors_path.exists() else {}
    valid=previous.get('fingerprint')==fp
    old_vectors=np.load(vectors_path,allow_pickle=False)['vectors'] if valid else np.empty((0,fp['embedding_dimension']))
    old={item['signature']:(item,old_vectors[i]) for i,item in enumerate(previous.get('records',[]))} if valid else {}
    with sqlite3.connect('file:'+str(db_path)+'?mode=ro',uri=True) as db:
        rows=db.execute(SYMBOL_SQL).fetchall()
    records=[];vectors=[];pending=[];pending_positions=[];file_lines={};read_files=0;reused=0
    for node_id,name,kind,file,start,end,qualified,content_hash in rows:
        signature=_hash(name+'\0'+kind+'\0'+str(content_hash)) if content_hash else None
        item={'node_id':node_id,'name':name,'kind':kind,'file':file,'line':start,'end_line':end,'qualified_name':qualified,'signature':signature}
        if signature and signature in old:
            vector=old[signature][1];reused+=1
        else:
            target=(project/file).resolve()
            if not target.is_relative_to(project) or not target.is_file():continue
            if file not in file_lines:file_lines[file]=target.read_text(errors='replace').splitlines();read_files+=1
            lines=file_lines[file];snippet='\n'.join(lines[max(0,(start or 1)-1):end or start or 1])
            text=kind+' '+name+'\n'+snippet
            if not signature:signature=_hash(text);item['signature']=signature
            if signature in old:vector=old[signature][1];reused+=1
            else:vector=None;pending.append(text);pending_positions.append(len(records))
        records.append(item);vectors.append(vector)
    if pending:
        for position,vector in zip(pending_positions,model.embed(pending)):vectors[position]=np.asarray(vector,dtype=np.float32)
    matrix=np.asarray(vectors,dtype=np.float32) if vectors else np.empty((0,fp['embedding_dimension']),dtype=np.float32)
    target=vectors_path.with_name('semantic.pending.npz');np.savez_compressed(target,vectors=matrix)
    result={'schema_version':1,'fingerprint':fp,'graph_fingerprint':_hash(json.dumps(rows,separators=(',',':'))),'records':records,'codegraph_db':str(db_path),'project_path':str(project),'record_count':len(records)}
    serialized=(json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode()
    if target.stat().st_size+len(serialized)+db_path.stat().st_size>MAX_BYTES:
        target.unlink();raise ValueError('Derived codegraph storage exceeds 256 MiB; existing semantic index retained')
    target.replace(vectors_path);_json(metadata_path,result)
    return {'passed':True,'symbols':len(records),'embedded':len(pending),'reused':reused,'source_files_read':read_files,'fingerprint':fp,'paid_api_calls':0,'model_downloaded':False,'storage_limit_bytes':MAX_BYTES}
def search(db_path,query,limit=None):
    import numpy as np
    db_path=Path(db_path);metadata=json.loads(db_path.with_name('semantic.json').read_text())
    model,fp=_model()
    if metadata['fingerprint']!=fp:raise ValueError('Semantic model fingerprint changed; run build to migrate derived vectors')
    with sqlite3.connect('file:'+str(db_path)+'?mode=ro',uri=True) as db:rows=db.execute(SYMBOL_SQL).fetchall()
    if metadata.get('graph_fingerprint')!=_hash(json.dumps(rows,separators=(',',':'))):raise ValueError('Structural graph changed; run incremental build before semantic search')
    vectors=np.load(db_path.with_name('semantic.npz'),allow_pickle=False)['vectors']
    if not len(vectors):return {'query':query,'candidates':[],'fingerprint':fp}
    vector=np.asarray(next(iter(model.embed([query]))),dtype=np.float32)
    norms=np.linalg.norm(vectors,axis=1)*np.linalg.norm(vector)
    scores=vectors.dot(vector)/np.maximum(norms,1e-9)
    candidates=[dict(metadata['records'][int(i)],score=float(scores[i])) for i in np.argsort(-scores)]
    if limit is not None:candidates=candidates[:limit]
    return {'query':query,'candidates':candidates,'fingerprint':fp,'fixed_token_budget':None,'default_result_limit':None,'paid_api_calls':0,'scope':'Similarity-ranked code-symbol candidates; verify original symbols/relationships before conclusions'}
