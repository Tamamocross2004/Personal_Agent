"""Read-only interpretation of saved operations, never regrading or repairing answers."""
from collections import Counter
from .reading import ReadingPlan
from .batching import select_batch

VERSION=1
MEANING='只覆盖固定版本已解析文字，不含OCR或未提取内容；送达、结构可用与事实完整正确分别判断。仅汇总当前任务及前序，不混入其他分支，不改写原结果。'
LABELS={'not_sent':'无确认发送记录','delivery_unknown':'发送结果未确认',
        'response_incomplete':'已返回但响应未完成','structure_checked':'有未截断的可用结构'}


def lineage(jobs,job):
    rows=[];seen=set();warnings=[];current=job
    identity=lambda j:(j['folder_id'],j['inputs'].get('generation'),j['inputs'].get('reading_plan'),j['inputs'].get('query'))
    while current:
        if current['id'] in seen:warnings.append('前序任务形成循环，未继续汇总');break
        if len(rows)>=200:warnings.append('前序超过200个任务，仅展示已读取记录');break
        if current['kind']!='answer' or identity(current)!=identity(job):
            warnings.append('前序任务范围或问题不一致，未混入');break
        rows.append(current);seen.add(current['id'])
        parent=current['inputs'].get('resume_from')
        if not parent:break
        try:current=jobs.get(parent)
        except KeyError:warnings.append('前序任务缺失，不能确认完整任务链');break
    return list(reversed(rows)),warnings


def scope_view(jobs,jid,offset=0,limit=50,*,compact=False):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:
        raise ValueError('范围分页无效')
    job=jobs.get(jid)
    if job['kind']!='answer':raise ValueError('仅资料问答支持范围视图')
    inputs=job['inputs']
    base={'version':VERSION,'task_id':jid,'generation':inputs['generation'],'strategy':inputs.get('strategy','retrieval'),
          'scope_files':inputs.get('source_snapshot',[]),'cloud_calls':False,'meaning':MEANING,
          'semantic_status':'not_certified','review_status':job.get('review_status','not_reviewed')}
    if base['strategy']!='exhaustive':
        returned=any(e.get('operation')=='llm' and e.get('status')=='returned' for e in job.get('usage_events',[]))
        base.update(mode='limited_retrieval',confirmed_evidence=len(job.get('evidence',[])) if returned else 0,
                    meaning='局部检索仅发送命中的有限片段，不能据此计算全文阅读率或断言完整抽取。'+MEANING,
                    counts=None,segments=[],runs=[],chain=[],warnings=[])
        return base
    plan=ReadingPlan(jobs.index,job['folder_id'],inputs['generation'],inputs.get('file_ids'))
    if offset>len(plan.segments):raise ValueError('范围分页越出读取计划')
    rows,warnings=lineage(jobs,job)
    if inputs.get('reading_plan')!=plan.signature:raise ValueError('保存的读取计划与来源版本不一致')
    states=[{'received':False,'usable':False,'uncertain':False,'failed_attempts':0,'attempts':[]} for _ in plan.segments]
    chain=[]
    for row in rows:
        batches=[]
        for b in row.get('batches',[]):
            received=b.get('status')=='returned'
            usable=received and bool((b.get('result') or {}).get('answer')) and b.get('finish_reason')=='stop'
            call=next((e for e in row.get('usage_events',[]) if e.get('id')==row['id']+f"-batch-{b['number']}"),None)
            uncertain=not received and call is not None and call.get('status') in ('started','failed','returned')
            attempt={'task_id':row['id'],'batch':b['number'],'start':b['start_offset'],'end':b['next_offset'],
                     'status':b.get('status'),'finish_reason':b.get('finish_reason'),'confirmed_return':received,
                     'usable_structure':usable,'delivery_unknown':uncertain,
                     'context_segments':sum(e.get('role')!='sequential' for e in b.get('evidence',[]))}
            batches.append(attempt)
            # The saved main evidence, not prefix counters or supplied context, establishes coverage.
            for e in b.get('evidence',[]):
                if e.get('role')!='sequential':continue
                n=e.get('segment')
                if type(n) is not int or not 0<=n<len(states):warnings.append('批次片段位置无效，未汇总');continue
                expected=plan.segments[n]
                if any(e.get(k)!=expected[k] for k in ('doc_id','source_block_id','start','end','text')):
                    warnings.append('批次片段与固定来源不一致，未汇总');continue
                state=states[n];state['received']|=received;state['usable']|=usable;state['uncertain']|=uncertain
                state['failed_attempts']+=int(received and not usable)
                state['attempts'].append({'task_id':row['id'],'batch':b['number']})
        events=row.get('usage_events',[])
        chain.append({'task_id':row['id'],'parent_id':row['inputs'].get('resume_from'),'status':row['status'],
                      'operation':'split_retry' if row['inputs'].get('retry_split') else ('continue' if row['inputs'].get('resume_from') else 'initial'),
                      'batches':batches,'known_tokens':sum((e.get('input_tokens') or 0)+(e.get('output_tokens') or 0) for e in events),
                      'unknown_usage_events':sum(e.get('input_tokens') is None or (e.get('operation')=='llm' and e.get('output_tokens') is None) for e in events)})
    segments=[];runs=[]
    for n,(part,state) in enumerate(zip(plan.segments,states)):
        status='structure_checked' if state['usable'] else ('response_incomplete' if state['received'] else ('delivery_unknown' if state['uncertain'] else 'not_sent'))
        item={k:part[k] for k in ('segment','name','source_location','source_block_id','start','end')}
        item.update(status=status,label=LABELS[status],failed_attempts=state['failed_attempts'],attempts=state['attempts'])
        if offset<=n<offset+limit and not compact:segments.append(item)
        if runs and runs[-1]['status']==status and runs[-1]['name']==part['name'] and runs[-1]['failed_before']==bool(state['failed_attempts']):runs[-1]['end']=n+1
        else:runs.append({'start':n,'end':n+1,'name':part['name'],'status':status,'label':LABELS[status],'failed_before':bool(state['failed_attempts'])})
    counts=Counter(r['status'] for r in runs for _ in range(r['end']-r['start']))
    base.update(mode='sequential',total_segments=len(states),counts={k:counts[k] for k in LABELS},
                confirmed_segments=sum(s['received'] for s in states),
                recovered_segments=sum(s['usable'] and s['failed_attempts']>0 for s in states),
                segments=segments,runs=runs,chain=chain,warnings=list(dict.fromkeys(warnings)),
                chain_complete=not warnings,offset=offset,next_offset=min(offset+limit,len(states)),
                counts_meaning='按片段去重的记录状态，不是内容正确率。曾失败后有可用结构仍保留失败历史。附带标题/首条记录不计为主片段进度。')
    return base


