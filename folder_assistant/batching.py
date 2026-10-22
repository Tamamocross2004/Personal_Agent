"""Versioned resource estimates, never answer validation or domain/query heuristics."""
import json
from agent.context import estimate_tokens
from knowledge.documents import sha
from .answers import prepare_messages

VERSION=1


def evidence_for(batch,number):
    return [{'citation_id':f'B{number}C{i+1}',**h,'delivered_sha256':sha(h['text'])} for i,h in enumerate(batch['hits'])]


def scope_for(batch,start):
    return {'strategy':'exhaustive_batch','start_segment':start,'end_segment':batch['next_offset'],
            'total_segments':batch['total_segments'],'meaning':'只整理本批送达内容，其他批次未提供。不要声称全目录已经完整整理。',
            'context_roles':'table_origin_unverified_header是实际表格首条记录，未认证为表头；explicit_markdown_heading为原文Markdown标题。'}


def output_estimate(hits,fields):
    # Estimate the actual v3 JSON envelope + one finding and N comparison fields
    # per extraction block. Full delivered quotes and 32-character labels are a
    # transparent planning assumption, not a cap on the model's answer or a score.
    groups={}
    for h in hits:groups.setdefault((h['doc_id'],h['source_block_id']),[]).append(h)
    body={'schema_version':3,'findings':[],'unknowns':[],'comparison':[]}
    for rows in groups.values():
        text=''.join(r['text'] for r in rows);cid=rows[0]['citation_id'];q={'citation_id':cid,'quote':text}
        body['findings'].append({'statement':text,'kind':'direct','citations':[cid],'support':[q],
                                  'assumptions':[],'limitations':[],'relation':None})
        for _ in range(fields):
            body['comparison'].append({'subject':'x'*32,'subject_type':'entity','subject_quote':q,'field':'x'*32,
                'value':'x'*32,'value_quote':q,'status':'stated','citations':[cid]})
    return estimate_tokens(json.dumps(body,ensure_ascii=False)),len(groups)


def select_batch(plan,inputs,offset,number,remaining,events,agent_cap):
    maximum=min(inputs['batch_size'],inputs.get('range_end',len(plan.segments))-offset)
    if maximum<=0:raise ValueError('没有剩余的操作范围')
    known=[inputs.get('previous_reasoning_tokens')]
    known.extend(e.get('reasoning_tokens') for e in events if e.get('status')=='returned')
    observed=[n for n in known if type(n) is int and n>=0]
    thinking=inputs.get('model',{}).get('thinking')!='disabled'
    reasoning=max(observed) if thinking and observed else 0
    unknown=thinking and not observed
    trials=[]
    for size in range(maximum,0,-1):
        batch=plan.batch(offset,size);hits=evidence_for(batch,number)
        messages=prepare_messages(inputs,hits,scope_for(batch,offset))
        estimated_input=estimate_tokens(json.dumps(messages,ensure_ascii=False))
        cap=min(inputs['output_tokens'],agent_cap)
        available=min(cap,remaining-estimated_input)
        estimated_output,records=output_estimate(hits,inputs['expected_fields'])
        fit=available>=128 and estimated_output+reasoning<=available and records*inputs['expected_fields']<=40
        trial={'segments':batch['selected_segments'],'input_tokens':estimated_input,'estimated_body_tokens':estimated_output,
               'estimated_reasoning_tokens':reasoning if thinking and observed else (None if thinking else 0),
               'effective_output_tokens':max(0,available),'fits_estimate':fit}
        trials.append(trial)
        if inputs['batch_mode']=='fixed' or fit or size==1:
            decision={'version':VERSION,'mode':inputs['batch_mode'],'max_segments':maximum,'selected_segments':size,
                      'expected_fields':inputs['expected_fields'],'remaining_tokens':remaining,'requested_output_tokens':inputs['output_tokens'],
                      'reasoning_usage_unknown':unknown,'trials':trials,**trial,
                      'meaning':'分批估算，不限制答案字段或认证完整性；思考预留只参考已返回用量，不是独立思考配额。'}
            if inputs['batch_mode']=='adaptive' and not fit:
                decision['stop_reason']='token_budget' if available<min(cap,estimated_output+reasoning) else 'output_space'
                return None,decision
            return batch,decision
