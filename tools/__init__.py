"""Agent 可调用的工具。"""

from tools.basic import BASIC_TOOLS
from tools.registry import Tool, ToolRegistry, ToolResult, tool


def default_registry() -> ToolRegistry:
    """当前阶段 Agent 默认可用的全部工具。后续阶段在这里追加搜索、读网页等工具。"""
    return ToolRegistry(list(BASIC_TOOLS))


__all__ = ["Tool", "ToolRegistry", "ToolResult", "tool", "default_registry"]
