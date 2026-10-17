"""命令行入口。

用法：
    python main.py "对比一下 DeepSeek 和通义千问的 Agent 能力"   # 研究模式：单个问题
    python main.py                                     # 研究模式：交互式，每个问题独立运行
    python main.py --chat                              # 阶段 0 的纯对话模式（无工具）
    python main.py --provider qwen "..."               # 临时切换厂商
    python main.py --max-steps 30 "..."                # 调整最大步数

报告保存在 workspace/<时间>-<问题>/ 目录下。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from typing import Any

import openai
from rich.console import Console
from rich.logging import RichHandler
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent.config import PROVIDERS, load_llm_config
from agent.llm import LLMClient
from agent.loop import Agent, AgentEvent, pretty_arguments
from agent.prompts import CHAT_SYSTEM_PROMPT, research_system_prompt
from agent.workspace import new_task_dir
from tools import default_registry

console = Console()

RESULT_PREVIEW_CHARS = 300  # 终端里工具结果只预览前 N 个字符（模型收到的是完整结果）


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="个人研究助理 Agent")
    p.add_argument("question", nargs="?", help="要提问的问题；省略则进入交互模式")
    p.add_argument("--chat", action="store_true", help="纯对话模式（不使用工具，阶段 0 行为）")
    p.add_argument("--provider", choices=list(PROVIDERS), help="覆盖 .env 中的 LLM_PROVIDER")
    p.add_argument("--model", help="覆盖模型名")
    p.add_argument("--max-steps", type=int, default=20, help="Agent 最大循环步数（默认 20）")
    p.add_argument("-v", "--verbose", action="store_true", help="打印调试日志（含重试信息）")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# 展示
# ---------------------------------------------------------------------------


def show_event(ev: AgentEvent) -> None:
    """把 Agent 循环中的每个事件打印出来 —— 读懂 Agent 的关键就是看清每一步。"""
    d = ev.data
    if ev.kind == "step":
        console.rule(f"[dim]Step {d['step']}[/]", style="dim")
    elif ev.kind == "thought":
        console.print(Text(d["text"], style="italic dim"))
    elif ev.kind == "tool_call":
        console.print(
            Text.assemble(
                ("→ 调用 ", "yellow"),
                (d["name"], "bold yellow"),
                ("(", "yellow"),
                (pretty_arguments(d["arguments"], max_value_chars=80), "cyan"),
                (")", "yellow"),
            )
        )
    elif ev.kind == "tool_result":
        content = d["content"]
        preview = content if len(content) <= RESULT_PREVIEW_CHARS else content[:RESULT_PREVIEW_CHARS] + "…"
        style = "red" if d["is_error"] else "green"
        console.print(Text.assemble(("← ", style), (preview, style)))
    elif ev.kind == "final":
        title = "最终回答" if d["stop_reason"] == "final_answer" else "最终回答（已达步数上限）"
        console.print(Panel(Markdown(d["text"] or "（空）"), title=f"[bold]{title}[/]", border_style="cyan"))


def print_usage(llm: LLMClient) -> None:
    u = llm.usage
    t = Table(title="Token 用量", show_header=True, header_style="bold")
    for col in ("调用次数", "输入 tokens", "输出 tokens", "合计", "耗时(s)"):
        t.add_column(col, justify="right")
    t.add_row(
        str(u.calls),
        str(u.prompt_tokens),
        str(u.completion_tokens),
        str(u.total_tokens),
        f"{u.total_seconds:.2f}",
    )
    console.print(t)


def read_input() -> str | None:
    """读取一行输入；遇到 exit/quit/Ctrl+C/EOF 返回 None。"""
    while True:
        try:
            q = console.input("[bold green]你> [/]").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not q:
            continue
        if q.lower() in {"exit", "quit"}:
            return None
        return q


# ---------------------------------------------------------------------------
# 两种模式
# ---------------------------------------------------------------------------


def research(llm: LLMClient, question: str, max_steps: int) -> None:
    """运行一次研究任务：每个问题有独立的工作目录和工具实例。"""
    task_dir = new_task_dir(question)
    agent = Agent(
        llm,
        default_registry(task_dir),
        system_prompt=research_system_prompt(date.today().isoformat()),
        max_steps=max_steps,
        on_event=show_event,
    )
    agent.run(question)

    reports = sorted(task_dir.glob("*.md")) if task_dir.exists() else []
    for r in reports:
        console.print(f"[bold green]报告已保存：[/]{r}")
    if not reports:
        console.print("[dim]本次没有生成报告文件。[/]")


def run_agent(llm: LLMClient, question: str | None, max_steps: int) -> None:
    if question:
        research(llm, question, max_steps)
        return
    console.print("[dim]研究模式：每个问题独立运行（暂无跨问题记忆），输入 exit 退出。[/]")
    while (q := read_input()) is not None:
        try:
            research(llm, q, max_steps)
        except openai.AuthenticationError:
            raise
        except openai.APIError as e:
            console.print(f"[red]调用失败：{type(e).__name__}: {e}[/]")


def run_chat(llm: LLMClient, question: str | None) -> None:
    """阶段 0 的纯对话：演示 messages 列表如何承载上下文。"""
    messages: list[dict[str, Any]] = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]

    def answer(q: str) -> None:
        messages.append({"role": "user", "content": q})
        with console.status("思考中…"):
            resp = llm.chat(messages)
        text = resp.choices[0].message.content or ""
        console.print(Panel(Markdown(text), title=f"[bold]{llm.config.model}[/]", border_style="cyan"))
        messages.append({"role": "assistant", "content": text})

    if question:
        answer(question)
        return
    console.print("[dim]纯对话模式（无工具），输入 exit 退出。[/]")
    while (q := read_input()) is not None:
        try:
            answer(q)
        except openai.AuthenticationError:
            raise
        except openai.APIError as e:
            messages.pop()  # 失败的这轮不计入历史
            console.print(f"[red]调用失败：{e}[/]")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING,  # 第三方库只显示警告以上，避免刷屏
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
    mode = "纯对话" if args.chat else f"研究 Agent（最多 {args.max_steps} 步）"
    console.print(f"[dim]厂商：{cfg.provider} | 模型：{cfg.model} | 模式：{mode}[/]")

    try:
        if args.chat:
            run_chat(llm, args.question)
        else:
            run_agent(llm, args.question, args.max_steps)
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
