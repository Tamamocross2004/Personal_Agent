"""write_report：把研究报告写到本次任务的工作目录。

文件名来自模型，属于不可信输入：只取文件名部分、过滤非法字符，
确保它只能写进任务目录，不能用 "../../" 之类的路径写到别处。

保存后附上计划、引用编号、来源身份和本地图片的机械检查，并保存 .quality.json。
阶段 7 的独立模型审稿由 Agent 调度，不在工具线程中偷偷调用模型，保证用量计入预算。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from agent.quality import inspect_report, save_check
from agent.numeric_evidence import arithmetic_errors, price_ratio_errors, report_cost_errors
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
    """对完整报告做机械检查；语义问题另由审稿模型检查。"""
    return inspect_report(text, task_dir, session).feedback()


def required_table_headers(question: str) -> list[tuple[str, ...]]:
    """仅识别明确写在“表头/列名”后的管道分隔英文字段，不猜测任意自然语言格式。"""
    field = r"`?[A-Za-z_][A-Za-z0-9_]*`?"
    pattern = rf"(?:表头|列名)[^|\n]{{0,24}}?({field}(?:\s*\|\s*{field})+)"
    return list(dict.fromkeys(tuple(c.strip().strip('`') for c in m.split('|'))
                              for m in re.findall(pattern, question)))


def missing_table_headers(text: str, required: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    # 代码示例里的表格不算正文表格；必须紧跟 Markdown 分隔行。
    text = re.sub(r"(?ms)^\s*(`{3,}|~{3,}).*?^\s*\1[^\n]*$", "", text)
    lines = text.splitlines()
    headers = set()
    for row, separator in zip(lines, lines[1:]):
        cells = tuple(c.strip().strip('`') for c in row.strip().strip('|').split('|'))
        dividers = separator.strip().strip('|').split('|')
        if len(cells) > 1 and len(cells) == len(dividers) and all(re.fullmatch(r"\s*:?-{3,}:?\s*", c) for c in dividers):
            headers.add(cells)
    return [header for header in required if header not in headers]


def make_write_report(task_dir: Path, session: ResearchSession | None = None, *, question: str = "") -> Tool:
    """session：本任务的笔记等状态，用于自动检查报告引用。不传则只检查计划。"""

    required = required_table_headers(question)

    @tool(name="write_report")
    def write_report(
        content: Annotated[str, Field(description="完整的 Markdown 报告内容")],
        filename: Annotated[str, Field(description="文件名，例如 report.md")] = "report.md",
        mode: Annotated[
            Literal["overwrite", "append"],
            Field(description="overwrite 覆盖写入（默认）；append 追加到文件末尾，用于分段写长报告"),
        ] = "overwrite",
        complete: Annotated[bool, Field(description="是否为完整报告。分段写作的中间段用 false，最后一段用 true；完整稿须满足用户指定表头并接受审稿")] = True,
    ) -> str:
        """把研究报告（Markdown）保存到本地。报告较长时可分多次写：先 overwrite 写开头，再 append 追加后续章节。
        返回值附有对报告的自动检查结果。"""
        if not content.strip():
            raise ValueError("报告内容为空")
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / safe_filename(filename)
        full = (path.read_text(encoding="utf-8") + "\n\n" + content
                if mode == "append" and path.exists() else content)
        missing = missing_table_headers(full, required) if complete else []
        if missing:
            raise ValueError("尚未保存：完整报告缺少用户指定表头（原文及列顺序必须一致）："
                             + "；".join(" | ".join(header) for header in missing)
                             + "。请修正后重试；分段写作中间段请设 complete=false，最后一段设 true。")
        errors = arithmetic_errors(full) if complete else []
        csv_path = task_dir / "costs.csv"
        if complete and csv_path.is_file() and not csv_path.is_symlink():
            with csv_path.open(encoding="utf-8-sig", errors="replace") as stream:
                sample = stream.read(12001)
            if len(sample) <= 12000:
                errors.extend(report_cost_errors(full, sample))
                errors.extend(price_ratio_errors(full, sample))
        if errors:
            raise ValueError("尚未保存：数值检查失败，请核对计算结果：" + "；".join(errors))
        if mode == "append" and path.exists():
            with path.open("a", encoding="utf-8") as f:
                f.write("\n\n" + content)
            full = path.read_text(encoding="utf-8")
            saved = f"已追加到报告：{path}（本次 {len(content)} 字符，文件共 {len(full)} 字符）"
        else:
            path.write_text(content, encoding="utf-8")
            full = content
            saved = f"报告已保存：{path}（{len(content)} 字符）"
        check = inspect_report(full, task_dir, session)
        save_check(path, check)
        return saved + ("\n【分段草稿】尚未完成，不执行语义审稿；最后一段须 complete=true。" if not complete else "") + "\n\n" + check.feedback()

    return write_report
