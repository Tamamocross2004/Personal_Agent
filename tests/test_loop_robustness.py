"""阶段 3 故障注入测试：故意制造各种异常情况，确认 Agent 的反应都合理。

每个测试对应 agent/loop.py 文档里列出的一项保护。全部用剧本化假模型，不花钱、不联网。
"""

import time

import openai
import pytest
from fakes import ScriptedLLM, api_error, completion

from agent.errors import classify_api_error
from agent.loop import Agent
from agent.prompts import (
    CONTINUE_PROMPT,
    EMPTY_REPLY_PROMPT,
    REDACTED_TOOL_RESULT,
    REPEAT_BLOCKED_RESULT,
    TRUNCATED_TOOL_CALL_RESULT,
    finalize_prompt,
)
from tools import ToolRegistry, tool

CALLS = {"flaky": 0}


@tool
def echo(text: str) -> str:
    """原样返回。"""
    return text


@tool
def flaky(x: int) -> str:
    """前两次失败，第三次成功。"""
    CALLS["flaky"] += 1
    if CALLS["flaky"] < 3:
        raise RuntimeError("暂时失败")
    return "ok"


@tool
def slow(seconds: float) -> str:
    """睡一会儿。"""
    time.sleep(seconds)
    return "睡醒了"


@tool
def interrupt() -> str:
    """模拟用户在工具执行期间按下 Ctrl+C。"""
    raise KeyboardInterrupt


@pytest.fixture(autouse=True)
def reset_counters():
    CALLS["flaky"] = 0


@pytest.fixture
def registry():
    return ToolRegistry([echo, flaky, slow])


def run(responses, registry, **kw):
    events = []
    llm = ScriptedLLM(responses)
    agent = Agent(llm, registry, system_prompt="SYS", on_event=events.append, **kw)
    return agent.run("q"), llm, events


def notices(events):
    return [e.data["text"] for e in events if e.kind == "notice"]


def tool_results(events):
    return [e.data for e in events if e.kind == "tool_result"]


# ---------------------------------------------------------------------------
# 重复调用检测
# ---------------------------------------------------------------------------


def test_repeat_of_successful_call_is_blocked_not_executed(registry):
    res, llm, events = run(
        [
            completion(tool_calls=[("c1", "echo", {"text": "a"})]),
            # 参数顺序 / 空格不同也算同一个调用
            completion(tool_calls=[("c2", "echo", '{ "text":"a" }')]),
            completion("done"),
        ],
        registry,
    )
    assert res.stop_reason == "final_answer"
    r = tool_results(events)
    assert r[0]["content"] == "a" and not r[0]["is_error"]
    assert r[1]["content"] == REPEAT_BLOCKED_RESULT and r[1]["is_error"]
    assert any("重复调用" in n for n in notices(events))


def test_failed_call_may_be_retried_up_to_three_times(registry):
    res, _, events = run(
        [completion(tool_calls=[(f"c{i}", "flaky", {"x": 1})]) for i in range(3)] + [completion("ok")],
        registry,
    )
    assert [r["is_error"] for r in tool_results(events)] == [True, True, False]
    assert CALLS["flaky"] == 3
    assert res.stop_reason == "final_answer"


def test_persistent_repeats_trigger_loop_detected_and_finalize(registry):
    looping = [completion(tool_calls=[(f"c{i}", "echo", {"text": "same"})]) for i in range(4)]
    res, llm, events = run(looping + [completion("收尾回答")], registry, max_blocked_repeats=3)

    assert res.stop_reason == "loop_detected"
    assert res.answer == "收尾回答"
    assert res.steps == 4  # 第 1 次执行，第 2-4 次被拦截，拦截 3 次后收尾
    last = llm.requests[-1]
    assert last["tools"] is None
    assert last["messages"][-1]["content"] == finalize_prompt("loop_detected")


# ---------------------------------------------------------------------------
# token 预算
# ---------------------------------------------------------------------------


def test_budget_warning_injected_once_at_80_percent(registry):
    res, llm, events = run(
        [
            completion(tool_calls=[("c1", "echo", {"text": "1"})], prompt_tokens=850, completion_tokens=0),
            completion(tool_calls=[("c2", "echo", {"text": "2"})], prompt_tokens=50, completion_tokens=0),
            completion("done", prompt_tokens=10, completion_tokens=0),
        ],
        registry,
        token_budget=1000,
    )
    assert res.stop_reason == "final_answer"
    assert res.total_tokens == 910
    warnings = [m for m in llm.requests[-1]["messages"] if "已使用 850 / 1000" in str(m.get("content"))]
    assert len(warnings) == 1  # 只提醒一次
    assert warnings[0]["role"] == "user"


