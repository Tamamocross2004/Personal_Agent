import json
import os
import subprocess
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from agent.checkpoint import CheckpointStore
from agent.context import ContextCompactor
from agent.loop import Agent
from checkpoint_fixture import actor
from fakes import ScriptedLLM,completion
from test_checkpoint import Crash,CrashStore
from test_desktop import OPTIONS
from tools import default_registry
from tools.session import ResearchSession


CHILD='''
import sys,time
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from agent.checkpoint import CheckpointStore
from checkpoint_fixture import actor
from fakes import ScriptedLLM,completion
root=Path(sys.argv[1]); phase=sys.argv[2]
class Pause(CheckpointStore):
 def save(self,data):
  value=super().save(data)
  if (phase=='response' and value['phase']=='response') or (phase=='loop' and value['phase']=='loop' and value['state']['_steps']==1 and not value.get('pending')):
   (root/'.desktop-test-ready').touch()
   while True:time.sleep(.1)
  return value
actor(root,ScriptedLLM([completion('{}'),completion(tool_calls=[('a','append_record',{})]),completion('完成')]),Pause(root)).run('追加')
'''


@pytest.mark.parametrize('phase',['response','loop','during_tool'])
def test_real_worker_kill_keeps_durable_boundary_or_blocks_uncertain_effect(tmp_path,phase):
    baseline_root=tmp_path.parent/(tmp_path.name+'-baseline')
    baseline=actor(baseline_root,ScriptedLLM([completion('{}'),completion(tool_calls=[('a','append_record',{})]),completion('完成')])).run('追加')
    if phase=='during_tool': (tmp_path/'.desktop-slow').touch()
    process=subprocess.Popen([sys.executable,'-c',CHILD,str(tmp_path),phase],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    try:
        deadline=time.monotonic()+15
        while not (tmp_path/'.desktop-test-ready').exists() and time.monotonic()<deadline:
            if process.poll() is not None:pytest.fail(process.communicate()[1].decode(errors='replace'))
            time.sleep(.05)
        assert (tmp_path/'.desktop-test-ready').exists()
        process.kill();process.wait(timeout=5)
        store=CheckpointStore(tmp_path)
        if phase=='during_tool':
            assert not store.status()['resumable']
            assert (tmp_path/'effects.txt').read_text(encoding='utf-8')=='x'
        else:
            assert store.status()['resumable']
            llm=ScriptedLLM([completion('完成')])
            result=actor(tmp_path,llm).run('追加',resume_state=store.read())
            assert (result.steps,result.total_tokens,result.equivalent_tokens,result.stop_reason,result.answer)==(
                baseline.steps,baseline.total_tokens,baseline.equivalent_tokens,baseline.stop_reason,baseline.answer)
            assert (tmp_path/'effects.txt').read_bytes()==(baseline_root/'effects.txt').read_bytes()==b'x'
            assert len(llm.requests)==1
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=5)


def test_pages_notes_evidence_and_compactor_state_are_restored(tmp_path):
    client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,
        text='<html><body><article><h1>手册</h1><p>Alpha保持关闭。'+'正文说明。'*80+'</p></article></body></html>',
        headers={'content-type':'text/html'})))
    s=ResearchSession(); compactor=ContextCompactor()
    def record(event):
        if event.kind=='tool_result':compactor._last_after=123
    cp=CrashStore(tmp_path,lambda d:d['phase']=='loop' and d['state']['_steps']==2 and not d.get('pending'))
    llm=ScriptedLLM([completion('{}'),completion(tool_calls=[('f','fetch_url',{'url':'https://example.org/manual'})]),
                    completion(tool_calls=[('n','save_note',{'source_url':'https://example.org/manual','content':'Alpha保持关闭。'})])])
    registry=default_registry(tmp_path,session=s,http_client=client,fetch_fallback='none')
    with pytest.raises(Crash):
        Agent(llm,registry,task_dir=tmp_path,research_session=s,checkpoint=cp,compactor=compactor,on_event=record).run('读取手册')
    def unexpected(request):raise AssertionError('恢复不应重新联网')
    s2=ResearchSession(); pages=s2.pages; compactor2=ContextCompactor()
    registry2=default_registry(tmp_path,session=s2,http_client=httpx.Client(transport=httpx.MockTransport(unexpected)),fetch_fallback='none')
    new=Agent(ScriptedLLM([completion(tool_calls=[('r','read_notes',{})]),completion('完成')]),registry2,
              task_dir=tmp_path,research_session=s2,checkpoint=CheckpointStore(tmp_path),compactor=compactor2)
    result=new.run('读取手册',resume_state=cp.read())
    assert result.steps==4 and result.total_tokens==75
    assert s2.pages is pages and len(s2.notes)==1 and s2.evidence.records['web-1']==s.evidence.records['web-1']
    assert compactor2._last_after==123 and 'Alpha保持关闭' in result.messages[-2]['content']
    assert not registry2.execute('fetch_url',{'url':'https://example.org/manual'}).is_error


def test_desktop_resume_same_task_queue_api_and_cancel_preserve_usage(tmp_path):
    from desktop_app.manager import Manager
    from desktop_app.server import create_app
    from agent.config import Settings
    manager=Manager(tmp_path,start=False,settings=Settings(deepseek_api_key='fake-key'))
    try:
        sid=manager.store.create_session()['id']
        row=manager.submit(sid,'追加',OPTIONS); root=__import__('pathlib').Path(row['task_dir'])
        with pytest.raises(Crash):
            actor(root,ScriptedLLM([completion('{}'),completion(tool_calls=[('a','append_record',{})])]),
                  CrashStore(root,lambda d:d['phase']=='response')).run('追加')
        manager.store.set_run(row['id'],'interrupted')
        (root/'.desktop-stop').touch()
        with TestClient(create_app(manager,'token')) as client:
            assert client.post(f"/api/runs/{row['id']}/resume").status_code==401
            client.headers['Authorization']='Bearer token'
            detail=client.get(f"/api/runs/{row['id']}").json()
            assert detail['checkpoint']['resumable']
            resumed=client.post(f"/api/runs/{row['id']}/resume")
            assert resumed.status_code==200 and resumed.json()['state']=='resuming'
            assert resumed.json()['task_dir']==row['task_dir'] and resumed.json()['options']==row['options']
            assert len(manager.store.runs(sid))==1 and not (root/'.desktop-stop').exists()
            assert client.post(f"/api/runs/{row['id']}/resume").status_code==400
            cancelled=manager.stop(row['id'])
            assert cancelled['state']=='cancelled' and not cancelled['result']['usage_not_started']
    finally:manager.close()


def test_resume_usage_uses_appended_calls_without_reset_or_double_count(tmp_path):
    from desktop_app.usage import TraceUsageReader
    path=tmp_path/'trace.jsonl'
    path.write_text(json.dumps({'kind':'llm_call','prompt_tokens':10,'completion_tokens':5})+'\n'+
                    json.dumps({'kind':'run_end'})+'\n',encoding='utf-8')
    reader=TraceUsageReader(); assert reader.read(path)['finished']
    with path.open('a',encoding='utf-8') as f:f.write(json.dumps({'kind':'run_resume'})+'\n')
    current=reader.read(path)
    assert not current['finished'] and len(current['events'])==1
    with path.open('a',encoding='utf-8') as f:
        f.write(json.dumps({'kind':'llm_call','prompt_tokens':20,'completion_tokens':8})+'\n')
        f.write(json.dumps({'kind':'run_end'})+'\n')
    assert reader.read(path)['finished'] and len(reader.read(path)['events'])==2
