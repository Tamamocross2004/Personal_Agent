"""终端展示：把 Agent 事件渲染成人能读懂的样子。main.py（实时）和 replay.py（回放）共用。"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent.loop import AgentEvent, pretty_arguments

console = Console()

RESULT_PREVIEW_CHARS = 300  # 工具结果只预览前 N 个字符（模型收到的是完整结果）

STOP_REASON_LABELS = {
    "final_answer": "正常完成",
    "max_steps": "达到最大步数，已强制收尾",
    "budget": "token 预算用完，已强制收尾",
    "loop_detected": "检测到重复调用循环，已强制收尾",
    "interrupted": "用户中断",
    "context_overflow": "上下文超出模型上限",
    "content_filter": "被内容审核拦截",
    "error": "出错结束",
}


def show_event(ev: AgentEvent, full: bool = False) -> None:
    """full=True 时完整显示工具结果（回放排查时用）。"""
    d = ev.data
    k = ev.kind
    if k == "run_start":
        console.print(Text.assemble(("问题：", "bold"), d.get("question", "")))
        console.print(
            f"[dim]工具：{', '.join(d.get('tools', []))} | 最多 {d.get('max_steps')} 步 | "
            f"token 预算 {d.get('token_budget')}[/]"
        )
    elif k == "step":
        console.rule(f"[dim]Step {d['step']}[/]", style="dim")
    elif k == "llm_call":
        finish = d.get("finish_reason")
        extra = f" · finish={finish}" if finish not in (None, "stop", "tool_calls") else ""
        console.print(
            f"[dim]  模型调用 {d.get('seconds', 0):.1f}s · 输入 {d.get('prompt_tokens', 0)} / "
            f"输出 {d.get('completion_tokens', 0)} tokens{extra}[/]"
        )
    elif k == "thought":
        console.print(Text(d["text"], style="italic dim"))
    elif k == "tool_call":
        console.print(
            Text.assemble(
                ("→ 调用 ", "yellow"),
                (d["name"], "bold yellow"),
                ("(", "yellow"),
                (pretty_arguments(d["arguments"], max_value_chars=None if full else 80), "cyan"),
                (")", "yellow"),
            )
        )
    elif k == "tool_result":
        content = d["content"]
        if not full and len(content) > RESULT_PREVIEW_CHARS:
            content = content[:RESULT_PREVIEW_CHARS] + "…"
        style = "red" if d["is_error"] else "green"
        secs = d.get("seconds")
        suffix = f"  [{secs:.1f}s]" if secs else ""
        console.print(Text.assemble(("← ", style), (content, style), (suffix, "dim")))
    elif k == "notice":
        console.print(Text.assemble(("⚠ ", "bold magenta"), (d["text"], "magenta")))
    elif k == "final":
        reason = d["stop_reason"]
        title = "最终回答" if reason == "final_answer" else f"最终回答（{STOP_REASON_LABELS.get(reason, reason)}）"
        console.print(Panel(Markdown(d["text"] or "（空）"), title=f"[bold]{title}[/]", border_style="cyan"))
    elif k == "run_end":
        reason = d.get("stop_reason", "")
        style = "green" if reason == "final_answer" else "yellow"
        line = (
            f"结束：{STOP_REASON_LABELS.get(reason, reason)} | {d.get('steps')} 步 | "
            f"{d.get('total_tokens')} tokens | {d.get('seconds')}s"
        )
        console.print(f"[{style}]{line}[/]")
        if d.get("error"):
            console.print(f"[red]原因：{d['error']}[/]")


def usage_table(rows: list[tuple[Any, ...]], title: str = "Token 用量") -> Table:
    t = Table(title=title, show_header=True, header_style="bold")
    for col in ("调用次数", "输入 tokens", "输出 tokens", "合计", "耗时(s)"):
        t.add_column(col, justify="right")
    for r in rows:
        t.add_row(*(str(x) for x in r))
    return t
