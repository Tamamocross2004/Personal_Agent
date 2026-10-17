"""write_report：把研究报告写到本次任务的工作目录。

文件名来自模型，属于不可信输入：只取文件名部分、过滤非法字符，
确保它只能写进任务目录，不能用 "../../" 之类的路径写到别处。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

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
        mode: Annotated[
            Literal["overwrite", "append"],
            Field(description="overwrite 覆盖写入（默认）；append 追加到文件末尾，用于分段写长报告"),
        ] = "overwrite",
    ) -> str:
        """把研究报告（Markdown）保存到本地。报告较长时可分多次写：先 overwrite 写开头，再 append 追加后续章节。"""
        if not content.strip():
            raise ValueError("报告内容为空")
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / safe_filename(filename)
        if mode == "append" and path.exists():
            with path.open("a", encoding="utf-8") as f:
                f.write("\n\n" + content)
            return f"已追加到报告：{path}（本次 {len(content)} 字符，文件共 {len(path.read_text(encoding='utf-8'))} 字符）"
        path.write_text(content, encoding="utf-8")
        return f"报告已保存：{path}（{len(content)} 字符）"

    return write_report
