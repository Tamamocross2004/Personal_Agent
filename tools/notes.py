"""笔记工具：save_note / read_notes。

为什么需要笔记：网页原文在上下文里每一轮都要重发，非常贵；读完后把有用的要点记下来，
原文就可以从上下文移走（见 agent/context.py）。笔记同时保存在任务目录的 notes.md，方便你自己查看。

来源校验：source_url 必须是本次任务里用 fetch_url 成功读过的网页。
阶段 3 实测发现模型会在报告里引用只在搜索摘要中见过、从未读过的网页 ——
在记笔记这一步就把关，报告以笔记为依据，就能从源头减少这类问题。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field

from tools.registry import Tool, tool
from tools.session import Note, ResearchSession

NOTES_FILE = "notes.md"
MAX_NOTE_CHARS = 3000


def make_notes_tools(task_dir: Path, session: ResearchSession) -> list[Tool]:
    path = task_dir / NOTES_FILE

    @tool(name="save_note")
    def save_note(
        content: Annotated[
            str,
            Field(description="要点：保留具体数字、日期、版本号、关键原话，写成能独立看懂的几句话或要点列表"),
        ],
        source_url: Annotated[str, Field(description="信息来源的网页 URL，必须是用 fetch_url 读过的网页")],
        topic: Annotated[str, Field(description="简短主题，例如“GLM-5.3 基准分数”")] = "",
    ) -> str:
        """记录从某个网页中提炼出的要点。每读完一个有用的网页就记一条；网页原文稍后会被移出上下文，只有笔记会保留。"""
        text = content.strip()
        if not text:
            raise ValueError("笔记内容为空")
        if len(text) > MAX_NOTE_CHARS:
            raise ValueError(f"笔记过长（{len(text)} 字符，上限 {MAX_NOTE_CHARS}）：请只保留要点，或拆成几条")
        page = session.find_page(source_url)
        if page is None:
            raise ValueError(
                f"{source_url} 不是本次任务中用 fetch_url 成功读过的网页。"
                "笔记只能记录读过的原文内容；如果信息来自搜索摘要，请先用 fetch_url 阅读原文。"
            )
        note = Note(len(session.notes) + 1, text, page.url, page.title, topic.strip())
        session.notes.append(note)

        task_dir.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(_format_note(note) + "\n\n")
        return f"已保存笔记 #{note.id}（本任务共 {len(session.notes)} 条）"

    @tool(name="read_notes")
    def read_notes() -> str:
        """读取本次任务的全部笔记。写报告前调用，报告内容应以笔记为依据。"""
        if not session.notes:
            return "还没有任何笔记。"
        sources = {n.source_url for n in session.notes}
        header = f"共 {len(session.notes)} 条笔记，来自 {len(sources)} 个网页：\n\n"
        return header + "\n\n".join(_format_note(n) for n in session.notes)

    return [save_note, read_notes]


def _format_note(n: Note) -> str:
    title = f"#{n.id}" + (f" {n.topic}" if n.topic else "")
    source = f"{n.source_title} — {n.source_url}" if n.source_title else n.source_url
    return f"### {title}\n来源：{source}\n\n{n.content}"
