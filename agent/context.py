"""上下文管理（阶段 4）：估算上下文大小，必要时把旧的网页原文、搜索结果移出上下文。

背景（阶段 3 实测）：一次 15 步的研究，最后一次请求的上下文只有 4.2 万 token，
但因为每一轮都要重发全部历史，累计输入高达 33.5 万。占大头的是 fetch_url 返回的网页原文。

做法：
- 模型读完网页后用 save_note 记要点（tools/notes.py）；
- 上下文超过阈值时，把"足够旧"的 fetch_url / web_search 结果替换成一行说明（压缩）。

三个必须做对的细节：
1. tool 消息不能删，只能替换内容 —— 每个 tool_call_id 都必须有对应的 tool 消息，否则接口报 400。
2. 最近几轮的结果不压缩 —— 刚返回的结果模型还没看过（或刚看过还没来得及记笔记）。
3. 攒够了再压缩，而不是每轮都压一点 —— 厂商的前缀缓存（DeepSeek 等）按"从开头起完全相同的部分"命中，
   改动历史中任意一处，它之后的内容就全部无法命中缓存。每轮都改一点 = 每轮缓存都失效。
   所以只在超过阈值时一次性把能压的都压掉，之后新的前缀又能稳定命中缓存。具体有两道闸：
   - 最小收益：这次能省下的 token 太少（< min_saving）就不压，不值得让缓存失效；
   - 回差（hysteresis）：压缩后，上下文要再涨 rearm_ratio × threshold 才允许下一次压缩。
     否则一旦超过阈值，每过一轮就会有一轮新结果"变旧"，变成每轮都压一点。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

COMPACTED_MARKER = "[已压缩]"

_CJK = re.compile(r"[　-〿一-鿿＀-￯]")


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：中文约 1.3 字 / token，其它字符约 3.3 字符 / token。

    系数是用阶段 3 一次真实运行（DeepSeek，15 次调用）的 messages.json 与接口返回的 prompt_tokens
    拟合出来的，平均误差约 2%。起初按"中文 1.5 字 / token、英文 4 字符 / token"估，系统性偏低约 15%：
    网页里的 Markdown 符号、URL、数字切出来的 token 比普通英文单词多。

    不用 tiktoken：它是 OpenAI 的分词器，和 DeepSeek / 通义等的计数本来就对不上。
    换模型后如果偏差明显，可以用同样的方法重新拟合。
    """
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    return int(cjk / 1.3 + (len(text) - cjk) / 3.3) + 1


def estimate_message_tokens(m: dict[str, Any]) -> int:
    n = 4  # 每条消息的角色、分隔符等固定开销
    n += estimate_tokens(m.get("content") or "")
    for c in m.get("tool_calls") or []:
        n += estimate_tokens(c["function"]["name"]) + estimate_tokens(c["function"]["arguments"]) + 8
    return n


def estimate_messages(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_message_tokens(m) for m in messages)


def estimate_tools(tools: list[dict[str, Any]] | None) -> int:
    return estimate_tokens(json.dumps(tools, ensure_ascii=False)) if tools else 0


@dataclass
class CompactionResult:
    compacted: list[tuple[str, str]]  # 被压缩的 (工具名, 参数)
    tokens_before: int
    tokens_after: int
    by_tool: dict[str, int] = field(default_factory=dict)

    @property
    def saved(self) -> int:
        return self.tokens_before - self.tokens_after


