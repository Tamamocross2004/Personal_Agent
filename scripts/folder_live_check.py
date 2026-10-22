"""One-attempt diagnostic against pre-registered synthetic sources. Never retries a run."""
import hashlib
import json
from pathlib import Path
import sys
import time

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT))
from agent.config import Settings,load_llm_config
from folder_assistant.store import FolderStore
from folder_assistant.index import FolderIndex
from folder_assistant.jobs import FolderJobs
from folder_assistant.exports import export_job
from knowledge.documents import sha


def main():
    plan_path=PROJECT/'docs/stage19-live-plan-20261022.json'
    plan=json.loads(plan_path.read_text('utf-8'));root=PROJECT/plan['workspace'];docs=root/'documents'
    if (root/'attempt.json').exists():raise ValueError('本次诊断已有尝试记录，拒绝重跑或覆盖')
    source_hashes={p.relative_to(docs).as_posix():sha(p.read_bytes()) for p in docs.rglob('*') if p.is_file()}
    if source_hashes!=plan['documents_sha256']:raise ValueError('资料与预登记哈希不同')
    settings=Settings().model_copy(update={'knowledge_rerank_enabled':False,'knowledge_build_token_budget':10000,
                                         'llm_timeout':30.0,'embedding_timeout':30.0})
    config=load_llm_config(settings=settings)
    runtime={p.as_posix():sha(p.read_bytes()) for p in [*Path('folder_assistant').glob('*.py'),Path('knowledge/store.py'),Path('knowledge/vectors.py')]}
    attempt={'plan_sha256':sha(plan_path.read_bytes()),'sources_sha256':source_hashes,'runtime_sha256':runtime,
             'configuration':{'provider':config.provider,'model':config.model,'base_url':config.base_url,
                              'embedding_model':settings.embedding_model,'dimensions':settings.embedding_dimensions,
                              'rerank':False,'retries':0,'llm_timeout':30,'embedding_timeout':30},
             'status':'started','questions':[],'stage12_accepted':False,'reviewer':'coding_assistant'}
    def save():
        temp=root/'attempt.tmp';temp.write_text(json.dumps(attempt,ensure_ascii=False,indent=2)+'\n','utf-8');temp.replace(root/'attempt.json')
    save()  # Durable before any paid request.
    folders=FolderStore(root/'state');fid=folders.grant(docs)['id'];index=FolderIndex(folders,settings);jobs=FolderJobs(index)
    def totals():
        events=index.store(fid).events()+[e for j in jobs.list(fid) for e in j.get('usage_events',[]) if e.get('operation')=='llm']
        unknown=[e['id'] for e in events if e.get('input_tokens') is None or (e.get('operation')=='llm' and e.get('output_tokens') is None)]
        return {'embedding_calls':sum(e.get('operation') not in ('llm','rerank') for e in events),
                'llm_calls':sum(e.get('operation')=='llm' for e in events),'rerank_calls':sum(e.get('operation')=='rerank' for e in events),
                'known_tokens':sum((e.get('input_tokens') or 0)+(e.get('output_tokens') or 0) for e in events),'unknown_usage_ids':unknown,'events':events}
    def guard():
        value=totals();attempt['usage']=value;save()
        if value['unknown_usage_ids']:raise ValueError('用量未知，停止诊断，未自动重跑')
        for key,limit in [('embedding_calls',4),('llm_calls',7),('rerank_calls',0),('known_tokens',60000)]:
            if value[key]>limit:raise ValueError('诊断超过预登记限制：'+key)
        return value
    try:
        scan=folders.scan(fid);attempt['scan_id']=scan['id'];save()
        first=index.sync(fid,scan['id']);attempt['initial_sync']=first;guard()
        calls=len(index.store(fid).events())
        repeated=index.sync(fid,scan['id']);attempt['repeat_sync']=repeated
        attempt['repeat_sync_no_new_embedding_requests']=len(index.store(fid).events())==calls
        generation=repeated['active']['id'];guard()
        files={d['name']:d['file_id'] for d in index.store(fid).generation()['files']}
        for case in plan['questions']:
            available=60000-guard()['known_tokens']
            if available<256:raise ValueError('总预算不足以启动下一个问题')
            inputs={'query':case['query'],'mode':case['mode'],'budget':min(10000,available),'generation':generation,
                    'file_ids':None if case['files'] is None else [files[name] for name in case['files']]}
            job=jobs.start(fid,'answer',inputs)
            record={'case_id':case['id'],'job_id':job['id'],'inputs':inputs};attempt['questions'].append(record);save()
            jobs.threads[job['id']].join(120)
            job=jobs.get(job['id']);record['status']=job['status'];record['content_status']=job.get('result',{}).get('content_status');save()
            if job['status'] in ('running','queued'):
                jobs.stop(job['id']);raise ValueError('请求超时，保留不确定状态并停止')
            if job['status']!='completed':raise ValueError('问题未完成，保留全部记录并停止：'+job['status'])
            record['export']=export_job(jobs,job['id'])
            guard();print(json.dumps({'case':case['id'],'status':job['status'],'content_status':record['content_status']},ensure_ascii=False),flush=True)
        attempt['source_hashes_after_questions']={p.relative_to(docs).as_posix():sha(p.read_bytes()) for p in docs.rglob('*') if p.is_file()}
        if attempt['source_hashes_after_questions']!=source_hashes:raise ValueError('问答或导出期间源文件发生变化')
        first_job=jobs.get(attempt['questions'][0]['job_id']);evidence=first_job['evidence'][0]
        (root/'original-source').mkdir();(root/'original-source'/'交付甲.txt').write_bytes((docs/'交付甲.txt').read_bytes())
        (docs/'交付甲.txt').write_bytes('澄远计划后续修改：交付期限改为20个工作日。\n'.encode())
        changed=folders.scan(fid);attempt['changed_diff']=index.diff(fid,changed['id']);save()
        attempt['changed_sync']=index.sync(fid,changed['id']);guard()
        old=index.citation(fid,generation,evidence['doc_id'],evidence['source_block_id'])
        attempt['old_citation_preserved']=old['block']['text']==evidence['text']
        attempt['status']='completed'
    except Exception as exc:
        attempt.update(status='failed',error_type=type(exc).__name__,message=str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
        attempt['usage']=totals();save();raise
    finally:jobs.close();save()
    print(json.dumps({'status':attempt['status'],'usage':{k:v for k,v in attempt['usage'].items() if k!='events'},
                      'old_citation_preserved':attempt.get('old_citation_preserved')},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
