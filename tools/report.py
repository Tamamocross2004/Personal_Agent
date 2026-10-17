"""write_report：把研究报告写到本次任务的工作目录。

文件名来自模型，属于不可信输入：只取文件名部分、过滤非法字符，
确保它只能写进任务目录，不能用 "../../" 之类的路径写到别处。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

from pydantic import Field

from tools.registry import Tool, tool

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')  # Windows 文件名非法字符


def safe_filename(name: str, default: str = "report.md") -> str:
    name = name.replace("\\", "/").split("/")[-1]  # 丢掉任何目录部分
    name = _ILLEGAL.sub("_", name).strip(" .")
    if not name:
        name = default
    if not name.lower().endswith(".md"):
        name += ".md"
    return name[:120]


def make_write_report(task_dir: Path) -> Tool:
    @tool(name="write_report")
    def write_report(
        content: Annotated[str, Field(description="完整的 Markdown 报告内容")],
        filename: Annotated[str, Field(description="文件名，例如 report.md")] = "report.md",
    ) -> str:
        """把最终研究报告（Markdown）保存到本地。研究完成后调用一次；再次调用同名文件会覆盖。"""
        if not content.strip():
            raise ValueError("报告内容为空")
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / safe_filename(filename)
        path.write_text(content, encoding="utf-8")
        return f"报告已保存：{path}（{len(content)} 字符）"

    return write_report
