"""重排接口、候选选择、计量和失败契约；不以假分数证明语义质量。"""
import json

import httpx
import pytest

from agent.loop import Agent
from desktop_app.manager import Manager
from fakes import ScriptedLLM,completion
from knowledge.reranking import Reranker,RerankFailure
from knowledge.store import KnowledgeStore
from tools.knowledge import make_search_knowledge
from tools.registry import ToolRegistry,ToolResult,tool
from tools.session import ResearchSession
from test_knowledge import kb


def client_for(calls,alter=None):
    def handle(request):
        payload=json.loads(request.content);calls.append(payload)
        body={'id':'fake-response','meta':{'tokens':{'input_tokens':37,'output_tokens':0}},
              'results':[{'index':i,'relevance_score':float(i)} for i in range(len(payload['documents']))]}
        status=200
        if alter: status,body=alter(body)
        return httpx.Response(status,json=body)
    return httpx.Client(transport=httpx.MockTransport(handle))


def populated(kb):
    store,embedding,vectors=kb
    store.build([(f'{i}.txt',f'unique item {i}'.encode()) for i in range(6)])
    return store,embedding,vectors


def test_rerank_before_topk_preserves_sources_and_separate_usage(kb):
    store,embedding,_=populated(kb);calls=[];store.reranker.client=client_for(calls)
    before=store.search('unrelated question',mode='vector',k=2,rerank=False)
    after=store.search('unrelated question',mode='vector',k=2,rerank=True)
    assert [h['chunk_id'] for h in before['hits']]==[0,1]
    assert [h['chunk_id'] for h in after['hits']]==[5,4]
    assert len(calls[0]['documents'])==6 and calls[0]['top_n']==6
    assert after['hits'][0]['name']=='5.txt' and after['hits'][0]['text']=='unique item 5'
    assert [r['chunk_id'] for r in before['candidates']]==[r['chunk_id'] for r in after['candidates']]
    assert before['generation']==after['generation']
    assert after['reranking']['status']=='applied' and not after['reranking']['cache_hit']
    assert store.status()['usage']['calls']==2
    assert store.status()['rerank']['usage']=={'calls':1,'input_tokens':37,'output_tokens':0,'unknown_usage_calls':0}
    assert store.events()[-1]['results'][-1]['index']==5


def test_rerank_cache_bound_to_model_query_and_ordered_text(kb):
    store,_,vectors=populated(kb);calls=[];client=client_for(calls);store.reranker.client=client
    store.search('query one',mode='vector',rerank=True)
    assert store.search('query one',mode='vector',rerank=True,k=1)['reranking']['cache_hit']
    assert len(calls)==1
    store.search('query two',mode='vector',rerank=True);assert len(calls)==2
    vectors.candidates=list(reversed(range(6)))
    store.search('query one',mode='vector',rerank=True);assert len(calls)==3
    changed=KnowledgeStore(store.settings.model_copy(update={'rerank_model':'another-model'}),
        vectors=vectors,embedding_client=store.embedder.client,rerank_client=client)
    changed.search('query one',mode='vector',rerank=True);assert len(calls)==4
    assert calls[-1]['model']=='another-model'


def test_keyword_never_calls_rerank_and_disabled_mode_keeps_baseline(kb):
    store,_,_=populated(kb);calls=[];store.reranker.client=client_for(calls)
    enabled=store.settings.model_copy(update={'knowledge_rerank_enabled':True})
    store.settings=enabled
    assert store.search('unique',mode='keyword')['reranking']['status']=='disabled'
    assert store.search('unique',mode='hybrid',rerank=False)['reranking']['status']=='disabled'
    with pytest.raises(ValueError,match='关键词模式'): store.search('unique',mode='keyword',rerank=True)
    assert calls==[]


def test_rerank_budget_is_checked_after_embedding_and_before_new_request(kb):
    store,_,_=populated(kb);calls=[];store.reranker.client=client_for(calls)
    with pytest.raises(ValueError,match='预算'): store.search('new question',mode='vector',rerank=True,budget=7)
    assert not calls and store.events()[-1]['purpose'].startswith('query:')


