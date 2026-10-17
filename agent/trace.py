"""运行日志（trace）：把 Agent 的每个事件写成 JSONL，一行一个事件。

为什么用 JSONL：边运行边追加写入，即使中途崩溃或 Ctrl+C，已经发生的步骤也都在文件里；
每行独立，方便用 grep / pandas 分析，也方便 replay 逐行回放。

任务目录下会有：
  trace.jsonl    事件流：每次模型调用的 token 与耗时、每次工具调用的参数 / 结果 / 耗时……
  messages.json  任务结束时完整的对话历史（发给模型的原始内容），用于深入排查
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import IO, Any, Callable

from agent.loop import AgentEvent, EventHandler

TRACE_FILE = "trace.jsonl"
MESSAGES_FILE = "messages.json"


class TraceWriter:
    """可直接作为 Agent 的 on_event 回调。第一次写入时才创建目录和文件。"""

    def __init__(self, path: Path):
        self.path = path
        self._f: IO[str] | None = None

    def __call__(self, ev: AgentEvent) -> None:
        if self._f is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._f = self.path.open("a", encoding="utf-8")
        record = {"ts": datetime.now().isoformat(timespec="milliseconds"), "kind": ev.kind, **ev.data}
        self._f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._f.flush()  # 每行立即落盘：崩溃时不丢日志

    def close(self) -> None:
        if self._f:
            self._f.close()
            self._f = None


def read_trace(path: Path) -> list[AgentEvent]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        kind = rec.pop("kind")
        events.append(AgentEvent(kind, rec))
    return events


def save_messages(path: Path, messages: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(messages, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def fanout(*handlers: EventHandler | None) -> Callable[[AgentEvent], None]:
    """把一个事件同时分发给多个处理器（例如：终端展示 + 写日志）。"""
    active = [h for h in handlers if h]

    def handle(ev: AgentEvent) -> None:
        for h in active:
            h(ev)

    return handle
