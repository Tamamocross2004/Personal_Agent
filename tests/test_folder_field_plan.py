import copy,json
from types import SimpleNamespace as NS
import pytest

from folder_assistant.field_plan import validate
from test_folder_index import library
from test_folder_reading import setup_reading,start,BatchChat
from test_folder_answers import finished

QUERY='整理cycle，并逐字摘录unit。'
FIELDS=[{'name':'cycle','mode':'attribute','instruction_quote':'整理cycle'},
        {'name':'unit','mode':'verbatim','instruction_quote':'逐字摘录unit'}]


class PlannerChat(BatchChat):
    def __init__(self):super().__init__();self.plan_mode='ok';self.plan_calls=[]
    def chat(self,messages,**kwargs):
        if messages[0]['content'].startswith('只规划用户问题要求整理的字段'):
            self.plan_calls.append((messages,kwargs))
            if self.plan_mode=='fail':raise RuntimeError('private-provider-body')
            if self.hook:self.hook()
            fields=FIELDS if self.plan_mode!='invalid' else [{**FIELDS[0],'name':'not-requested'}]
            return NS(model='fake',usage=None if self.plan_mode=='unknown' else NS(prompt_tokens=80,completion_tokens=40),
                      choices=[NS(message=NS(content=json.dumps({'fields':fields})),finish_reason='length' if self.plan_mode=='length' else 'stop')])
        return super().chat(messages,**kwargs)


def configured(library):
    _,jobs,_=setup_reading(library,rows=12);chat=PlannerChat();jobs.llm_factory=lambda c:chat
    return jobs,chat


def test_plan_sees_only_query_then_bound_metadata_reaches_real_requests(library):
    jobs,chat=configured(library)
    job=finished(jobs,start(library,jobs,query=QUERY,read_to_end=True,plan_fields=True,max_calls=16,batch_size=1))
    assert len(chat.plan_calls)==1 and len(chat.calls)==13
    assert json.loads(chat.plan_calls[0][0][1]['content'])=={'question':QUERY}
    assert 'Unit-000' not in json.dumps(chat.plan_calls[0][0])
    assert job['field_plan']['status']=='ready' and job['inputs']['field_specs']==FIELDS
    assert job['coverage']['input_coverage_complete'] and len(job['usage_events'])==14
    assert jobs.used_tokens(job['id'])==120+13*140
    assert job['batch_decisions'][0]['remaining_tokens']==20000-120
    for messages,_ in chat.calls:
        assert json.loads(messages[1]['content'])['field_intents']==FIELDS
    assert job['field_plan']['model_output'] and job['field_plan']['request']['messages']==chat.plan_calls[0][0]


def test_plan_counts_against_call_limit_and_resume_inherits_without_replanning(library):
    jobs,chat=configured(library)
    first=finished(jobs,start(library,jobs,query=QUERY,read_to_end=True,plan_fields=True,max_calls=2,batch_size=1))
    original=copy.deepcopy(first)
    assert len(first['batches'])==1 and first['coverage']['stop_reason']=='call_limit'
    second=finished(jobs,start(library,jobs,resume_from=first['id'],read_to_end=True,plan_fields=True,max_calls=16,batch_size=1))
    assert len(chat.plan_calls)==1 and second['field_plan']['status']=='inherited'
    assert second['inputs']['field_specs']==FIELDS and second['inputs']['field_plan_source_job_id']==first['id']
    assert second['coverage']['input_coverage_complete'] and len(second['usage_events'])==12
    assert jobs.get(first['id'])==original


@pytest.mark.parametrize('mode',['invalid','unknown','length','fail'])
def test_invalid_or_unfinished_plan_never_reads_sources_or_retries(library,mode):
    jobs,chat=configured(library);chat.plan_mode=mode
    value=finished(jobs,start(library,jobs,query=QUERY,read_to_end=True,plan_fields=True,max_calls=16))
    assert value['status']=='failed' and len(chat.plan_calls)==1 and not chat.calls
    assert value['batches']==[] and value['coverage']['next_offset']==0
    assert value['usage_events'][0]['purpose']=='field_plan'
    if mode!='fail':assert value['field_plan']['model_output']
    assert 'private-provider-body' not in json.dumps(value)


def test_stop_during_planning_keeps_raw_and_usage_without_sending_evidence(library):
    jobs,chat=configured(library)
    chat.hook=lambda:jobs.stop(jobs.list()[0]['id'])
    value=finished(jobs,start(library,jobs,query=QUERY,read_to_end=True,plan_fields=True,max_calls=16))
    assert value['status']=='stopped' and not chat.calls
    assert len(chat.plan_calls)==1 and jobs.used_tokens(value['id'])==120
    assert value['field_plan']['model_output'] and value['field_plan']['status']=='stopped'


def test_stop_in_client_construction_prevents_request(library):
    jobs,chat=configured(library)
    def factory(config):jobs.stop(jobs.list()[0]['id']);return chat
    jobs.llm_factory=factory
    value=finished(jobs,start(library,jobs,query=QUERY,read_to_end=True,plan_fields=True,max_calls=16))
    assert value['status']=='stopped' and not chat.plan_calls and not value.get('usage_events')


def test_planner_only_limit_and_tiny_budget_cannot_claim_source_delivery(library):
    jobs,chat=configured(library)
    value=finished(jobs,start(library,jobs,query=QUERY,read_to_end=True,plan_fields=True,max_calls=1))
    assert value['coverage']['stop_reason']=='call_limit' and value['coverage']['next_offset']==0
    assert len(chat.plan_calls)==1 and not chat.calls
    low=finished(jobs,start(library,jobs,query=QUERY,read_to_end=True,plan_fields=True,max_calls=16,budget=256))
    assert low['status']=='failed' and not low.get('usage_events') and len(chat.plan_calls)==1


@pytest.mark.parametrize('fields',[FIELDS+[FIELDS[0]], [{'name':'extra','mode':'attribute','instruction_quote':QUERY}],
    [{'name':'cycle','mode':'verbatim','instruction_quote':'fabricated'}], [{'name':'cycle','mode':'other','instruction_quote':'整理cycle'}]])
def test_names_quotes_modes_and_duplicate_fields_are_checked_without_answer_specific_rules(fields):
    with pytest.raises(ValueError):validate(json.dumps({'fields':fields}),QUERY)
    assert validate(json.dumps({'fields':FIELDS}),QUERY)==FIELDS
    assert validate('{"fields":[]}',QUERY)==[]
