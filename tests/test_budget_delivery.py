from types import SimpleNamespace

from agent.loop import Agent
from fakes import ScriptedLLM, completion
from tools.registry import ToolRegistry, tool
from tools.report import make_write_report
from tools.session import ResearchSession


@tool
def echo(text:str)->str:
    """返回材料。"""
    return text


def test_insufficient_planning_input_is_not_dispatched(tmp_path):
    llm=ScriptedLLM([])
    result=Agent(llm,ToolRegistry([]),task_dir=tmp_path,token_budget=1).run('测试')
    assert result.stop_reason=='budget' and not llm.requests
    assert result.total_tokens==0 and result.answer==''


def test_output_is_limited_by_remaining_estimate_and_provider_setting():
    llm=ScriptedLLM([completion('完成')]); llm.config=SimpleNamespace(max_tokens=100)
    result=Agent(llm,ToolRegistry([]),system_prompt='简答',token_budget=3000).run('只回答完成')
    assert result.stop_reason=='final_answer' and llm.requests[0]['max_tokens']==100
    llm=ScriptedLLM([completion('完成')])
    Agent(llm,ToolRegistry([]),system_prompt='简答',token_budget=500).run('完成')
    assert 64<=llm.requests[0]['max_tokens']<250


def test_actual_overrun_still_counted_and_no_unfunded_fallback():
    llm=ScriptedLLM([completion(tool_calls=[('e','echo',{'text':'材料'})],prompt_tokens=11000)])
    result=Agent(llm,ToolRegistry([echo]),token_budget=10000,max_steps=1,wrapup_max_calls=0).run('读取')
    assert result.stop_reason=='budget' and len(llm.requests)==1
    assert result.equivalent_tokens==11010 and result.total_tokens==11005


def test_delivery_reserve_moves_to_wrapup_before_another_research_call(tmp_path):
    events=[]; session=ResearchSession()
    llm=ScriptedLLM([completion('{}'),completion('额度不足，材料未完善。')])
    agent=Agent(llm,ToolRegistry([echo,make_write_report(tmp_path,session)]),
        task_dir=tmp_path,token_budget=15000,review_reports=True,research_session=session,on_event=events.append)
    result=agent.run('分析现有材料')
    assert result.stop_reason=='budget' and len(llm.requests)==2
    assert any('交付预留' in e.data.get('text','') for e in events)
    assert '收尾' in llm.requests[-1]['messages'][-1]['content']
    assert not (tmp_path/'report.md').exists()
