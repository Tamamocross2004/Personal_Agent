"""前缀缓存探针：同一段对话历史，换几种请求写法各发一次，看厂商的缓存命中多少。

用法：
    python -m agent.cache_probe                 # 用 .env 里的厂商
    python -m agent.cache_probe --provider qwen

为什么需要它：阶段 4、5 实测，强制收尾那一次调用几乎不命中缓存——
"不发工具"时 4.2 万输入只命中 384，"照常发工具 + tool_choice=none"时 3.5 万输入只命中 640，
而平时每一步都能命中 85% 以上。推测：厂商把工具说明书渲染在提示开头，tool_choice 不同时开头就不同。
这个脚本直接验证这一点，换厂商时也可以先跑一遍，看哪种写法能保住缓存。

做法：开头放一个随机串，保证第 1 次请求一定不命中（不受以前运行的影响）；之后各变体与它对比。
每次请求只要 1 个输出 token，总花费约 2 万输入 token。
"""

from __future__ import annotations

import argparse
import tempfile
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from rich.table import Table

from agent.config import PROVIDERS, Settings, load_llm_config
from agent.display import console
from agent.llm import LLMClient
from agent.loop import _cached_tokens
from agent.prompts import research_system_prompt
from tools import default_registry

FILLER = "这是一段用来撑长上下文的模拟网页原文，内容本身没有意义。" * 120


def build_messages() -> list[dict[str, Any]]:
    nonce = uuid.uuid4().hex
    return [
        {"role": "system", "content": f"[probe {nonce}]\n" + research_system_prompt(date.today().isoformat())},
        {"role": "user", "content": "测试问题：请忽略下面的内容，只回复“好”。"},
        {"role": "assistant", "content": "好。"},
        {"role": "user", "content": FILLER},
        {"role": "assistant", "content": "好。"},
        {"role": "user", "content": FILLER.replace("模拟", "仿真")},
    ]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="测试不同请求写法对前缀缓存的影响")
    p.add_argument("--provider", choices=list(PROVIDERS))
    p.add_argument("--model")
    p.add_argument("--sleep", type=float, default=2.0, help="两次请求之间等待的秒数（缓存写入可能有延迟）")
    args = p.parse_args(argv)

    cfg = load_llm_config(provider=args.provider, model=args.model, settings=Settings())
    llm = LLMClient(cfg)
    with tempfile.TemporaryDirectory() as d:
        tools = default_registry(Path(d)).schemas()
    base = build_messages()
    extra = base + [{"role": "user", "content": "【系统通知】进入收尾阶段。"}]

    # (说明, messages, tools, 额外参数)
    variants: list[tuple[str, list, Any, dict]] = [
        ("1 首次请求（工具 + auto，冷启动）", base, tools, {}),
        ("2 完全相同的请求", base, tools, {}),
        ("3 工具 + tool_choice=none（旧收尾写法）", base, tools, {"tool_choice": "none"}),
        ("4 不发工具（更早的收尾写法）", base, None, {}),
        ("5 工具 + auto，末尾追加一条 user（新收尾写法）", extra, tools, {}),
    ]

    table = Table(title=f"前缀缓存探针：{cfg.provider} / {cfg.model}")
    for col in ("请求写法", "输入 tokens", "命中缓存", "命中率"):
        table.add_column(col, justify="left" if col == "请求写法" else "right")
    for i, (label, messages, t, kw) in enumerate(variants):
        if i:
            time.sleep(args.sleep)
        try:
            resp = llm.chat(messages, tools=t, max_tokens=1, **kw)
        except Exception as e:  # noqa: BLE001 —— 某种写法不被支持也是有用的结论
            table.add_row(label, "-", "-", f"[red]{type(e).__name__}[/]")
            continue
        prompt = resp.usage.prompt_tokens if resp.usage else 0
        cached = _cached_tokens(resp.usage)
        table.add_row(label, str(prompt), str(cached), f"{cached / prompt:.0%}" if prompt else "-")
    console.print(table)
    console.print(
        "[dim]第 2 行命中率高说明缓存正常工作；第 3、4 行明显偏低，说明 tool_choice / 工具说明书会改变提示开头；"
        "第 5 行应接近第 2 行。[/]"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
