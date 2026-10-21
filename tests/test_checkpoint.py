import json
import threading
from pathlib import Path

import pytest

from agent.checkpoint import CheckpointError, CheckpointStore
from agent.loop import Agent
from fakes import ScriptedLLM, completion
from tools.registry import ToolRegistry, tool
from tools.report import make_write_report
from tools.session import ResearchSession


class Crash(BaseException):
    pass


class CrashStore(CheckpointStore):
    def __init__(self,root,predicate):
        super().__init__(root); self.predicate=predicate

    def save(self,data):
        result=super().save(data)
        if self.predicate(result): raise Crash()
        return result


def create(root,llm,registry,session,store=None,**kw):
    return Agent(llm,registry,task_dir=root,research_session=session,
                 checkpoint=store or CheckpointStore(root),max_steps=kw.pop('max_steps',5),**kw)


def test_saved_response_reused_without_repaying_or_repeating_tool(tmp_path):
    seen=[]
    @tool
    def record()->str:
        """写入一次测试效果。"""
        seen.append('written'); return '已写入'
    registry=ToolRegistry([record]); s=ResearchSession()
    original=ScriptedLLM([completion('{}'),completion(tool_calls=[('r','record',{})])])
    store=CrashStore(tmp_path,lambda d:d['phase']=='response')
    with pytest.raises(Crash): create(tmp_path,original,registry,s,store).run('记录')
    saved=store.read()
    assert seen==[] and saved['state']['_tokens']==30 and saved['state']['_steps']==1
    replacement=ScriptedLLM([completion('完成')])
    result=create(tmp_path,replacement,registry,ResearchSession()).run('记录',resume_state=saved)
    assert seen==['written'] and result.steps==2
    assert result.total_tokens==45 and result.equivalent_tokens==60
    assert len(replacement.requests)==1
    assert sum(m.get('role')=='tool' for m in result.messages)==1


REPORT='# 核对\n\n## 摘要\n业务状态未知。\n\n## 方法与范围\n仅依据材料。\n\n## 正文\n资料不足。\n\n## 结论\n业务状态未知。\n\n## 局限性\n未核实。\n\n## 参考资料\n用户材料。'


@pytest.mark.parametrize('after_review',[False,True])
def test_report_saved_before_review_and_review_committed_do_not_repeat(tmp_path,after_review):
    s=ResearchSession(); registry=ToolRegistry([make_write_report(tmp_path,s)])
    responses=[completion('{}'),completion(tool_calls=[('w','write_report',{
        'content':REPORT,'claims':[{'quote':'业务状态未知。','kind':'unknown'}]})])]
    if after_review: responses.append(completion('{"issues":[]}'))
    store=CrashStore(tmp_path,lambda d:d['phase']=='review_pending' and
                     bool(d['state']['_pending_reviews'])!=after_review)
    with pytest.raises(Crash):
        create(tmp_path,ScriptedLLM(responses),registry,s,store,review_reports=True).run('业务状态未知')
    before=(tmp_path/'report.md').read_bytes()
    new_s=ResearchSession(); new_llm=ScriptedLLM(([completion('{"issues":[]}')] if not after_review else [])+[completion('完成')])
    new=create(tmp_path,new_llm,ToolRegistry([make_write_report(tmp_path,new_s)]),new_s,review_reports=True)
    result=new.run('业务状态未知',resume_state=store.read())
    assert result.stop_reason=='final_answer' and result.steps==2 and result.total_tokens==60
    assert new._review_count==1 and (tmp_path/'report.md').read_bytes()==before
    assert len((tmp_path/'report.reviews.jsonl').read_text(encoding='utf-8').splitlines())==1
    assert len(new_llm.requests)==(1 if after_review else 2)


def test_inflight_model_is_not_automatically_retried(tmp_path):
    s=ResearchSession(); registry=ToolRegistry([])
    with pytest.raises(Crash):
        create(tmp_path,ScriptedLLM([Crash()]),registry,s).run('测试')
    store=CheckpointStore(tmp_path)
    assert store.read()['pending']['kind']=='model'
    assert not store.status()['resumable']
    llm=ScriptedLLM([])
    with pytest.raises(CheckpointError,match='需核对'):
        create(tmp_path,llm,registry,ResearchSession()).run('测试',resume_state=store.read())
    assert not llm.requests


def test_uncertain_tool_timeout_stops_dispatch_and_never_replays(tmp_path):
    gate=threading.Event(); seen=[]
    @tool
    def slow()->str:
        """模拟不确定副作用。"""
        gate.wait(3); seen.append('done'); return '完成'
    llm=ScriptedLLM([completion('{}'),completion(tool_calls=[('s','slow',{})])])
    try:
        result=create(tmp_path,llm,ToolRegistry([slow]),ResearchSession(),tool_timeout=.01).run('测试')
        assert result.stop_reason=='needs_verification'
        assert not CheckpointStore(tmp_path).status()['resumable']
        assert len(llm.requests)==2
    finally:
        gate.set()


