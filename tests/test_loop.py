"""Agent 主循环测试：用剧本化的假模型驱动，验证消息协议和控制流。"""

import pytest
from fakes import ScriptedLLM, completion

from agent.loop import Agent, pretty_arguments
from agent.prompts import MAX_STEPS_PROMPT
from tools import ToolRegistry, tool


@tool
def add(a: int, b: int) -> int:
    """两数相加。"""
    return a + b


@tool
def echo(text: str) -> str:
    """原样返回。"""
    return text


@pytest.fixture
def registry():
    return ToolRegistry([add, echo])


def make_agent(llm, registry, **kw):
    events = []
    agent = Agent(llm, registry, system_prompt="SYS", on_event=events.append, **kw)
    return agent, events


def test_direct_answer_without_tools(registry):
    llm = ScriptedLLM([completion("你好！")])
    agent, events = make_agent(llm, registry)

    res = agent.run("hi")

    assert res.answer == "你好！"
    assert (res.steps, res.stop_reason) == (1, "final_answer")
    assert res.tool_calls == []
    # 第一次请求：system + user，并且带上了工具说明书
    req = llm.requests[0]
    assert req["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "hi"},
    ]
    assert [t["function"]["name"] for t in req["tools"]] == ["add", "echo"]
    assert [e.kind for e in events] == ["step", "final"]


def test_single_tool_call_then_answer(registry):
    llm = ScriptedLLM(
        [
            completion(tool_calls=[("call_1", "add", {"a": 2, "b": 3})]),
            completion("结果是 5"),
        ]
    )
    agent, events = make_agent(llm, registry)

    res = agent.run("2+3?")

    assert res.answer == "结果是 5"
    assert res.steps == 2
    assert res.tool_calls == [("add", '{"a": 2, "b": 3}')]

    # 第二次请求时模型应该看到：它自己的 tool_calls 消息 + 对应 id 的工具结果
    second = llm.requests[1]["messages"]
    assistant, tool_msg = second[2], second[3]
    assert assistant["role"] == "assistant"
    assert assistant["content"] is None
    assert assistant["tool_calls"] == [
        {"id": "call_1", "type": "function", "function": {"name": "add", "arguments": '{"a": 2, "b": 3}'}}
    ]
    assert tool_msg == {"role": "tool", "tool_call_id": "call_1", "content": "5"}
    assert [e.kind for e in events] == ["step", "tool_call", "tool_result", "step", "final"]


def test_parallel_tool_calls_each_get_a_result_in_order(registry):
    llm = ScriptedLLM(
        [
            completion(
                content="我先同时算两个。",
                tool_calls=[("c1", "add", {"a": 1, "b": 1}), ("c2", "echo", {"text": "hi"})],
            ),
            completion("done"),
        ]
    )
    agent, events = make_agent(llm, registry)

    agent.run("q")

    msgs = llm.requests[1]["messages"]
    assert msgs[2]["content"] == "我先同时算两个。"
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    assert [(m["tool_call_id"], m["content"]) for m in tool_msgs] == [("c1", "2"), ("c2", "hi")]
    # 调用前的说明文字作为 thought 事件展示
    assert events[1].kind == "thought" and events[1].data["text"] == "我先同时算两个。"


def test_tool_error_is_fed_back_and_loop_continues(registry):
    llm = ScriptedLLM(
        [
            completion(tool_calls=[("c1", "add", '{"a": 1')]),  # 坏 JSON
            completion(tool_calls=[("c2", "nope", "{}")]),  # 不存在的工具
            completion(tool_calls=[("c3", "add", {"a": 1, "b": 2})]),  # 修正后成功
            completion("3"),
        ]
    )
    agent, events = make_agent(llm, registry)

    res = agent.run("q")

    assert res.answer == "3"
    results = [e.data for e in events if e.kind == "tool_result"]
    assert [r["is_error"] for r in results] == [True, True, False]
    assert "不是合法的 JSON" in results[0]["content"]
    assert "不存在名为 'nope'" in results[1]["content"]


def test_max_steps_forces_final_answer_without_tools(registry):
    looping = [completion(tool_calls=[(f"c{i}", "echo", {"text": "again"})]) for i in range(3)]
    llm = ScriptedLLM(looping + [completion("只能先回答到这里")])
    agent, events = make_agent(llm, registry, max_steps=3)

    res = agent.run("q")

    assert res.stop_reason == "max_steps"
    assert res.steps == 3
    assert res.answer == "只能先回答到这里"
    assert len(llm.requests) == 4
    last = llm.requests[-1]
    assert last["tools"] is None  # 收尾调用不再提供工具
    assert last["messages"][-1] == {"role": "user", "content": MAX_STEPS_PROMPT}
    assert events[-1].data["stop_reason"] == "max_steps"


def test_long_tool_result_is_truncated(registry):
    llm = ScriptedLLM(
        [completion(tool_calls=[("c1", "echo", {"text": "x" * 100})]), completion("ok")]
    )
    agent, _ = make_agent(llm, registry, max_tool_result_chars=10)

    agent.run("q")

    content = llm.requests[1]["messages"][-1]["content"]
    assert content.startswith("x" * 10)
    assert "已截断" in content and "100" in content


def test_extra_fields_from_provider_are_not_sent_back(registry):
    """模拟厂商返回额外字段（如 reasoning_content），回传历史时应被丢弃。"""
    resp = completion("ok")
    resp.choices[0].message.reasoning_content = "内部推理"  # type: ignore[attr-defined]
    llm = ScriptedLLM([resp])
    agent, _ = make_agent(llm, registry)

    res = agent.run("q")

    assert res.messages[-1] == {"role": "assistant", "content": "ok"}


def test_without_event_handler(registry):
    llm = ScriptedLLM([completion("ok")])
    assert Agent(llm, registry).run("q").answer == "ok"


@pytest.mark.parametrize(
    "raw,expected",
    [('{"a": 1, "b": "x"}', "a=1, b='x'"), ("", ""), ("{bad", "{bad"), ("[1]", "[1]")],
)
def test_pretty_arguments(raw, expected):
    assert pretty_arguments(raw) == expected