def test_budget_exhausted_forces_finalize(registry):
    res, llm, _ = run(
        [
            completion(tool_calls=[("c1", "echo", {"text": "1"})], prompt_tokens=1200),
            completion("预算内的收尾"),
        ],
        registry,
        token_budget=1000,
    )
    assert res.stop_reason == "budget"
    assert res.answer == "预算内的收尾"
    assert len(llm.requests) == 2
    assert llm.requests[-1]["tools"] is None
    assert llm.requests[-1]["messages"][-1]["content"] == finalize_prompt("budget")


# ---------------------------------------------------------------------------
# 工具超时
# ---------------------------------------------------------------------------


def test_slow_tool_times_out_and_loop_continues(registry):
    started = time.perf_counter()
    res, _, events = run(
        [completion(tool_calls=[("c1", "slow", {"seconds": 3})]), completion("换个方法")],
        registry,
        tool_timeout=0.3,
    )
    assert time.perf_counter() - started < 2  # 没有傻等 3 秒
    r = tool_results(events)[0]
    assert r["is_error"] and "超时" in r["content"]
    assert res.stop_reason == "final_answer"


# ---------------------------------------------------------------------------
# 回复被截断（finish_reason = length）
# ---------------------------------------------------------------------------


def test_truncated_tool_call_is_not_executed(tmp_path):
    from tools.report import make_write_report

    reg = ToolRegistry([make_write_report(tmp_path)])
    res, llm, events = run(
        [
            # 写到一半被截断的报告（JSON 不完整）
            completion(tool_calls=[("c1", "write_report", '{"content": "# 报告\\n一半')], finish_reason="length"),
            completion(tool_calls=[("c2", "write_report", {"content": "# 报告"})]),
            completion("done"),
        ],
        reg,
    )
    first = tool_results(events)[0]
    assert first["content"] == TRUNCATED_TOOL_CALL_RESULT and first["is_error"]
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "# 报告"  # 只有第二次真正写入
    assert res.stop_reason == "final_answer"


def test_truncated_text_answer_is_continued_and_joined(registry):
    res, llm, events = run(
        [completion("前半段，", finish_reason="length"), completion("后半段。")],
        registry,
    )
    assert res.answer == "前半段，后半段。"
    assert llm.requests[1]["messages"][-1] == {"role": "user", "content": CONTINUE_PROMPT}
    assert any("截断" in n for n in notices(events))


# ---------------------------------------------------------------------------
# 空回复
# ---------------------------------------------------------------------------


def test_single_empty_reply_gets_a_nudge(registry):
    res, llm, _ = run([completion(None), completion("好了")], registry)
    assert res.answer == "好了"
    sent = llm.requests[1]["messages"]
    assert sent[-1] == {"role": "user", "content": EMPTY_REPLY_PROMPT}
    assert all(m["role"] != "assistant" for m in sent)  # 空回复没有进入历史


def test_repeated_empty_replies_stop_with_error(registry):
    res, _, _ = run([completion(""), completion("  ")], registry)
    assert res.stop_reason == "error"
    assert "空回复" in res.error


# ---------------------------------------------------------------------------
# 接口错误：内容审核、上下文超长、鉴权
# ---------------------------------------------------------------------------


def test_content_filter_redacts_last_tool_results_and_retries(registry):
    res, llm, events = run(
        [
            completion(tool_calls=[("c1", "echo", {"text": "敏感网页内容"})]),
            api_error(openai.BadRequestError, "Content Exists Risk"),
            completion("换了来源"),
        ],
        registry,
    )
    assert res.stop_reason == "final_answer"
    retried = llm.requests[2]["messages"]
    assert retried[-1] == {"role": "tool", "tool_call_id": "c1", "content": REDACTED_TOOL_RESULT}
    assert any("内容审核" in n for n in notices(events))


def test_content_filter_without_tool_results_stops(registry):
    res, _, _ = run([api_error(openai.BadRequestError, "data_inspection_failed")], registry)
    assert res.stop_reason == "content_filter"


