from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from agent.config import PROJECT_ROOT, Settings, load_llm_config
from agent.evidence import audit_mapping, sha
from agent.memory import format_context
from agent.workspace import new_task_dir
from desktop_app.store import Store
from desktop_app.usage import PricingBook, TraceUsageReader, summarize
from agent.checkpoint import CheckpointStore

ACTIVE={'queued','resuming','running','cancelling'}
FINAL={'completed','partial','failed','cancelled','interrupted','imported','needs_verification'}


def read_json(path,default=None):
    try:
        if path.is_symlink() or path.stat().st_size>4_000_000:return default
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else default
    except (OSError,ValueError):return default


def kill_tree(process):
    if process.poll() is not None:return
    if os.name=='nt':
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        import signal
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
    try:process.wait(timeout=5)
    except subprocess.TimeoutExpired:pass


class Manager:
    def __init__(self,root:Path,*,start=True,settings=None):
        self.root=root.resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        self.settings=settings or Settings()
        self.store=Store(self.root/'desktop.sqlite3')
        self.pricing=PricingBook(self.root/'desktop-pricing.json')
        self.usage_reader=TraceUsageReader()
        self.lock=threading.RLock()
        self.process=None
        self.active=None
        self.log=None
        self.closed=threading.Event()
        for row in self.store.runs():
            if row['state'] in ACTIVE:self.store.set_run(row['id'],'interrupted',{'error':'本地服务重启；未自动恢复。可查看检查点恢复条件。'})
        self.thread=None
        if start:
            self.thread=threading.Thread(target=self._watch,daemon=True)
            self.thread.start()

    def safe_task(self,row):
        path=Path(row['task_dir']).resolve()
        if path==self.root or not path.is_relative_to(self.root):raise ValueError('任务目录越界')
        return path

    def artifact(self,rid,name):
        root=self.safe_task(self.store.run(rid))
        relative=Path(name.replace('\\','/'))
        if relative.is_absolute() or any(p.startswith('.') for p in relative.parts):raise ValueError('文件路径无效')
        path=(root/relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():raise ValueError('文件不存在或越界')
        if path.suffix.lower() not in {'.md','.json','.jsonl','.csv','.png','.jpg','.jpeg','.webp','.txt','.py','.svg'}:raise ValueError('不支持的文件类型')
        if path.stat().st_size>20_000_000:raise ValueError('文件超过20MB，请在任务目录查看')
        return path

    def config(self):
        s=self.settings
        try:c=load_llm_config(settings=s); configured=True; model=c.model
        except ValueError:configured=False; model=s.llm_model or 'deepseek-flash'
        return {'provider':s.llm_provider,'model':model,'key_configured':configured,
                'max_steps':s.agent_max_steps,'budget':s.agent_token_budget,'memory':s.memory_enabled,
                'review_enabled':s.report_review_enabled,'review_max_calls':s.report_review_max_calls,
                'project_root':str(PROJECT_ROOT),'workspace_root':str(self.root)}

    def submit(self,sid,question,options):
        with self.lock:
            if self.store.session(sid)['archived']:raise ValueError('请先取消归档再提交')
            # 先核对所选配置，配置错误不创建看似运行中的任务。
            load_llm_config(provider=options['provider'],model=options['model'],settings=self.settings)
            path=new_task_dir(question,root=self.root)
            path.mkdir(parents=True)
            options={**options,'pricing':self.pricing.get(options['provider'],options['model'])}
            row=self.store.add_run(sid,question,path,options)
            if self.store.session(sid)['title']=='新研究':self.store.update_session(sid,title=question[:28])
            return row

    def context(self,row):
        if not row['options'].get('memory'):return ''
        history=self.store.runs(row['session_id'])
        previous=history[:next(i for i,r in enumerate(history) if r['id']==row['id'])]
        for old in reversed(previous):
            if old['state'] not in {'completed','partial','imported'}:continue
            path=self.safe_task(old)
            report=path/'report.md'
            if not report.is_file() or report.is_symlink():continue
            with report.open(encoding='utf-8') as stream:text=stream.read(7000)
            review=read_json(path/'report.review.json',{})
            return format_context([{'id':old['id'],'created_at':old['created_at'],'question':old['question'],
                'complete':old['state']=='completed','stop_reason':old['result'].get('stop_reason','unknown'),
                'quality':[{'review_status':review.get('status','unknown'),'semantic_support':'not_certified'}],
                'summary':text,'sources':[]}],6000)
        return None  # 第一轮沿用CLI的历史召回规则。

    def resume(self,rid):
        with self.lock:
            row=self.store.run(rid)
            if row['state'] in ACTIVE:raise ValueError('任务已在运行或排队，不能重复恢复')
            if self.store.session(row['session_id'])['archived']:raise ValueError('请先取消归档再恢复')
            root=self.safe_task(row)
            status=CheckpointStore(root).status()
            if not status['resumable']:raise ValueError(status['reason'])
            with CheckpointStore(root).lease(control=True):
                for name in ('.desktop-stop','.desktop-result.json'):
                    (root/name).unlink(missing_ok=True)
            return self.store.set_run(rid,'resuming',{'error':'已请求从检查点恢复，继承原步数、预算和审稿次数。'})

    def tick(self):
        with self.lock:
            if self.process is not None:
                row=self.store.run(self.active)
                if self.process.poll() is None:return
                result=read_json(self.safe_task(row)/'.desktop-result.json')
                if result:
                    stop=result.get('stop_reason')
                    state=('cancelled' if stop=='interrupted' else 'completed' if stop=='final_answer' else
                           'needs_verification' if stop=='needs_verification' else
                           'partial' if stop in {'max_steps','budget','loop_detected'} else 'failed')
                else:
                    state='interrupted' if row['state']=='cancelling' else 'failed'
                    result={'error':'任务进程退出，未取得完整结束记录；已保存文件保留。'}
                self.store.set_run(row['id'],state,result)
                if self.log:self.log.close()
                self.log=None; self.process=None; self.active=None
            if self.closed.is_set():return
            pending=next((r for r in self.store.runs() if r['state'] in {'queued','resuming'}),None)
            if pending is None:return
            path=self.safe_task(pending)
            resuming=pending['state']=='resuming'
            request={'question':pending['question'],'options':pending['options'],'resume':resuming,
                     'historical_context':None if resuming else self.context(pending)}
            (path/'.desktop-request.json').write_text(json.dumps(request,ensure_ascii=False),encoding='utf-8')
            self.log=(path/'.desktop-console.log').open('ab' if resuming else 'wb')
            env=os.environ.copy(); env['PYTHONIOENCODING']='utf-8'
            try:
                self.process=subprocess.Popen([sys.executable,'-m','desktop_app.worker',str(path),'--parent',str(os.getpid())],
                    cwd=PROJECT_ROOT,env=env,stdout=self.log,stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,start_new_session=os.name!='nt')
            except OSError as exc:
                self.log.close(); self.log=None
                self.store.set_run(pending['id'],'failed',{'error':f'工作进程启动失败：{type(exc).__name__}'})
                return
            self.active=pending['id']; self.store.set_run(self.active,'running')

    def _watch(self):
        while not self.closed.wait(.4):
            try:self.tick()
            except Exception:
                # 故障不能被标成完成；后续状态读取与关闭仍可用。
                with self.lock:
                    if self.process:kill_tree(self.process)
                    if self.log:self.log.close()
                    self.process=None;self.active=None;self.log=None
                    for row in self.store.runs():
                        if row['state'] in ACTIVE:self.store.set_run(row['id'],'failed',{'error':'任务管理器发生错误；队列已停止，未自动重跑。请重启应用后新建任务。'})
                    self.closed.set()

    def stop(self,rid,force=False):
        with self.lock:
            row=self.store.run(rid)
            if row['state'] in {'queued','resuming'}:
                return self.store.set_run(rid,'cancelled',{'error':'排队请求已取消，未发出新调用。',
                    'usage_not_started':row['state']=='queued'})
            if row['state'] not in {'running','cancelling'}:return row
            (self.safe_task(row)/'.desktop-stop').touch()
            if force and self.active==rid and self.process:
                kill_tree(self.process)
                if self.log:self.log.close()
                self.log=None; self.process=None; self.active=None
                return self.store.set_run(rid,'interrupted',{'error':'已强制停止；部分工具结果可能不完整，请核对已保存文件。'})
            return self.store.set_run(rid,'cancelling',{'error':'等待当前模型或工具调用返回后停止；不会自动续跑。'})

    def close(self):
        self.closed.set()
        with self.lock:
            if self.active and self.process:
                row=self.store.run(self.active)
                (self.safe_task(row)/'.desktop-stop').touch()
                kill_tree(self.process)
                self.store.set_run(self.active,'interrupted',{'error':'应用已关闭；已保存文件保留，未自动恢复。'})
                if self.log:self.log.close()
                self.process=None;self.active=None;self.log=None
            for row in self.store.runs():
                if row['state'] in {'queued','resuming'}:self.store.set_run(row['id'],'interrupted',{'error':'应用关闭，排队请求未执行。'})
        if self.thread and self.thread is not threading.current_thread():self.thread.join(timeout=2)

    def detail(self,rid):
        row=self.store.run(rid); root=self.safe_task(row)
        files=[]
        for p in sorted(root.iterdir()) if root.is_dir() else []:
            if p.is_file() and not p.is_symlink() and not p.name.startswith('.'):
                files.append({'name':p.name,'size':p.stat().st_size,'modified':p.stat().st_mtime_ns,'type':p.suffix.lower()})
        review=read_json(root/'report.review.json',{})
        report=root/'report.md'
        if review:
            try:
                current=(report.is_file() and not report.is_symlink() and report.stat().st_size<=4_000_000
                         and review.get('report_sha256')==sha(report.read_text(encoding='utf-8')))
            except (OSError,UnicodeError):current=False
            if not current:review={**review,'status':'stale'}
        try:
            if any((root/name).is_symlink() or (root/name).stat().st_size>4_000_000 for name in ('report.md','report.evidence.json','evidence.json')):raise ValueError('linked or oversized artifact')
            evidence=audit_mapping(root)
        except (OSError,ValueError,KeyError,TypeError):evidence={'status':'unavailable','semantic_support':'not_certified'}
        if review.get('evidence_mapping_sha256') and review['evidence_mapping_sha256']!=evidence.get('mapping_sha256'):
            review={**review,'status':'stale'}
        return {**row,'files':files,'review':review,'evidence':evidence,
                'checkpoint':CheckpointStore(root).status(),
                'usage':self.usage(row),
                'quality':read_json(root/'report.quality.json',{}),'plan':read_json(root/'plan.json',{}),
                'contract':read_json(root/'task_contract.json',{}),
                'claims':read_json(root/'report.evidence.json',{}),'sources':read_json(root/'evidence.json',{})}

    def usage(self,row):
        try:trace=self.usage_reader.read(self.safe_task(row)/'trace.jsonl')
        except OSError:trace=None
        return summarize(trace,row,self.pricing)

    def import_history(self):
        imported=0
        known={Path(r['task_dir']).resolve() for r in self.store.runs()}
        # 仅索引顶层用户任务，评估目录不会挤满会话列表。
        for path in sorted(self.root.iterdir(),reverse=True):
            if not path.is_dir() or path.is_symlink() or path.resolve() in known or not (path/'trace.jsonl').is_file() or (path/'trace.jsonl').is_symlink():continue
            question=path.name; result={}; started=None
            with (path/'trace.jsonl').open(encoding='utf-8') as f:
                for line in f:
                    try:e=json.loads(line)
                    except ValueError:continue
                    if e.get('kind')=='run_start':question=e.get('question',question); started=e.get('ts')
                    if e.get('kind')=='run_end':result={k:v for k,v in e.items() if k not in ('ts','kind')}
            session=self.store.create_session(question[:28])
            row=self.store.add_run(session['id'],question,path,{},state='imported',result=result)
            if started:
                with self.store.connect() as db:
                    db.execute('UPDATE runs SET created_at=? WHERE id=?',(started,row['id']))
                    db.execute('UPDATE sessions SET created_at=?,updated_at=? WHERE id=?',(started,started,session['id']))
            imported+=1
            if imported>=100:break
        return imported