def test_corruption_artifact_change_identity_and_lease_are_rejected(tmp_path,monkeypatch):
    store=CrashStore(tmp_path,lambda d:d['phase']=='response')
    with pytest.raises(Crash): create(tmp_path,ScriptedLLM([completion('{}'),completion('结束')]),ToolRegistry([]),ResearchSession(),store).run('测试')
    data=store.read()
    with store.lease():
        assert not store.status()['resumable']
        with pytest.raises(CheckpointError,match='另一个进程'):
            with CheckpointStore(tmp_path).lease(): pass
    with pytest.raises(CheckpointError,match='配置已变化'):
        create(tmp_path,ScriptedLLM([]),ToolRegistry([]),ResearchSession(),max_steps=6).run('测试',resume_state=data)
    (tmp_path/'unexpected.txt').write_text('changed',encoding='utf-8')
    with pytest.raises(CheckpointError,match='产物版本'):
        store.validate(data)
    monkeypatch.setattr('agent.checkpoint.runtime_identity',lambda:'different')
    with pytest.raises(CheckpointError,match='代码已变化'): store.validate(data)
    store.path.write_bytes(b'broken')
    assert not store.status()['available']


def test_completed_research_cannot_be_resumed_and_checkpoint_excludes_api_keys(tmp_path):
    from main import research,resume_research
    from agent.config import Settings
    settings=Settings(memory_enabled=False,report_review_enabled=False,deepseek_api_key='do-not-persist-this-key')
    result=research(ScriptedLLM([completion('{}'),completion('完成')]),settings,'测试',5,80000,task_dir=tmp_path)
    assert result.stop_reason=='final_answer'
    store=CheckpointStore(tmp_path); data=store.read()
    assert data['status']=='complete' and 'do-not-persist-this-key' not in json.dumps(data)
    with pytest.raises(CheckpointError,match='任务已经结束'):
        resume_research(tmp_path,llm=ScriptedLLM([]))


@pytest.mark.parametrize('committed',[False,True])
def test_memory_commit_before_crash_is_idempotent_without_new_model_calls(tmp_path,monkeypatch,committed):
    import main
    from agent.config import Settings
    from agent.memory import MemoryStore
    task=tmp_path/'task'; settings=Settings(memory_enabled=True,memory_db_path=tmp_path/'memory.db',report_review_enabled=False)
    save=MemoryStore.save; crashed=[]
    def once(self,**kw):
        if not crashed:
            crashed.append(True)
            if committed:save(self,**kw)
            raise Crash()
        return save(self,**kw)
    monkeypatch.setattr(MemoryStore,'save',once)
    llm=ScriptedLLM([completion('{}'),completion(tool_calls=[('w','write_report',{
        'content':REPORT,'claims':[{'quote':'业务状态未知。','kind':'unknown'}]})]),completion('完成')])
    with pytest.raises(Crash): main.research(llm,settings,'业务状态未知',5,80000,task_dir=task)
    store=CheckpointStore(task)
    assert store.read()['phase']=='postprocess' and store.status()['resumable']
    before=MemoryStore(settings.memory_db_path).list()
    replacement=ScriptedLLM([])
    result=main.resume_research(task,llm=replacement)
    assert result.stop_reason=='final_answer' and not replacement.requests
    after=MemoryStore(settings.memory_db_path).list()
    assert len(before)==int(committed) and len(after)==1
    if before:assert before[0]['id']==after[0]['id']
    assert store.read()['status']=='complete'


def test_finalization_interruption_cannot_gain_more_wrapup_calls(tmp_path):
    store=CrashStore(tmp_path,lambda d:d['phase']=='finalizing' and d.get('pending',{}).get('returned'))
    llm=ScriptedLLM([completion('{}'),completion('',finish_reason='length'),completion('收尾')])
    with pytest.raises(Crash): create(tmp_path,llm,ToolRegistry([]),ResearchSession(),store,max_steps=1).run('测试')
    assert not store.status()['resumable']


def test_resume_inherits_actual_budget_overrun_and_does_not_dispatch(tmp_path):
    seen=[]
    @tool
    def record()->str:
        """记录一次。"""
        seen.append('done'); return '完成'
    reg=ToolRegistry([record]); session=ResearchSession()
    store=CrashStore(tmp_path,lambda d:d['phase']=='response')
    llm=ScriptedLLM([completion('{}'),completion(tool_calls=[('r','record',{})],prompt_tokens=11000)])
    with pytest.raises(Crash): create(tmp_path,llm,reg,session,store,token_budget=10000).run('执行')
    saved=store.read(); replacement=ScriptedLLM([])
    result=create(tmp_path,replacement,reg,ResearchSession(),token_budget=10000).run('执行',resume_state=saved)
    assert seen==['done'] and result.stop_reason=='budget' and not replacement.requests
    assert result.equivalent_tokens==saved['state']['_equiv']>10000


