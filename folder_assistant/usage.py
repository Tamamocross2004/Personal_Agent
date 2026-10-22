"""Read saved events only. Index events cover CLI and preview calls as well as jobs."""
from decimal import Decimal

from desktop_app.usage import summarize


def summarize_folder(jobs,fid,book):
    # Authorization can have been revoked: saved history remains readable.
    events={e['id']:(e,{}) for e in jobs.index.store(fid).events()}
    for job in jobs.list(fid):
        for event in job.get('usage_events',[]):
            # KnowledgeStore is canonical for embedding/rerank; don't double count callbacks.
            events.setdefault(event['id'],(event,job['inputs']))
    groups={}
    for operation in ('embedding','rerank','llm'):
        rows=[]
        for event,inputs in events.values():
            if event.get('operation','embedding')!=operation:continue
            converted={**event,'kind':'llm_call' if event['status']=='returned' else 'llm_error',
                       'prompt_tokens':event.get('input_tokens'),
                       'completion_tokens':event.get('output_tokens') if operation=='llm' else 0,
                       'cached_tokens':event.get('cached_tokens') if operation=='llm' else 0,
                       'usage_reported':event.get('input_tokens') is not None,
                       'cache_usage_reported':operation!='llm' or event.get('cached_tokens') is not None,
                       'requested_model':event.get('model')}
            rows.append(summarize({'events':[converted],'finished':True,'invalid_lines':0,'partial_line':False},
                                  {'options':{'pricing':inputs.get('pricing')}},book))
        groups[operation]={'calls':len(rows),
            'input_tokens':sum(r['input_tokens'] for r in rows),
            'output_tokens':sum(r['output_tokens'] for r in rows),
            'unknown_usage_calls':sum(r['unknown_usage_calls'] if r['returned'] else 1 for r in rows),
            'unpriced_calls':sum(r['unpriced_calls'] for r in rows),
            'known_cost':str(sum((Decimal(r['known_cost']) for r in rows),Decimal(0))),
            'unpriced_reasons':sorted({s for r in rows for s in r['unpriced_reasons']}),
            'pricing_basis':sorted({s for r in rows for s in r['pricing_basis']})}
    known=sum((Decimal(g['known_cost']) for g in groups.values()),Decimal(0))
    return {'by_operation':groups,'calls':len(events),'known_cost':str(known),
            'estimated_cost':None if any(g['unpriced_calls'] for g in groups.values()) else str(known),
            'currency':'CNY','scope':'已保存的文件夹调用；失败或未返回请求用量可能未知。费用为匹配费率估算，不是平台账单。'}
