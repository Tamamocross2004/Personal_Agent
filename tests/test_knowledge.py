"""RAG的存储、独立召回、证据与计量契约。假向量只测试控制流，不认证语义质量。"""
import io
import json
import time
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from agent.config import Settings
from agent.evidence import Claim, EvidenceLedger, audit_mapping
from agent.loop import Agent
from desktop_app.manager import Manager
from desktop_app.server import create_app
from fakes import ScriptedLLM, completion
from knowledge.documents import parse, split
from knowledge.embedding import EmbeddingFailure
from knowledge.locking import lease
from knowledge.store import KnowledgeStore
from knowledge.vectors import MilvusVectors
from tools import default_registry
from tools.knowledge import make_search_knowledge
from tools.registry import ToolRegistry, ToolResult, tool
from tools.session import ResearchSession


class FakeEmbedding:
    def __init__(self):
        self.embeddings=self; self.calls=[]; self.failure=False; self.unknown=False; self.invalid=False

    def create(self,**kw):
        self.calls.append(kw)
        if self.failure: raise RuntimeError('secret-that-must-not-escape')
        return NS(usage=None if self.unknown else NS(prompt_tokens=7*len(kw['input'])),
            data=[NS(index=i,embedding=[0.,0.] if self.invalid else [1.,float(i+1)]) for i,_ in enumerate(kw['input'])])


class FakeVectors:
    def __init__(self): self.rows={}; self.failure=False; self.candidates=None
    def build(self,root,generation,vectors,dimension):
        if self.failure: raise RuntimeError('disk unavailable')
        self.rows[generation]=vectors
    def search(self,root,generation,vector,limit):
        return self.candidates if self.candidates is not None else list(range(min(limit,len(self.rows[generation]))))


@pytest.fixture
def kb(tmp_path):
    settings=Settings(_env_file=None,siliconflow_api_key='test-only-key',knowledge_path=tmp_path/'kb',embedding_dimensions=2,knowledge_rerank_enabled=False)
    client=FakeEmbedding(); vectors=FakeVectors()
    return KnowledgeStore(settings,vectors=vectors,embedding_client=client),client,vectors


def test_chunk_offsets_and_overlap_cover_original():
    pages=parse(('第一段。\n\n'+'有边界的原文。'*100).encode(),'资料.md')
    chunks=split(pages,200,40)
    assert len(chunks)>2
    assert chunks[0]['start']==0 and chunks[-1]['end']==len(pages[0]['text'])
    for before,after in zip(chunks,chunks[1:]): assert before['start']<after['start']<=before['end']
    for c in chunks: assert pages[c['page']-1]['text'][c['start']:c['end']]==c['text']
    with pytest.raises(ValueError): split(pages,200,200)


@pytest.mark.parametrize('name,data',[('../a.txt',b'a'),('a.exe',b'a'),('a.txt',b''),('a.txt',b'\xff'),('a.pdf',b'bad pdf')])
def test_invalid_documents_rejected(name,data):
    with pytest.raises(Exception): parse(data,name)


def test_scanned_pdf_does_not_silently_build_empty_index():
    from pypdf import PdfWriter
    writer=PdfWriter(); writer.add_blank_page(width=200,height=200)
    output=io.BytesIO(); writer.write(output)
    with pytest.raises(ValueError,match='OCR'): parse(output.getvalue(),'scan.pdf')


def test_text_pdf_preserves_page_numbers():
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
    writer=PdfWriter()
    for text in ('original first page','exception second page'):
        page=writer.add_blank_page(width=300,height=300)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject();stream.set_data(f'BT /F1 12 Tf 10 200 Td ({text}) Tj ET'.encode())
        page[NameObject('/Contents')]=writer._add_object(stream)
    output=io.BytesIO();writer.write(output)
    pages=parse(output.getvalue(),'text.pdf')
    assert [p['page'] for p in pages]==[1,2]
    assert 'first page' in pages[0]['text'] and 'second page' in pages[1]['text']


def test_rebuild_append_cache_and_atomic_failure(kb):
    store,client,vectors=kb
    first=store.build([('a.txt',b'alpha original')])['active']['id']
    assert len(client.calls)==1
    rebuilt=store.build(mode='rebuild')
    assert len(client.calls)==1 and rebuilt['active']['cache_hits']==1
    store.build([('b.md',b'beta original')])
    assert len(client.calls)==2 and client.calls[-1]['input']==['beta original']
    active=store.generation()['id']
    vectors.failure=True
    with pytest.raises(RuntimeError): store.build([('c.txt',b'gamma original')])
    assert store.generation()['id']==active and store.status()['latest']['status']=='failed'
    assert store.search('alpha',generation=first,mode='keyword')['hits'][0]['text']=='alpha original'
    assert store.status()['usage']['input_tokens']==21


