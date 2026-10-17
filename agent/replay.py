"""回放一次任务的运行日志：不调用模型、不花钱，按时间线重新展示整个过程。

用法：
    python -m agent.replay                         # 回放 workspace 下最近的一次任务
    python -m agent.replay workspace/20261017-...  # 指定任务目录（或直接给 trace.jsonl 路径）
    python -m agent.replay <目录> --full            # 完整显示工具结果和参数
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent.display import console, show_event, usage_table
from agent.trace import TRACE_FILE, read_trace
from agent.workspace import WORKSPACE_ROOT


def find_trace(arg: str | None, root: Path = WORKSPACE_ROOT) -> Path:
    if arg:
        p = Path(arg)
        return p / TRACE_FILE if p.is_dir() else p
    traces = sorted(root.glob(f"*/{TRACE_FILE}"), key=lambda x: x.stat().st_mtime)
    if not traces:
        raise FileNotFoundError(f"{root} 下还没有任何运行日志")
    return traces[-1]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="回放 Agent 运行日志")
    p.add_argument("path", nargs="?", help="任务目录或 trace.jsonl；省略则回放最近一次")
    p.add_argument("--full", action="store_true", help="完整显示工具结果和参数")
    args = p.parse_args(argv)

    try:
        path = find_trace(args.path)
        events = read_trace(path)
    except (FileNotFoundError, OSError) as e:
        console.print(f"[red]{e}[/]")
        return 1

    console.print(f"[dim]回放：{path}[/]")
    calls = prompt = completion = 0
    seconds = 0.0
    for ev in events:
        show_event(ev, full=args.full)
        if ev.kind == "llm_call":
            calls += 1
            prompt += ev.data.get("prompt_tokens", 0)
            completion += ev.data.get("completion_tokens", 0)
            seconds += ev.data.get("seconds", 0)
    if calls:
        console.print(usage_table([(calls, prompt, completion, prompt + completion, f"{seconds:.2f}")]))
    if not any(ev.kind == "run_end" for ev in events):
        console.print("[yellow]日志没有 run_end 事件：这次运行可能被强制终止了。[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
