"""Bounded continuous reading uses one budget and stops on any unsafe response."""
import copy
import pytest

from folder_assistant.full_reading import preview_full_reading,start_full_reading
from folder_assistant.scope import preview_next,scope_view
from test_folder_index import library
from test_folder_reading import setup_reading,start
from test_folder_answers import ask,finished


def test_opt_in_reads_past_eight_calls_without_resetting_budget(library):
    _,jobs,chat=setup_reading(library,rows=12)
    normal=finished(jobs,start(library,jobs,batch_size=1,max_batches=3))
    assert len(chat.calls)==3 and normal['coverage']['stop_reason']=='batch_limit'
    original=copy.deepcopy(normal)
    value=finished(jobs,start(library,jobs,read_to_end=True,max_calls=16,batch_size=1,max_batches=1))
    assert len(value['batches'])==13 and value['coverage']['structured_coverage_complete']
    assert value['coverage']['stop_reason']=='end_of_extraction'
    assert value['inputs']['batch_call_limit']==16 and value['inputs']['budget']==20000
    remaining=[d['remaining_tokens'] for d in value['batch_decisions']]
    assert remaining==[20000-140*n for n in range(13)]
    assert jobs.get(normal['id'])==original and value['review_status']=='not_reviewed'


@pytest.mark.parametrize('limit',[1,2,8])
def test_explicit_call_limit_preserves_unsent_range(library,limit):
    _,jobs,chat=setup_reading(library,rows=12)
    value=finished(jobs,start(library,jobs,read_to_end=True,max_calls=limit,batch_size=1))
    assert len(chat.calls)==limit and len(value['batches'])==limit
    assert value['coverage']['next_offset']==limit
    assert value['coverage']['remaining_segments']==13-limit
    assert value['coverage']['stop_reason']=='call_limit' and value['result']['content_status']=='partial'


def test_one_total_budget_stops_continuous_reading(library):
    _,jobs,chat=setup_reading(library,rows=30)
    value=finished(jobs,start(library,jobs,read_to_end=True,max_calls=64,batch_size=1,budget=4500))
    assert 0<len(chat.calls)<31 and jobs.used_tokens(value['id'])<=4500
    assert value['coverage']['stop_reason']=='token_budget'
    assert value['coverage']['remaining_segments']>0
    assert all(d['remaining_tokens']==4500-140*i for i,d in enumerate(value['batch_decisions']))


@pytest.mark.parametrize('flag',['invalid','unknown','fail_at'])
def test_bad_response_or_unknown_usage_never_retries(library,flag):
    _,jobs,chat=setup_reading(library)
    setattr(chat,flag,1 if flag=='fail_at' else True)
    value=finished(jobs,start(library,jobs,read_to_end=True,max_calls=64,batch_size=1))
    assert len(chat.calls)==1 and value['coverage']['next_offset']==0
    assert not value['coverage']['structured_coverage_complete']
    assert value['status']==('completed' if flag=='invalid' else 'failed')
    if flag=='invalid':assert value['coverage']['stop_reason']=='invalid_or_truncated_output'


def test_stopping_in_flight_preserves_response_and_stops_next_call(library):
    _,jobs,chat=setup_reading(library)
    chat.hook=lambda:jobs.stop(jobs.list()[0]['id'])
    value=finished(jobs,start(library,jobs,read_to_end=True,max_calls=64,batch_size=1))
    assert len(chat.calls)==1 and value['status']=='stopped'
    assert value['model_output'] and jobs.used_tokens(value['id'])==140


def test_preview_hash_and_continuation_use_effective_call_limit(library):
    _,jobs,chat=setup_reading(library,rows=12)
    source=finished(jobs,ask(library,jobs,query='cycle'))
    controls={'read_to_end':True,'max_calls':2,'batch_size':1,'batch_mode':'fixed','budget':20000}
    preview=preview_full_reading(jobs,source['id'],controls);calls=len(chat.calls)
    assert preview['read_to_end'] and preview['batch_call_limit']==2
    with pytest.raises(ValueError,match='计划已变化'):
        start_full_reading(jobs,source['id'],{**controls,'max_calls':3},preview['plan_hash'])
    assert len(chat.calls)==calls
    first=finished(jobs,start_full_reading(jobs,source['id'],controls,preview['plan_hash']))
    follow={**controls,'max_calls':16}
    view=preview_next(jobs,first['id'],follow)
    assert view['batch_call_limit']==16 and view['operation_start']==2
    second=finished(jobs,start(library,jobs,resume_from=first['id'],**follow))
    assert second['coverage']['input_coverage_complete'] and len(second['batches'])==11
    assert len(scope_view(jobs,second['id'])['chain'])==2


@pytest.mark.parametrize('controls',[{'max_calls':0},{'max_calls':65},{'max_calls':True},{'read_to_end':'yes'},
    {'strategy':'retrieval','read_to_end':True}])
def test_invalid_limits_never_call(library,controls):
    _,jobs,chat=setup_reading(library)
    with pytest.raises(ValueError):start(library,jobs,**controls)
    assert not chat.calls
