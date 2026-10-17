"""离线模拟：在一次真实运行的对话历史上，评估不同压缩阈值的效果（不调用模型、不花钱）。

用法：
    python -m agent.simulate <任务目录>                         # 默认比较 不压缩 / 15k / 25k / 40k / 60k
    python -m agent.simulate <任务目录> --thresholds 0 20000 60000
    python -m agent.simulate <任务目录> --hit-price 0.02 --miss-price 1

原理：把 messages.json 按原来的顺序逐次"重放"，每次请求前交给 ContextCompactor 判断是否压缩，
然后用缓存成本模型估算这次请求的花费：
    - 与上一次请求完全相同的开头部分 → 命中前缀缓存，按 hit 价
    - 从第一处不同开始的所有内容     → 未命中，按 miss 价
（DeepSeek 等厂商的自动前缀缓存就是这样工作的。）

阶段 4 的教训：只看"累计 token"会得出"阈值越低越省"的错误结论 —— 在有前缀缓存的厂商上，
压缩会把便宜的缓存命中换成昂贵的重新计算。所以这里同时给出累计 token 和按缓存计价的输入成本。

局限：重放的是原运行中模型的决策；压缩后模型可能会做出不同决策（例如重读网页），模拟无法反映。
token 数用 agent.context 的估算函数，不是真实计数。
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.table import Table

from agent.context import COMPACTED_MARKER, ContextCompactor, estimate_message_tokens, estimate_tools
from agent.display import console
from agent.trace import MESSAGES_FILE, TRACE_FILE, read_trace

# 默认价格比例取自 DeepSeek 定价页（2026-10）：缓存命中 0.02 元 vs 未命中 1 元 / 百万 tokens
DEFAULT_HIT_PRICE = 0.02
DEFAULT_MISS_PRICE = 1.0


@dataclass
class SimResult:
    threshold: int  # 0 表示不压缩
    calls: int
    total_input: int
    uncached_input: int
    compactions: int
    peak_context: int
    final_context: int

    def cost(self, hit: float, miss: float) -> float:
        return self.uncached_input * miss + (self.total_input - self.uncached_input) * hit


def simulate(
    messages: list[dict[str, Any]],
    threshold: int,
    tools: list[dict[str, Any]] | None = None,
    keep_recent_turns: int = 2,
) -> SimResult:
    work = copy.deepcopy(messages)
    tools_tokens = estimate_tools(tools)
    compactor = ContextCompactor(threshold=threshold or 10**12, keep_recent_turns=keep_recent_turns)

    # 每条 assistant 消息对应一次模型调用，那次请求的内容就是它之前的全部消息
    request_ends = [i for i, m in enumerate(work) if m["role"] == "assistant"]
    prev: list[str] | None = None
    total = uncached = compactions = peak = size = 0
    for end in request_ends:
        prefix = work[:end]  # 新列表，但元素是同一批 dict，压缩时会直接修改 work 中的消息
        estimate = tools_tokens + sum(estimate_message_tokens(m) for m in prefix)
        if compactor.maybe_compact(prefix, estimate):
            compactions += 1

        serialized = [json.dumps(m, ensure_ascii=False, sort_keys=True) for m in prefix]
        sizes = [estimate_message_tokens(m) for m in prefix]
        size = tools_tokens + sum(sizes)
        common = 0
        if prev is not None:
            for a, b in zip(prev, serialized):
                if a != b:
                    break
                common += 1
        cached = (tools_tokens + sum(sizes[:common])) if prev is not None else 0

        total += size
        uncached += size - cached
        peak = max(peak, size)
        prev = serialized
    return SimResult(threshold, len(request_ends), total, uncached, compactions, peak, size)


def restore_original_history(task_dir: Path) -> tuple[list[dict[str, Any]], int]:
    """读取 messages.json，并用 trace.jsonl 里的工具结果还原被压缩过的内容。

    messages.json 是任务结束时保存的，压缩过的工具结果已经被替换成占位说明；
    而 trace.jsonl 在工具返回时就记录了完整结果（与当时发给模型的内容一致）。返回 (历史, 还原条数)。
    """
    messages = json.loads((task_dir / MESSAGES_FILE).read_text(encoding="utf-8"))
    trace = task_dir / TRACE_FILE
    if not trace.exists():
        return messages, 0
    originals = {e.data["id"]: e.data["content"] for e in read_trace(trace) if e.kind == "tool_result"}
    restored = 0
    for m in messages:
        if m["role"] == "tool" and (m.get("content") or "").startswith(COMPACTED_MARKER):
            original = originals.get(m.get("tool_call_id"))
            if original is not None:
                m["content"] = original
                restored += 1
    return messages, restored


def load_tools_schema() -> list[dict[str, Any]]:
    """当前版本的工具说明书（只用来估算 token，所以用临时目录即可）。"""
    import tempfile

    from tools import default_registry

    return default_registry(Path(tempfile.gettempdir())).schemas()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="在真实运行的历史上离线评估压缩阈值")
    p.add_argument("task_dir", help="任务目录（需要有 messages.json）")
    p.add_argument("--thresholds", type=int, nargs="+", default=[0, 15_000, 25_000, 40_000, 60_000])
    p.add_argument("--keep-recent-turns", type=int, default=2)
    p.add_argument("--hit-price", type=float, default=DEFAULT_HIT_PRICE, help="缓存命中单价（相对值）")
    p.add_argument("--miss-price", type=float, default=DEFAULT_MISS_PRICE, help="缓存未命中单价（相对值）")
    args = p.parse_args(argv)

    task_dir = Path(args.task_dir)
    if not (task_dir / MESSAGES_FILE).exists():
        console.print(f"[red]找不到 {task_dir / MESSAGES_FILE}[/]")
        return 1
    messages, restored = restore_original_history(task_dir)
    if restored:
        console.print(f"[dim]已用 trace.jsonl 还原 {restored} 条被压缩的工具结果，按未压缩的历史模拟。[/]")
    left = sum(1 for m in messages if m["role"] == "tool" and (m.get("content") or "").startswith(COMPACTED_MARKER))
    if left:
        console.print(f"[yellow]仍有 {left} 条被压缩的结果无法还原（trace 缺失），模拟结果会偏低。[/]")
    tools = load_tools_schema()

    results = [simulate(messages, t, tools, args.keep_recent_turns) for t in args.thresholds]
    base = results[0]
    t = Table(title=f"离线模拟：{Path(args.task_dir).name}", header_style="bold")
    for col in ("阈值", "压缩次数", "累计输入", "未命中缓存", "峰值上下文", "输入成本(相对)", "相对第一行"):
        t.add_column(col, justify="right")
    for r in results:
        cost, base_cost = r.cost(args.hit_price, args.miss_price), base.cost(args.hit_price, args.miss_price)
        delta = (cost - base_cost) / base_cost if base_cost else 0
        color = "green" if delta < 0 else ("red" if delta > 0 else "")
        t.add_row(
            "不压缩" if r.threshold == 0 else str(r.threshold),
            str(r.compactions),
            str(r.total_input),
            str(r.uncached_input),
            str(r.peak_context),
            f"{cost / 1000:.1f}",
            f"[{color}]{delta:+.0%}[/]" if color else "—",
        )
    console.print(t)
    console.print(
        f"[dim]成本模型：命中 {args.hit_price} / 未命中 {args.miss_price}（相对单价）。"
        "重放的是原运行中模型的决策，压缩后模型的实际行为可能不同。[/]"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