def test_content_filter_finish_reason(registry):
    res, _, _ = run([completion(None, finish_reason="content_filter")], registry)
    assert res.stop_reason == "content_filter"


def test_context_overflow_stops_cleanly(registry):
    err = api_error(
        openai.BadRequestError, "This model's maximum context length is 131072 tokens. However, you requested 140000"
    )
    res, _, events = run([completion(tool_calls=[("c1", "echo", {"text": "x"})]), err], registry)
    assert res.stop_reason == "context_overflow"
    assert "maximum context length" in res.error
    assert events[-1].kind == "run_end" and events[-1].data["stop_reason"] == "context_overflow"


def test_auth_error_stops_with_error(registry):
    res, _, _ = run([api_error(openai.AuthenticationError, "invalid api key", 401)], registry)
    assert res.stop_reason == "error"
    assert "AuthenticationError" in res.error


def test_finalize_call_failure_is_reported(registry):
    res, _, _ = run(
        [completion(tool_calls=[("c1", "echo", {"text": "1"})]), api_error(openai.InternalServerError, "down", 500)],
        registry,
        max_steps=1,
    )
    assert res.stop_reason == "max_steps"
    assert res.answer == ""
    assert "收尾调用失败" in res.error


# ---------------------------------------------------------------------------
# Ctrl+C
# ---------------------------------------------------------------------------


def test_keyboard_interrupt_during_llm_call(registry):
    res, _, events = run([completion(tool_calls=[("c1", "echo", {"text": "1"})]), KeyboardInterrupt()], registry)
    assert res.stop_reason == "interrupted"
    assert res.steps == 2
    assert len(res.messages) == 4  # system, user, assistant(tool_calls), tool —— 已发生的都保留
    assert events[-1].kind == "run_end"


def test_keyboard_interrupt_inside_tool_is_not_swallowed_as_tool_error():
    # 工具线程里的 KeyboardInterrupt 不会传到主线程，这里只确认不会让 Agent 崩溃
    res, _, events = run(
        [completion(tool_calls=[("c1", "interrupt", "{}")]), completion("继续")], ToolRegistry([interrupt])
    )
    assert tool_results(events)[0]["is_error"]
    assert res.stop_reason == "final_answer"


# ---------------------------------------------------------------------------
# 事件与统计
# ---------------------------------------------------------------------------


def test_run_start_and_end_events_and_token_total(registry):
    res, _, events = run(
        [
            completion(tool_calls=[("c1", "echo", {"text": "1"})], prompt_tokens=100, completion_tokens=10),
            completion("ok", prompt_tokens=200, completion_tokens=20),
        ],
        registry,
    )
    assert events[0].kind == "run_start" and events[0].data["tools"] == ["echo", "flaky", "slow"]
    llm_calls = [e.data for e in events if e.kind == "llm_call"]
    assert [(c["prompt_tokens"], c["completion_tokens"]) for c in llm_calls] == [(100, 10), (200, 20)]
    end = events[-1].data
    assert end["stop_reason"] == "final_answer" and end["total_tokens"] == 330 == res.total_tokens
    assert "seconds" in tool_results(events)[0]


# ---------------------------------------------------------------------------
# 错误分类
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cls,message,expected",
    [
        (openai.BadRequestError, "Content Exists Risk", "content_filter"),
        (openai.BadRequestError, "data_inspection_failed: Input data may contain inappropriate content.", "content_filter"),
        (openai.BadRequestError, '{"code":"1301","message":"系统检测到输入或生成内容可能包含不安全或敏感内容"}', "content_filter"),
        (openai.BadRequestError, "This model's maximum context length is 65536 tokens", "context_overflow"),
        (openai.BadRequestError, "Input tokens exceed the configured limit of 131072 tokens", "context_overflow"),
        (openai.BadRequestError, "Range of input length should be [1, 129024]", "context_overflow"),
        (openai.BadRequestError, "输入长度超出限制", "context_overflow"),
        (openai.BadRequestError, "Model Not Exist", "error"),
        (openai.AuthenticationError, "invalid api key", "auth"),
        (openai.NotFoundError, "model not found", "error"),
    ],
)
def test_classify_api_error(cls, message, expected):
    assert classify_api_error(api_error(cls, message)) == expected
