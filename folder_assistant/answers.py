"""Bounded retrieve-once / answer-once workflow, separate from formal research review."""
from dataclasses import replace
import json
from typing import Literal

from pydantic import BaseModel,ConfigDict,Field

from agent.config import load_llm_config
from agent.context import estimate_tokens
from knowledge.documents import sha
from knowledge.store import now
from .contracts import VERSION, GUIDE_VERSION, instruction, ContractError, validate as validate_v2
from .output_diagnostics import diagnose


class Finding(BaseModel):
    model_config=ConfigDict(extra='forbid')
    statement:str=Field(min_length=1,max_length=2500)
    kind:Literal['direct','inference','conflict','unknown']
    citations:list[str]=Field(max_length=12)


class Comparison(BaseModel):
    model_config=ConfigDict(extra='forbid')
    subject:str=Field(min_length=1,max_length=300)
    field:str=Field(min_length=1,max_length=300)
    value:str|None=Field(max_length=2000)
    status:Literal['stated','unknown','blank','conflict']
    citations:list[str]=Field(max_length=12)


class Answer(BaseModel):
    model_config=ConfigDict(extra='forbid')
    findings:list[Finding]=Field(min_length=1,max_length=20)
    unknowns:list[str]=Field(max_length=20)
    comparison:list[Comparison]=Field(max_length=40)



def validate_answer(text,evidence):
    """Legacy v1 check for old callers only; new paid requests strictly use validate_v2."""
    answer=Answer.model_validate_json(text)
    allowed={e['citation_id'] for e in evidence}
    for row in [*answer.findings,*answer.comparison]:
        if not set(row.citations).issubset(allowed): raise ValueError('模型返回了未送达证据的引用')
        category=row.kind if isinstance(row,Finding) else row.status
        if category!='unknown' and not row.citations: raise ValueError('非未知内容缺少依据引用')
        if category=='conflict' and len(set(row.citations))<2: raise ValueError('冲突内容缺少双方引用')
        if isinstance(row,Comparison) and row.status in ('unknown','blank') and row.value not in (None,''):
            raise ValueError('未知或空白字段被填写为具体值')
    return answer.model_dump()


def answer_once(jobs,jid):
    job=jobs.get(jid);inputs=job['inputs'];fid=job['folder_id']
    budget=inputs['budget'];generation=inputs['generation']
    def event(row): jobs.update_event(jid,row)
    result=jobs.index.search(fid,inputs['query'],generation=generation,file_ids=inputs['file_ids'],
                             mode=inputs['mode'],budget=budget,event_callback=event)
    evidence=[{'citation_id':'C'+str(i+1),**h,'delivered_sha256':sha(h['text'])} for i,h in enumerate(result['hits'])]
    jobs.patch(jid,evidence=evidence,retrieval={k:v for k,v in result.items() if k!='hits'})
    return answer_evidence(jobs,jid,evidence)


class BudgetInsufficient(ValueError):pass


def prepare_messages(inputs,evidence,reading_scope=None):
    supplied=[{k:e[k] for k in ('citation_id','name','file_sha256','source_location','text')} for e in evidence]
    if reading_scope is not None:
        supplied=[{**s,'role':e['role'],'block_start':e['start'],'block_end':e['end']} for s,e in zip(supplied,evidence)]
    messages=[{'role':'system','content':instruction()},
              {'role':'user','content':json.dumps({'question':inputs['query'],'evidence':supplied},ensure_ascii=False)}]
    if reading_scope is not None:
        messages[1]['content']=json.dumps({'question':inputs['query'],'reading_scope':reading_scope,'evidence':supplied},ensure_ascii=False)
    if inputs.get('project_context') is not None:
        content=json.loads(messages[1]['content']);content['project_context']=inputs['project_context']
        messages[1]['content']=json.dumps(content,ensure_ascii=False)
    return messages


