"""Agent 主循环 —— 整个项目的心脏。

    while 没超过步数上限:
        把 [对话历史 + 工具说明书] 发给模型
        if 模型没有要求调用工具:
            它给出的就是最终回答 → 结束
        else:
            逐个执行它要求的工具，把结果作为 role="tool" 消息追加到历史 → 继续循环

阶段 1 的核心细节：
- 模型的回复（含 tool_calls）必须原样追加进历史，紧接着是每个调用对应的 tool 消息；
  tool 消息用 tool_call_id 与调用一一对应，缺一条接口就会报 400。
- 模型可能在一轮里同时要求调用多个工具（parallel tool calls），要逐个执行、逐条回传。
- 工具出错不中断循环：错误信息交给模型，由它决定如何补救。

阶段 3 加上的保护（每一项都对应 tests/test_loop_robustness.py 里的故障测试）：
- 步数上限 / token 预算 / 重复调用检测 → 触发后"强制收尾"：再请求一次但不提供工具，让模型基于已有信息作答
- token 用到 80% 时提醒模型尽快收尾
- 单个工具执行超时
- 回复被截断（finish_reason=length）：工具参数不完整就不执行并告知原因；文字回答则让模型接着写
- 空回复：提醒一次
- 内容审核拦截：移除最近一批工具结果（多半是某个网页触发的）后重试一次
- 上下文超长、Key 错误等无法恢复的错误：干净地结束并给出原因
- Ctrl+C：立即停止，但已发生的一切都通过事件写进了日志

无论以哪种方式结束，run() 都会返回 AgentResult，并发出 run_end 事件，不会把异常抛给调用方。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Literal, Protocol

import openai
from openai.types.chat import ChatCompletion, ChatCompletionMessage

from agent.errors import classify_api_error
from agent.prompts import (
    CONTINUE_PROMPT,
    EMPTY_REPLY_PROMPT,
    REDACTED_TOOL_RESULT,
    REPEAT_BLOCKED_RESULT,
    TRUNCATED_TOOL_CALL_RESULT,
    budget_warning_prompt,
    finalize_prompt,
    research_system_prompt,
)
from tools.registry import ToolRegistry, ToolResult

StopReason = Literal[
    "final_answer",  # 正常完成
    "max_steps",  # 步数用完，强制收尾
    "budget",  # token 预算用完，强制收尾
    "loop_detected",  # 反复重复调用，强制收尾
    "interrupted",  # 用户 Ctrl+C
    "context_overflow",  # 上下文超出模型上限
    "content_filter",  # 被内容审核拦截且无法恢复
    "error",  # 其它无法恢复的错误（Key 错误、模型名错误……）
]

# 这几种情况会做"强制收尾"调用，因此 answer 里通常仍有内容
FINALIZE_REASONS = ("max_steps", "budget", "loop_detected")


class ChatModel(Protocol):
    """Agent 只依赖这一个方法 —— LLMClient 满足它，测试里的假模型也满足它。"""

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> ChatCompletion: ...


@dataclass
class AgentEvent:
    """循环中发生的事件，交给 on_event 回调（终端展示、写 trace 日志都基于它）。

    kind:
      run_start    任务开始         data: question, max_steps, token_budget, tools
      step         新一轮开始       data: step
      llm_call     一次模型调用完成 data: step, prompt_tokens, cached_tokens, completion_tokens, seconds, finish_reason
      thought      调用工具前的说明 data: text
      tool_call    模型请求调用工具 data: id, name, arguments
      tool_result  工具执行结果     data: id, name, content, is_error, seconds
      notice       系统干预说明     data: text   （预算提醒、重复拦截、截断处理……）
      final        最终回答         data: text, stop_reason
      run_end      任务结束         data: stop_reason, steps, total_tokens, seconds, error
    """

    kind: str
    data: dict[str, Any] = field(default_factory=dict)


EventHandler = Callable[[AgentEvent], None]


@dataclass
class AgentResult:
    answer: str
    steps: int  # 调用模型的轮数（不含收尾调用）
    stop_reason: StopReason
    messages: list[dict[str, Any]]  # 完整对话历史，便于调试 / 复盘
    total_tokens: int = 0
    error: str | None = None

    @property
    def tool_calls(self) -> list[tuple[str, str]]:
        """按顺序列出本次运行中所有 (工具名, 参数)。"""
        out = []
        for m in self.messages:
            for c in m.get("tool_calls") or []:
                out.append((c["function"]["name"], c["function"]["arguments"]))
        return out


class Agent:
    def __init__(
        self,
        llm: ChatModel,
        registry: ToolRegistry,
        system_prompt: str | None = None,
        max_steps: int = 20,
        max_tool_result_chars: int = 8000,
        token_budget: int = 300_000,
        budget_warn_ratio: float = 0.8,
        tool_timeout: float = 60.0,
        max_blocked_repeats: int = 3,
        on_event: EventHandler | None = None,
    ):
        self.llm = llm
        self.registry = registry
        self.system_prompt = system_prompt or research_system_prompt(date.today().isoformat())
        self.max_steps = max_steps
        self.max_tool_result_chars = max_tool_result_chars
        self.token_budget = token_budget
        self.budget_warn_ratio = budget_warn_ratio
        self.tool_timeout = tool_timeout
        self.max_blocked_repeats = max_blocked_repeats
        self.on_event = on_event

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def run(self, question: str) -> AgentResult:
        # 每次 run 的状态都重新初始化
        self._tokens = 0
        self._last_prompt = 0  # 上一次调用的输入 token 数，用来预估下一次调用的开销
        self._steps = 0
        self._budget_warned = False
        self._blocked = 0
        self._call_stats: dict[tuple[str, str], list[int]] = {}  # 调用签名 → [调用次数, 成功次数]

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": question},
        ]
        self._emit(
            "run_start",
            question=question,
            max_steps=self.max_steps,
            token_budget=self.token_budget,
            tools=self.registry.names,
        )
        started = time.perf_counter()
        try:
            stop, answer, error = self._loop(messages)
        except KeyboardInterrupt:
            self._emit("notice", text="用户中断（Ctrl+C），任务停止。")
            stop, answer, error = "interrupted", "", "用户中断"

        self._emit(
            "run_end",
            stop_reason=stop,
            steps=self._steps,
            total_tokens=self._tokens,
            seconds=round(time.perf_counter() - started, 2),
            error=error,
        )
        return AgentResult(answer, self._steps, stop, messages, self._tokens, error)

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def _loop(self, messages: list[dict[str, Any]]) -> tuple[StopReason, str, str | None]:
        tools = self.registry.schemas()
        pending_text: list[str] = []  # 被截断、正在续写的回答
        empty_replies = 0

        for step in range(1, self.max_steps + 1):
            self._steps = step
            self._emit("step", step=step)

            # 每次调用都要把整个历史重新发一遍，下一次的输入至少和上一次一样大。
            # 所以不是等超了才停，而是预估"再调用一次就会超"时就收尾（收尾调用本身仍会花掉约一次的量）。
            if self._tokens + self._last_prompt >= self.token_budget:
                return self._finalize(messages, "budget")

            # ---- 1. 调用模型 ----
            try:
                resp = self._call_llm(messages, tools, step)
            except openai.APIError as e:
                kind = classify_api_error(e)
                if kind == "content_filter" and self._redact_last_tool_results(messages):
                    self._emit("notice", text="请求被模型服务的内容审核拦截，已移除最近一批工具结果后重试。")
                    continue
                return _stop_for(kind), "", f"{type(e).__name__}: {e}"

            choice = resp.choices[0]
            msg = choice.message
            if choice.finish_reason == "content_filter":  # 生成的内容被拦截
                if self._redact_last_tool_results(messages):
                    self._emit("notice", text="模型输出被内容审核拦截，已移除最近一批工具结果后重试。")
                    continue
                return "content_filter", "", "模型输出被内容审核拦截"
            truncated = choice.finish_reason == "length"

            # ---- 2. 没有工具调用：最终回答（或需要续写 / 重试） ----
            if not msg.tool_calls:
                text = msg.content or ""
                if truncated:
                    messages.append(_assistant_message_to_dict(msg))
                    messages.append({"role": "user", "content": CONTINUE_PROMPT})
                    pending_text.append(text)
                    self._emit("notice", text="回答因输出长度上限被截断，已要求模型接着写。")
                    continue
                if not text.strip() and not pending_text:
                    empty_replies += 1
                    if empty_replies >= 2:
                        return "error", "", "模型连续返回空回复"
                    # 空的 assistant 消息有的厂商不接受，所以不放进历史，只追加提醒
                    messages.append({"role": "user", "content": EMPTY_REPLY_PROMPT})
                    self._emit("notice", text="模型返回了空回复，已提醒它继续。")
                    continue
                messages.append(_assistant_message_to_dict(msg))
                answer = "".join(pending_text) + text
                self._emit("final", text=answer, stop_reason="final_answer")
                return "final_answer", answer, None

            # ---- 3. 有工具调用：逐个执行，结果写回历史 ----
            empty_replies = 0
            messages.append(_assistant_message_to_dict(msg))
            if msg.content:
                self._emit("thought", text=msg.content)

            for call in msg.tool_calls:
                name, arguments = call.function.name, call.function.arguments
                self._emit("tool_call", id=call.id, name=name, arguments=arguments)
                if truncated:
                    # 参数大概率是半截 JSON；即使碰巧能解析，内容也不完整（例如半篇报告）
                    result, seconds = ToolResult(TRUNCATED_TOOL_CALL_RESULT, True), 0.0
                else:
                    result, seconds = self._run_tool(name, arguments)
                content = _truncate(result.content, self.max_tool_result_chars)
                self._emit(
                    "tool_result",
                    id=call.id,
                    name=name,
                    content=content,
                    is_error=result.is_error,
                    seconds=round(seconds, 2),
                )
                messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
            if truncated:
                self._emit("notice", text="回复因输出长度上限被截断，工具调用未执行，已告知模型分段处理。")

            # ---- 4. 本轮结束后的检查 ----
            if self._blocked >= self.max_blocked_repeats:
                return self._finalize(messages, "loop_detected")
            self._maybe_warn_budget(messages)

        return self._finalize(messages, "max_steps")

    # ------------------------------------------------------------------
    # 各个环节
    # ------------------------------------------------------------------

    def _call_llm(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, step: int | None
    ) -> ChatCompletion:
        started = time.perf_counter()
        resp = self.llm.chat(messages, tools=tools)
        u = resp.usage
        p = u.prompt_tokens if u else 0
        c = u.completion_tokens if u else 0
        self._tokens += p + c
        self._last_prompt = p
        self._emit(
            "llm_call",
            step=step,
            prompt_tokens=p,
            cached_tokens=_cached_tokens(u),
            completion_tokens=c,
            seconds=round(time.perf_counter() - started, 2),
            finish_reason=resp.choices[0].finish_reason if resp.choices else None,
        )
        return resp

    def _run_tool(self, name: str, arguments: str) -> tuple[ToolResult, float]:
        """执行一次工具调用：先做重复检测，再带超时执行。"""
        sig = (name, _normalize_args(arguments))
        stats = self._call_stats.setdefault(sig, [0, 0])
        calls, successes = stats
        # 同样的调用：成功过一次就不必再做（结果就在历史里）；失败了允许重试，但最多 3 次
        if successes >= 1 or calls >= 3:
            self._blocked += 1
            self._emit("notice", text=f"拦截了一次重复调用：{name}（累计 {self._blocked} 次）")
            return ToolResult(REPEAT_BLOCKED_RESULT, True), 0.0

        started = time.perf_counter()
        result = self._execute_with_timeout(name, arguments)
        stats[0] += 1
        if not result.is_error:
            stats[1] += 1
        return result, time.perf_counter() - started

    def _execute_with_timeout(self, name: str, arguments: str) -> ToolResult:
        """在后台线程里执行工具，超时就放弃等待。

        Python 无法强制杀死线程，超时的线程会在后台继续跑完（daemon 线程，不会阻止程序退出）。
        这里用"分段等待"而不是一次 wait(timeout)，这样 Windows 上 Ctrl+C 也能及时响应。
        """
        box: dict[str, ToolResult] = {}
        done = threading.Event()

        def target() -> None:
            try:
                box["r"] = self.registry.execute(name, arguments)
            except BaseException as e:  # noqa: BLE001 —— execute 本身不抛异常，这里是双保险
                box["r"] = ToolResult(f"ERROR: {type(e).__name__}: {e}", True)
            finally:
                done.set()

        threading.Thread(target=target, daemon=True, name=f"tool-{name}").start()
        deadline = time.monotonic() + self.tool_timeout
        while not done.wait(0.1):
            if time.monotonic() >= deadline:
                return ToolResult(
                    f"ERROR: 工具执行超时（超过 {self.tool_timeout:g} 秒），已放弃本次调用。请换个参数或来源。",
                    True,
                )
        return box["r"]

    def _maybe_warn_budget(self, messages: list[dict[str, Any]]) -> None:
        if self._budget_warned or self._tokens < self.token_budget * self.budget_warn_ratio:
            return
        self._budget_warned = True
        messages.append({"role": "user", "content": budget_warning_prompt(self._tokens, self.token_budget)})
        self._emit("notice", text=f"token 已用 {self._tokens}/{self.token_budget}，已提醒模型尽快收尾。")

    def _redact_last_tool_results(self, messages: list[dict[str, Any]]) -> bool:
        """把历史末尾那一批工具结果替换成占位说明。返回是否真的替换了内容。

        内容审核多半是刚读进来的网页触发的；已替换过的不会再替换，所以最多重试一次。
        """
        changed = False
        for m in reversed(messages):
            if m["role"] != "tool":
                break
            if m["content"] != REDACTED_TOOL_RESULT:
                m["content"] = REDACTED_TOOL_RESULT
                changed = True
        return changed

    def _finalize(
        self, messages: list[dict[str, Any]], reason: StopReason
    ) -> tuple[StopReason, str, str | None]:
        """强制收尾：不提供工具，让模型基于已有信息作答。"""
        self._emit("notice", text=f"触发强制收尾（{reason}），不再调用工具。")
        messages.append({"role": "user", "content": finalize_prompt(reason)})
        try:
            resp = self._call_llm(messages, None, None)
        except openai.APIError as e:
            return reason, "", f"收尾调用失败：{type(e).__name__}: {e}"
        msg = resp.choices[0].message
        messages.append(_assistant_message_to_dict(msg))
        answer = msg.content or ""
        self._emit("final", text=answer, stop_reason=reason)
        return reason, answer, None

    def _emit(self, kind: str, **data: Any) -> None:
        if self.on_event:
            self.on_event(AgentEvent(kind, data))


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------


def _stop_for(kind: str) -> StopReason:
    return {"context_overflow": "context_overflow", "content_filter": "content_filter"}.get(
        kind, "error"
    )  # type: ignore[return-value]


def _cached_tokens(usage: Any) -> int:
    """输入中命中厂商缓存的 token 数（命中部分通常只收 1/10～1/50 的价格）。

    各家字段不同：OpenAI 标准是 prompt_tokens_details.cached_tokens，DeepSeek 是 prompt_cache_hit_tokens。
    """
    if usage is None:
        return 0
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details else None
    if cached is None:
        cached = (getattr(usage, "model_extra", None) or {}).get("prompt_cache_hit_tokens")
    return int(cached or 0)


def _normalize_args(arguments: str) -> str:
    """参数规范化后再比较：{"a":1,"b":2} 和 {"b": 2, "a": 1} 视为同一个调用。"""
    try:
        return json.dumps(json.loads(arguments or "{}"), sort_keys=True, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        return arguments or ""


def _assistant_message_to_dict(msg: ChatCompletionMessage) -> dict[str, Any]:
    """把 SDK 返回的消息对象转成普通 dict 再放回历史。

    不直接 append 对象、也不用 model_dump() 全量导出，是为了只保留协议需要的字段：
    有些厂商会返回额外字段（如 DeepSeek 推理模型的 reasoning_content），
    原样回传可能导致下一次请求报错。
    """
    out: dict[str, Any] = {"role": "assistant", "content": msg.content}
    if msg.tool_calls:
        out["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.function.name, "arguments": c.function.arguments},
            }
            for c in msg.tool_calls
        ]
    return out


def _truncate(text: str, limit: int) -> str:
    """工具结果过长会撑爆上下文。先简单截断，阶段 4 再做摘要。"""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[结果过长已截断，原文共 {len(text)} 字符]"


def pretty_arguments(arguments: str, max_value_chars: int | None = None) -> str:
    """把 JSON 参数格式化成一行便于阅读的文本；解析失败就原样返回。

    max_value_chars：终端展示时截断过长的参数值（例如 write_report 的整篇报告）。
    """
    try:
        obj = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError:
        return arguments
    if not isinstance(obj, dict):
        return arguments
    parts = []
    for k, v in obj.items():
        text = repr(v)
        if max_value_chars and len(text) > max_value_chars:
            text = text[:max_value_chars] + f"…(共 {len(str(v))} 字符)"
        parts.append(f"{k}={text}")
    return ", ".join(parts)