class ContextCompactor:
    def __init__(
        self,
        threshold: int = 60_000,
        keep_recent_turns: int = 2,
        min_chars: int = 600,
        min_saving: int = 2_000,
        rearm_ratio: float = 0.3,
        has_note: Callable[[str], bool] | None = None,
    ):
        """
        threshold：估算的上下文 token 超过它才压缩
        keep_recent_turns：最近几轮（assistant 消息）之后的工具结果不压缩
        min_chars：比这还短的结果（错误信息、计算结果）不值得压缩
        min_saving：预计节省少于这么多 token 就不压缩
        rearm_ratio：上次压缩后，上下文需再增长 threshold × rearm_ratio 才允许再次压缩
        has_note：判断某个 URL 是否已经记过笔记，用来在占位说明里提醒模型
        """
        self.threshold = threshold
        self.keep_recent_turns = keep_recent_turns
        self.min_chars = min_chars
        self.min_saving = min_saving
        self.rearm_ratio = rearm_ratio
        self.has_note = has_note
        self.reset()

    def reset(self) -> None:
        """每个任务开始时调用（Agent.run 会调用）。"""
        self._last_after: int | None = None  # 上次压缩后的上下文大小

    def maybe_compact(self, messages: list[dict[str, Any]], estimated_tokens: int) -> CompactionResult | None:
        if estimated_tokens < self.threshold:
            return None
        if self._last_after is not None and estimated_tokens - self._last_after < self.threshold * self.rearm_ratio:
            return None

        calls: dict[str, tuple[str, str]] = {}  # tool_call_id → (工具名, 参数)
        assistant_idx: list[int] = []
        for i, m in enumerate(messages):
            if m["role"] == "assistant":
                assistant_idx.append(i)
                for c in m.get("tool_calls") or []:
                    calls[c["id"]] = (c["function"]["name"], c["function"]["arguments"])
        if len(assistant_idx) <= self.keep_recent_turns:
            return None
        protect_from = assistant_idx[-self.keep_recent_turns] if self.keep_recent_turns else len(messages)

        # 先算出所有候选替换，确认收益足够再真正修改
        plan: list[tuple[dict[str, Any], str, str, str]] = []  # (消息, 新内容, 工具名, 参数)
        saving = 0
        for m in messages[:protect_from]:
            if m["role"] != "tool":
                continue
            content = m.get("content") or ""
            if content.startswith(COMPACTED_MARKER) or len(content) < self.min_chars:
                continue
            name, arguments = calls.get(m.get("tool_call_id", ""), ("", ""))
            if name == "fetch_url":
                new = self._stub_fetch(content)
            elif name == "web_search":
                new = self._stub_search(content)
            elif name == "search_knowledge":
                new = (f"{COMPACTED_MARKER} 本地资料检索的旧原文已移出上下文。"
                       "这不是原文摘要，也不证明全文已读。用read_evidence重读本轮已保留的证据，"
                       "或用search_knowledge重新检索本任务固定代际；嵌入缓存未命中时仍可能产生用量。")
            else:
                continue
            saving += estimate_tokens(content) - estimate_tokens(new)
            plan.append((m, new, name, arguments))
        if not plan or saving < self.min_saving:
            return None

        result = CompactionResult([], estimated_tokens, estimated_tokens - saving)
        for m, new, name, arguments in plan:
            m["content"] = new
            result.compacted.append((name, arguments))
            result.by_tool[name] = result.by_tool.get(name, 0) + 1
        self._last_after = result.tokens_after
        return result

    # ---- 占位说明 ----

    def _stub_fetch(self, content: str) -> str:
        url = _field(content, "URL")
        title = _field(content, "标题")
        span = _field(content, "正文")
        desc = f"{url}" + (f"（{title}）" if title else "") + (f"，{span}" if span else "")
        if self.has_note and url and self.has_note(url):
            hint = "要点已记录在笔记中，可用 read_notes 查看。"
        else:
            hint = "⚠ 这个网页还没有记笔记；如果其中有需要的信息，请重新 fetch_url 阅读并用 save_note 记下要点。"
        return (
            f"{COMPACTED_MARKER} 曾读取网页 {desc}。为节省上下文，原文已被系统移出。{hint}"
            "如需原文可再次调用 fetch_url（不会重复下载）。"
        )

    @staticmethod
    def _stub_search(content: str) -> str:
        keep = [
            line
            for line in content.splitlines()
            if line.startswith("搜索「") or re.match(r"^\[\d+\] ", line) or line.strip().startswith("URL:")
        ]
        return f"{COMPACTED_MARKER} 以下搜索结果的摘要已被系统移出，仅保留标题和 URL：\n" + "\n".join(keep)


def _field(content: str, name: str) -> str:
    m = re.search(rf"^{name}: (.+)$", content, re.MULTILINE)
    return m.group(1).strip() if m else ""
