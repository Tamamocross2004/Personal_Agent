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
    plan = generate_plan("问题", chat_from(llm), tmp_path, today="2026-10-17")
    # "写报告"是流程步骤，不是报告要回答的方面，被过滤掉
    assert [t["title"] for t in plan["tasks"]] == ["搜索工具调用能力", "搜索价格"]
    assert [t["status"] for t in plan["tasks"]] == ["pending"] * 2
    assert all(t["note"] == "" for t in plan["tasks"])
    assert load_plan(tmp_path) == plan
    assert llm.requests[0]["response_format"] == {"type": "json_object"}
    assert "今天是 2026-10-17" in llm.requests[0]["messages"][0]["content"]  # 模型知道今天的日期


def test_planner_prompt_asks_for_dimensions_not_process():
    from agent.planner import PLANNER_PROMPT

    text = PLANNER_PROMPT.format(today="x")
    assert "不要按\"怎么做\"拆" in text
    assert "写报告" in text and "不要单独列为子任务" in text


def test_normalize_tasks():
    from agent.planner import MAX_TASKS, normalize_tasks

    raw = [
        {"id": "a", "title": "价格"},
        {"title": ""},
        "不是 dict",
        {"id": "x", "title": "撰写对比报告，总结各自优势与不足"},
        {"title": "报告中常见的误区"},  # 不是以"写报告"开头，保留
    ] + [{"title": f"维度 {i}"} for i in range(10)]
    tasks = normalize_tasks(raw)
    assert len(tasks) == MAX_TASKS
    assert [t["id"] for t in tasks] == [f"task-{i}" for i in range(1, MAX_TASKS + 1)]  # 重新编号
    assert tasks[0]["title"] == "价格" and tasks[1]["title"] == "报告中常见的误区"
    assert len(normalize_tasks(None)) == 3  # 兜底计划
    assert len(normalize_tasks([{"title": "写报告"}])) == 3


def test_unfinished_tasks():
    from agent.planner import unfinished_tasks

    plan = {"tasks": [{"id": "1", "status": "done"}, {"id": "2", "status": "in_progress"}, {"id": "3"}]}
    assert [t["id"] for t in unfinished_tasks(plan)] == ["2", "3"]
    assert unfinished_tasks(None) == []


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


def test_steps_warning_lists_unfinished_plan_tasks(tmp_path):
    from tools.basic import get_current_time

    calls = [completion(tool_calls=[(f"c{i}", "get_current_time", {})]) for i in range(2)]
    update = completion(tool_calls=[("u1", "update_plan", {"task_id": "task-1", "status": "done"})])
    llm = ScriptedLLM([completion(PLAN_JSON), update, *calls, completion("完成")])
    reg = ToolRegistry([make_update_plan(tmp_path), get_current_time])
    Agent(llm, reg, task_dir=tmp_path, max_steps=6, steps_warn_remaining=3).run("问题")

    warning = llm.requests[4]["messages"][-1]  # 第 3 步结束后（剩 3 步）提醒
    assert warning["role"] == "user" and "只剩 3 轮" in warning["content"]
    assert "task-2 搜索价格" in warning["content"] and "task-1" not in warning["content"]  # 只列未完成的
