"""Explicit retrieval-to-sequential operation; local preview never invokes providers."""
import json

from knowledge.documents import sha
from .batching import select_batch
from .contracts import GUIDE_VERSION,instruction
from .reading import ReadingPlan


CONTROLS=('budget','max_batches','output_tokens','batch_mode','batch_size','expected_fields','read_to_end','max_calls','plan_fields')


def plan_hash(inputs):
    stable={k:v for k,v in inputs.items() if k!='full_reading_plan_hash'}
    return sha(json.dumps({'inputs':stable,'guide_version':GUIDE_VERSION,'system_sha256':sha(instruction())},
                          sort_keys=True,ensure_ascii=False,separators=(',',':')))


def _inputs(jid,controls):
    if controls.get('retry_split'):raise ValueError('全量整理从头新建，不能拆小重做旧局部问答')
    return {'budget':10000,**{k:v for k,v in controls.items() if k in CONTROLS},
            'strategy':'exhaustive','full_reading_from':jid}


def preview_full_reading(jobs,jid,controls):
    source=jobs.get(jid)
    inputs=jobs.prepare_answer(source['folder_id'],_inputs(jid,controls))
    plan=ReadingPlan(jobs.index,source['folder_id'],inputs['generation'],inputs['file_ids'])
    batch=None;decision={'stop_reason':'empty_scope'}
    if plan.segments:
        batch,decision=select_batch(plan,inputs,0,1,inputs['budget'],[],jobs.index.settings.agent_call_max_tokens)
        if decision['effective_output_tokens']<128:
            batch=None;decision={**decision,'stop_reason':'token_budget'}
    return {**plan.summary(),'source_job_id':jid,'query':inputs['query'],'strength':inputs['strength'],
            'plan_hash':plan_hash(inputs),'cloud_calls':False,'services':['llm'] if batch else [],
            'budget':inputs['budget'],'max_batches':inputs['max_batches'],'output_tokens':inputs['output_tokens'],
            'read_to_end':inputs['read_to_end'],'batch_call_limit':inputs['batch_call_limit'],
            'plan_fields':inputs['plan_fields'],
            'model':inputs['model'],'decision':decision,'first_batch_end':batch['next_offset'] if batch else 0,
            'first_batch':(batch or {}).get('hits',[]),
            'source_usage_unknown':any(e.get('input_tokens') is None or
                (e.get('operation')=='llm' and e.get('output_tokens') is None) for e in source.get('usage_events',[])),
            'meaning':'从原局部问答的固定版本和所选文件起点新建任务，不只读取命中片段；不带旧答案，也不计入旧任务进度。'
                      '预览不调用云端；开始后另计模型调用，原结果和费用保留。批数、输出空间与预算可能使本次只完成部分。'
                      '全部送达仍不证明完整抽取或语义正确；只包括已解析文字，不含OCR。'}


def start_full_reading(jobs,jid,controls,expected_hash):
    if not isinstance(expected_hash,str) or len(expected_hash)!=64:
        raise ValueError('请先预览全量整理计划')
    source=jobs.get(jid)
    return jobs.start(source['folder_id'],'answer',{
        **_inputs(jid,controls),'full_reading_plan_hash':expected_hash})
