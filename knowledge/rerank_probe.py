"""固定资料、标注、顺序后执行真实rerank对照；新目录保留原始结果，不调通过门槛。"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import random
import time

from agent.checkpoint import runtime_identity
from agent.config import Settings,load_llm_config
from agent.llm import LLMClient
from .documents import sha
from .store import KnowledgeStore


def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def metrics(result,relevant):
    names=[hit['name'] for hit in result['hits'] if hit['role']=='direct']
    if not relevant:return {'direct_names':names,'precision_at_4':None,'recall_at_4':None,'mrr_at_4':None}
    matches=[i+1 for i,name in enumerate(names) if name in relevant]
    return {'direct_names':names,'precision_at_4':len(set(names)&set(relevant))/4,
            'recall_at_4':len(set(names)&set(relevant))/len(relevant),'mrr_at_4':1/matches[0] if matches else 0}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--skip-research',action='store_true',help='只测检索，不调用聊天模型')
    args=parser.parse_args();root=args.output.resolve();root.mkdir(parents=True,exist_ok=False)
    source=Path(__file__).with_name('rerank_cases.json');suite=json.loads(source.read_text(encoding='utf-8'))
    settings=Settings().model_copy(update={'knowledge_path':root/'knowledge','knowledge_enabled':True,'memory_enabled':False})
    randomizer=random.Random(suite['protocol']['seed']);orders={}
    for case in suite['queries']:
        order=[False,True];randomizer.shuffle(order);orders[case['id']]=order
    plan={'suite':suite,'suite_sha256':sha(source.read_bytes()),'source_sha256':runtime_identity(),
        'embedding':{'model':settings.embedding_model,'dimensions':settings.embedding_dimensions},
        'rerank':{'model':settings.rerank_model,'candidates':settings.knowledge_rerank_candidates},
        'chat_model':load_llm_config(settings=settings).model,'review_max_calls':settings.report_review_max_calls,
        'arm_order':orders,'research_order':[False,True],'skip_research':args.skip_research}
    write(root/'plan.json',plan)
    if settings.knowledge_rerank_candidates!=suite['protocol']['candidate_pool']:
        raise ValueError('当前候选数与预定协议不一致；请使用20，不改变题目或阈值')
    store=KnowledgeStore(settings);results={'cases':[],'research':[],'error_type':None}
    try:
        results['build']=store.build([(name,text.encode()) for name,text in suite['documents'].items()])
        for case in suite['queries']:
            # 两臂都复用同一查询嵌入；单独计入准备消耗，避免把嵌入缓存差异冒充重排加速。
            store._cached_vectors([case['query']],'probe:warmup',settings.knowledge_search_token_budget)
            row={'id':case['id'],'query':case['query'],'relevant':case['relevant'],'arms':{}}
            for enabled in orders[case['id']]:
                started=time.perf_counter()
                result=store.search(case['query'],mode='hybrid',k=4,rerank=enabled)
                row['arms']['rerank' if enabled else 'baseline']={'wall_seconds':round(time.perf_counter()-started,4),
                    'metrics':metrics(result,case['relevant']),'result':result}
            a=row['arms']['baseline']['result'];b=row['arms']['rerank']['result']
            if a['generation']!=b['generation'] or a['candidates'] and [c['chunk_id'] for c in a['candidates']]!=[c['chunk_id'] for c in b['candidates']]:
                raise ValueError('两臂知识库或候选池不一致，停止比较')
            results['cases'].append(row);write(root/'results.json',results)
        first=suite['queries'][0];count=len(store.events())
        cached=store.search(first['query'],mode='hybrid',k=4,rerank=True)
        results['cache_check']={'new_requests':len(store.events())-count,'hit':cached['reranking']['cache_hit']}
        if not args.skip_research:
            from main import research
            for enabled in plan['research_order']:
                arm='rerank' if enabled else 'baseline';task=root/('research-'+arm)
                configured=settings.model_copy(update={'knowledge_rerank_enabled':enabled})
                result=research(LLMClient(load_llm_config(settings=configured)),configured,suite['research_question'],
                    suite['protocol']['research_max_steps'],suite['protocol']['research_budget'],task_dir=task,historical_context='')
                value=asdict(result);value.pop('messages',None)
                results['research'].append({'arm':arm,'task':str(task),'result':value});write(root/'results.json',results)
    except Exception as exc:
        results['error_type']=type(exc).__name__;raise
    finally:
        results['source_unchanged']=runtime_identity()==plan['source_sha256']
        results['suite_unchanged']=sha(source.read_bytes())==plan['suite_sha256']
        results['status']=store.status()
        summary={}
        for arm in ('baseline','rerank'):
            values=[row['arms'][arm] for row in results['cases']]
            summary[arm]={}
            for key in ('precision_at_4','recall_at_4','mrr_at_4'):
                known=[v['metrics'][key] for v in values if v['metrics'][key] is not None]
                summary[arm][key]=sum(known)/len(known) if known else None
            summary[arm]['mean_wall_seconds']=sum(v['wall_seconds'] for v in values)/len(values) if values else None
        results['summary']=summary
        write(root/'results.json',results)
        write(root/'artifacts.json',{p.relative_to(root).as_posix():sha(p.read_bytes()) for p in root.rglob('*')
            if p.is_file() and p.suffix in ('.json','.jsonl','.md','.txt')})
        print(json.dumps({'completed_cases':len(results['cases']),'summary':summary,'error_type':results['error_type']},ensure_ascii=False))


if __name__=='__main__':main()
