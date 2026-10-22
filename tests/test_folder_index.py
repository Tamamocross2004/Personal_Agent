"""Control-flow contracts; fake vectors do not certify semantic retrieval quality."""
import json
import time
import threading
from types import SimpleNamespace as NS

import pytest

from agent.config import Settings
from folder_assistant.store import FolderStore
from folder_assistant.index import FolderIndex
from folder_assistant.jobs import FolderJobs
from knowledge.store import KnowledgeStore


class Embedding:
    def __init__(self): self.embeddings=self; self.calls=[]; self.fail=False; self.hook=None
    def create(self,**kw):
        self.calls.append(kw)
        if self.hook: self.hook()
        if self.fail: raise RuntimeError('sensitive-provider-error')
        return NS(usage=NS(prompt_tokens=10*len(kw['input'])),data=[NS(index=i,embedding=[1.,float(i+1)]) for i in range(len(kw['input']))])


class Vectors:
    def __init__(self): self.rows={}; self.scopes=[]
    def build(self,root,generation,vectors,dimension): self.rows[generation]=vectors
    def search(self,root,generation,vector,limit,indices=None):
        self.scopes.append(indices)
        return (list(range(len(self.rows[generation]))) if indices is None else indices)[:limit]


@pytest.fixture
def library(tmp_path):
    docs=tmp_path/'docs';docs.mkdir()
    (docs/'a.txt').write_text('alpha one\nsecond line',encoding='utf-8')
    folders=FolderStore(tmp_path/'state');fid=folders.grant(docs)['id']
    embedding=Embedding();vectors=Vectors()
    settings=Settings(_env_file=None,siliconflow_api_key='fake',embedding_dimensions=2)
    index=FolderIndex(folders,settings,factory=lambda s:KnowledgeStore(s,vectors=vectors,embedding_client=embedding))
    return docs,folders,fid,index,embedding,vectors


def sync(library):
    _,folders,fid,index,_,_=library
    return index.sync(fid,folders.scan(fid)['id'])['active']['id']


def test_sync_diff_cache_versions_and_same_names(library):
    docs,folders,fid,index,embedding,_=library
    (docs/'nested').mkdir();(docs/'nested'/'a.txt').write_text('alpha other',encoding='utf-8')
    first=sync(library);calls=len(embedding.calls)
    assert len(index.store(fid).generation()['files'])==2
    second=sync(library)
    assert len(embedding.calls)==calls and second!=first
    assert index.status(fid)['active']['cache_hits']==3
    (docs/'a.txt').write_text('alpha changed',encoding='utf-8')
    (docs/'nested'/'a.txt').rename(docs/'nested'/'b.txt')
    scan=folders.scan(fid);diff=index.diff(fid,scan['id'])
    assert diff['added']==['nested/b.txt'] and diff['modified']==['a.txt'] and diff['missing']==['nested/a.txt']
    index.sync(fid,scan['id'])
    assert len(embedding.calls)==calls+1
    old=index.store(fid).generation(first)
    doc=next(d for d in old['files'] if d['name']=='a.txt')
    assert index.citation(fid,first,doc['id'],1)['block']['text']==doc['pages'][0]['text']
    assert doc['pages'][0]['text'].splitlines()==['alpha one']
    (docs/'a.txt').unlink();folders.scan(fid)
    assert index.diff(fid)['missing']==['a.txt']
    sync(library)
    assert index.store(fid).generation(first)['files']==old['files']