def preview_next(jobs,jid,controls):
    job=jobs.get(jid);jobs.folders.authorized(job['folder_id'])
    inputs=jobs.prepare_answer(job['folder_id'],{**controls,'query':job['inputs']['query'],'mode':job['inputs']['mode'],
                                               'strategy':'exhaustive','resume_from':jid})
    plan=ReadingPlan(jobs.index,job['folder_id'],inputs['generation'],inputs['file_ids'])
    start=inputs['start_offset'];end=inputs['range_end']
    batch,decision=select_batch(plan,inputs,start,1,inputs['budget'],[],jobs.index.settings.agent_call_max_tokens)
    if decision['effective_output_tokens']<128:
        batch=None;decision={**decision,'stop_reason':'token_budget'}
    return {'cloud_calls':False,'generation':inputs['generation'],'parent_id':jid,'operation_start':start,'operation_end':end,
            'first_batch_end':batch['next_offset'] if batch else start,'max_batches':inputs['max_batches'],
            'budget':inputs['budget'],'output_tokens':inputs['output_tokens'],'strength':inputs['strength'],
            'retry_split':inputs['retry_split'],'decision':decision,'services':['llm'] if batch else [],
            'model':inputs['model'],'first_batch':[ {k:h[k] for k in ('segment','name','source_location','start','end','role')} for h in (batch or {}).get('hits',[])],
            'meaning':'本地估算，未发送模型；启动时重新检查授权、任务状态、预算和分批。后续批次大小取决于实际返回，不保证处理完区间。原失败和费用保留。'}
