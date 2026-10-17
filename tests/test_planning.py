"""阶段 5：规划（planner）与 update_plan 工具的测试。"""

import json

import openai
from fakes import ScriptedLLM, api_error, completion

from agent.loop import Agent
from agent.planner import format_plan_for_prompt, generate_plan, load_plan
from tools import ToolRegistry
from tools.plan import make_update_plan

PLAN_JSON = json.dumps(
    {
        "tasks": [
            {"id": "task-1", "title": "搜索工具调用能力"},
            {"id": "task-2", "title": "搜索价格"},
            {"id": "task-3", "title": "写报告"},
        ]
    },
    ensure_ascii=False,
)


def chat_from(llm):
    return lambda messages, **kw: llm.chat(messages, **kw)


def test_generate_plan_saves_and_fills_defaults(tmp_path):
    llm = ScriptedLLM([completion(PLAN_JSON)])
    plan = generate_plan("问题", chat_from(llm), tmp_path)
    assert [t["status"] for t in plan["tasks"]] == ["pending"] * 3
    assert all(t["note"] == "" for t in plan["tasks"])
    assert load_plan(tmp_path) == plan
    assert llm.requests[0]["response_format"] == {"type": "json_object"}


def test_generate_plan_falls_back_on_bad_json(tmp_path):
    for bad in ("不是 JSON", "[1, 2]"):
        llm = ScriptedLLM([completion(bad)])
        plan = generate_plan("问题", chat_from(llm), tmp_path)
        assert len(plan["tasks"]) == 3


def test_update_plan_tool(tmp_path):
    generate_plan("问题", chat_from(ScriptedLLM([completion(PLAN_JSON)])), tmp_path)
    reg = ToolRegistry([make_update_plan(tmp_path)])
    r = reg.execute("update_plan", {"task_id": "task-1", "status": "done", "note": "已完成"})
    assert not r.is_error
    assert "✅ task-1" in r.content and "已完成" in r.content  # 返回值带回最新计划
    assert load_plan(tmp_path)["tasks"][0]["status"] == "done"

    bad = reg.execute("update_plan", {"task_id": "task-9", "status": "done"})
    assert bad.is_error and "task-9" in bad.content
    bad = reg.execute("update_plan", {"task_id": "task-1", "status": "bogus"})
    assert bad.is_error


def test_format_plan():
    text = format_plan_for_prompt({"tasks": [{"id": "task-1", "title": "A", "status": "done", "note": "ok"}]})
    assert "task-1" in text and "A" in text and "ok" in text
    assert format_plan_for_prompt({}) == ""


def test_agent_plans_first_and_keeps_system_prefix_stable(tmp_path):
    llm = ScriptedLLM(
        [
            completion(PLAN_JSON),
            completion(tool_calls=[("c1", "update_plan", {"task_id": "task-1", "status": "in_progress"})]),
            completion("完成"),
        ]
    )
    events = []
    agent = Agent(llm, ToolRegistry([make_update_plan(tmp_path)]), task_dir=tmp_path, on_event=events.append)
    result = agent.run("问题")

    assert result.stop_reason == "final_answer"
    plan_req, main1, main2 = llm.requests
    assert plan_req["tools"] is None and plan_req["response_format"]
    # 主循环的 system 消息带计划，且之后不再改动（改动开头会让前缀缓存失效）
    assert "task-1" in main1["messages"][0]["content"]
    assert main1["messages"][0] == main2["messages"][0]
    # 进展通过 tool 结果追加到末尾
    assert "🔄 task-1" in main2["messages"][-1]["content"]
    assert any(e.kind == "plan" for e in events)


def test_agent_continues_without_plan_when_planning_fails(tmp_path):
    llm = ScriptedLLM([api_error(openai.APIConnectionError, "down", 500), completion("直接回答")])
    agent = Agent(llm, ToolRegistry([make_update_plan(tmp_path)]), task_dir=tmp_path)
    result = agent.run("问题")
    assert result.stop_reason == "final_answer"
    assert "当前计划" not in llm.requests[-1]["messages"][0]["content"]


def test_agent_without_task_dir_does_not_plan():
    llm = ScriptedLLM([completion("hi")])
    Agent(llm, ToolRegistry([])).run("问题")
    assert len(llm.requests) == 1