def test_parse_embedding_and_mid_build_change_keep_old(library):
    docs,folders,fid,index,embedding,_=library
    first=sync(library)
    (docs/'bad.docx').write_bytes(b'broken')
    with pytest.raises(ValueError,match='bad.docx'): sync(library)
    assert index.store(fid).generation()['id']==first
    (docs/'bad.docx').unlink();(docs/'a.txt').write_text('new content',encoding='utf-8')
    embedding.fail=True
    with pytest.raises(ValueError,match='嵌入调用失败'): sync(library)
    assert index.store(fid).generation()['id']==first
    assert index.status(fid)['latest']['status']=='failed'
    embedding.fail=False
    embedding.hook=lambda:(docs/'a.txt').write_text('raced change',encoding='utf-8')
    with pytest.raises(ValueError,match='变化'): sync(library)
    assert index.store(fid).generation()['id']==first


def test_scope_before_candidates_no_cross_folder_and_pinned_version(library,tmp_path):
    docs,folders,fid,index,embedding,vectors=library
    (docs/'other.txt').write_text('alpha blocked',encoding='utf-8')
    old=sync(library)
    doc=next(d for d in index.store(fid).generation()['files'] if d['name']=='a.txt')
    for mode in ('keyword','vector','hybrid'):
        result=index.search(fid,'alpha',file_ids=[doc['file_id']],mode=mode)
        assert result['hits'] and {h['name'] for h in result['hits']}=={'a.txt'}
        assert all('page' not in h for h in result['hits'])
        if mode!='keyword': assert vectors.scopes[-1]==[0,1]
    with pytest.raises(ValueError): index.search(fid,'alpha',file_ids=['foreign'],mode='keyword')
    assert index.search(fid,'alpha',file_ids=[],mode='keyword')['hits']==[]
    other=tmp_path/'other';other.mkdir();(other/'a.txt').write_text('foreign alpha',encoding='utf-8')
    fid2=folders.grant(other)['id'];index.sync(fid2,folders.scan(fid2)['id'])
    with pytest.raises(ValueError):index.search(fid2,'alpha',generation=old,mode='keyword')
    (docs/'a.txt').write_text('new alpha',encoding='utf-8');sync(library)
    assert 'alpha one' in index.search(fid,'alpha',generation=old,mode='keyword')['hits'][0]['text']
    folders.revoke(fid)
    with pytest.raises(ValueError):index.search(fid,'alpha',mode='keyword')
    assert index.citation(fid,old,doc['id'],1)['historical_snapshot']


def test_job_stop_and_restart_do_not_retry_requests(library):
    docs,folders,fid,index,embedding,_=library
    first=sync(library)
    (docs/'a.txt').write_text('different alpha',encoding='utf-8')
    entered=threading.Event();release=threading.Event()
    def hold(): entered.set();release.wait(10)
    embedding.hook=hold
    jobs=FolderJobs(index);job=jobs.start(fid,'sync',{'scan_id':folders.scan(fid)['id']})
    assert entered.wait(10)
    jobs.stop(job['id']);release.set();jobs.threads[job['id']].join(10)
    assert jobs.get(job['id'])['status']=='stopped'
    assert index.store(fid).generation()['id']==first
    calls=len(embedding.calls)
    jobs.save({**job,'id':'abandoned','status':'running'})
    recovered=FolderJobs(index)
    assert recovered.get('abandoned')['status']=='interrupted' and len(embedding.calls)==calls


def test_incomplete_scan_rejected_before_cloud(library):
    _,folders,fid,index,embedding,_=library
    scan=folders.scan(fid);scan['complete']=False
    with folders.connect() as db:db.execute('UPDATE scans SET payload=? WHERE id=?',(json.dumps(scan),scan['id']))
    with pytest.raises(ValueError,match='扫描不完整'):index.sync(fid,scan['id'])
    assert not embedding.calls


def test_actual_vector_engine_filters_before_top_k(tmp_path):
    from knowledge.vectors import MilvusVectors
    vectors=MilvusVectors();gid='c'*32
    vectors.build(tmp_path,gid,[[1.,0.],[0.,1.]],2)
    assert vectors.search(tmp_path,gid,[1.,0.],1,indices=[1])==[1]
    assert vectors.search(tmp_path,gid,[1.,0.],1,indices=[])==[]
