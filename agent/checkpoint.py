"""本机单任务检查点。JSON状态+SQLite事务，不反序列化可执行对象。"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

NAME = '.agent-checkpoint.sqlite3'
VERSION = 1
SAFE_PHASES = {'before_plan', 'loop', 'response', 'review_pending', 'postprocess'}
STATE_FIELDS = ('_tokens','_equiv','_last_prompt','_len_at_last_call','_steps','_budget_warned',
                '_blocked','_review_count','_report_completeness','_structure_repair_requested',
                '_length_repair_requested','_calculation_results','_calculations_omitted',
                '_source_readings','_source_readings_omitted','_last_report_texts','_last_reviews',
                '_pending_text','_empty_replies','_pending_reviews','_finalizing_reason','_delivery_reserved_tokens')
OPTION_FIELDS = ('max_steps','max_tool_result_chars','token_budget','budget_warn_ratio','tool_timeout',
                 'max_blocked_repeats','steps_warn_remaining','wrapup_max_calls','review_reports',
                 'review_max_calls','review_max_tokens','generation_max_tokens')


class CheckpointError(ValueError):
    pass


class NeedsVerification(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()


def runtime_identity():
    root=Path(__file__).resolve().parent.parent
    files=[root/'main.py',*sorted((root/'agent').glob('*.py')),*sorted((root/'tools').glob('*.py')),
           *sorted((root/'knowledge').glob('*.py'))]
    return digest({p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files})


def manifest(root):
    result={}
    total=0
    for path in sorted(root.rglob('*')):
        relative=path.relative_to(root)
        if any(p in {'__pycache__','.git'} for p in relative.parts):
            continue
        if relative.parts[0].startswith(('.agent-checkpoint','.desktop-')) or relative.as_posix() in {'trace.jsonl','messages.json'}:
            continue
        if not path.resolve().is_relative_to(root.resolve()):
            raise CheckpointError('检查点产物路径越界：'+relative.as_posix())
        if path.is_symlink():
            raise CheckpointError('检查点不接受链接产物：'+relative.as_posix())
        if not path.is_file():
            continue
        if path.stat().st_size>20_000_000:
            raise CheckpointError('产物超过检查点单文件20MB上限：'+relative.as_posix())
        total+=path.stat().st_size
        if total>100_000_000 or len(result)>=512:
            raise CheckpointError('产物超过检查点100MB或512文件上限')
        result[relative.as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


class CheckpointStore:
    def __init__(self, root: Path):
        self.root=Path(root).resolve()
        self.path=self.root/NAME

    def read(self):
        if not self.path.exists():
            raise CheckpointError('该任务没有检查点，旧trace不能直接恢复')
        if self.path.is_symlink() or self.path.stat().st_size>100_000_000:
            raise CheckpointError('检查点不是受支持的普通文件')
        try:
            with sqlite3.connect(self.path) as db:
                db.execute('PRAGMA query_only=ON')
                row=db.execute('SELECT payload,sha256 FROM checkpoint WHERE id=1').fetchone()
            if not row: raise CheckpointError('检查点为空')
            data=json.loads(row[0])
            if not isinstance(data,dict): raise CheckpointError('检查点格式无效')
            if digest(data)!=row[1]: raise CheckpointError('检查点校验和错误')
            if data.get('version')!=VERSION: raise CheckpointError('检查点版本不兼容')
            if data.get('task_dir')!=str(self.root): raise CheckpointError('检查点任务目录不匹配')
            for key in ('state','identity','artifacts','research_settings','contract'):
                if not isinstance(data.get(key),dict): raise CheckpointError('检查点字段无效：'+key)
            if not isinstance(data.get('messages'),list) or not isinstance(data.get('phase'),str):
                raise CheckpointError('检查点消息或执行阶段格式无效')
            return data
        except CheckpointError:
            raise
        except (sqlite3.Error, OSError, TypeError, KeyError, ValueError) as exc:
            raise CheckpointError('检查点损坏或不可读') from exc

    def save(self, data):
        self.root.mkdir(parents=True,exist_ok=True)
        if self.path.is_symlink(): raise CheckpointError('检查点不得为符号链接')
        value={**data,'version':VERSION,'task_dir':str(self.root),'saved_at':time.time(),
               'artifacts':manifest(self.root)}
        raw=json.dumps(value,ensure_ascii=False,allow_nan=False)
        if len(raw.encode())>32_000_000: raise CheckpointError('检查点状态超过32MB上限')
        with sqlite3.connect(self.path,timeout=15) as db:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE IF NOT EXISTS checkpoint(id INTEGER PRIMARY KEY,payload TEXT NOT NULL,sha256 TEXT NOT NULL)')
            db.execute('INSERT OR REPLACE INTO checkpoint VALUES(1,?,?)',(raw,digest(value)))
        return value

    @contextmanager
    def lease(self, *, control=False):
        self.root.mkdir(parents=True,exist_ok=True)
        path=self.root/('.agent-checkpoint.control.lock' if control else '.agent-checkpoint.lock')
        if path.is_symlink(): raise CheckpointError('任务锁不得为符号链接')
        stream=path.open('a+b')
        if stream.tell()==0: stream.write(b'0'); stream.flush()
        stream.seek(0)
        acquired=False
        try:
            if os.name=='nt':
                import msvcrt
                try: msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                except OSError as exc: raise CheckpointError('任务正在另一个进程运行') from exc
            else:
                import fcntl
                try: fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                except OSError as exc: raise CheckpointError('任务正在另一个进程运行') from exc
            acquired=True
            yield
        finally:
            if acquired:
                stream.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
            stream.close()

    def request_stop(self):
        self.read()  # 旧日志或不存在的目录不能伪造停止/恢复状态。
        with self.lease(control=True):
            if self.read().get('status')=='complete': raise CheckpointError('任务已经结束')
            marker=self.root/'.desktop-stop'
            if marker.is_symlink(): raise CheckpointError('停止标记不得为符号链接')
            marker.touch()

    def validate(self, data, identity=None):
        if data.get('runtime')!=runtime_identity(): raise CheckpointError('运行代码已变化，首版不迁移旧检查点')
        if identity is not None and data.get('identity')!=identity: raise CheckpointError('模型、工具或运行配置已变化')
        if data.get('status')=='complete': raise CheckpointError('任务已经结束，不能恢复以重置预算')
        if data.get('pending') or data.get('phase') not in SAFE_PHASES:
            raise CheckpointError('需核对：存在结果未提交或用量不确定的操作，未自动重做；'+str(data.get('pending') or data.get('phase')))
        if manifest(self.root)!=data.get('artifacts'): raise CheckpointError('产物版本与检查点不匹配，需核对，未覆盖文件')

    def status(self):
        try:
            data=self.read()
            try:
                with self.lease(): self.validate(data)
                resumable=True; reason='可从已提交边界恢复，继承累计预算'
            except CheckpointError as exc:
                resumable=False; reason=str(exc)
            return {'available':True,'resumable':resumable,'reason':reason,'phase':data['phase'],
                    'status':data['status'],'saved_at':data['saved_at'],'pending':data.get('pending'),
                    'steps':data['state'].get('_steps',0),'equivalent_tokens':data['state'].get('_equiv',0),
                    'review_calls':data['state'].get('_review_count',0)}
        except (CheckpointError,OSError) as exc:
            return {'available':False,'resumable':False,'reason':str(exc)}


def agent_identity(agent):
    config=getattr(agent.llm,'config',None)
    public={k:getattr(config,k,None) for k in ('provider','model','base_url','temperature','timeout','max_retries','max_tokens','thinking')}
    return {'model':public,'options':{k:getattr(agent,k) for k in OPTION_FIELDS},
            'token_weights':asdict(agent.token_weights),'tools':agent.registry.schemas(),
            'compactor':({k:getattr(agent.compactor,k) for k in ('threshold','keep_recent_turns','min_chars','min_saving','rearm_ratio')} if agent.compactor else None)}


def snapshot(agent):
    state={k:getattr(agent,k,None) for k in STATE_FIELDS}
    state['_reviewed_hashes']=[list(x) for x in agent._reviewed_hashes]
    state['_call_stats']=[[list(k),v] for k,v in agent._call_stats.items()]
    state['_write_errors']=[[list(k),v] for k,v in agent._write_errors.items()]
    session=agent.research_session
    return {'runtime':agent._runtime_identity,'identity':agent_identity(agent),'question':agent._question,
            'system_prompt':agent.system_prompt,'memory_context':agent.memory_context,
            'contract':agent._contract.model_dump(),'state':state,
            'compactor_last_after':agent.compactor._last_after if agent.compactor else None,
            'session':{'pages':{k:asdict(v) for k,v in session.pages.items()},
                       'notes':[asdict(n) for n in session.notes],'failed_fetches':session.failed_fetches,
                       'evidence_records':agent._evidence.records if agent._evidence else {},
                       'evidence_omitted':agent._evidence.omitted if agent._evidence else 0} if session else None,
            'research_settings':getattr(agent,'research_settings',{}),
            'elapsed':getattr(agent,'_elapsed_before',0)+time.perf_counter()-agent._segment_started}


def restore(agent,data):
    from agent.evidence import EvidenceLedger
    from agent.task_contract import TaskContract
    from tools.fetch import Page
    from tools.session import Note
    for k in STATE_FIELDS: setattr(agent,k,data['state'].get(k))
    agent._reviewed_hashes={tuple(x) for x in data['state']['_reviewed_hashes']}
    agent._call_stats={tuple(k):v for k,v in data['state']['_call_stats']}
    agent._write_errors={tuple(k):v for k,v in data['state']['_write_errors']}
    agent._question=data['question']; agent.system_prompt=data['system_prompt']; agent.memory_context=data['memory_context']
    agent._contract=TaskContract.model_validate(data['contract'])
    agent._elapsed_before=data.get('elapsed',0)
    if agent.compactor: agent.compactor._last_after=data['compactor_last_after']
    session=agent.research_session
    if session and data['session']:
        s=data['session']; session.pages.clear(); session.pages.update({k:Page(**v) for k,v in s['pages'].items()})
        session.notes[:]=[Note(**n) for n in s['notes']]; session.failed_fetches=s['failed_fetches']; session.task_contract=agent._contract
        ledger=EvidenceLedger.__new__(EvidenceLedger)
        ledger.path=agent.task_dir/'evidence.json'; ledger.request_sha256=agent._contract.request_sha256
        ledger.records=s['evidence_records']; ledger.omitted=s['evidence_omitted']
        session.evidence=agent._evidence=ledger


class CheckpointMixin:
    def _save_checkpoint(self,phase=None,*,pending=None,status='running',response=None,result=None):
        if not self.checkpoint: return
        if getattr(self,'_finalizing_reason',None) and phase!='postprocess':
            phase='finalizing'
            pending=pending or {'kind':'finalize','reason':self._finalizing_reason}
        if phase is not None: self._checkpoint_phase=phase
        self._checkpoint_pending=pending
        value={**snapshot(self),'phase':self._checkpoint_phase,'status':status,'pending':pending,
               'messages':self._checkpoint_messages,'response':response,'result':result}
        self._checkpoint_data=self.checkpoint.save(value)
