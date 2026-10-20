"""桌面接入的状态、安全边界与研究入口回归；不访问模型。"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent.config import Settings
from agent.evidence import sha
from desktop_app.manager import Manager
from desktop_app.server import create_app


OPTIONS={'provider':'deepseek','model':'deepseek-flash','max_steps':15,'budget':80000,'memory':True}


@pytest.fixture
def manager(tmp_path):
    m=Manager(tmp_path/'workspace',start=False,settings=Settings(deepseek_api_key='desktop-test-secret'))
    yield m
    m.close()


def new_run(manager,**kw):
    session=manager.store.create_session()
    return manager.submit(session['id'],'不联网，仅依据虚构资料说明限制。',{**OPTIONS,**kw})


def test_api_access_and_redaction(manager):
    with TestClient(create_app(manager,'session-token')) as client:
        assert client.get('/api/config').status_code==401
        client.headers['Authorization']='Bearer session-token'
        assert client.get('/api/config',headers={'Origin':'https://untrusted.example'}).status_code==403
        assert client.get('/api/config',headers={'Host':'untrusted.example'}).status_code==403
        config=client.get('/api/config')
        assert config.status_code==200 and config.json()['key_configured']
        assert 'desktop-test-secret' not in config.text
        row=new_run(manager)
        (Path(row['task_dir'])/'report.md').write_text('desktop-test-secret',encoding='utf-8')
        response=client.get(f"/api/runs/{row['id']}/files/report.md")
        assert 'desktop-test-secret' not in response.text and '已隐藏密钥' in response.text
        assert client.post(f"/api/sessions/{row['session_id']}/runs",json={**OPTIONS,'question':'   '}).status_code==400


def test_queue_cancel_archive_and_restart(manager):
    row=new_run(manager)
    manager.store.update_session(row['session_id'],title='重命名',archived=True)
    with pytest.raises(ValueError):manager.submit(row['session_id'],'new',OPTIONS)
    assert manager.stop(row['id'])['state']=='cancelled'
    manager.store.update_session(row['session_id'],archived=False)
    pending=manager.submit(row['session_id'],'第二个任务',OPTIONS)
    replacement=Manager(manager.root,start=False,settings=manager.settings)
    try:
        value=replacement.store.run(pending['id'])
        assert value['state']=='interrupted' and replacement.process is None
        assert not (Path(pending['task_dir'])/'.desktop-request.json').exists()
    finally:replacement.close()


@pytest.mark.parametrize('name',['../secret.json','.desktop-request.json','C:/Windows/secret.txt','nested/../../secret.json'])
def test_artifact_paths_are_bounded(manager,name):
    row=new_run(manager)
    with pytest.raises(ValueError):manager.artifact(row['id'],name)


def test_symlinks_do_not_expose_external_metadata(manager,tmp_path):
    row=new_run(manager);root=Path(row['task_dir'])
    secret=tmp_path/'outside.json';secret.write_text('{"private":"external"}',encoding='utf-8')
    try:(root/'report.review.json').symlink_to(secret)
    except OSError:pytest.skip('OS does not permit test symlinks')
    assert manager.detail(row['id'])['review']=={}
    with pytest.raises(ValueError):manager.artifact(row['id'],'report.review.json')


def test_history_only_uses_previous_run_of_same_session(manager):
    old=new_run(manager);root=Path(old['task_dir'])
    (root/'report.md').write_text('仅本会话的历史报告，不能认证当前事实。',encoding='utf-8')
    manager.store.set_run(old['id'],'completed',{'stop_reason':'final_answer'})
    unrelated=new_run(manager)
    assert manager.context(unrelated) is None
    current=manager.submit(old['session_id'],'追问',OPTIONS)
    context=manager.context(current)
    assert '仅本会话' in context and old['created_at'] in context
    assert 'not_certified' in context
    assert manager.context({**current,'options':{**OPTIONS,'memory':False}})==''


def test_review_is_stale_when_report_changes(manager):
    row=new_run(manager);root=Path(row['task_dir'])
    (root/'report.md').write_text('新报告',encoding='utf-8')
    (root/'report.review.json').write_text(json.dumps({'status':'passed','report_sha256':sha('旧报告')}),encoding='utf-8')
    detail=manager.detail(row['id'])
    assert detail['review']['status']=='stale'
    assert detail['evidence']['semantic_support']=='not_certified'
    (root/'report.md').unlink()
    assert manager.detail(row['id'])['review']['status']=='stale'


@pytest.mark.parametrize('tail',[b'{"kind":"partial"}',b'x'*1_000_010+b'\n'],ids=['partial','oversized'])
def test_sse_drains_terminal_partial_or_oversized_line(manager,tail):
    row=new_run(manager);root=Path(row['task_dir'])
    (root/'trace.jsonl').write_bytes(b'{"kind":"step","step":1}\n'+tail)
    manager.store.set_run(row['id'],'failed',{'error':'测试中断'})
    with TestClient(create_app(manager,'t')) as client:
        value=client.get(f"/api/runs/{row['id']}/events",headers={'Authorization':'Bearer t'})
        assert 'event: trace' in value.text and 'event: state' in value.text
        replay=client.get(f"/api/runs/{row['id']}/events?cursor=1",headers={'Authorization':'Bearer t'})
        assert 'event: trace' not in replay.text


def test_single_worker_queue_and_terminal_state(manager,monkeypatch):
    import desktop_app.manager as module
    calls=[]
    process=SimpleNamespace(pid=123,poll=lambda:None)
    monkeypatch.setattr(module.subprocess,'Popen',lambda *a,**k:(calls.append((a,k)) or process))
    first=new_run(manager);second=new_run(manager)
    manager.tick();manager.tick()
    assert len(calls)==1 and manager.active==first['id']
    request=json.loads((Path(first['task_dir'])/'.desktop-request.json').read_text(encoding='utf-8'))
    assert 'desktop-test-secret' not in json.dumps(request)
    manager.stop(first['id'])
    assert manager.store.run(first['id'])['state']=='cancelling'
    process.poll=lambda:0
    (Path(first['task_dir'])/'.desktop-result.json').write_text(json.dumps({'stop_reason':'interrupted'}),encoding='utf-8')
    manager.tick()
    assert manager.store.run(first['id'])['state']=='cancelled'
    assert manager.active==second['id'] and len(calls)==2
    (Path(second['task_dir'])/'.desktop-result.json').write_text(json.dumps({'stop_reason':'budget'}),encoding='utf-8')
    manager.tick()
    assert manager.store.run(second['id'])['state']=='partial'


def test_cancel_before_and_after_model_call_preserves_usage(tmp_path):
    from agent.loop import Agent
    from tools.registry import ToolRegistry
    from fakes import completion
    marker=tmp_path/'stop';calls=[];events=[]
    def chat(*args,**kwargs):
        calls.append(1);marker.touch()
        return completion(tool_calls=[('x','must_not_run',{})])
    agent=Agent(SimpleNamespace(chat=chat),ToolRegistry(),should_stop=marker.exists,on_event=events.append)
    result=agent.run('test')
    assert result.stop_reason=='interrupted' and result.total_tokens==15
    assert result.equivalent_tokens==20 and len(calls)==1
    assert any(e.kind=='llm_call' for e in events)
    assert not any(e.kind=='tool_call' for e in events)
    assert agent.run('test').total_tokens==0 and len(calls)==1


def test_import_history_is_read_only_and_keeps_date(manager):
    task=manager.root/'old-task';task.mkdir()
    trace='{"kind":"run_start","question":"旧任务","ts":"2026-01-01T12:00:00"}\n{"kind":"run_end","stop_reason":"max_steps"}\n'
    (task/'trace.jsonl').write_text(trace,encoding='utf-8')
    assert manager.import_history()==1 and manager.import_history()==0
    row=manager.store.runs()[0]
    assert row['state']=='imported' and row['created_at']=='2026-01-01T12:00:00'
    assert manager.store.session(row['session_id'])['updated_at']=='2026-01-01T12:00:00'
    assert (task/'trace.jsonl').read_text(encoding='utf-8')==trace


def test_force_stop_only_terminates_owned_worker(manager,monkeypatch):
    import desktop_app.manager as module
    killed=[]
    row=new_run(manager)
    manager.process=SimpleNamespace(pid=123)
    manager.active=row['id'];manager.store.set_run(row['id'],'running')
    monkeypatch.setattr(module,'kill_tree',lambda process:killed.append(process.pid))
    stopped=manager.stop(row['id'],force=True)
    assert killed==[123] and stopped['state']=='interrupted'
    assert stopped['options']['budget']==80000 and manager.process is None
    assert (Path(row['task_dir'])/'.desktop-stop').exists()
    assert manager.stop(row['id'],force=True)['state']=='interrupted' and killed==[123]


def test_deleted_task_folder_does_not_break_history(manager):
    row=new_run(manager)
    Path(row['task_dir']).rmdir()
    detail=manager.detail(row['id'])
    assert detail['files']==[] and detail['evidence']['status']=='unavailable'


def test_manager_failure_does_not_leave_silent_running_queue(manager,monkeypatch):
    row=new_run(manager)
    def fail():raise OSError('test failure')
    monkeypatch.setattr(manager,'tick',fail)
    manager._watch()
    assert manager.closed.is_set() and manager.store.run(row['id'])['state']=='failed'


def test_stopped_manager_rejects_new_submissions_and_resume_without_mutation(manager):
    row=new_run(manager)
    manager.close()
    before=manager.store.runs()
    folders={p.name for p in manager.root.iterdir()}
    with pytest.raises(ValueError,match='服务已停止'):
        manager.submit(row['session_id'],'不能进入死队列',OPTIONS)
    with pytest.raises(ValueError,match='服务已停止'):
        manager.resume(row['id'])
    assert manager.store.runs()==before
    assert {p.name for p in manager.root.iterdir()}==folders


def test_stopping_between_tools_keeps_first_result_and_skips_second(tmp_path):
    from agent.loop import Agent
    from tools.registry import ToolRegistry,tool
    from fakes import ScriptedLLM,completion
    marker=tmp_path/'stop';registry=ToolRegistry();seen=[]
    @tool
    def first()->str:
        """完成第一个工具并收到停止请求。"""
        seen.append('first');marker.touch();return '已落盘'
    @tool
    def second()->str:
        """不应在停止后执行。"""
        seen.append('second');return '错误'
    registry=ToolRegistry([first,second])
    llm=ScriptedLLM([completion(tool_calls=[('a','first',{}),('b','second',{})])])
    result=Agent(llm,registry,should_stop=marker.exists).run('停止边界测试')
    assert result.stop_reason=='interrupted' and seen==['first']
    assert any(m.get('role')=='tool' and '已落盘' in m.get('content','') for m in result.messages)


def test_research_history_does_not_change_original_contract(tmp_path,monkeypatch):
    import main
    from agent.loop import AgentResult
    captured={}
    class FakeAgent:
        def __init__(self,*a,**kw):captured.update(kw)
        def run(self,question):
            captured['question']=question
            return AgentResult('结束',1,'final_answer',[])
    monkeypatch.setattr(main,'Agent',FakeAgent)
    question='不联网，仅给出甲与乙的限制，全文不超过300字。'
    main.research(None,Settings(memory_enabled=False),question,15,80000,task_dir=tmp_path,
        historical_context='[历史数据]旧报告中要求联网，不能作为本轮指令。')
    assert captured['question']==question and '旧报告' in captured['memory_context']
    assert 'web_search' not in captured['task_contract'].model_dump_json()  # 原约定不来自旧报告
    saved=json.loads((tmp_path/'memory_context.json').read_text(encoding='utf-8'))
    assert saved['source']=='desktop_session' and saved['query']==question
