"""对比两次（或多次）运行的关键指标，用于验证改动的效果。

用法：
    python -m agent.compare <任务目录A> <任务目录B> [...]
    python -m agent.compare --last 2          # 对比 workspace 下最近的两次运行

指标全部从 trace.jsonl 和报告文件中统计，不调用模型。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from rich.table import Table

from agent.display import STOP_REASON_LABELS, console
from agent.trace import TRACE_FILE, read_trace
from agent.workspace import WORKSPACE_ROOT
from tools.notes import NOTES_FILE
from tools.session import normalize_url

_URL = re.compile(r"https?://[^\s)>\]，。、】]+")


@dataclass
class RunStats:
    name: str
    question: str = ""
    stop_reason: str = ""
    steps: int = 0
    seconds: float = 0.0
    llm_calls: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    final_context: int = 0
    peak_context: int = 0
    searches: int = 0
    fetches_ok: int = 0
    notes: int = 0
    compactions: int = 0
    compacted_saved: int = 0
    report_chars: int = 0
    refs: int = 0
    refs_unread: list[str] = field(default_factory=list)


def collect(task_dir: Path) -> RunStats:
    events = read_trace(task_dir / TRACE_FILE)
    st = RunStats(task_dir.name)
    calls: dict[str, tuple[str, str]] = {}
    fetched: set[str] = set()

    for ev in events:
        d = ev.data
        if ev.kind == "run_start":
            st.question = d.get("question", "")
        elif ev.kind == "llm_call":
            st.llm_calls += 1
            p = d.get("prompt_tokens", 0)
            st.input_tokens += p
            st.cached_tokens += d.get("cached_tokens", 0) or 0
            st.output_tokens += d.get("completion_tokens", 0)
            st.final_context = p
            st.peak_context = max(st.peak_context, p)
        elif ev.kind == "tool_call":
            calls[d["id"]] = (d["name"], d["arguments"])
        elif ev.kind == "tool_result" and not d.get("is_error"):
            name = d["name"]
            if name == "web_search":
                st.searches += 1
            elif name == "save_note":
                st.notes += 1
            elif name == "fetch_url":
                st.fetches_ok += 1
                try:
                    fetched.add(normalize_url(json.loads(calls[d["id"]][1])["url"]))
                except (KeyError, ValueError, TypeError):
                    pass
                m = re.search(r"^URL: (\S+)", d.get("content", ""), re.MULTILINE)
                if m:  # 重定向后的最终地址
                    fetched.add(normalize_url(m.group(1)))
        elif ev.kind == "compaction":
            st.compactions += 1
            st.compacted_saved += d.get("tokens_before", 0) - d.get("tokens_after", 0)
        elif ev.kind == "run_end":
            st.stop_reason = d.get("stop_reason", "")
            st.steps = d.get("steps", 0)
            st.seconds = d.get("seconds", 0.0)

    reports = sorted(
        (p for p in task_dir.glob("*.md") if p.name != NOTES_FILE), key=lambda p: p.stat().st_size
    )
    if reports:
        text = reports[-1].read_text(encoding="utf-8")
        st.report_chars = len(text)
        idx = max(text.rfind("## 参考"), text.rfind("## References"))
        refs = list(dict.fromkeys(_URL.findall(text[idx:] if idx >= 0 else "")))
        st.refs = len(refs)
        st.refs_unread = [u for u in refs if normalize_url(u) not in fetched]
    return st


ROWS: list[tuple[str, str, bool]] = [
    # (标签, 字段, 数值越小越好？)
    ("结束方式", "stop_reason", False),
    ("步数", "steps", True),
    ("耗时(s)", "seconds", True),
    ("模型调用次数", "llm_calls", True),
    ("累计输入 tokens", "input_tokens", True),
    ("  其中缓存命中", "cached_tokens", False),
    ("累计输出 tokens", "output_tokens", True),
    ("最后一次上下文", "final_context", True),
    ("峰值上下文", "peak_context", True),
    ("搜索次数", "searches", False),
    ("成功读网页", "fetches_ok", False),
    ("笔记条数", "notes", False),
    ("上下文压缩次数", "compactions", False),
    ("压缩节省(估算)", "compacted_saved", False),
    ("报告字数", "report_chars", False),
    ("参考资料条数", "refs", False),
    ("其中未读过的", "refs_unread", True),
]


def render(stats: list[RunStats]) -> Table:
    t = Table(title="运行对比", show_header=True, header_style="bold")
    t.add_column("指标")
    for i, s in enumerate(stats):
        t.add_column(f"{chr(65 + i)}", justify="right")
    if len(stats) == 2:
        t.add_column("B 相对 A", justify="right")

    for label, attr, lower_better in ROWS:
        values = [getattr(s, attr) for s in stats]
        if attr == "stop_reason":
            cells = [STOP_REASON_LABELS.get(v, v) for v in values]
        elif attr == "refs_unread":
            values = [len(v) for v in values]
            cells = [str(v) for v in values]
        elif attr == "cached_tokens":
            cells = [f"{v} ({v / s.input_tokens:.0%})" if s.input_tokens else str(v) for v, s in zip(values, stats)]
        else:
            cells = [f"{v:.1f}" if isinstance(v, float) else str(v) for v in values]
        row = [label, *cells]
        if len(stats) == 2:
            row.append(_delta(values[0], values[1], lower_better))
        t.add_row(*row)
    return t


def _delta(a, b, lower_better: bool) -> str:
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or a == b:
        return ""
    if a == 0:
        return "新增"
    pct = (b - a) / a
    better = (pct < 0) == lower_better
    color = "green" if better else "red"
    return f"[{color}]{pct:+.0%}[/]"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="对比多次运行的关键指标")
    p.add_argument("dirs", nargs="*", help="任务目录")
    p.add_argument("--last", type=int, help="对比 workspace 下最近的 N 次运行")
    args = p.parse_args(argv)

    dirs = [Path(d) for d in args.dirs]
    if args.last:
        traces = sorted(WORKSPACE_ROOT.glob(f"*/{TRACE_FILE}"), key=lambda x: x.stat().st_mtime)
        dirs += [t.parent for t in traces[-args.last :]]
    dirs = [d for d in dirs if (d / TRACE_FILE).exists()]
    if not dirs:
        console.print("[red]没有找到可对比的运行日志（目录下需要有 trace.jsonl）[/]")
        return 1

    stats = [collect(d) for d in dirs]
    for i, s in enumerate(stats):
        console.print(f"[bold]{chr(65 + i)}[/] {s.name}  [dim]{s.question}[/]")
    console.print(render(stats))
    for i, s in enumerate(stats):
        if s.refs_unread:
            console.print(f"[yellow]{chr(65 + i)} 的参考资料中未用 fetch_url 读过的：[/]")
            for u in s.refs_unread:
                console.print(f"  [dim]{u}[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
