"""Plan field interpretation from the user request only, never from expected answers."""
import json
from dataclasses import replace
from typing import Literal

from pydantic import BaseModel,ConfigDict,Field,ValidationError
from agent.config import load_llm_config
from agent.context import estimate_tokens
from knowledge.documents import sha
from knowledge.store import now

VERSION=1


class FieldIntent(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=300)
    mode:Literal['attribute','verbatim']
    instruction_quote:str=Field(min_length=1,max_length=1000)


class FieldPlan(BaseModel):
    model_config=ConfigDict(extra='forbid')
    fields:list[FieldIntent]=Field(max_length=12)


SYSTEM='''只规划用户问题要求整理的字段，不回答资料内容，不补字段值，不访问工具。输出JSON，顶层只有fields数组。
每个字段包含name、mode、instruction_quote。name必须逐字出现在用户问题中，instruction_quote必须是包含该字段名的用户问题连续原句。
逐字段区分：attribute表示用户要了解属性事实或状态；verbatim仅表示用户明确要求摘录该字段的文字、名称或标签本身。用户要求保留原值/原文、单位或引用，并不自动把属性事实变成文字摘录。
同一问题中不同字段可以采用不同模式，不能把某字段的摘录要求扩散到其他字段。不根据资料值或不确定措辞选模式，因为本请求不提供资料。
若问题不要求按明确命名字段整理，返回fields空数组，不编造字段或替用户改问题。这里规划的是请求意图，不认证资料或后续答案正确。'''


def instruction():
    return SYSTEM+'\nJSON Schema：\n'+json.dumps(FieldPlan.model_json_schema(),ensure_ascii=False,separators=(',',':'))


def validate(text,query):
    try:plan=FieldPlan.model_validate_json(text)
    except ValidationError:raise ValueError('字段意图计划结构无效；原始响应保留，未自动修补') from None
    fields=plan.model_dump()['fields'];names=set()
    for item in fields:
        if (not item['name'].strip() or item['name'] in names or item['name'] not in query
            or item['instruction_quote'] not in query or item['name'] not in item['instruction_quote']):
            raise ValueError('字段意图须引用用户问题中的真实字段及连续原句，且不能重复；未自动修补')
        names.add(item['name'])
    return fields


def prepare_request(query,output_tokens,agent_cap,remaining):
    messages=[{'role':'system','content':instruction()},{'role':'user','content':json.dumps({'question':query},ensure_ascii=False)}]
    estimate=estimate_tokens(json.dumps(messages,ensure_ascii=False))
    return {'messages':messages,'max_output_tokens':max(0,min(output_tokens,agent_cap,1024,remaining-estimate)),
            'estimated_input_tokens':estimate,'system_sha256':sha(messages[0]['content'])}


def plan_fields(jobs,jid):
    job=jobs.get(jid);inputs=job['inputs'];query=inputs['query'];jobs.check(jid)
    if 'field_specs' in inputs:
        fields=[FieldIntent.model_validate(x).model_dump() for x in inputs['field_specs']]
        validate(json.dumps({'fields':fields},ensure_ascii=False),query)
        jobs.patch(jid,field_plan={'version':VERSION,'status':'inherited','source_job_id':inputs.get('field_plan_source_job_id'),
                                  'fields':fields,'meaning':'只继承同问题的字段意图，不发送旧答案或新增规划请求。'})
        return fields
    request=prepare_request(query,inputs['output_tokens'],jobs.index.settings.agent_call_max_tokens,inputs['budget']-jobs.used_tokens(jid))
    messages=request['messages'];cap=request['max_output_tokens']
    saved={'version':VERSION,'status':'prepared','request':request,
           'meaning':'仅从用户问题规划字段意图，不含资料或答案；此调用计入同一个预算和调用上限。'}
    if cap<128:
        jobs.patch(jid,field_plan={**saved,'status':'budget_insufficient'})
        raise ValueError('剩余预算不足以规划字段；未调用模型')
    jobs.patch(jid,field_plan=saved)
    try:
        config=replace(load_llm_config(settings=jobs.index.settings,strength=inputs.get('strength','default')),max_retries=0)
        client=jobs.llm_factory(config)
        jobs.check(jid)
    except Exception as exc:
        from .jobs import Stopped
        # Separate local setup failures from cancellation; neither is a paid event.
        jobs.patch(jid,field_plan={**saved,'status':'stopped' if isinstance(exc,Stopped) else 'setup_failed'})
        if isinstance(exc,Stopped):raise
        raise ValueError('字段规划调用准备失败：'+type(exc).__name__+'；未发送请求') from None
    event={'id':jid+'-field-plan','operation':'llm','purpose':'field_plan','status':'started','provider':config.provider,'model':config.model,
           'thinking':config.thinking,'reasoning_effort':config.reasoning_effort,'input_tokens':None,'output_tokens':None,'request_attempts':1,'started':now()}
    jobs.update_event(jid,event)
    try:
        response=client.chat(messages,response_format={'type':'json_object'},max_tokens=cap)
        valid=lambda x:type(x) is int and x>=0
        usage=response.usage;prompt=getattr(usage,'prompt_tokens',None);output=getattr(usage,'completion_tokens',None)
        event.update(status='returned',input_tokens=prompt if valid(prompt) else None,output_tokens=output if valid(output) else None,
                     cached_tokens=getattr(usage,'prompt_cache_hit_tokens',None),returned_model=response.model,finished=now())
        details=getattr(usage,'completion_tokens_details',None)
        reasoning=details.get('reasoning_tokens') if isinstance(details,dict) else getattr(details,'reasoning_tokens',None)
        event['reasoning_tokens']=reasoning if valid(reasoning) and valid(output) and reasoning<=output else None
        jobs.update_event(jid,event)
        saved.update(model_output=response.choices[0].message.content or '',finish_reason=response.choices[0].finish_reason)
        jobs.patch(jid,field_plan=saved)
    except Exception as exc:
        event.update(status='failed',error_type=type(exc).__name__,finished=now());jobs.update_event(jid,event)
        jobs.patch(jid,field_plan={**saved,'status':'failed'})
        raise ValueError('字段规划请求失败或响应无效：'+type(exc).__name__+'；用量可能未知，未自动重试') from None
    try:jobs.check(jid)
    except Exception:
        jobs.patch(jid,field_plan={**saved,'status':'stopped'});raise
    if event['input_tokens'] is None or event['output_tokens'] is None:
        jobs.patch(jid,field_plan={**saved,'status':'usage_unknown'})
        raise ValueError('字段规划用量未知；已保留响应，停止后续请求')
    if saved['finish_reason']!='stop':
        jobs.patch(jid,field_plan={**saved,'status':'unfinished'})
        raise ValueError('字段规划响应未完整结束；停止整理，未自动重试')
    if jobs.used_tokens(jid)>inputs['budget']:
        jobs.patch(jid,field_plan={**saved,'status':'budget_exceeded'})
        raise ValueError('字段规划已超过总预算，停止后续请求')
    try:fields=validate(saved['model_output'],query)
    except ValueError:
        jobs.patch(jid,field_plan={**saved,'status':'invalid'});raise
    jobs.patch(jid,inputs={**inputs,'field_specs':fields},field_plan={**saved,'status':'ready','fields':fields})
    return fields
