"""Agent 可调用的工具。"""

from __future__ import annotations

from pathlib import Path

import httpx

from tools.basic import calculator, days_between, get_current_time
from tools.fetch import make_fetch_url
from tools.registry import Tool, ToolRegistry, ToolResult, tool
from tools.report import make_write_report
from tools.search import DDGSBackend, SearchBackend, make_web_search


def default_registry(
    task_dir: Path,
    search_backend: SearchBackend | None = None,
    http_client: httpx.Client | None = None,
) -> ToolRegistry:
    """为一次研究任务组装全部工具。

    search / fetch / report 依赖外部资源（搜索后端、HTTP 客户端、任务目录），
    所以用工厂函数创建，而不是写死成全局函数 —— 测试时可以注入假的后端。
    """
    return ToolRegistry(
        [
            make_web_search(search_backend or DDGSBackend()),
            make_fetch_url(http_client),
            make_write_report(task_dir),
            calculator,
            days_between,
            get_current_time,
        ]
    )


__all__ = ["Tool", "ToolRegistry", "ToolResult", "tool", "default_registry"]