@pytest.mark.parametrize('fault',['duplicate','missing','out_of_range','nan','boolean','usage','http'])
def test_bad_rerank_response_never_falls_back_or_caches(kb,fault):
    store,_,_=populated(kb);calls=[]
    def alter(body):
        if fault=='duplicate':body['results'][-1]['index']=0
        elif fault=='missing':body['results'].pop()
        elif fault=='out_of_range':body['results'][-1]['index']=99
        elif fault=='nan':body['results'][0]['relevance_score']='NaN'
        elif fault=='boolean':body['results'][0]['relevance_score']=True
        elif fault=='usage':body.pop('meta')
        return (401,{'message':'test-only-key'}) if fault=='http' else (200,body)
    store.reranker.client=client_for(calls,alter)
    with pytest.raises(RerankFailure) as error: store.search('query',mode='vector',rerank=True)
    assert 'test-only-key' not in str(error.value)
    assert len(calls)==1 and store.events()[-1]['status']=='failed'
    with store.connect() as db: assert db.execute('SELECT COUNT(*) FROM rerank_cache').fetchone()[0]==0
    assert store.search('query',mode='vector',rerank=False)['hits']
    assert len(calls)==1


def test_negative_cross_encoder_logits_are_valid_not_probabilities(kb):
    store,_,_=populated(kb);calls=[]
    def alter(body):
        for row in body['results']:row['relevance_score']-=10
        return 200,body
    store.reranker.client=client_for(calls,alter)
    result=store.search('query',mode='vector',rerank=True)
    assert result['hits'][0]['rerank_score']==-5


@pytest.mark.parametrize('unknown',[False,True])
def test_agent_counts_rerank_separately_and_stops_on_unknown_output(unknown):
    @tool
    def query_test()->ToolResult:
        """返回一条受控重排计量事件。"""
        return ToolResult('result',usage=[{'id':'r1','operation':'rerank','input_tokens':37,
            'output_tokens':None if unknown else 2,'status':'returned'}])
    llm=ScriptedLLM([completion(tool_calls=[('r','query_test',{})]),completion('done')]);events=[]
    result=Agent(llm,ToolRegistry([query_test]),system_prompt='SYS',on_event=events.append).run('q')
    assert len([e for e in events if e.kind=='rerank_call'])==1
    assert not [e for e in events if e.kind=='embedding_call']
    if unknown: assert result.stop_reason=='needs_verification' and result.total_tokens==52
    else: assert result.total_tokens==69 and result.equivalent_tokens==81


def test_tool_journal_and_default_off_cannot_be_overridden(kb,tmp_path):
    store,_,_=populated(kb);calls=[];client=client_for(calls)
    session=ResearchSession();session.embedding_budget_remaining=10000
    def factory(settings,**kwargs):return KnowledgeStore(settings,vectors=store.vectors,embedding_client=store.embedder.client,rerank_client=client,**kwargs)
    task=tmp_path/'task';task.mkdir()
    reg=ToolRegistry([make_search_knowledge(store.settings,task,session,store_factory=factory)])
    assert reg.execute('search_knowledge',{'query':'query','rerank':True}).is_error and not calls
    enabled=store.settings.model_copy(update={'knowledge_rerank_enabled':True})
    reg=ToolRegistry([make_search_knowledge(enabled,task,session,store_factory=factory)])
    result=reg.execute('search_knowledge',{'query':'query'})
    assert not result.is_error and (task/'rerank_usage.jsonl').exists()
    journal=[json.loads(line) for line in (task/'rerank_usage.jsonl').read_text().splitlines()]
    assert [e['status'] for e in journal]==['started','returned']
    assert len(calls)==1 and result.usage[-1]['operation']=='rerank'
    assert result.evidence[0]['chunk_id']==5


def test_task_usage_merges_pending_and_returned_rerank_journal(tmp_path,kb):
    store,_,_=kb;manager=Manager(tmp_path/'desktop',start=False,settings=store.settings)
    task=manager.root/'task';task.mkdir()
    row={'task_dir':str(task),'options':{},'state':'interrupted','result':{}}
    journal=task/'rerank_usage.jsonl'
    journal.write_text(json.dumps({'id':'r','input_tokens':None,'output_tokens':None})+'\n')
    assert manager.usage(row)['rerank']['unknown_usage_calls']==1
    with journal.open('a') as f:f.write(json.dumps({'id':'r','input_tokens':37,'output_tokens':0})+'\n')
    value=manager.usage(row)
    assert value['rerank']['calls']==1 and value['embedding']['calls']==0
    assert value['rerank']['input_tokens']==37 and value['calls']==0
    manager.close()
