"""write_report：把研究报告写到本次任务的工作目录。

文件名来自模型，属于不可信输入：只取文件名部分、过滤非法字符，
确保它只能写进任务目录，不能用 "../../" 之类的路径写到别处。

阶段 5：保存后附上【自动检查】结果。提示词里要求模型写报告前自检，但实测它不一定做（或没机会做）；
能用代码检查的部分就用代码检查，把结果交给模型处理：
- 计划中还有哪些子任务没完成；
- 报告里出现的 URL 有哪些不在笔记来源中（阶段 3、4 实测模型会引用没读过的网页）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from agent.planner import load_plan, unfinished_tasks
from tools.registry import Tool, tool
from tools.session import ResearchSession

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')  # Windows 文件名非法字符
# 报告里的 URL：遇到空白、Markdown 的括号/尖括号、引号或中文标点就结束
_URL = re.compile(r"https?://[^\s<>()\[\]\"'`，。；、）】》]+")


def safe_filename(name: str, default: str = "report.md") -> str:
    name = name.replace("\\", "/").split("/")[-1]  # 丢掉任何目录部分
    name = _ILLEGAL.sub("_", name).strip(" .")
    if not name:
        name = default
    if not name.lower().endswith(".md"):
        name += ".md"
    return name[:120]


def report_urls(text: str) -> list[str]:
    """按出现顺序列出报告中的 URL（去重，去掉末尾的英文标点）。"""
    seen: dict[str, None] = {}
    for m in _URL.finditer(text):
        seen.setdefault(m.group(0).rstrip(".,;:!?"), None)
    return list(seen)


def check_report(text: str, task_dir: Path, session: ResearchSession | None) -> str:
    """对已保存的完整报告做机械检查，返回给模型看的文字。"""
    problems = []
    unfinished = unfinished_tasks(load_plan(task_dir))
    if unfinished:
        items = "；".join(f"{t.get('id')} {t.get('title', '')}（{t.get('status')}）" for t in unfinished)
        problems.append(
            f"计划中还有未完成的子任务：{items}。"
            "如果报告已覆盖，用 update_plan 标为 done；来不及研究的标为 skipped，并在报告里写明。"
        )
    if session is not None:
        # has_note_for 同时匹配请求地址和重定向后的地址：模型引用的可能是跳转前的 URL
        missing = [u for u in report_urls(text) if not session.has_note_for(u)]
        if missing:
            problems.append(
                "以下 URL 不在笔记来源中（没读过原文，或读过但没记笔记）："
                + "、".join(missing)
                + "。请从参考资料中删除，或先 fetch_url 阅读并 save_note 后再引用。"
            )
    if not problems:
        return "【自动检查】通过：计划中的子任务都已处理，报告中的 URL 都来自笔记。"
    return (
        "【自动检查】发现以下问题（分段写报告、后面还要追加时，与尚未写的部分有关的问题可以先忽略）：\n"
        + "\n".join(f"- {p}" for p in problems)
    )


def make_write_report(task_dir: Path, session: ResearchSession | None = None) -> Tool:
    """session：本任务的笔记等状态，用于自动检查报告引用。不传则只检查计划。"""

    @tool(name="write_report")
    def write_report(
        content: Annotated[str, Field(description="完整的 Markdown 报告内容")],
        filename: Annotated[str, Field(description="文件名，例如 report.md")] = "report.md",
        mode: Annotated[
            Literal["overwrite", "append"],
            Field(description="overwrite 覆盖写入（默认）；append 追加到文件末尾，用于分段写长报告"),
        ] = "overwrite",
    ) -> str:
        """把研究报告（Markdown）保存到本地。报告较长时可分多次写：先 overwrite 写开头，再 append 追加后续章节。
        返回值附有对报告的自动检查结果。"""
        if not content.strip():
            raise ValueError("报告内容为空")
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / safe_filename(filename)
        if mode == "append" and path.exists():
            with path.open("a", encoding="utf-8") as f:
                f.write("\n\n" + content)
            full = path.read_text(encoding="utf-8")
            saved = f"已追加到报告：{path}（本次 {len(content)} 字符，文件共 {len(full)} 字符）"
        else:
            path.write_text(content, encoding="utf-8")
            full = content
            saved = f"报告已保存：{path}（{len(content)} 字符）"
        return saved + "\n\n" + check_report(full, task_dir, session)

    return write_report