def test_duplicate_and_remove_preserve_task_snapshot(kb):
    store,client,_=kb
    before=store.build([('a.txt',b'alpha text')])['active']
    store.build([('a.txt',b'alpha text')]); assert len(client.calls)==1
    with pytest.raises(ValueError,match='同名'): store.build([('a.txt',b'changed')])
    store.build(remove=[before['files'][0]['id']])
    assert not store.search('alpha',mode='keyword')['hits']
    assert store.search('alpha',generation=before['id'],mode='keyword')['hits']


def test_lexical_recall_is_independent_of_vector_candidates(kb):
    store,client,vectors=kb
    store.build([('alpha.txt',b'uniqueterm evidence'),('beta.txt',b'irrelevant text')])
    vectors.candidates=[1]
    result=store.search('uniqueterm',mode='hybrid')
    assert result['lexical_candidates']==1 and result['vector_candidates']==1
    assert {h['name'] for h in result['hits']}=={'alpha.txt','beta.txt'}
    assert len(client.calls)==2
    store.search('uniqueterm',mode='hybrid'); assert len(client.calls)==2
    assert all(h['role']=='direct' for h in result['hits'])


def test_keyword_works_without_key_and_model_change_requires_rebuild(kb):
    store,client,vectors=kb
    store.build([('a.txt','项目原始约定：申请需在两个工作日内提交。'.encode())])
    changed=KnowledgeStore(store.settings.model_copy(update={'siliconflow_api_key':'','embedding_model':'another'}),vectors=vectors)
    assert changed.search('申请提交',mode='keyword')['hits']
    with pytest.raises(ValueError,match='模型配置'): changed.search('申请提交')
    assert len(client.calls)==1


def test_adjacent_context_cannot_displace_direct_hits(kb):
    store,_,vectors=kb
    text='alpha border. '*300
    store.build([('a.txt',text.encode())])
    vectors.candidates=[2,4]
    result=store.search('question',mode='vector',k=2)
    assert [(h['chunk_id'],h['role']) for h in result['hits'][:2]]==[(2,'direct'),(4,'direct')]
    assert any(h['role']=='adjacent' for h in result['hits'][2:])
    assert sum(len(h['text']) for h in result['hits'])<=5500


@pytest.mark.parametrize('fault',['failure','unknown','invalid'])
def test_embedding_failures_journal_and_never_overwrite_active(kb,fault):
    store,client,_=kb
    old=store.build([('a.txt',b'alpha')])['active']['id']
    setattr(client,fault,True)
    with pytest.raises(EmbeddingFailure) as exc: store.build([('b.txt',b'beta')])
    assert 'secret-that' not in str(exc.value)
    assert store.generation()['id']==old
    assert store.events()[-1]['status']=='failed'
    assert store.events()[-1]['input_tokens']==(7 if fault=='invalid' else None)


def test_budget_checked_before_embedding_request(kb):
    store,client,_=kb
    with pytest.raises(ValueError,match='预算'): store._cached_vectors(['long text '*100],'query',1)
    assert not client.calls and not store.events()


def test_knowledge_context_compaction_preserves_recent_results_and_protocol():
    from agent.context import ContextCompactor,COMPACTED_MARKER
    content='原始资料的有界片段。'*400
    messages=[{'role':'system','content':'规则'},
        {'role':'assistant','tool_calls':[{'id':'old','function':{'name':'search_knowledge','arguments':'{"query":"早期问题"}'}}]},
        {'role':'tool','tool_call_id':'old','content':content},
        {'role':'assistant','tool_calls':[{'id':'recent','function':{'name':'search_knowledge','arguments':'{"query":"最近问题"}'}}]},
        {'role':'tool','tool_call_id':'recent','content':content}]
    result=ContextCompactor(threshold=1,keep_recent_turns=1,min_saving=1).maybe_compact(messages,10000)
    assert result.by_tool=={'search_knowledge':1}
    assert messages[2]['content'].startswith(COMPACTED_MARKER)
    assert 'read_evidence' in messages[2]['content'] and '这不是原文摘要' in messages[2]['content']
    assert messages[4]['content']==content and messages[0]['content']=='规则'
    assert [(m.get('tool_call_id')) for m in messages if m['role']=='tool']==['old','recent']


def test_build_lock_prevents_simultaneous_mutations(kb):
    store,client,_=kb
    with lease(store.root/'.build.lock'):
        with pytest.raises(ValueError,match='正在使用'): store.build([('a.txt',b'alpha')])
    assert not client.calls


def test_real_milvus_reopens_files_after_build_and_search(tmp_path):
    adapter=MilvusVectors()
    adapter.build(tmp_path,'a'*32,[[1.,0.],[0.,1.]],2)
    assert adapter.search(tmp_path,'a'*32,[0.,1.],1)==[1]
    assert adapter.search(tmp_path,'a'*32,[1.,0.],1)==[0]


