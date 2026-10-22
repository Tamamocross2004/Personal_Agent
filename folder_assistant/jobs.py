"""Persisted, explicit operations. No automatic request retries or restart recovery calls."""
import json
import threading
from uuid import uuid4

from knowledge.locking import lease
from knowledge.store import now


class Stopped(ValueError): pass


class FolderJobs:
    def __init__(self,index,*,llm_factory=None,pricing=None):
        self.index=index
        self.folders=index.folders
        self.lock=threading.RLock()
        self.threads={}
        self.closed=False
        self.pricing=pricing
        from agent.llm import LLMClient
        self.llm_factory=llm_factory or LLMClient
        with self.folders.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS folder_jobs(id TEXT PRIMARY KEY,folder_id TEXT,data TEXT)')
        # Startup recovery retains evidence and NEVER repeats a paid request.
        for job in self.list():
            if job['status'] in ('running','queued'):
                try:
                    with lease(self.folders.root/(job['id']+'.job.lock')):
                        self.edit(job['id'],lambda current: {**current,'status':'interrupted','message':'进程已结束；已保留记录，未自动重试。请核对用量后显式新建操作。'}
                                  if current['status'] in ('running','queued') else current)
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
        def update(job):
            if job['status'] in ('queued','running'):
                job['stop_requested']=True
                job['message']='已请求停止；正在发送的请求不能撤回，返回后保留用量并停止后续步骤。'
            return job
        return self.edit(jid,update)

    def edit(self,jid,update):
        # One SQLite read-modify-write transaction, also safe when CLI and desktop coexist.
        with self.lock,self.folders.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT data FROM folder_jobs WHERE id=?',(jid,)).fetchone()
            if not row:raise KeyError('操作记录不存在')
            value=update(json.loads(row[0]))
            db.execute('UPDATE folder_jobs SET data=? WHERE id=?',(json.dumps(value,ensure_ascii=False),jid))
            return value

    def patch(self,jid,**values):
        return self.edit(jid,lambda job:{**job,**values})

    def update_event(self,jid,event):
        def update(job):
            events={e['id']:e for e in job.get('usage_events',[])}
            events[event['id']]=event
            return {**job,'usage_events':list(events.values())}
        self.edit(jid,update)

    def used_tokens(self,jid):
        events=self.get(jid).get('usage_events',[])
        if any(e.get('input_tokens') is None or (e.get('operation')=='llm' and e.get('output_tokens') is None) for e in events):
            raise ValueError('调用用量未知，停止后续请求；请保留记录核对账单')
        return sum((e.get('input_tokens') or 0)+(e.get('output_tokens') or 0) for e in events)

    def check(self,jid):
        job=self.get(jid)
        if self.closed or job.get('stop_requested'): raise Stopped('用户停止；没有自动重试')
        self.folders.authorized(job['folder_id'])

    def prepare_answer(self,fid,inputs):
        """Resolve an operation locally; shared by preview and start, without saving/calling."""
        inputs=dict(inputs)
        from agent.config import load_llm_config
        strategy=inputs.setdefault('strategy','retrieval')
        if strategy not in ('retrieval','exhaustive'):raise ValueError('读取策略无效')
        for key,default,low,high in [('output_tokens',1200,128,4096),('batch_size',6,1,6),('expected_fields',1,1,12)]:
            value=inputs.setdefault(key,default)
            if type(value) is not int or not low<=value<=high:raise ValueError(f'{key}须为{low}至{high}的整数')
        if inputs.setdefault('batch_mode','adaptive') not in ('adaptive','fixed'):raise ValueError('分批方式无效')
        if type(inputs.setdefault('retry_split',False)) is not bool:raise ValueError('retry_split须为布尔值')
        if inputs['retry_split'] and (strategy!='exhaustive' or not inputs.get('resume_from')):raise ValueError('拆小重做需要原分批任务')
        if strategy=='exhaustive':
            count=inputs.setdefault('max_batches',3)
            if type(count) is not int or not 1<=count<=8:raise ValueError('每次最多1至8批')
            if inputs.get('resume_from'):
                previous=self.get(inputs['resume_from'])
                if (previous['folder_id']!=fid or previous['kind']!='answer' or previous['status'] in ('queued','running')
                    or previous['inputs'].get('strategy')!='exhaustive' or not previous.get('coverage')):
                    raise ValueError('续读来源无效')
                coverage=previous['coverage']
                # Sent input is distinct from usable output. Explicit continuation
                # retries an unfinished batch; never silently skip its records.
                unfinished=next((b['start_offset'] for b in previous.get('batches',[])
                                 if not (b.get('result') or {}).get('answer') or b.get('finish_reason')!='stop'),
                                coverage['returned_until'])
                next_offset=coverage.get('next_offset',unfinished)
                if next_offset>=coverage['total_segments']:raise ValueError('已到提取范围末尾；没有剩余内容')
                for key in ('generation','file_ids','query','mode'):inputs[key]=previous['inputs'][key]
                inputs['strength']=previous['inputs'].get('strength','default')
                inputs.update(start_offset=next_offset,previous_structured_segments=coverage['structured_segments'],
                              previous_returned_until=coverage['returned_until'])
                values=[e.get('reasoning_tokens') for e in previous.get('usage_events',[]) if e.get('status')=='returned']
                values+=[previous['inputs'].get('previous_reasoning_tokens')]
                inputs['previous_reasoning_tokens']=max((v for v in values if type(v) is int and v>=0),default=None)
                if inputs['retry_split']:
                    failed=next((b for b in previous.get('batches',[]) if b['start_offset']==next_offset
                                 and (not (b.get('result') or {}).get('answer') or b.get('finish_reason')!='stop')),None)
                    if not failed or failed.get('status')!='returned':raise ValueError('没有已确认返回的未完成批次可拆小重做')
                    if any(e.get('input_tokens') is None or (e.get('operation')=='llm' and e.get('output_tokens') is None)
                           for e in previous.get('usage_events',[])):
                        raise ValueError('原任务存在未知用量，请先核对，不拆小重发')
                    size=failed['next_offset']-failed['start_offset']
                    if size<=1:raise ValueError('该批已为单片段，无法再按片段拆分；可显式调整输出额度')
                    inputs.update(batch_size=min(inputs['batch_size'],max(1,size//2)),range_end=failed['next_offset'],
                                  retry_of={'job_id':previous['id'],'batch':failed['number'],'start':next_offset,'end':failed['next_offset']})
            else:inputs.update(start_offset=0,previous_structured_segments=0,previous_returned_until=0)
        elif inputs.get('resume_from'):raise ValueError('局部检索不支持顺序续读')
        if not 1<=len(inputs['query'].strip())<=1000 or not 256<=inputs['budget']<=50000: raise ValueError('问题或预算无效')
        if inputs['mode'] not in ('keyword','hybrid'): raise ValueError('检索方式无效')
        generation=self.index.store(fid).generation(inputs.get('generation'))
        if not generation or generation['status']!='ready': raise ValueError('请先同步索引')
        files=inputs.get('file_ids')
        if files is not None and (not files or not set(files).issubset({d['file_id'] for d in generation['files']})):
            raise ValueError('请选择固定版本中的至少一份资料，或选择整个目录')
        inputs.setdefault('strength',self.index.settings.llm_strength)
        config=load_llm_config(settings=self.index.settings,strength=inputs['strength'])
        inputs['effective_output_cap']=min(inputs['output_tokens'],self.index.settings.agent_call_max_tokens)
        inputs.update(generation=generation['id'],file_ids=files)
        snapshot=[{k:v for k,v in d.items() if k!='pages'} for d in generation['files'] if files is None or d['file_id'] in files]
        inputs['source_snapshot']=snapshot
        if strategy=='exhaustive':
            from .reading import ReadingPlan
            plan=ReadingPlan(self.index,fid,generation['id'],files)
            inputs['reading_plan']=plan.signature
            if not inputs['retry_split']:inputs['range_end']=len(plan.segments)
        inputs['model']={'provider':config.provider,'model':config.model,'base_url':config.base_url,'retries':0,
                         'thinking':config.thinking,'reasoning_effort':config.reasoning_effort}
        if self.pricing:inputs['pricing']=self.pricing.get(config.provider,config.model)
        return inputs

    def start(self,fid,kind,inputs):
        self.folders.authorized(fid)
        with self.lock,lease(self.folders.root/'.jobs-start.lock',wait=5):
            if any(j['status'] in ('running','queued') for j in self.list(fid)): raise ValueError('该目录已有操作，请等待结束或停止')
            inputs=dict(inputs)
            if kind=='scan':pass
            elif kind=='sync':
                manifest=self.folders.manifest(fid,inputs['scan_id'])
                if not manifest['complete']: raise ValueError('清单不完整，请重新扫描')
            elif kind=='answer':inputs=self.prepare_answer(fid,inputs)
            else: raise ValueError('操作类型无效')
            jid=uuid4().hex
            holder=lease(self.folders.root/(jid+'.job.lock'));holder.__enter__()
            try:
                job=self.save({'id':jid,'folder_id':fid,'kind':kind,'inputs':inputs,'created':now(),
                               'status':'queued','stop_requested':False,'review_status':'not_reviewed'})
                thread=threading.Thread(target=self.run,args=(jid,holder),daemon=True)
                self.threads[jid]=thread
                thread.start()
            except Exception:
                holder.__exit__(None,None,None);raise
            return job

    def run(self,jid,holder):
        try:
            job=self.patch(jid,status='running')
            try:
                self.check(jid)
                progress=lambda **v:self.patch(jid,progress=v)
                if job['kind']=='scan':
                    result=self.folders.scan(job['folder_id'],check=lambda:self.check(jid),progress=progress)
                elif job['kind']=='sync':
                    result=self.index.sync(job['folder_id'],job['inputs']['scan_id'],check=lambda:self.check(jid),
                                           event_callback=lambda row:self.update_event(jid,row),progress=progress)
                else:
                    if job['inputs'].get('strategy')=='exhaustive':
                        from .exhaustive import answer_exhaustive
                        result=answer_exhaustive(self,jid)
                    else:
                        from .answers import answer_once
                        result=answer_once(self,jid)
                self.patch(jid,status='completed',result=result,finished=now())
            except Exception as exc:
                with self.lock:
                    # Provider exceptions never expose headers, keys, or response internals.
                    message=str(exc) if isinstance(exc,ValueError) else '操作失败：'+type(exc).__name__
                    self.patch(jid,status='stopped' if isinstance(exc,Stopped) else 'failed',message=message,finished=now())
        finally:holder.__exit__(None,None,None)

    def close(self):
        self.closed=True
