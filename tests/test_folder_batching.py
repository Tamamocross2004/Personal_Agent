import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from folder_assistant.batching import select_batch
from folder_assistant.reading import ReadingPlan
from test_folder_index import library,sync
from test_folder_reading import setup_reading,start
from test_folder_answers import finished


def test_adaptive_responds_to_declared_fields_output_and_length_without_changing_scope(library):
    generation,jobs,chat=setup_reading(library)
    value=finished(jobs,start(library,jobs,batch_mode='adaptive',max_batches=8))
    assert value['status']=='completed' and value['coverage']['structured_coverage_complete']
    ids=[e['segment'] for e in value['evidence'] if e['role']=='sequential']
    assert ids==list(range(16))
    assert all(b['sizing']['fits_estimate'] for b in value['batches'])
    assert len(chat.calls)>1
    plan=ReadingPlan(library[3],library[2],generation)
    base={**value['inputs'],'range_end':16}
    _,one=select_batch(plan,base,0,1,20000,[],4096)
    _,many=select_batch(plan,{**base,'expected_fields':5},0,1,20000,[],4096)
    _,large=select_batch(plan,{**base,'output_tokens':4096},0,1,20000,[],4096)
    assert many['selected_segments']<=one['selected_segments']<=large['selected_segments']
    assert value['inputs']['output_tokens']==1200 and value['inputs']['budget']==20000


def test_output_estimate_refusal_and_explicit_fixed_choice_do_not_change_validation(library):
    _,jobs,chat=setup_reading(library)
    first=finished(jobs,start(library,jobs,batch_mode='adaptive',output_tokens=128))
    assert not chat.calls and first['coverage']['next_offset']==0
    assert first['coverage']['stop_reason']=='output_space'
    assert first['batch_decisions'] and not first['batch_decisions'][-1]['fits_estimate']
    second=finished(jobs,start(library,jobs,batch_mode='fixed',batch_size=1,output_tokens=128))
    assert len(chat.calls)==1 and chat.calls[0][1]['max_tokens']==128
    assert second['result']['review_status']=='not_reviewed'


def test_actual_remaining_budget_and_project_output_cap_are_respected(library):
    _,jobs,chat=setup_reading(library)
    jobs.index.settings=jobs.index.settings.model_copy(update={'agent_call_max_tokens':800})
    original=chat.chat
    def charged(*args,**kwargs):
        result=original(*args,**kwargs)
        result.usage=NS(prompt_tokens=800,completion_tokens=700)
        return result
    chat.chat=charged
    value=finished(jobs,start(library,jobs,batch_mode='adaptive',output_tokens=4096,max_batches=8,budget=4000))
    assert all(options['max_tokens']<=800 for _,options in chat.calls)
    assert value['inputs']['effective_output_cap']==800
    assert all(b['request']['max_output_tokens']<=4000-b['request']['estimated_input_tokens'] for b in value['batches'])
    assert value['coverage']['remaining_segments']>0
    assert value['coverage']['stop_reason']=='token_budget'


def test_explicit_split_preserves_parent_and_does_not_go_past_failed_range(library):
    generation,jobs,chat=setup_reading(library)
    chat.invalid=True
    first=finished(jobs,start(library,jobs,max_batches=8))
    before=copy.deepcopy(jobs.get(first['id']))
    assert len(chat.calls)==1
    chat.invalid=False
    # Replace original disk source and active index; the retry still uses the pinned version.
    (library[0]/'table.csv').write_text('replacement\n',encoding='utf-8');sync(library)
    second=finished(jobs,start(library,jobs,resume_from=first['id'],retry_split=True,max_batches=8,
                              batch_mode='adaptive',output_tokens=2400))
    assert second['inputs']['generation']==generation and second['inputs']['output_tokens']==2400
    assert second['inputs']['batch_size']==3 and second['inputs']['range_end']==6
    assert second['coverage']['next_offset']==6 and second['coverage']['remaining_segments']==10
    assert second['coverage']['stop_reason']=='retry_range_finished'
    assert [e['segment'] for e in second['evidence'] if e['role']=='sequential']==list(range(6))
    assert jobs.get(first['id'])==before
    assert len(chat.calls)==3  # original invalid + two explicitly requested smaller batches
    from folder_assistant.exports import export_job
    directory=Path(export_job(jobs,second['id'])['directory'])
    assert json.loads((directory/'sources.json').read_text('utf-8'))['output_policy']['retry_of']['job_id']==first['id']


def test_single_segment_or_unknown_usage_cannot_be_split(library):
    _,jobs,chat=setup_reading(library)
    chat.invalid=True
    first=finished(jobs,start(library,jobs,batch_size=1))
    with pytest.raises(ValueError,match='单片段'):start(library,jobs,resume_from=first['id'],retry_split=True)
    chat.invalid=False;chat.unknown=True
    second=finished(jobs,start(library,jobs))
    with pytest.raises(ValueError,match='未知用量'):start(library,jobs,resume_from=second['id'],retry_split=True)
    assert len(chat.calls)==2


def test_reasoning_usage_unknown_is_explicit_and_observed_tokens_affect_later_sizing(library):
    generation,jobs,_=setup_reading(library)
    config={'batch_mode':'adaptive','output_tokens':1200,'expected_fields':1,'batch_size':6,'range_end':16,
            'query':'any question','model':{'thinking':'enabled'}}
    plan=ReadingPlan(library[3],library[2],generation)
    _,unknown=select_batch(plan,config,0,1,20000,[],4096)
    _,known=select_batch(plan,config,0,1,20000,[{'status':'returned','reasoning_tokens':700}],4096)
    assert unknown['reasoning_usage_unknown'] and unknown['estimated_reasoning_tokens'] is None
    assert not known['reasoning_usage_unknown'] and known['estimated_reasoning_tokens']==700
    assert known['selected_segments']<=unknown['selected_segments']


def test_returned_reasoning_breakdown_is_a_subset_not_added_to_total(library):
    _,jobs,chat=setup_reading(library)
    original=chat.chat
    def with_breakdown(*args,**kwargs):
        result=original(*args,**kwargs)
        result.usage.completion_tokens_details={'reasoning_tokens':20}
        return result
    chat.chat=with_breakdown
    value=finished(jobs,start(library,jobs))
    assert value['usage_events'][0]['reasoning_tokens']==20
    assert jobs.used_tokens(value['id'])==140
    chat.chat=lambda *a,**kw:NS(model='fake',usage=NS(prompt_tokens=100,completion_tokens=40,
        completion_tokens_details=NS(reasoning_tokens=41)),choices=original(*a,**kw).choices)
    second=finished(jobs,start(library,jobs))
    assert second['usage_events'][0]['reasoning_tokens'] is None
    assert jobs.used_tokens(second['id'])==140


@pytest.mark.parametrize('kw',[{'output_tokens':4097},{'batch_size':0},{'expected_fields':0},{'batch_mode':'secret'},{'retry_split':True}])
def test_bad_budget_controls_fail_before_any_request(library,kw):
    sync(library);from test_folder_answers import service
    jobs,chat=service(library)
    with pytest.raises(ValueError):start(library,jobs,**kw)
    assert not chat.calls