def test_tool_pins_generation_and_records_only_delivered_text(kb,tmp_path):
    store,_,_=kb
    old=store.build([('原始.md','取消申请须在五日内提交。'.encode())])['active']['id']
    task=tmp_path/'task'; task.mkdir()
    ledger=EvidenceLedger(task,'根据知识库说明要求')
    session=ResearchSession(evidence=ledger);session.embedding_budget_remaining=100
    def factory(settings,**kw): return KnowledgeStore(settings,vectors=store.vectors,embedding_client=store.embedder.client,**kw)
    registry=ToolRegistry([make_search_knowledge(store.settings,task,session,store_factory=factory)])
    result=registry.execute('search_knowledge',{'query':'取消申请','mode':'keyword'})
    assert not result.is_error
    metadata=result.evidence[0]; cut=metadata['output_start']+5
    ids=ledger.capture('search_knowledge','{}',result.content[:cut],'c1',result.evidence,cut)
    source=ledger.records[ids[0]]
    assert source['kind']=='document' and source['text']==metadata['text'][:5]
    assert source['end']-source['start']==5 and source['delivery_truncated']
    assert source['generation']==old and source['document_name']=='原始.md'
    store.build(mode='replace')
    again=registry.execute('search_knowledge',{'query':'取消申请','mode':'keyword'})
    assert '五日' in again.content and again.evidence[0]['generation']==old


@pytest.mark.parametrize('unknown',[False,True])
def test_embedding_usage_charges_agent_budget_or_stops_unknown(tmp_path,unknown):
    @tool
    def search_test()->ToolResult:
        """返回一次实际嵌入请求的受控计量事件。"""
        return ToolResult('result',usage=[{'id':'e1','status':'returned','input_tokens':None if unknown else 123}])
    llm=ScriptedLLM([completion(tool_calls=[('c1','search_test',{})]),completion('done')])
    events=[]
    agent=Agent(llm,ToolRegistry([search_test]),system_prompt='SYS',on_event=events.append)
    result=agent.run('q')
    assert len([e for e in events if e.kind=='embedding_call'])==1
    if unknown:
        assert result.stop_reason=='needs_verification' and len(llm.requests)==1
    else:
        assert result.total_tokens==153 and result.equivalent_tokens==163


def test_default_disabled_does_not_register_knowledge_tool(tmp_path):
    assert 'search_knowledge' not in {t['function']['name'] for t in default_registry(tmp_path).schemas()}


def test_document_excerpt_scope_locates_without_certifying_full_document(tmp_path):
    ledger=EvidenceLedger(tmp_path,'根据资料说明期限')
    source=ledger.add('document','设备普通借用期为四十八小时。',start=10,end=25,document_name='设备.txt',page=2,generation='a'*32)
    # 偏移由真实送达文本长度决定，不要求完整原文才能提出摘录级结论。
    ledger.records[source]['end']=10+len(ledger.records[source]['text']);ledger.save()
    report='设备普通借用期为四十八小时。'
    claim=Claim(quote=report,kind='direct',scope='excerpt',supports=[{'source_id':source,'excerpt':report}])
    mapping=ledger.prepare(tmp_path/'report.md',report,[claim],complete=True)
    (tmp_path/'report.md').write_text(report,encoding='utf-8')
    (tmp_path/'report.evidence.json').write_text(json.dumps(mapping,ensure_ascii=False),encoding='utf-8')
    assert audit_mapping(tmp_path)['status']=='mapped'
    with pytest.raises(ValueError,match='仅支持excerpt'):
        ledger.validate(report,[claim.model_copy(update={'scope':'whole_document'})])


def test_desktop_api_job_search_preview_and_isolation(tmp_path,kb):
    store,client,_=kb
    manager=Manager(tmp_path/'desktop',start=False,settings=store.settings)
    manager.knowledge_store=lambda:store
    with TestClient(create_app(manager,'token')) as api:
        assert api.get('/api/knowledge').status_code==401
        api.headers['Authorization']='Bearer token'
        assert api.post('/api/knowledge/import?filename=bad.exe',content=b'data').status_code==400
        response=api.post('/api/knowledge/import?filename=a.txt',content=b'alpha policy original')
        assert response.status_code==200 and response.json()['accepted']
        manager.knowledge_thread.join(5)
        status=api.get('/api/knowledge').json()
        assert not status['busy'] and not status['job_error'] and status['usage']['calls']==1
        doc_id=status['active']['files'][0]['id']
        preview=api.get('/api/knowledge/documents/'+doc_id).json()
        assert preview['text']=='alpha policy original'
        hits=api.post('/api/knowledge/search',json={'query':'policy'}).json()['hits']
        assert hits and len(client.calls)==1
        assert 'test-only-key' not in json.dumps(status)
        assert manager.knowledge_settings.knowledge_path==manager.root/'knowledge'
        assert api.post('/api/knowledge/search',json={'query':'x','mode':'unrecognized'}).status_code==422
        api.delete('/api/knowledge/documents/'+doc_id)
        manager.knowledge_thread.join(5)
        assert not api.get('/api/knowledge').json()['active']['files']
