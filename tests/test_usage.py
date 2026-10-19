"""调用/缓存/费用独立核算，失败与旧日志不冒充完整账单。"""
import json
from decimal import Decimal
from pathlib import Path

import openai
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agent.config import Settings
from agent.loop import Agent
from desktop_app.manager import Manager
from desktop_app.server import create_app
from desktop_app.usage import PriceProfile,PricingBook,TraceUsageReader,flash_profile,summarize
from tools.registry import ToolRegistry


def call(**values):
    return {'kind':'llm_call','purpose':'agent','provider':'deepseek','requested_model':'deepseek-flash',
        'prompt_tokens':1000000,'cached_tokens':800000,'completion_tokens':500000,
        'usage_reported':True,'cache_usage_reported':True,'request_attempts':1,**values}


def write_trace(path,events):
    path.write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in events),encoding='utf-8')


def usage(tmp_path,events,options=None,state='completed'):
    path=tmp_path/'trace.jsonl';write_trace(path,[*events,{'kind':'run_end'}])
    return summarize(TraceUsageReader().read(path),{'options':options or {},'state':state},PricingBook(tmp_path/'prices.json'))


def test_cost_uses_real_split_tokens_and_decimal_not_equivalent_budget(tmp_path):
    value=usage(tmp_path,[call(equivalent_tokens=999999999)])
    assert Decimal(value['estimated_cost'])==Decimal('4.432')
    assert value['input_tokens']==1000000 and value['cached_tokens']==800000
    assert value['uncached_tokens']==200000 and value['cache_hit_ratio']==.8
    assert value['pricing_basis']==['current_reference']


def test_all_purposes_and_retries_are_counted(tmp_path):
    value=usage(tmp_path,[call(purpose=p,request_attempts=2) for p in ('planning','agent','report_review','future-purpose')])
    assert value['calls']==4 and value['returned']==4 and value['failed']==0
    assert [g['calls'] for g in value['by_purpose'].values()]==[1,1,1,1]
    assert value['http_attempts_lower_bound']==8 and value['retry_attempts_lower_bound']==4
    assert value['http_attempts_complete']


def test_failed_requests_keep_known_partial_cost(tmp_path):
    value=usage(tmp_path,[call(),{'kind':'llm_error','purpose':'report_review','request_attempts':3}])
    assert value['calls']==2 and value['failed']==1
    assert value['estimated_cost'] is None and Decimal(value['known_cost'])==Decimal('4.432')
    assert value['priced_calls']==1 and value['unpriced_calls']==1
    assert value['http_attempts_lower_bound']==4 and value['retry_attempts_lower_bound']==2


def test_failure_before_transport_does_not_invent_http_attempt(tmp_path):
    value=usage(tmp_path,[{'kind':'llm_error','request_attempts':0},call(request_attempts=3)])
    assert value['calls']==2 and value['http_attempts_lower_bound']==3
    assert value['http_attempts_complete'] and value['retry_attempts_lower_bound']==2


@pytest.mark.parametrize('changes',[
    {'provider':'custom'}, {'requested_model':'unknown-model'}, {'usage_reported':False},
    {'prompt_tokens':None}, {'cached_tokens':1000001}, {'cached_tokens':-1}, {'cache_usage_reported':False},
])
def test_unknown_identity_or_usage_never_becomes_zero_cost(tmp_path,changes):
    value=usage(tmp_path,[call(**changes)])
    assert value['estimated_cost'] is None and value['unpriced_calls']==1


def test_old_calls_have_lower_bound_retry_count_and_warning(tmp_path):
    event=call();event.pop('request_attempts');event.pop('usage_reported');event.pop('cache_usage_reported')
    value=usage(tmp_path,[event])
    assert value['calls']==1 and value['http_attempts_lower_bound']==1
    assert not value['http_attempts_complete'] and value['warnings']


def test_zero_rate_is_explicitly_valid(tmp_path):
    profile=PriceProfile(**{**flash_profile(),'input_per_million':0,'cached_per_million':0,'output_per_million':0})
    book=PricingBook(tmp_path/'prices.json');book.save(profile.provider,profile.model,profile)
    path=tmp_path/'trace.jsonl';write_trace(path,[call(cache_usage_reported=False)])
    value=summarize(TraceUsageReader().read(path),{'options':{},'state':'running'},book)
    assert Decimal(value['estimated_cost'])==0 and value['unpriced_calls']==0


def test_snapshot_is_frozen_while_old_runs_are_repriced(tmp_path):
    book=PricingBook(tmp_path/'prices.json')
    snapshot=book.get('deepseek','deepseek-flash')
    profile=PriceProfile(**{**snapshot,'input_per_million':100,'cached_per_million':10,'output_per_million':200})
    book.save(profile.provider,profile.model,profile)
    path=tmp_path/'trace.jsonl';write_trace(path,[call()]);trace=TraceUsageReader().read(path)
    frozen=summarize(trace,{'options':{'pricing':snapshot},'state':'running'},book)
    historic=summarize(trace,{'options':{},'state':'running'},book)
    assert Decimal(frozen['estimated_cost'])==Decimal('4.432')
    assert frozen['pricing_basis']==['task_snapshot']
    assert Decimal(historic['estimated_cost'])==128 and historic['pricing_basis']==['current_reference']


def test_missing_trace_unknown_and_never_started_zero(tmp_path):
    book=PricingBook(tmp_path/'prices.json')
    value=summarize(None,{'state':'completed'},book)
    assert not value['available'] and value['estimated_cost'] is None
    for row in ({'state':'queued'},{'state':'cancelled','result':{'usage_not_started':True}}):
        value=summarize(None,row,book)
        assert value['available'] and value['calls']==0 and Decimal(value['estimated_cost'])==0


