import copy
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient
from agent.config import Settings,load_llm_config
from agent.strength import capability
from agent.context import estimate_message_tokens
from agent.loop import Agent
from agent.checkpoint import agent_identity
from agent.llm import LLMClient
from desktop_app.manager import Manager
from desktop_app.server import create_app
from fakes import completion
from tools.registry import ToolRegistry,tool


@pytest.mark.parametrize('level,effort',[('default',None),('low','low'),('medium','high'),('high','max')])
def test_real_request_parameters_and_original_limits(level,effort):
    config=load_llm_config(settings=Settings(_env_file=None,deepseek_api_key='fake'),strength=level)
    class SDK:
        def __init__(self):self.chat=self;self.completions=self;self.requests=[]
        def create(self,**kw):self.requests.append(kw);return completion('ok')
    sdk=SDK();LLMClient(config,client=sdk).chat([{'role':'user','content':'hi'}],max_tokens=1200)
    params=sdk.requests[0]
    assert params['max_tokens']==1200
    assert config.reasoning_effort==effort
    if effort:
        assert params['reasoning_effort']==effort and params['extra_body']['thinking']=={'type':'enabled'}
        assert 'temperature' not in params
    else:
        assert 'reasoning_effort' not in params and params['extra_body']['thinking']=={'type':'disabled'}


@pytest.mark.parametrize('provider,model,url',[('qwen','qwen-plus',None),('deepseek','unknown-model',None),
    ('deepseek','deepseek-flash','https://proxy.example.com'),('deepseek','deepseek-flash','https://api.deepseek.com.evil.test'),
    ('custom','deepseek-flash','https://api.deepseek.com')])
def test_unknown_capability_rejected_before_any_request(provider,model,url):
    settings=Settings(_env_file=None,llm_provider=provider,llm_model=model,llm_base_url=url,
                      deepseek_api_key='fake',dashscope_api_key='fake',custom_api_key='fake')
    with pytest.raises(ValueError,match='尚未验证'):load_llm_config(settings=settings,strength='high')
    assert load_llm_config(settings=settings,strength='default').reasoning_effort is None


def test_thinking_tool_roundtrip_keeps_protocol_data_and_counts_context(tmp_path):
    @tool
    def local_record()->str:
        """返回本地测试记录。"""
        return 'local result'
    responses=[completion(tool_calls=[('t1','local_record',{})]),completion('完成')]
    responses[0].choices[0].message.reasoning_content='opaque provider continuation data'
    class SDK:
        def __init__(self):self.chat=self;self.completions=self;self.requests=[]
        def create(self,**kw):self.requests.append(copy.deepcopy(kw));return responses.pop(0)
    sdk=SDK();config=load_llm_config(settings=Settings(_env_file=None,deepseek_api_key='fake'),strength='high')
    llm=LLMClient(config,client=sdk)
    agent=Agent(llm,ToolRegistry([local_record]),max_steps=5,token_budget=30000)
    result=agent.run('只读取本地测试记录，不联网')
    assert result.stop_reason=='final_answer' and len(sdk.requests)==2
    prior=next(m for m in sdk.requests[-1]['messages'] if m.get('tool_calls'))
    assert prior['reasoning_content']=='opaque provider continuation data'
    assert estimate_message_tokens(prior)>estimate_message_tokens({k:v for k,v in prior.items() if k!='reasoning_content'})
    assert agent_identity(agent)['model']['reasoning_effort']=='max'
    assert all(p['reasoning_effort']=='max' for p in sdk.requests)


def test_api_capability_and_saved_run_are_read_only_without_provider_requests(tmp_path):
    manager=Manager(tmp_path/'state',start=False,settings=Settings(_env_file=None,deepseek_api_key='fake'))
    with TestClient(create_app(manager,'test')) as client:
        assert client.get('/api/model-strength').status_code==401
        client.headers['Authorization']='Bearer test'
        cap=client.get('/api/model-strength').json()
        assert cap['supported'] and cap['effort_mapping']=={'low':'low','medium':'high','high':'max'}
        assert not client.get('/api/model-strength?provider=qwen&model=qwen-plus').json()['supported']
        sid=client.post('/api/sessions',json={}).json()['id']
        body={'question':'local test','strength':'medium'}
        run=client.post(f'/api/sessions/{sid}/runs',json=body).json()
        assert run['options']['strength']=='medium' and run['options']['model_parameters']['reasoning_effort']=='high'
        assert run['options']['budget']==300000 and run['options']['max_steps']==30
        assert client.post(f'/api/sessions/{sid}/runs',json={**body,'model':'unsupported'}).status_code==400
        assert client.post(f'/api/sessions/{sid}/runs',json={**body,'strength':'extreme'}).status_code==422
        assert len(client.get(f'/api/sessions/{sid}/runs').json())==1


def test_worker_uses_saved_strength_instead_of_new_environment(tmp_path,monkeypatch):
    import desktop_app.worker as worker
    config=Settings(_env_file=None,deepseek_api_key='fake')
    captured={}
    @dataclass
    class Result:
        stop_reason:str='final_answer'
    def research(client,settings,question,max_steps,budget,**kw):
        captured.update(effort=client.config.reasoning_effort,setting=settings.llm_strength,steps=max_steps,budget=budget)
        return Result()
    (tmp_path/'.desktop-request.json').write_text(json.dumps({'question':'test','options':{
        'provider':'deepseek','model':'deepseek-flash','strength':'medium','memory':False,'max_steps':10,'budget':5000}}),'utf-8')
    monkeypatch.setattr(worker,'Settings',lambda:config)
    monkeypatch.setattr(worker,'LLMClient',lambda c:NS(config=c))
    monkeypatch.setattr(worker,'research',research)
    monkeypatch.setattr('sys.argv',['worker',str(tmp_path)])
    worker.main()
    assert captured=={'effort':'high','setting':'medium','steps':10,'budget':5000}


def test_folder_question_and_continuation_pin_strength(library):
    from test_folder_reading import setup_reading,start
    from test_folder_answers import finished
    _,jobs,chat=setup_reading(library)
    configs=[];factory=jobs.llm_factory
    def configured(config):configs.append(config);return factory(config)
    jobs.llm_factory=configured
    first=finished(jobs,start(library,jobs,strength='high'))
    second=finished(jobs,start(library,jobs,resume_from=first['id'],strength='low'))
    assert first['inputs']['strength']==second['inputs']['strength']=='high'
    assert [c.reasoning_effort for c in configs]==['max','max']
    assert all(e['reasoning_effort']=='max' for e in second['usage_events'])
    from folder_assistant.exports import export_job
    out=Path(export_job(jobs,second['id'])['directory'])
    assert json.loads((out/'sources.json').read_text('utf-8'))['strength']=='high'


from test_folder_index import library
