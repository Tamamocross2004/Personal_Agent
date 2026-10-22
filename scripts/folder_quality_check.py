"""Run a pre-registered folder diagnostic once; criteria are never sent to the model."""
import argparse
import json
from pathlib import Path
import sys

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from agent.config import Settings,load_llm_config
from folder_assistant.store import FolderStore
from folder_assistant.index import FolderIndex
from folder_assistant.jobs import FolderJobs
from folder_assistant.exports import export_job
from knowledge.documents import sha


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--plan',type=Path,required=True);args=parser.parse_args()
    plan_path=args.plan.resolve();plan=json.loads(plan_path.read_text('utf-8'))
    root=(PROJECT/plan['workspace']).resolve();workspace=(PROJECT/'workspace').resolve()
    if root==workspace or not root.is_relative_to(workspace):raise ValueError('诊断须使用workspace内独立子目录')
    if (root/'attempt.json').exists():raise ValueError('已存在尝试记录，拒绝重跑或覆盖')
    docs=root/'documents'
    sources={p.relative_to(docs).as_posix():sha(p.read_bytes()) for p in docs.rglob('*') if p.is_file()}
    if sources!=plan['documents_sha256']:raise ValueError('资料与预登记哈希不同')
    for name,digest in plan['runtime_sha256'].items():
        path=(PROJECT/name).resolve()
        if not path.is_relative_to(PROJECT) or sha(path.read_bytes())!=digest:raise ValueError('运行代码与预登记不符')
    settings=Settings().model_copy(update={'knowledge_rerank_enabled':False,'knowledge_build_token_budget':10000,
                                         'llm_timeout':30.0,'embedding_timeout':30.0})
    config=load_llm_config(settings=settings)
    identity={'provider':config.provider,'model':config.model,'base_url':config.base_url,'embedding_model':settings.embedding_model,
              'embedding_base_url':settings.embedding_base_url,'dimensions':settings.embedding_dimensions,'rerank':False,
              'retries':0,'llm_timeout':30,'embedding_timeout':30,'build_budget':10000,
              'temperature':config.temperature,'thinking':config.thinking,
              'output_cap':min(1200,settings.agent_call_max_tokens),'chunk_size':settings.knowledge_chunk_size,
              'chunk_overlap':settings.knowledge_chunk_overlap,'lexical_candidates':settings.knowledge_lexical_candidates,
              'vector_candidates':settings.knowledge_vector_candidates,'rerank_candidates':settings.knowledge_rerank_candidates}
    if identity!=plan['configuration']:raise ValueError('当前服务配置与预登记不同')
    attempt={'status':'started','plan_sha256':sha(plan_path.read_bytes()),'configuration':identity,'sources_sha256':sources,
             'runtime_sha256':plan['runtime_sha256'],'questions':[],'reviewer':'coding_assistant','stage12_accepted':False}
    def save():
        temp=root/'attempt.tmp';temp.write_text(json.dumps(attempt,ensure_ascii=False,indent=2)+'\n','utf-8');temp.replace(root/'attempt.json')
    save()  # Durable before any request; a subsequent invocation refuses to run.
    folders=FolderStore(root/'state');fid=folders.grant(docs)['id'];index=FolderIndex(folders,settings);jobs=FolderJobs(index)
    def totals():
        events=index.store(fid).events()+[e for job in jobs.list(fid) for e in job.get('usage_events',[]) if e.get('operation')=='llm']
        return {'embedding_calls':sum(e.get('operation') not in ('llm','rerank') for e in events),
                'llm_calls':sum(e.get('operation')=='llm' for e in events),'rerank_calls':sum(e.get('operation')=='rerank' for e in events),
                'known_tokens':sum((e.get('input_tokens') or 0)+(e.get('output_tokens') or 0) for e in events),
                'unknown_usage_ids':[e['id'] for e in events if e.get('input_tokens') is None or (e.get('operation')=='llm' and e.get('output_tokens') is None)],
                'events':events}
    def guard():
        used=totals();attempt['usage']=used;save()
        if used['unknown_usage_ids']:raise ValueError('用量未知，停止诊断，未自动重跑')
        for field in ('embedding_calls','llm_calls','rerank_calls','known_tokens'):
            if used[field]>plan['limits'][field]:raise ValueError('超过预登记限制：'+field)
        return used
    try:
        scan=folders.scan(fid);attempt['scan_id']=scan['id'];save()
        built=index.sync(fid,scan['id']);attempt['sync']=built;guard()
        generation=built['active']['id'];files={d['name']:d['file_id'] for d in index.store(fid).generation()['files']}
        for case in plan['questions']:
            available=plan['limits']['known_tokens']-guard()['known_tokens']
            if available<256:raise ValueError('剩余诊断预算不足，未发送下一题')
            inputs={'query':case['query'],'mode':case['mode'],'budget':min(plan['limits']['per_question_tokens'],available),
                    'generation':generation,'file_ids':None if case['files'] is None else [files[name] for name in case['files']]}
            job=jobs.start(fid,'answer',inputs)
            row={'case_id':case['id'],'job_id':job['id'],'inputs':inputs};attempt['questions'].append(row);save()
            jobs.threads[job['id']].join(120);job=jobs.get(job['id'])
            row.update(status=job['status'],content_status=job.get('result',{}).get('content_status'),finish_reason=job.get('finish_reason'));save()
            if job['status'] in ('queued','running'):
                jobs.stop(job['id']);raise ValueError('超时，保留未确定状态并停止')
            if job['status']!='completed':raise ValueError('请求未正常完成，保留记录并停止：'+job['status'])
            row['export']=export_job(jobs,job['id']);guard()
            print(json.dumps({k:row[k] for k in ('case_id','status','content_status','finish_reason')},ensure_ascii=False),flush=True)
        final_sources={p.relative_to(docs).as_posix():sha(p.read_bytes()) for p in docs.rglob('*') if p.is_file()}
        if final_sources!=sources:raise ValueError('问答或导出期间源文件变化')
        attempt.update(status='completed',sources_unchanged=True)
    except Exception as exc:
        attempt.update(status='failed',error_type=type(exc).__name__,message=str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
        attempt['usage']=totals();save();raise
    finally:jobs.close();save()
    print(json.dumps({'status':attempt['status'],'usage':{k:v for k,v in attempt['usage'].items() if k!='events'}},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
