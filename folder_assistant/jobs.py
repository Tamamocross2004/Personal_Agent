"""Persisted, explicit operations. No automatic request retries or restart recovery calls."""
import json
import threading
from uuid import uuid4

from knowledge.locking import lease
from knowledge.store import now


class Stopped(ValueError): pass


class FolderJobs:
    def __init__(self,index):
        self.index=index
        self.folders=index.folders
        self.lock=threading.RLock()
        self.threads={}
        self.closed=False
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

    def check(self,jid):
        if self.closed or self.get(jid).get('stop_requested'): raise Stopped('用户停止；没有自动重试')

    def start(self,fid,kind,inputs):
        self.folders.authorized(fid)
        with self.lock:
            if any(j['status'] in ('running','queued') for j in self.list(fid)): raise ValueError('该目录已有操作，请等待结束或停止')
            if kind!='sync': raise ValueError('操作类型无效')
            manifest=self.folders.manifest(fid,inputs['scan_id'])
            if not manifest['complete']: raise ValueError('清单不完整，请重新扫描')
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
                result=self.index.sync(job['folder_id'],job['inputs']['scan_id'],check=lambda:self.check(jid))
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
