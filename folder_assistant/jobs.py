"""Persisted, explicit operations. No automatic request retries or restart recovery calls."""
import json
import threading
from uuid import uuid4

from knowledge.locking import lease
from knowledge.store import now


class Stopped(ValueError): pass


class FolderJobs:
    def __init__(self,index,*,llm_factory=None):
        self.index=index
        self.folders=index.folders
        self.lock=threading.RLock()
        self.threads={}
        self.closed=False
        from agent.llm import LLMClient
        self.llm_factory=llm_factory or LLMClient
        with self.folders.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS folder_jobs(id TEXT PRIMARY KEY,folder_id TEXT,data TEXT)')
        # Startup recovery retains evidence and NEVER repeats a paid request.
        for job in self.list():
            if job['status'] in ('running','queued'):
                try:
                    with lease(self.folders.root/(job['id']+'.job.lock')):
                        self.save({**job,'status':'interrupted','message':'进程已结束；已保留记录，未自动重试。请核对用量后显式新建操作。'})
                except ValueError: pass

    def save(self,job):
        with self.folders.connect() as db:
            db.execute('INSERT OR REPLACE INTO folder_jobs VALUES (?,?,?)',
                       (job['id'],job['folder_id'],json.dumps(job,ensure_ascii=False)))
        return job

    def get(self,jid):
        with self.folders.connect() as db:
            row=db.execute('SELECT data FROM folder_jobs WHERE id=?',(jid,)).fetchone()
        if not row: raise KeyError('操作记录不存在')
        return json.loads(row[0])

    def list(self,fid=None):
        with self.folders.connect() as db:
            rows=db.execute('SELECT data FROM folder_jobs'+(' WHERE folder_id=?' if fid else '')+' ORDER BY rowid DESC',
                            (fid,) if fid else ()).fetchall()
        return [json.loads(r[0]) for r in rows]

    def stop(self,jid):
        with self.lock:
            job=self.get(jid)
            if job['status'] in ('queued','running'):
                job['stop_requested']=True
                job['message']='已请求停止；正在发送的请求不能撤回，返回后保留用量并停止后续步骤。'
                self.save(job)
            return job

    def patch(self,jid,**values):
        with self.lock: return self.save({**self.get(jid),**values})

    def update_event(self,jid,event):
        with self.lock:
            job=self.get(jid);events={e['id']:e for e in job.get('usage_events',[])}
            events[event['id']]=event
            self.save({**job,'usage_events':list(events.values())})

    def used_tokens(self,jid):
        events=self.get(jid).get('usage_events',[])
        if any(e.get('input_tokens') is None or (e.get('operation')=='llm' and e.get('output_tokens') is None) for e in events):
            raise ValueError('调用用量未知，停止后续请求；请保留记录核对账单')
        return sum((e.get('input_tokens') or 0)+(e.get('output_tokens') or 0) for e in events)

    def check(self,jid):
        job=self.get(jid)
        if self.closed or job.get('stop_requested'): raise Stopped('用户停止；没有自动重试')
        self.folders.authorized(job['folder_id'])

    def start(self,fid,kind,inputs):
        self.folders.authorized(fid)
        with self.lock:
            if any(j['status'] in ('running','queued') for j in self.list(fid)): raise ValueError('该目录已有操作，请等待结束或停止')
            inputs=dict(inputs)
            if kind=='sync':
                manifest=self.folders.manifest(fid,inputs['scan_id'])
                if not manifest['complete']: raise ValueError('清单不完整，请重新扫描')
            elif kind=='answer':
                from agent.config import load_llm_config
                if not 1<=len(inputs['query'].strip())<=1000 or not 256<=inputs['budget']<=50000: raise ValueError('问题或预算无效')
                if inputs['mode'] not in ('keyword','hybrid'): raise ValueError('检索方式无效')
                generation=self.index.store(fid).generation(inputs.get('generation'))
                if not generation or generation['status']!='ready': raise ValueError('请先同步索引')
                files=inputs.get('file_ids')
                if files is not None and (not files or not set(files).issubset({d['file_id'] for d in generation['files']})):
                    raise ValueError('请选择固定版本中的至少一份资料，或选择整个目录')
                config=load_llm_config(settings=self.index.settings)
                inputs.update(generation=generation['id'],file_ids=files)
                snapshot=[{k:v for k,v in d.items() if k!='pages'} for d in generation['files'] if files is None or d['file_id'] in files]
                inputs['source_snapshot']=snapshot
                inputs['model']={'provider':config.provider,'model':config.model,'base_url':config.base_url,'retries':0}
            else: raise ValueError('操作类型无效')
            jid=uuid4().hex
            job=self.save({'id':jid,'folder_id':fid,'kind':kind,'inputs':inputs,'created':now(),
                           'status':'queued','stop_requested':False,'review_status':'not_reviewed'})
            thread=threading.Thread(target=self.run,args=(jid,),daemon=True)
            self.threads[jid]=thread
            thread.start()
            return job

    def run(self,jid):
        with lease(self.folders.root/(jid+'.job.lock')):
            with self.lock:
                job=self.get(jid);job.update(status='running');self.save(job)
            try:
                self.check(jid)
                if job['kind']=='sync':
                    result=self.index.sync(job['folder_id'],job['inputs']['scan_id'],check=lambda:self.check(jid))
                else:
                    from .answers import answer_once
                    result=answer_once(self,jid)
                with self.lock:
                    job=self.get(jid)
                    self.save({**job,'status':'completed','result':result,'finished':now()})
            except Exception as exc:
                with self.lock:
                    job=self.get(jid)
                    # Provider exceptions never expose headers, keys, or response internals.
                    message=str(exc) if isinstance(exc,ValueError) else '操作失败：'+type(exc).__name__
                    self.save({**job,'status':'stopped' if isinstance(exc,Stopped) else 'failed',
                               'message':message,'finished':now()})

    def close(self):
        self.closed=True
