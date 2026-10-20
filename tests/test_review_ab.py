"""固定报告对照实验的冻结、无标签请求、记账与失败保存；不验证模型语义能力。"""
import copy
import json

import httpx
import openai
import pytest

from agent.config import LLMConfig
from experiments import review_ab as ab
from fakes import completion


@pytest.fixture
def frozen(tmp_path):
    folder=tmp_path/'plan'
    plan=ab.freeze(folder)
    return folder,plan


def fake_config(**kwargs):
    return LLMConfig(provider='deepseek',base_url='https://offline.invalid',api_key='offline-not-a-secret',
                     model='deepseek-flash',temperature=.3,timeout=60,max_retries=0,
                     max_tokens=None,thinking='disabled')


class Model:
    def __init__(self,fail=False,missing_usage=False,truncate=False):
        self.request_attempts=0
        self.requests=[]
        self.fail=fail
        self.missing_usage=missing_usage
        self.truncate=truncate

    def chat(self,messages,**kwargs):
        self.request_attempts+=1
        self.requests.append((messages,kwargs))
        if self.fail:
            raise openai.APIConnectionError(request=httpx.Request('POST','https://offline.invalid'))
        body={'issues':[]}
        if 'claim_checks' in messages[0]['content']:
            body['claim_checks']=[{'claim_index':0,'status':'supported','reason':'测试夹具的合成意见。'}]
        result=completion(json.dumps(body,ensure_ascii=False),finish_reason='length' if self.truncate else 'stop')
        if self.missing_usage:
            result.usage=None
        return result


def test_paired_requests_have_identical_evidence_and_no_hidden_gold_fields(frozen):
    _,plan=frozen
    assert len(plan['definitions'])==12 and len(plan['trials'])==48
    for d in plan['definitions']:
        baseline=d['requests']['baseline']
        challenger=d['requests']['challenger']
        assert baseline[1]==challenger[1] and baseline[0]!=challenger[0]
        payload=json.loads(baseline[1]['content'])
        assert ab.digest(payload)==d['payload_sha256']
        assert payload['mechanical_check']['issues']==[]
        assert d['report'].count(d['focus'])==1
        for forbidden in ('rubric','case_id','correct','incorrect','ambiguous'):
            assert forbidden not in baseline[1]['content']
        assert 'premises' not in payload['claim_evidence']['items'][0]['claim']
        assert 'reasoning' not in payload['claim_evidence']['items'][0]['claim']


def test_order_is_reproducible_paired_and_counterbalanced(frozen):
    _,plan=frozen
    trials=plan['trials']
    assert trials==ab.schedule(plan['definitions'])
    for index in range(0,len(trials),2):
        left,right=trials[index:index+2]
        assert (left['case_id'],left['repeat'])==(right['case_id'],right['repeat'])
        assert left['arm']!=right['arm']
    assert sum(t['arm']=='baseline' for t in trials[::2])==12
    assert sum(t['arm']=='challenger' for t in trials[::2])==12


@pytest.mark.parametrize('target',['definition','lock','policy'])
def test_changed_inputs_or_lock_reject_before_api(frozen,target):
    _,original=frozen
    plan=copy.deepcopy(original)
    if target=='definition':
        plan['definitions'][0]['report']+='篡改'
    elif target=='lock':
        plan['lock_sha256']='wrong'
    else:
        plan['identity']['policy']['seed']+=1
    with pytest.raises(ValueError):
        ab.verify(plan)


def test_all_trials_record_raw_responses_and_equal_call_parameters(frozen,tmp_path,monkeypatch):
    folder,_=frozen
    monkeypatch.setattr(ab,'load_llm_config',fake_config)
    model=Model()
    out=tmp_path/'results'
    state=ab.run(folder/'lock.json',out,model)
    assert state['state']=='complete' and len(state['trials'])==48
    assert all(t['status']=='complete' for t in state['trials'])
    assert state['request_attempts']==48 and state['equivalent_tokens']==48*20
    assert len(list(out.glob('*-response.json')))==48
    assert all(kwargs=={'tools':None,'response_format':{'type':'json_object'},'max_tokens':2048}
               for _,kwargs in model.requests)
    assert 'offline-not-a-secret' not in (out/'results.json').read_text(encoding='utf-8')
    with pytest.raises(FileExistsError):
        ab.run(folder/'lock.json',out,model)
    assert model.request_attempts==48


