"""Explicit bounded sequential batches. No query heuristics, answer repair or paid retry."""
from knowledge.documents import sha
from .answers import answer_evidence,BudgetInsufficient
from .reading import ReadingPlan,BATCH_SEGMENTS


def answer_exhaustive(jobs,jid):
    job=jobs.get(jid);inputs=job['inputs']
    plan=ReadingPlan(jobs.index,job['folder_id'],inputs['generation'],inputs['file_ids'])
    if plan.signature!=inputs['reading_plan']:raise ValueError('固定读取计划与任务不一致')
    offset=inputs.get('start_offset',0);start=offset
    batches=[];evidence=[];structured=inputs.get('previous_structured_segments',0)
    returned=inputs.get('previous_returned_until',offset)
    coverage={**plan.summary(),'protocol_version':2,'start_offset':start,'returned_until':returned,'next_offset':offset,'structured_segments':structured,
              'remaining_segments':len(plan.segments)-offset,'unsent_segments':len(plan.segments)-returned,'input_coverage_complete':returned==len(plan.segments),
              'structured_coverage_complete':False,'continuation_of':inputs.get('resume_from')}
    jobs.patch(jid,coverage=coverage,batches=[])
    reason='batch_limit'
    for number in range(inputs['max_batches']):
        jobs.check(jid)
        if offset==len(plan.segments):reason='end_of_extraction';break
        batch=plan.batch(offset)
        hits=[{'citation_id':f'B{number+1}C{i+1}',**h,'delivered_sha256':sha(h['text'])} for i,h in enumerate(batch['hits'])]
        entry={'number':number+1,'start_offset':offset,'next_offset':batch['next_offset'],
               'evidence':hits,'status':'prepared'}
        # Durable preparation is distinct from confirmed input delivery.
        batches.append(entry)
        jobs.patch(jid,batches=batches,evidence=evidence+hits,
                   progress={'phase':'reading','completed':offset,'total':len(plan.segments),'current':f'第{number+1}批；分批结果不保证跨批推理或完整抽取'})
        try:
            result=answer_evidence(jobs,jid,hits,call_suffix=f'batch-{number+1}',reading_scope={
                'strategy':'exhaustive_batch','start_segment':offset,'end_segment':batch['next_offset'],
                'total_segments':len(plan.segments),'meaning':'只整理本批送达内容，其他批次未提供。不要声称全目录已经完整整理。',
                'context_roles':'table_origin_unverified_header是实际表格首条记录，未认证为表头；explicit_markdown_heading为原文Markdown标题。'})
            entry.update(status='returned',result=result)
        except BudgetInsufficient:
            batches.pop();jobs.patch(jid,batches=batches,evidence=evidence)
            reason='token_budget';break
        except Exception:
            current=jobs.get(jid)
            call=next((e for e in current.get('usage_events',[]) if e['id']==jid+f'-batch-{number+1}'),None)
            entry.update(status=call['status'] if call else 'not_sent',request=current.get('request') if call else None,
                         model_output=current.get('model_output') if call and call['status']=='returned' else None,
                         finish_reason=current.get('finish_reason') if call and call['status']=='returned' else None)
            if call and call['status']=='returned':
                coverage.update(returned_until=max(coverage['returned_until'],batch['next_offset']))
            # Failed/in-flight attempts are not confirmed delivered evidence. Keep their
            # planned evidence in the batch record, but don't export it as received text.
            confirmed=evidence+hits if call and call['status']=='returned' else evidence
            coverage['input_coverage_complete']=coverage['returned_until']==len(plan.segments)
            coverage['unsent_segments']=len(plan.segments)-coverage['returned_until']
            jobs.patch(jid,batches=batches,coverage=coverage,evidence=confirmed)
            raise
        current=jobs.get(jid)
        entry.update(request=current['request'],model_output=current['model_output'],finish_reason=current['finish_reason'])
        evidence+=hits
        usable=result.get('answer') and current['finish_reason']=='stop'
        if usable:structured+=batch['selected_segments'];offset=batch['next_offset']
        received=max(coverage['returned_until'],batch['next_offset'])
        coverage.update(returned_until=received,next_offset=offset,structured_segments=structured,remaining_segments=len(plan.segments)-offset,
                        unsent_segments=len(plan.segments)-received,input_coverage_complete=received==len(plan.segments),
                        structured_coverage_complete=structured==len(plan.segments))
        jobs.patch(jid,batches=batches,evidence=evidence,coverage=coverage)
        if result.get('answer') is None or current['finish_reason']!='stop':reason='invalid_or_truncated_output';break
        if jobs.used_tokens(jid)>inputs['budget']:reason='provider_reported_budget_exceeded';break
    if offset==len(plan.segments) and reason=='batch_limit':reason='end_of_extraction'
    coverage['stop_reason']=reason
    jobs.patch(jid,coverage=coverage,progress={'phase':'reading','completed':offset,'total':len(plan.segments),'current':'本次分批处理结束；请核对送达范围与内容质量'})
    return {'answer':None,'answer_schema_version':3,'content_status':'unreviewed' if coverage['structured_coverage_complete'] else 'partial',
            'review_status':'not_reviewed','coverage':coverage,
            'message':'逐批结果分别保留，未自动合并去重、补全漏项或推断跨批关系。送达全部提取文字不代表内容正确或完整整理。'}
