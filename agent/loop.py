"""Agent 主循环 —— 整个项目的心脏。

    while 没超过步数上限:
        把 [对话历史 + 工具说明书] 发给模型
        if 模型没有要求调用工具:
            它给出的就是最终回答 → 结束
        else:
            逐个执行它要求的工具，把结果作为 role="tool" 消息追加到历史 → 继续循环

几个必须做对的细节：
- 模型的回复（含 tool_calls）必须原样追加进历史，紧接着是每个调用对应的 tool 消息；
  tool 消息用 tool_call_id 与调用一一对应，缺一条接口就会报 400。
- 模型可能在一轮里同时要求调用多个工具（parallel tool calls），要逐个执行、逐条回传。
- 工具出错不中断循环：错误信息交给模型，由它决定如何补救。
- 一定要有步数上限，防止模型陷入无限循环烧钱。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Literal, Protocol

from openai.types.chat import ChatCompletion, ChatCompletionMessage

from agent.prompts import MAX_STEPS_PROMPT, research_system_prompt
from tools.registry import ToolRegistry

StopReason = Literal["final_answer", "max_steps"]


class ChatModel(Protocol):
    """Agent 只依赖这一个方法 —— LLMClient 满足它，测试里的假模型也满足它。"""

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> ChatCompletion: ...


@dataclass
class AgentEvent:
    """循环中发生的事件，交给 on_event 回调（用于终端展示，后续阶段也用于写日志）。

    kind:
      step         新一轮开始           data: step
      thought      模型在调用工具前说的话 data: text
      tool_call    模型请求调用工具       data: id, name, arguments
      tool_result  工具执行结果          data: id, name, content, is_error
      final        最终回答             data: text, stop_reason
    """

    kind: str
    data: dict[str, Any] = field(default_factory=dict)


EventHandler = Callable[[AgentEvent], None]


@dataclass
class AgentResult:
    answer: str
    steps: int  # 调用模型的轮数（不含步数耗尽后的收尾调用）
    stop_reason: StopReason
    messages: list[dict[str, Any]]  # 完整对话历史，便于调试 / 复盘

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
        on_event: EventHandler | None = None,
    ):
        self.llm = llm
        self.registry = registry
        self.system_prompt = system_prompt or research_system_prompt(date.today().isoformat())
        self.max_steps = max_steps
        self.max_tool_result_chars = max_tool_result_chars
        self.on_event = on_event

    def run(self, question: str) -> AgentResult:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": question},
        ]
        tools = self.registry.schemas()

        for step in range(1, self.max_steps + 1):
            self._emit("step", step=step)
            resp = self.llm.chat(messages, tools=tools)
            msg = resp.choices[0].message
            messages.append(_assistant_message_to_dict(msg))

            # ---- 没有工具调用：模型认为可以回答了 ----
            if not msg.tool_calls:
                answer = msg.content or ""
                self._emit("final", text=answer, stop_reason="final_answer")
                return AgentResult(answer, step, "final_answer", messages)

            # ---- 有工具调用：逐个执行，结果写回历史 ----
            if msg.content:
                self._emit("thought", text=msg.content)
            for call in msg.tool_calls:
                name, arguments = call.function.name, call.function.arguments
                self._emit("tool_call", id=call.id, name=name, arguments=arguments)

                result = self.registry.execute(name, arguments)
                content = _truncate(result.content, self.max_tool_result_chars)

                self._emit(
                    "tool_result", id=call.id, name=name, content=content, is_error=result.is_error
                )
                messages.append({"role": "tool", "tool_call_id": call.id, "content": content})

        # ---- 步数耗尽：不再提供工具，强制模型基于已有信息作答 ----
        messages.append({"role": "user", "content": MAX_STEPS_PROMPT})
        resp = self.llm.chat(messages)
        msg = resp.choices[0].message
        messages.append(_assistant_message_to_dict(msg))
        answer = msg.content or ""
        self._emit("final", text=answer, stop_reason="max_steps")
        return AgentResult(answer, self.max_steps, "max_steps", messages)

    def _emit(self, kind: str, **data: Any) -> None:
        if self.on_event:
            self.on_event(AgentEvent(kind, data))


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
    """工具结果过长会撑爆上下文。阶段 1 先简单截断，阶段 4 再做摘要。"""
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