def test_status_for_legacy_trace_never_invents_a_checkpoint(tmp_path):
    (tmp_path/'trace.jsonl').write_text('{}\n',encoding='utf-8')
    from agent.resume import main
    assert main(['status',str(tmp_path)])==1
    assert not (tmp_path/'.agent-checkpoint.sqlite3').exists()
    from main import research,resume_research
    from agent.config import Settings
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(CheckpointError):resume_research(tmp_path,llm=ScriptedLLM([]))
    assert main(['stop',str(tmp_path)])==2
    with pytest.raises(CheckpointError,match='不能覆盖旧任务'):
        research(ScriptedLLM([]),Settings(memory_enabled=False),'新任务',5,80000,task_dir=tmp_path)
    assert {p.name:p.read_bytes() for p in tmp_path.iterdir()}==before
    missing=tmp_path/'does-not-exist'
    with pytest.raises(CheckpointError):resume_research(missing,llm=ScriptedLLM([]))
    assert not missing.exists()


def test_cli_cooperative_stop_after_response_then_resume_preserves_budget(tmp_path):
    import main
    from agent.resume import main as resume_cli
    from agent.config import Settings
    class StopModel(ScriptedLLM):
        def chat(self,*a,**kw):
            value=super().chat(*a,**kw)
            if len(self.requests)==2:assert resume_cli(['stop',str(tmp_path)])==0
            return value
    model=StopModel([completion('{}'),completion('完成')])
    settings=Settings(memory_enabled=False,report_review_enabled=False)
    stopped=main.research(model,settings,'测试停止',5,80000,task_dir=tmp_path)
    assert stopped.stop_reason=='interrupted' and stopped.equivalent_tokens==40
    cp=CheckpointStore(tmp_path); assert cp.status()['resumable']
    replacement=ScriptedLLM([])
    finished=main.resume_research(tmp_path,llm=replacement)
    assert finished.stop_reason=='final_answer' and finished.equivalent_tokens==40
    assert not replacement.requests and not (tmp_path/'.desktop-stop').exists()


def test_partial_answer_and_empty_reply_counter_survive_resume(tmp_path):
    for mode in ('partial','empty'):
        root=tmp_path/mode
        cp=CrashStore(root,lambda d:d['phase']=='loop' and d['state']['_steps']==1 and not d.get('pending'))
        response=completion('前半',finish_reason='length') if mode=='partial' else completion('')
        with pytest.raises(Crash):
            create(root,ScriptedLLM([completion('{}'),response]),ToolRegistry([]),ResearchSession(),cp).run('测试')
        llm=ScriptedLLM([completion('后半' if mode=='partial' else '')])
        result=create(root,llm,ToolRegistry([]),ResearchSession()).run('测试',resume_state=cp.read())
        assert result.steps==2 and result.total_tokens==45 and len(llm.requests)==1
        if mode=='partial':assert result.answer=='前半后半'
        else:assert result.stop_reason=='error'


def test_stop_after_report_before_review_does_not_consume_or_suppress_review(tmp_path):
    s=ResearchSession(); stopped=[]
    def event(e):
        if e.kind=='tool_result' and e.data.get('name')=='write_report':stopped.append(True)
    llm=ScriptedLLM([completion('{}'),completion(tool_calls=[('w','write_report',{
        'content':REPORT,'claims':[{'quote':'业务状态未知。','kind':'unknown'}]})])])
    result=create(tmp_path,llm,ToolRegistry([make_write_report(tmp_path,s)]),s,
                  review_reports=True,should_stop=lambda:bool(stopped),on_event=event).run('业务状态未知')
    cp=CheckpointStore(tmp_path); data=cp.read()
    assert result.stop_reason=='interrupted' and data['state']['_review_count']==0
    assert data['state']['_reviewed_hashes']==[] and cp.status()['resumable']
    new_s=ResearchSession(); new_llm=ScriptedLLM([completion('{"issues":[]}'),completion('完成')])
    agent=create(tmp_path,new_llm,ToolRegistry([make_write_report(tmp_path,new_s)]),new_s,review_reports=True)
    assert agent.run('业务状态未知',resume_state=data).stop_reason=='final_answer'
    assert agent._review_count==1


def test_resume_preserves_repeat_guard_instead_of_granting_another_main_step(tmp_path):
    seen=[]
    @tool
    def record()->str:
        """测试重复保护。"""
        seen.append(True); return '完成'
    registry=ToolRegistry([record])
    cp=CrashStore(tmp_path,lambda d:d['phase']=='review_pending' and d['state']['_blocked']==1)
    llm=ScriptedLLM([completion('{}'),completion(tool_calls=[('a','record',{})]),
                    completion(tool_calls=[('b','record',{})])])
    with pytest.raises(Crash):create(tmp_path,llm,registry,ResearchSession(),cp,max_blocked_repeats=1).run('测试')
    replacement=ScriptedLLM([completion('收尾')])
    result=create(tmp_path,replacement,registry,ResearchSession(),max_blocked_repeats=1).run('测试',resume_state=cp.read())
    assert result.stop_reason=='loop_detected' and result.steps==2 and len(seen)==1
