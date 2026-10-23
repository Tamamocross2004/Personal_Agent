"""Read-only, literal-and-provenance diagnostics. Never a semantic grade or repair."""
import argparse
import json
from pathlib import Path
from knowledge.documents import sha


def observations(job,batch_numbers=None):
    """Project a frozen job, retaining failed attempts and original response bodies."""
    rows=[]
    for piece in job.get('batches') or [job]:
        number=piece.get('number') if 'batches' in job else None
        if batch_numbers is not None and number not in batch_numbers:continue
        calls=[e for e in job.get('usage_events',[]) if e.get('operation')=='llm' and
               (number is None or e['id']==job['id']+f'-batch-{number}')]
        confirmed=any(e.get('status')=='returned' for e in calls)
        if number is not None:confirmed=confirmed and piece.get('status')=='returned'
        rows.append({'job_id':job['id'],'batch':number,'job_status':job['status'],'confirmed_return':confirmed,'record':piece})
    return rows


def source_matches(source,evidence):
    return (source['name']==evidence.get('name') and source['file_sha256']==evidence.get('file_sha256') and
            all(evidence.get('source_location',{}).get(k)==v for k,v in source.get('location',{}).items()))


def value_matches(expected,actual):
    return (expected['subject']==actual.get('subject') and expected['field']==actual.get('field') and
            expected['status']==actual.get('status') and any(type(v) is type(actual.get('value')) and v==actual.get('value') for v in expected['values']))


def bound_sources(row,evidence):
    # A correct value citing some other record must not pass just because the right
    # source was also included in the broad citations list.
    q=row.get('value_quote') if row.get('status')=='stated' else row.get('subject_quote')
    refs=[q['citation_id']] if q else row.get('citations',[])
    return [e for e in evidence if e.get('citation_id') in refs]


def evaluate_case(expected,attempts):
    rows=expected['expected_rows'];ids=[r['id'] for r in rows]
    if len(set(ids))!=len(ids):raise ValueError('预期记录ID重复')
    for row in rows:
        if not row['values'] or any(v is not None and not isinstance(v,str) for v in row['values']):raise ValueError('预期值须预先列出字符串或null')
    delivered=set();matched=set();actual=[];responses=[];findings=[]
    for attempt in attempts:
        record=attempt['record'];evidence=record.get('evidence',[])
        result=record.get('result') or {};answer=result.get('answer')
        usable=attempt['confirmed_return'] and record.get('finish_reason')=='stop' and bool(answer)
        origin={k:attempt[k] for k in ('job_id','batch','job_status')}
        responses.append({**origin,'confirmed_return':attempt['confirmed_return'],'finish_reason':record.get('finish_reason'),
                          'usable_structure':usable,'content_status':result.get('content_status'),
                          'message':result.get('message') or record.get('message'),
                          'raw_output_sha256':sha(record['model_output']) if record.get('model_output') is not None else None,
                          'diagnostic':record.get('output_diagnostics'),'contract_checks':result.get('contract_checks')})
        if attempt['confirmed_return']:
            for row in rows:
                if any(source_matches(row['source'],e) and all(t in e.get('text','') for t in row.get('required_literals',[])) for e in evidence):delivered.add(row['id'])
        if not usable:continue  # No partial JSON salvage, answer repair or revalidation.
        findings.extend({**origin,'finding':f} for f in answer.get('findings',[]))
        for index,row in enumerate(answer.get('comparison',[])):
            candidates=[e for e in rows if value_matches(e,row)]
            provenance=[e for e in candidates if any(source_matches(e['source'],s) for s in bound_sources(row,evidence))]
            available=[e for e in provenance if e['id'] not in matched]
            key_matches=[e for e in rows if e['subject']==row.get('subject') and e['field']==row.get('field')]
            if available:
                selected=available[0];matched.add(selected['id']);classification='literal_and_source_matched';eid=selected['id']
            else:
                eid=None
                classification=('duplicate' if provenance else 'source_mismatch' if candidates else 'value_or_status_mismatch' if key_matches else 'unrequested_row')
            actual.append({**origin,'row_index':index,'classification':classification,'expected_id':eid,'row':row,
                           'bound_sources':[{k:s.get(k) for k in ('name','file_sha256','source_location')} for s in bound_sources(row,evidence)]})
    categories=('literal_and_source_matched','duplicate','source_mismatch','value_or_status_mismatch','unrequested_row')
    return {'case_id':expected['id'],'expected_rows':len(rows),'confirmed_delivered_literal_rows':len(delivered),
            'response_counts':{'attempts':len(responses),'confirmed_returns':sum(r['confirmed_return'] for r in responses),
                               'usable_structures':sum(r['usable_structure'] for r in responses),
                               'output_limit':sum(r['finish_reason']=='length' for r in responses),
                               'invalid_contract':sum((r.get('contract_checks') or {}).get('status')=='invalid' for r in responses)},
            'not_confirmed_delivered':[r for r in rows if r['id'] not in delivered],
            'row_counts':{k:sum(r['classification']==k for r in actual) for k in categories},
            'missing_or_different_rows':[r for r in rows if r['id'] not in matched],
            'actual_rows':actual,'responses':responses,'findings_for_review':findings,
            'semantic_status':'not_certified','review_status':'not_reviewed',
            'meaning':'逐字值、状态及引用来源匹配，不认证语义、字段含义、业务关系或完整抽取；省略与错误不自动修复。'}


def usage_summary(events):
    unique={}
    for event in events:
        if event['id'] in unique and unique[event['id']]!=event:raise ValueError('同一用量事件有不同快照，须先核对')
        unique[event['id']]=event
    known=0;unknown=[];counts={}
    for event in unique.values():
        operation=event.get('operation','embedding');counts[operation]=counts.get(operation,0)+1
        fields=['input_tokens']+(['output_tokens'] if operation!='embedding' else [])
        values=[event.get(k) for k in fields]
        known+=sum(v for v in values if type(v) is int and v>=0)
        if any(type(v) is not int or v<0 for v in values):unknown.append(event['id'])
    return {'calls_by_operation':counts,'known_tokens':known,'unknown_usage_ids':unknown,'cny_total':None,
            'meaning':'按原事件ID去重；未知不是零，思考Token若已含在输出中不重复相加。人民币账单未据此认证。'}


def report(plan,bundle):
    cases={c['id']:c for c in plan['cases']};saved={c['id']:c for c in bundle['cases']}
    if len(cases)!=len(plan['cases']) or len(saved)!=len(bundle['cases']):raise ValueError('诊断案例ID重复，不覆盖尝试')
    return {'version':1,'cases':[evaluate_case(c,saved.get(cid,{}).get('observations',[])) for cid,c in cases.items()],
            'unregistered_case_ids':sorted(set(saved)-set(cases)),'missing_case_ids':sorted(set(cases)-set(saved)),
            'usage':usage_summary(bundle.get('usage_events',[])),'stage12_accepted':False,'semantic_status':'not_certified'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True);parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    a=args.plan.read_bytes();b=args.bundle.read_bytes();result=report(json.loads(a),json.loads(b))
    result.update(plan_sha256=sha(a),bundle_sha256=sha(b))
    # Exclusive create; never rewrite source records, expectations or old reports.
    with args.output.open('x',encoding='utf-8') as stream:stream.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'cases':len(result['cases']),'semantic_status':'not_certified','output':str(args.output)},ensure_ascii=False))


if __name__=='__main__':main()
