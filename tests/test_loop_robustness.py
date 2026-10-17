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
    assert "tool_choice" not in last
    assert last["messages"][-1]["content"] == finalize_prompt("loop_detected")


# ---------------------------------------------------------------------------
# token 预算
# ---------------------------------------------------------------------------


def test_budget_warning_then_projected_overrun_finalizes(registry):
    """（无缓存时等效 token = 输入 + 2×输出）调用 1 后累计 300；调用 2 后累计 900（≥80%）→ 提醒；
    第 3 步预估：上次输入 600 按缓存价 60 + 新增部分全价 → 900 + 60 + 新增 > 1000 → 不再开始新一轮，直接收尾。"""
    res, llm, events = run(
        [
            completion(tool_calls=[("c1", "echo", {"text": "1"})], prompt_tokens=300, completion_tokens=0),
            completion(tool_calls=[("c2", "echo", {"text": "2"})], prompt_tokens=600, completion_tokens=0),
            completion("收尾", prompt_tokens=620, completion_tokens=0),
        ],
        registry,
        token_budget=1000,
    )
    assert res.stop_reason == "budget"
    assert res.answer == "收尾"
    final_msgs = llm.requests[-1]["messages"]
    warnings = [m for m in final_msgs if "已使用 900 / 1000" in str(m.get("content"))]
    assert len(warnings) == 1 and warnings[0]["role"] == "user"  # 只提醒一次
    assert final_msgs[-1]["content"] == finalize_prompt("budget")
    assert "tool_choice" not in llm.requests[-1]


def _cached_completion(content=None, tool_calls=None, prompt=0, cached=0, out=0):
    resp = completion(content, tool_calls=tool_calls, prompt_tokens=prompt, completion_tokens=out)
    usage = type(resp.usage).model_validate(
        {"prompt_tokens": prompt, "completion_tokens": out, "total_tokens": prompt + out, "prompt_cache_hit_tokens": cached}
    )
    return resp.model_copy(update={"usage": usage})


def test_budget_counts_cached_tokens_at_discount(registry):
    """阶段 4 实测的情形：输入大部分命中缓存。按原始 token 早就超预算了，按等效 token 还很宽裕。"""
    responses = [
        _cached_completion(tool_calls=[(f"c{i}", "echo", {"text": str(i)})], prompt=1000, cached=900, out=10)
        for i in range(5)
    ] + [_cached_completion("done", prompt=1000, cached=900, out=50)]
    res, _, events = run(responses, registry, token_budget=2000)

    assert res.stop_reason == "final_answer"
    assert res.total_tokens == 6 * 1000 + 5 * 10 + 50  # 原始 6100，远超 2000
    # 等效：每次 100×1 + 900×0.1 + 输出×2
    assert res.equivalent_tokens == 5 * (100 + 90 + 20) + (100 + 90 + 100)
    calls = [e.data for e in events if e.kind == "llm_call"]
    assert calls[0]["equivalent_tokens"] == 210
    assert events[-1].data["equivalent_tokens"] == res.equivalent_tokens


def test_custom_token_weights(registry):
    from agent.loop import TokenWeights

    w = TokenWeights(uncached=1, cached=1, output=1)  # 等同于原始 token 计数
    res, _, _ = run([_cached_completion("x", prompt=1000, cached=900, out=10)], registry, token_weights=w)
    assert res.equivalent_tokens == res.total_tokens == 1010


def test_budget_allows_normal_finish(registry):
    res, _, _ = run(
        [
            completion(tool_calls=[("c1", "echo", {"text": "1"})], prompt_tokens=100, completion_tokens=0),
            completion("done", prompt_tokens=150, completion_tokens=0),
        ],
        registry,
        token_budget=1000,
    )
    assert res.stop_reason == "final_answer" and res.total_tokens == 250


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
    assert "tool_choice" not in llm.requests[-1]
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
    assert all(c["cached_tokens"] == 0 for c in llm_calls)
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


# ---------------------------------------------------------------------------
# 缓存命中统计
# ---------------------------------------------------------------------------


def test_cached_tokens_from_openai_and_deepseek_fields():
    from agent.loop import _cached_tokens

    std = completion("x").usage.model_copy(update={"prompt_tokens_details": {"cached_tokens": 70}})
    std = type(completion("x").usage).model_validate(
        {"prompt_tokens": 100, "completion_tokens": 1, "total_tokens": 101, "prompt_tokens_details": {"cached_tokens": 70}}
    )
    ds = type(completion("x").usage).model_validate(
        {"prompt_tokens": 100, "completion_tokens": 1, "total_tokens": 101, "prompt_cache_hit_tokens": 90}
    )
    assert _cached_tokens(std) == 70
    assert _cached_tokens(ds) == 90
    assert _cached_tokens(None) == 0
    assert _cached_tokens(completion("x").usage) == 0


# ---------------------------------------------------------------------------
# 收尾阶段（阶段 5）
# 实测：不发工具（4.2 万输入命中 384）和 tools + tool_choice=none（3.5 万输入命中 640）都几乎不命中缓存。
# 现在收尾请求与平时完全相同，由代码拒绝不允许的工具；tool_choice=none 只作为最后手段。
# ---------------------------------------------------------------------------

WRITES: list[str] = []


