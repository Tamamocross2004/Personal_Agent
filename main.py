"""命令行入口（阶段 0：纯 LLM 对话，尚无工具）。

用法：
    python main.py "介绍一下你自己"            # 单次提问
    python main.py                              # 进入多轮对话，输入 exit 退出
    python main.py --provider qwen "你好"       # 临时切换厂商
    python main.py --model deepseek-reasoner "1+1=?"
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

import openai
from rich.console import Console
from rich.logging import RichHandler
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from agent.config import PROVIDERS, load_llm_config
from agent.llm import LLMClient

console = Console()

SYSTEM_PROMPT = "你是一名严谨的个人研究助理。回答使用中文，条理清晰，不确定的内容要明确说明。"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="个人研究助理 Agent（阶段 0）")
    p.add_argument("question", nargs="?", help="要提问的问题；省略则进入多轮对话")
    p.add_argument("--provider", choices=list(PROVIDERS), help="覆盖 .env 中的 LLM_PROVIDER")
    p.add_argument("--model", help="覆盖模型名")
    p.add_argument("-v", "--verbose", action="store_true", help="打印调试日志（含重试信息）")
    return p.parse_args(argv)


def print_usage(llm: LLMClient) -> None:
    u = llm.usage
    t = Table(title="Token 用量", show_header=True, header_style="bold")
    t.add_column("调用次数", justify="right")
    t.add_column("输入 tokens", justify="right")
    t.add_column("输出 tokens", justify="right")
    t.add_column("合计", justify="right")
    t.add_column("耗时(s)", justify="right")
    t.add_row(
        str(u.calls),
        str(u.prompt_tokens),
        str(u.completion_tokens),
        str(u.total_tokens),
        f"{u.total_seconds:.2f}",
    )
    console.print(t)


def answer(llm: LLMClient, messages: list[dict[str, Any]]) -> str:
    with console.status("思考中…"):
        resp = llm.chat(messages)
    text = resp.choices[0].message.content or ""
    console.print(Panel(Markdown(text), title=f"[bold]{llm.config.model}[/]", border_style="cyan"))
    return text


def repl(llm: LLMClient) -> None:
    """多轮对话：演示 messages 列表如何承载上下文 —— 这正是 Agent 记忆的起点。"""
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    console.print("[dim]进入多轮对话，输入 exit / quit 退出。[/]")
    while True:
        try:
            q = console.input("[bold green]你> [/]").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            continue
        if q.lower() in {"exit", "quit"}:
            break
        messages.append({"role": "user", "content": q})
        try:
            text = answer(llm, messages)
        except openai.APIError as e:
            messages.pop()  # 失败的这轮不计入历史
            console.print(f"[red]调用失败：{e}[/]")
            continue
        messages.append({"role": "assistant", "content": text})


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING,  # 第三方库（httpx 等）只显示警告以上，避免刷屏
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False)],
    )
    if args.verbose:
        logging.getLogger("agent").setLevel(logging.DEBUG)

    try:
        cfg = load_llm_config(provider=args.provider, model=args.model)
    except ValueError as e:
        console.print(f"[red]配置错误：{e}[/]")
        return 2

    llm = LLMClient(cfg)
    console.print(f"[dim]厂商：{cfg.provider} | 模型：{cfg.model} | 接口：{cfg.base_url}[/]")

    try:
        if args.question:
            answer(
                llm,
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": args.question},
                ],
            )
        else:
            repl(llm)
    except openai.AuthenticationError:
        console.print("[red]鉴权失败：请检查 API Key 是否正确、是否与所选厂商匹配。[/]")
        return 1
    except openai.APIError as e:
        console.print(f"[red]调用失败：{type(e).__name__}: {e}[/]")
        return 1
    finally:
        if llm.usage.calls:
            print_usage(llm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
