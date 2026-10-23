"""Resource decisions only; fake outputs never certify semantic quality."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from folder_assistant.batching import select_batch
from folder_assistant.field_plan import prepare_request
from folder_assistant.full_reading import preview_full_reading
from folder_assistant.scope import preview_next
from folder_assistant.exports import export_job
from test_folder_index import library
from test_folder_answers import finished
from test_folder_reading import start
from test_folder_field_plan import configured,QUERY,FIELDS


class Plan:
    def __init__(self):
        self.segments=[{'doc_id':'document','source_block_id':str(i),'text':f'row {i}',
                        'name':'sample.txt','file_sha256':'a'*64,'source_location':{'line':i+1},
                        'role':'sequential','start':0,'end':5} for i in range(6)]
    def batch(self,offset,size):
        return {'hits':self.segments[offset:offset+size],'selected_segments':size,
                'next_offset':offset+size,'total_segments':len(self.segments)}


@pytest.mark.parametrize('declared,planned',[(1,0),(1,2),(1,6),(1,12),(7,2)])
def test_sizing_uses_larger_declared_or_planned_count_without_mutating_input(declared,planned):
    specs=[{'name':f'field {n}','mode':'attribute','instruction_quote':f'field {n}'} for n in range(planned)]
    inputs={'expected_fields':declared,'field_specs':specs,'query':' '.join(s['name'] for s in specs) or 'summarize',
            'batch_mode':'adaptive','output_tokens':4096,'batch_size':6,'model':{'thinking':'disabled'}}
    original=copy.deepcopy(inputs);plan=Plan()
    batch,decision=select_batch(plan,inputs,0,1,50000,[],4096)
    explicit={**inputs,'expected_fields':max(declared,planned)}
    other,control=select_batch(plan,explicit,0,1,50000,[],4096)
    assert inputs==original
    assert decision['expected_fields']==max(declared,planned)
    assert decision['requested_expected_fields']==declared and decision['planned_fields']==planned
    assert batch==other and decision['trials']==control['trials']
    if batch:assert batch['selected_segments']*max(declared,planned)<=40


def test_default_adaptive_job_and_resume_use_actual_plan_without_changing_limits_or_answers(library):
    jobs,chat=configured(library)
    first=finished(jobs,start(library,jobs,query=QUERY,read_to_end=True,plan_fields=True,max_calls=2,batch_mode='adaptive'))
    assert first['inputs']['expected_fields']==1 and first['inputs']['output_tokens']==1200
    decision=first['batch_decisions'][0]
    assert decision['expected_fields']==2 and decision['requested_expected_fields']==1
    assert decision['planned_fields']==2 and decision['fits_estimate']
    before=copy.deepcopy(first);calls=len(chat.calls)
    controls={'read_to_end':True,'plan_fields':True,'max_calls':1,'budget':20000}
    preview=preview_next(jobs,first['id'],controls)
    assert preview['planning']['status']=='inherited' and not preview['planning']['uses_call']
    assert preview['first_request']=='evidence' and not preview['first_batch_provisional']
    assert preview['decision']['expected_fields']==2 and len(chat.calls)==calls
    second=finished(jobs,start(library,jobs,resume_from=first['id'],**controls,batch_mode='adaptive'))
    assert len(chat.plan_calls)==1 and second['batches'][0]['next_offset']==preview['first_batch_end']
    assert second['batch_decisions'][0]['expected_fields']==2
    assert jobs.get(first['id'])==before and first['inputs']['field_specs']==FIELDS
    directory=Path(export_job(jobs,second['id'])['directory'])
    saved=json.loads((directory/'sources.json').read_text('utf-8'))
    assert saved['batch_decisions'][0]['requested_expected_fields']==1
    assert saved['batch_decisions'][0]['expected_fields']==2


def test_new_and_continuation_previews_describe_planner_only_and_budget_refusal_without_calls(library):
    jobs,chat=configured(library)
    from test_folder_answers import ask
    source=finished(jobs,ask(library,jobs,query=QUERY))
    parent=finished(jobs,start(library,jobs,query=QUERY,batch_size=1))
    history=copy.deepcopy(jobs.list());calls=len(chat.calls)
    controls={'read_to_end':True,'plan_fields':True,'max_calls':1,'budget':20000}
    for preview in (preview_full_reading(jobs,source['id'],controls),preview_next(jobs,parent['id'],controls)):
        assert preview['planning']['status']=='pending' and preview['planning']['data_call_slots']==0
        assert preview['first_request']=='field_plan' and preview['services']==['llm']
        assert preview['decision']['stop_reason']=='call_limit' and not preview['first_batch']
        assert not preview['first_batch_provisional']
    for preview in (preview_full_reading(jobs,source['id'],{**controls,'budget':256}),
                    preview_next(jobs,parent['id'],{**controls,'budget':256})):
        assert preview['planning']['status']=='budget_insufficient'
        assert preview['first_request'] is None and not preview['services'] and not preview['first_batch']
    provisional=preview_full_reading(jobs,source['id'],{**controls,'max_calls':3})
    assert provisional['first_batch_provisional'] and provisional['planning']['data_call_slots']==2
    request=prepare_request(QUERY,1200,jobs.index.settings.agent_call_max_tokens,20000)
    assert provisional['planning']['estimated_input_tokens']==request['estimated_input_tokens']
    assert provisional['planning']['max_output_tokens']==request['max_output_tokens']
    assert jobs.list()==history and len(chat.calls)==calls and not chat.plan_calls


def test_sizing_version_change_invalidates_saved_preview_without_calling(library,monkeypatch):
    from folder_assistant import batching
    from folder_assistant.full_reading import start_full_reading
    from test_folder_answers import ask
    jobs,chat=configured(library);source=finished(jobs,ask(library,jobs,query=QUERY))
    preview=preview_full_reading(jobs,source['id'],{})
    history=copy.deepcopy(jobs.list());calls=len(chat.calls)
    monkeypatch.setattr(batching,'VERSION',batching.VERSION+1)
    with pytest.raises(ValueError,match='计划已变化'):
        start_full_reading(jobs,source['id'],{},preview['plan_hash'])
    assert jobs.list()==history and len(chat.calls)==calls


@pytest.mark.parametrize('failure',['stop','setup','budget','overrun'])
def test_planner_terminal_status_matches_cause_and_never_sends_data(library,failure):
    jobs,chat=configured(library)
    kwargs={}
    if failure=='stop':
        def factory(config):jobs.stop(jobs.list()[0]['id']);return chat
        jobs.llm_factory=factory
    elif failure=='setup':
        def factory(config):raise ValueError('private-setup-body')
        jobs.llm_factory=factory
    elif failure=='budget':kwargs['budget']=256
    else:
        original=chat.chat
        def overrun(*a,**kw):
            value=original(*a,**kw);value.usage=NS(prompt_tokens=20000,completion_tokens=100);return value
        chat.chat=overrun
    result=finished(jobs,start(library,jobs,query=QUERY,read_to_end=True,plan_fields=True,**kwargs))
    assert result['field_plan']['status']=={'stop':'stopped','setup':'setup_failed','budget':'budget_insufficient','overrun':'budget_exceeded'}[failure]
    assert not chat.calls and result['coverage']['next_offset']==0
    assert result['status']==('stopped' if failure=='stop' else 'failed')
    if failure=='overrun':
        assert len(chat.plan_calls)==1 and jobs.used_tokens(result['id'])==20100
        assert result['field_plan']['model_output'] and len(result['usage_events'])==1
    else:assert not chat.plan_calls and not result.get('usage_events')
    assert 'private-setup-body' not in json.dumps(result)