@tool(name="write_report")
def fake_write_report(content: str) -> str:
    """假的写报告工具。"""
    WRITES.append(content)
    return "报告已保存"


@pytest.fixture
def wrapup_registry():
    WRITES.clear()
    return ToolRegistry([echo, fake_write_report])


def test_finalize_keeps_same_request_shape_for_cache(registry):
    res, llm, _ = run(
        [completion(tool_calls=[("c1", "echo", {"text": "1"})]), completion("收尾")], registry, max_steps=1
    )
    assert res.answer == "收尾"
    assert llm.requests[-1]["tools"] == llm.requests[0]["tools"]  # 与正常调用完全相同的工具说明书
    assert "tool_choice" not in llm.requests[-1]


def test_wrapup_lets_model_write_report_then_answer(wrapup_registry):
    res, llm, events = run(
        [
            completion(tool_calls=[("c1", "echo", {"text": "1"})]),
            completion(tool_calls=[("c2", "write_report", {"content": "# 报告"})]),
            completion("总结"),
        ],
        wrapup_registry,
        max_steps=1,
    )
    assert res.stop_reason == "max_steps" and res.answer == "总结"
    assert WRITES == ["# 报告"]  # 收尾阶段真的执行了 write_report
    assert res.steps == 1  # 收尾调用不计入步数
    assert all("tool_choice" not in r for r in llm.requests)


def test_wrapup_refuses_research_tools(wrapup_registry):
    res, _, events = run(
        [
            completion(tool_calls=[("c1", "echo", {"text": "1"})]),
            completion(tool_calls=[("c2", "echo", {"text": "还想再查"})]),
            completion("总结"),
        ],
        wrapup_registry,
        max_steps=1,
    )
    refused = tool_results(events)[-1]
    assert refused["is_error"] and "收尾阶段不能再调用 echo" in refused["content"]
    assert res.answer == "总结"
    # 每个工具调用都有对应的 tool 消息，历史合法
    assert res.messages[-2] == {"role": "tool", "tool_call_id": "c2", "content": refused["content"]}


def test_wrapup_exhausted_falls_back_to_tool_choice_none(wrapup_registry):
    from agent.prompts import WRAPUP_LAST_CALL_PROMPT

    res, llm, _ = run(
        [completion(tool_calls=[("c1", "echo", {"text": "1"})])]
        + [completion(tool_calls=[(f"w{i}", "echo", {"text": str(i)})]) for i in range(2)]
        + [completion("最后的回答")],
        wrapup_registry,
        max_steps=1,
        wrapup_max_calls=2,
    )
    assert res.answer == "最后的回答"
    assert len(llm.requests) == 4
    assert llm.requests[-1]["tool_choice"] == "none"
    assert llm.requests[-1]["messages"][-1]["content"] == WRAPUP_LAST_CALL_PROMPT


def test_finalize_falls_back_when_tool_choice_ignored(registry):
    res, llm, _ = run(
        [
            completion(tool_calls=[("c1", "echo", {"text": "1"})]),
            completion(tool_calls=[("c2", "echo", {"text": "2"})]),  # 无视 tool_choice=none
            completion("不带工具的收尾"),
        ],
        registry,
        max_steps=1,
        wrapup_max_calls=0,
    )
    assert res.answer == "不带工具的收尾"
    assert llm.requests[-1]["tools"] is None
    assert "tool_calls" not in res.messages[-1]  # 历史里不留下没有回应的工具调用


def test_finalize_falls_back_when_tool_choice_rejected(registry):
    res, llm, _ = run(
        [
            completion(tool_calls=[("c1", "echo", {"text": "1"})]),
            api_error(openai.BadRequestError, "tool_choice none is not supported"),
            completion("不带工具的收尾"),
        ],
        registry,
        max_steps=1,
        wrapup_max_calls=0,
    )
    assert res.answer == "不带工具的收尾" and res.stop_reason == "max_steps"
    assert llm.requests[-1]["tools"] is None


def test_finalize_prompt_restricts_references_to_notes():
    assert "参考资料只能列出笔记中出现过的来源" in finalize_prompt("budget")
    assert "write_report" in finalize_prompt("max_steps")


# ---------------------------------------------------------------------------
# 剩余步数提醒（阶段 5）
# ---------------------------------------------------------------------------


def test_steps_warning_once_when_few_steps_remain(registry):
    responses = [completion(tool_calls=[(f"c{i}", "echo", {"text": str(i)})]) for i in range(4)]
    res, llm, events = run(responses + [completion("done")], registry, max_steps=8, steps_warn_remaining=5)
    assert res.stop_reason == "final_answer"
    warned = [r for r in llm.requests if "只剩 5 轮" in str(r["messages"][-1].get("content"))]
    assert len(warned) == 1  # 第 3 步结束后提醒，只提醒一次
    assert len(llm.requests[3]["messages"]) == len(llm.requests[2]["messages"]) + 3  # assistant + tool + 提醒
    assert any("只剩 5 步" in n for n in notices(events))


def test_no_steps_warning_when_max_steps_is_small(registry):
    res, llm, _ = run(
        [completion(tool_calls=[("c1", "echo", {"text": "1"})]), completion("done")], registry, max_steps=3
    )
    assert not any("只剩" in str(m.get("content")) for m in llm.requests[-1]["messages"])