def test_incremental_reader_handles_partial_lines_rotation_and_400_plus_calls(tmp_path):
    path=tmp_path/'trace.jsonl';reader=TraceUsageReader()
    event=json.dumps(call(),ensure_ascii=False).encode()
    path.write_bytes(event[:80])
    assert reader.read(path)['events']==[]
    with path.open('ab') as stream:stream.write(event[80:]+b'\n')
    first=reader.read(path);assert len(first['events'])==1
    with path.open('ab') as stream:stream.write((event+b'\n')*450)
    latest=reader.read(path)
    assert len(latest['events'])==451 and len(first['events'])==1
    assert len(reader.read(path)['events'])==451
    value=summarize(latest,{'options':{},'state':'running'},PricingBook(tmp_path/'prices.json'))
    assert value['calls']==451 and Decimal(value['estimated_cost'])==Decimal('4.432')*451
    write_trace(path,[call(purpose='planning')])
    assert len(reader.read(path)['events'])==1
    assert reader.read(path)['events'][0]['purpose']=='planning'


def test_corrupt_and_oversized_lines_do_not_hide_incompleteness(tmp_path):
    path=tmp_path/'trace.jsonl';path.write_bytes(b'bad\n'+b'x'*1000010+b'\n')
    reader=TraceUsageReader();trace=reader.read(path)
    value=summarize(trace,{'options':{},'state':'interrupted'},PricingBook(tmp_path/'prices.json'))
    assert trace['invalid_lines']==2 and value['warnings']
    assert value['estimated_cost'] is None
    assert any('未取得完整结束记录' in s for s in value['warnings'])


@pytest.mark.parametrize('changes',[{'provider':{}},{'requested_model':[]},{'purpose':[]}])
def test_malformed_identity_and_purpose_do_not_break_statistics(tmp_path,changes):
    value=usage(tmp_path,[call(**changes)])
    assert value['calls']==1


def test_pricing_validation_missing_configuration_and_api_snapshot(tmp_path):
    manager=Manager(tmp_path/'workspace',start=False,settings=Settings(deepseek_api_key='test-key'))
    with TestClient(create_app(manager,'token')) as client:
        assert client.get('/api/pricing').status_code==401
        client.headers['Authorization']='Bearer token'
        profile=client.get('/api/pricing').json()['profiles'][0]
        assert client.put('/api/pricing',json={**profile,'input_per_million':'NaN'}).status_code==422
        assert client.put('/api/pricing',json={**profile,'input_per_million':'-1'}).status_code==422
        updated={**profile,'input_per_million':'3'}
        assert client.put('/api/pricing',json=updated).status_code==200
        sid=client.post('/api/sessions').json()['id']
        row=client.post(f'/api/sessions/{sid}/runs',json={'question':'test','pricing':{'input_per_million':'999'}}).json()
        assert row['options']['pricing']['input_per_million']=='3'
        restarted=PricingBook(manager.root/'desktop-pricing.json')
        assert restarted.get('deepseek','deepseek-flash')['input_per_million']=='3'
        assert client.delete('/api/pricing?provider=deepseek&model=deepseek-flash').status_code==200
        assert restarted.get('deepseek','deepseek-flash') is None
        assert manager.store.run(row['id'])['options']['pricing']['input_per_million']=='3'
        manager.stop(row['id'])
        value=client.get(f"/api/runs/{row['id']}").json()['usage']
        assert value['available'] and value['calls']==0 and Decimal(value['estimated_cost'])==0


def test_broken_price_file_does_not_silently_fallback_to_default(tmp_path):
    path=tmp_path/'pricing.json';path.write_text('bad',encoding='utf-8');book=PricingBook(path)
    assert book.get('deepseek','deepseek-flash') is None and book.read()['warning']
    profile=PriceProfile(**flash_profile());book.save(profile.provider,profile.model,profile)
    assert book.read()['warning'] is None


def test_actual_client_retry_attempts_and_safe_failure_trace():
    from test_llm import make_client,api_error,fake_completion
    client,fake=make_client([api_error(openai.RateLimitError,429),fake_completion('ok')])
    events=[];result=Agent(client,ToolRegistry(),on_event=events.append).run('test')
    event=next(e.data for e in events if e.kind=='llm_call')
    assert result.stop_reason=='final_answer' and event['request_attempts']==2
    assert event['usage_reported'] and event['cache_usage_reported'] is False
    assert event['provider']=='deepseek' and client.request_attempts==2
    client,fake=make_client([api_error(openai.InternalServerError,500) for _ in range(3)])
    events=[];result=Agent(client,ToolRegistry(),on_event=events.append).run('test')
    event=next(e.data for e in events if e.kind=='llm_error')
    assert result.stop_reason=='error' and event['request_attempts']==3
    assert event['error_type']=='InternalServerError' and 'api_key' not in event


@pytest.mark.parametrize('cache_fields,expected',[
    ({'prompt_cache_hit_tokens':0},0),({'prompt_cache_hit_tokens':3},3),
    ({'prompt_tokens_details':{'cached_tokens':0}},0),
    ({'prompt_tokens_details':{'cached_tokens':3}},3),
])
def test_zero_and_nonzero_cache_metadata_is_distinguished_from_missing(cache_fields,expected):
    from fakes import completion,ScriptedLLM
    from openai.types.chat import ChatCompletion
    value=completion('ok').model_dump()
    value['usage'].update(cache_fields)
    response=ChatCompletion.model_validate(value)
    events=[]
    Agent(ScriptedLLM([response]),ToolRegistry(),on_event=events.append).run('test')
    event=next(e.data for e in events if e.kind=='llm_call')
    assert event['cache_usage_reported'] is True and event['cached_tokens']==expected