@pytest.mark.parametrize('fault,status',[('fail','failed_unknown_outcome'),('missing_usage','usage_unknown')])
def test_unknown_outcome_stops_without_retries_and_preserves_not_run(frozen,tmp_path,monkeypatch,fault,status):
    folder,_=frozen
    monkeypatch.setattr(ab,'load_llm_config',fake_config)
    model=Model(**{fault:True})
    state=ab.run(folder/'lock.json',tmp_path/'results',model)
    assert model.request_attempts==1 and state['trials'][0]['status']==status
    assert sum(t['status']=='not_run' for t in state['trials'])==47
    assert state['state'].startswith('stopped_')


def test_truncated_replies_are_not_clean_and_do_not_requeue_same_trial(frozen,tmp_path,monkeypatch):
    folder,_=frozen
    monkeypatch.setattr(ab,'load_llm_config',fake_config)
    model=Model(truncate=True)
    state=ab.run(folder/'lock.json',tmp_path/'results',model)
    assert model.request_attempts==48 and all(t['status']=='truncated' for t in state['trials'])
    assert all(t['usage']['equivalent_tokens']==20 for t in state['trials'])


def test_budget_skips_are_explicit_not_replaced_by_success(tmp_path,monkeypatch):
    monkeypatch.setattr(ab,'POLICY',{**ab.POLICY,'equivalent_budget':1})
    ab.freeze(tmp_path/'plan')
    monkeypatch.setattr(ab,'load_llm_config',fake_config)
    model=Model()
    state=ab.run(tmp_path/'plan/lock.json',tmp_path/'results',model)
    assert model.request_attempts==0 and all(t['status']=='skipped_budget' for t in state['trials'])


def test_assistant_summary_is_bound_to_results_and_not_an_automatic_model_grade(frozen):
    _,plan=frozen
    definitions={d['id']:d for d in plan['definitions']}
    state={'lock_sha256':plan['lock_sha256'],
           'trials':[{**t,'status':'complete','usage':{'prompt_tokens':10,'completion_tokens':5,
            'cached_tokens':0,'cache_reported':False,'equivalent_tokens':20}} for t in plan['trials']]}
    decisions=[]
    for trial in state['trials']:
        kind=definitions[trial['case_id']]['kind']
        decisions.append({'id':trial['id'],'trial_sha256':ab.digest(trial),'reviewer_kind':'coding_assistant',
            'outcome':{'correct':'clean','incorrect':'misses_error','ambiguous':'preserves_ambiguity'}[kind],
            'notes':['本测试仅验证汇总，不评价实际语义。']})
    result=ab.summarize(plan,state,decisions)
    assert result['metrics']['baseline']['outcomes']['misses_error']==8
    assert result['metrics']['challenger']['cache_unreported_calls']==24
    assert result['blind_review'] is False
    with pytest.raises(ValueError): ab.summarize(plan,state,decisions[:-1])
    decisions[0]['trial_sha256']='wrong'
    with pytest.raises(ValueError): ab.summarize(plan,state,decisions)


def test_raw_response_changes_reject_even_when_result_metadata_is_unchanged(frozen,tmp_path,monkeypatch):
    folder,_=frozen
    monkeypatch.setattr(ab,'load_llm_config',fake_config)
    out=tmp_path/'results'
    state=ab.run(folder/'lock.json',out,Model(missing_usage=True))
    ab.verify_outputs(out,state)
    (out/'001-response.json').write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='raw response'):
        ab.verify_outputs(out,state)


def test_summary_cannot_omit_trials_from_the_frozen_schedule(frozen):
    _,plan=frozen
    state={'lock_sha256':plan['lock_sha256'],'trials':plan['trials'][:-1]}
    with pytest.raises(ValueError,match='schedule'):
        ab.summarize(plan,state,[])
