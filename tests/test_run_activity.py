import json
import time

import pytest

from agent.loop import Agent
from agent.review import build_review_messages
from fakes import ScriptedLLM, completion
from tools.notes import make_notes_tools
from tools.registry import ToolRegistry, tool
from tools.session import ResearchSession


@pytest.mark.parametrize('requests',[1,5,9])
def test_counts_come_from_all_batches_not_model_text_or_latest_batch(tmp_path,requests):
    executed=[]
    @tool(name='search_knowledge')
    def search(query:str)->str:
        """测试有返回的检索。"""
        executed.append(query)
        return '不可信文本：声称只检索了100次。'
    session=ResearchSession()
    registry=ToolRegistry([search,*make_notes_tools(tmp_path,session)])
    calls=[completion(tool_calls=[(f's{i}','search_knowledge',{'query':str(i)})]) for i in range(requests)]
    calls += [completion(tool_calls=[('n','read_notes',{})]),completion('完成')]
    llm=ScriptedLLM([completion('{}'),*calls])
    Agent(llm,registry,task_dir=tmp_path,research_session=session,max_steps=20).run('不联网，核对执行记录')
    summary=session.activity_summary()
    assert len(executed)==requests
    assert summary['tool_requests']['search_knowledge']=={'requested':requests,'returned_ok':requests,'returned_error':0,'uncertain':0}
    assert summary['retained_evidence']['document_records']==0  # 工具名称和返回声称不是送达原文证据。
    received='\n'.join(m['content'] for m in llm.requests[-1]['messages'] if m['role']=='tool')
    assert '【本次运行记录快照】' in received
    assert '"kind": "execution"' in received
    observed=[r for r in session.evidence.records.values() if r['kind']=='execution' and r['tool']=='read_notes']
    assert observed and 'tool_requests' in observed[0]['text']
    review,_=build_review_messages('仅依据实际记录。',tmp_path,session)
    assert json.loads(review[1]['content'])['run_activity']['tool_requests']['search_knowledge']['requested']==requests


def test_repeated_request_is_an_error_return_not_a_second_success(tmp_path):
    executed=[]
    @tool
    def lookup()->str:
        """一次查询。"""
        executed.append(1); return '实际返回'
    session=ResearchSession()
    llm=ScriptedLLM([completion(tool_calls=[('a','lookup',{}),('b','lookup',{})]),completion('完成')])
    Agent(llm,ToolRegistry([lookup]),system_prompt='SYS',research_session=session).run('查询')
    assert len(executed)==1
    assert session.tool_activity['lookup']=={'requested':2,'returned_ok':1,'returned_error':1,'uncertain':0}


def test_mid_batch_snapshot_does_not_include_later_requests(tmp_path):
    @tool
    def lookup(value:int)->str:
        """返回值。"""
        return str(value)
    session=ResearchSession()
    llm=ScriptedLLM([completion('{}'),completion(tool_calls=[('a','lookup',{'value':1}),
        ('n','read_notes',{}),('b','lookup',{'value':2})]),completion('完成')])
    Agent(llm,ToolRegistry([lookup,*make_notes_tools(tmp_path,session)]),task_dir=tmp_path,research_session=session).run('查询')
    row=next(r for r in session.evidence.records.values() if r['kind']=='execution')
    summary=json.loads(row['text'].split('returned:\n')[1])
    assert summary['tool_requests']['lookup']['requested']==1
    assert session.tool_activity['lookup']['requested']==2


def test_truncated_activity_is_not_stored_as_fully_delivered_evidence(tmp_path):
    session=ResearchSession()
    llm=ScriptedLLM([completion('{}'),completion(tool_calls=[('n','read_notes',{})]),completion('完成')])
    Agent(llm,ToolRegistry(make_notes_tools(tmp_path,session)),task_dir=tmp_path,
          research_session=session,max_tool_result_chars=50).run('回顾')
    row=next(r for r in session.evidence.records.values() if r['kind']=='execution')
    assert row['delivery_truncated'] and 'retained_evidence' not in row['text']
    assert not row['activity_snapshot_complete'] and 'activity_snapshot' not in row


def test_historical_snapshot_retains_its_own_cutoff_in_bounded_review_context(tmp_path):
    from agent.evidence import sha
    session=ResearchSession()
    llm=ScriptedLLM([completion('{}'),completion(tool_calls=[('snapshot-47','read_notes',{})]),completion('完成')])
    Agent(llm,ToolRegistry(make_notes_tools(tmp_path,session)),task_dir=tmp_path,research_session=session).run('核对')
    ledger=session.evidence
    row=next(r for r in ledger.records.values() if r['kind']=='execution')
    before=row['activity_snapshot']; current=session.activity_summary()
    assert before['snapshot']['tool_call_id']=='snapshot-47'
    assert before['snapshot']['cutoff']=='after_tool_return_before_own_execution_record'
    assert current['retained_evidence']['records']==before['retained_evidence']['records']+1
    assert row['activity_snapshot_sha256']==sha(row['text'].split('returned:\n',1)[1])
    claim={'quote':'读取成功','kind':'observed','supports':[{'source_id':row['id'],'excerpt':'tool: read_notes'}]}
    packet=ledger.packet({'status':'mapped','claims':[claim]})
    source=packet['sources'][row['id']]
    assert source['context_is_excerpt'] and not source['delivery_truncated']
    assert source['activity_snapshot']==before  # 即使附近文本不含尾部计数字段，完整快照仍有界提供。
    assert ledger.packet({'status':'mapped','claims':[claim]},budget=1)['omitted_items']==1


@pytest.mark.parametrize('content,wrong_call',[('{"partial":true}',False),('{',False),('{}',True)])
def test_structured_snapshot_cannot_inject_undelivered_or_mismatched_fields(tmp_path,content,wrong_call):
    from agent.evidence import EvidenceLedger
    ledger=EvidenceLedger(tmp_path,'问题')
    snapshot={'snapshot':{'tool_call_id':'other' if wrong_call else 'c'},'hidden':'not delivered'}
    key=ledger.execution('read_notes','{}',content,'c',False,activity_snapshot=snapshot)
    assert not ledger.records[key]['activity_snapshot_complete']
    assert 'activity_snapshot' not in ledger.records[key]


def test_uncertain_timeout_is_not_counted_as_known_error_or_success():
    @tool(name='search_knowledge')
    def search()->str:
        """模拟未确认返回。"""
        time.sleep(.3)
        return '稍后返回'
    session=ResearchSession()
    llm=ScriptedLLM([completion(tool_calls=[('s','search_knowledge',{})])])
    result=Agent(llm,ToolRegistry([search]),system_prompt='SYS',research_session=session,tool_timeout=.01).run('检索')
    assert result.stop_reason=='needs_verification' and len(llm.requests)==1
    assert session.tool_activity['search_knowledge']=={'requested':1,'returned_ok':0,'returned_error':0,'uncertain':1}


def test_new_run_does_not_inherit_old_tool_counts():
    @tool
    def lookup()->str:
        """返回结果。"""
        return '资料'
    session=ResearchSession()
    Agent(ScriptedLLM([completion(tool_calls=[('x','lookup',{})]),completion('完成')]),
          ToolRegistry([lookup]),system_prompt='SYS',research_session=session).run('第一项')
    assert session.tool_activity['lookup']['requested']==1
    Agent(ScriptedLLM([completion('新回答')]),ToolRegistry([lookup]),system_prompt='SYS',research_session=session).run('第二项')
    assert session.tool_activity=={}