def answer_evidence(jobs,jid,evidence,*,call_suffix='answer',reading_scope=None):
    job=jobs.get(jid);inputs=job['inputs'];budget=inputs['budget']
    def event(row):jobs.update_event(jid,row)
    jobs.check(jid)
    if not evidence:
        return {'answer':{'schema_version':VERSION,'findings':[{'statement':'当前限定范围的有限检索没有返回依据，无法据此判断原资料是否包含答案。',
                                     'kind':'unknown','citations':[],'support':[],'assumptions':[],'limitations':[],'relation':None}],
                          'unknowns':['尚缺可引用的资料片段'],'comparison':[]},
                'answer_schema_version':VERSION,'content_status':'insufficient_evidence','review_status':'not_reviewed'}
    messages=prepare_messages(inputs,evidence,reading_scope)
    input_estimate=estimate_tokens(json.dumps(messages,ensure_ascii=False))
    used=jobs.used_tokens(jid)
    output_cap=min(inputs.get('output_tokens',1200),jobs.index.settings.agent_call_max_tokens)
    max_output=min(output_cap,budget-used-input_estimate)
    if max_output<128: raise BudgetInsufficient('剩余预算不足以发送短答请求；未调用语言模型')
    jobs.patch(jid,request={'messages':messages,'estimated_input_tokens':input_estimate,'max_output_tokens':max_output,
                          'requested_output_tokens':inputs.get('output_tokens',1200),'effective_output_cap':output_cap,
                          'budget_limited':max_output<output_cap,'answer_schema_version':VERSION,
                          'output_guide_version':GUIDE_VERSION,'system_sha256':sha(messages[0]['content'])},
               output_diagnostics=None)
    config=replace(load_llm_config(settings=jobs.index.settings,strength=inputs.get('strength','default')),max_retries=0)
    client=jobs.llm_factory(config)
    jobs.check(jid)
    call={'id':jid+'-'+call_suffix,'operation':'llm','status':'started','provider':config.provider,'model':config.model,
          'thinking':config.thinking,'reasoning_effort':config.reasoning_effort,
          'input_tokens':None,'output_tokens':None,'request_attempts':1,'started':now()}
    event(call)
    try:
        response=client.chat(messages,response_format={'type':'json_object'},max_tokens=max_output)
        usage=response.usage
        prompt=getattr(usage,'prompt_tokens',None);output=getattr(usage,'completion_tokens',None)
        valid=lambda v:type(v) is int and v>=0
        call.update(status='returned',input_tokens=prompt if valid(prompt) else None,
                    output_tokens=output if valid(output) else None,returned_model=response.model,
                    cached_tokens=getattr(usage,'prompt_cache_hit_tokens',None),finished=now())
        details=getattr(usage,'completion_tokens_details',None)
        reasoning=details.get('reasoning_tokens') if isinstance(details,dict) else getattr(details,'reasoning_tokens',None)
        call['reasoning_tokens']=reasoning if valid(reasoning) and valid(output) and reasoning<=output else None
        event(call)
        text=response.choices[0].message.content or ''
        jobs.patch(jid,model_output=text,finish_reason=response.choices[0].finish_reason,
                   output_diagnostics=diagnose(response.choices[0].finish_reason,
                       usage_known=call['input_tokens'] is not None and call['output_tokens'] is not None))
    except Exception as exc:
        call.update(status='failed',error_type=type(exc).__name__,finished=now());event(call)
        raise ValueError('短答请求失败或响应无效：'+type(exc).__name__+'；用量可能未知，未自动重试') from None
    jobs.check(jid)
    if call['input_tokens'] is None or call['output_tokens'] is None:
        raise ValueError('服务未返回有效用量，保留原始响应，停止后续处理，未自动重试')
    try:
        answer,checks=validate_v2(text,evidence)
    except ContractError as exc:
        jobs.patch(jid,output_diagnostics=diagnose(response.choices[0].finish_reason,issues=exc.issues))
        return {'answer':None,'content_status':'needs_verification','review_status':'not_reviewed',
                'answer_schema_version':VERSION,'contract_checks':{'version':VERSION,'status':'invalid','issues':exc.issues},
                'message':str(exc)+'；原始响应已保留，未自动重试或修补。'}
    jobs.patch(jid,output_diagnostics=diagnose(response.choices[0].finish_reason,checked=True))
    partial=response.choices[0].finish_reason!='stop'
    status='budget_exceeded' if jobs.used_tokens(jid)>budget else ('partial' if partial else ('needs_verification' if checks['warnings'] else 'unreviewed'))
    return {'answer':answer,'content_status':status,'answer_schema_version':VERSION,'contract_checks':checks,
            'review_status':'not_reviewed'}
