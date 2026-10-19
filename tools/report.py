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

from pydantic import BaseModel, ConfigDict, Field

from agent.quality import inspect_report, save_check, structure_repair_feedback
from agent.evidence import Claim, write_json
from agent.markdown_tables import markdown_tables
from agent.task_contract import TaskContract, check_contract, explicit_headers, extract_contract, length_repair_hint, needs_brief_reason, save_contract_check
from agent.numeric_evidence import arithmetic_errors, price_ratio_errors, report_cost_errors, scaling_explanation_errors
from tools.registry import Tool, tool
from tools.session import ResearchSession

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')  # Windows 文件名非法字符
# 报告里的 URL：遇到空白、Markdown 的括号/尖括号、引号或中文标点就结束
_URL = re.compile(r"https?://[^\s<>()\[\]\"'`，。；、）】》]+")


class ReportReplacement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    old: str = Field(min_length=1, max_length=6000)
    new: str = Field(max_length=6000)
    expected_occurrences: int = Field(default=1, ge=1, le=20)


def patch_report(original: str, replacements: list[ReportReplacement], base_sha256: str) -> str:
    """所有定位均基于原稿；任一定位失败就拒绝整批，不保存半份修订。"""
    from agent.review import fingerprint
    if base_sha256 != fingerprint(original):
        raise ValueError("报告版本已变化：base_sha256 必须是当前报告的 SHA-256，请核对最新审稿反馈。")
    spans = []
    for replacement in replacements:
        starts = [m.start() for m in re.finditer(re.escape(replacement.old), original)]
        if len(starts) != replacement.expected_occurrences:
            raise ValueError(f"替换原文实际匹配 {len(starts)} 次，预期 {replacement.expected_occurrences} 次；请提供精确原文。")
        spans.extend((start, start + len(replacement.old), replacement.new) for start in starts)
    spans.sort()
    if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
        raise ValueError("替换范围重叠，请合并为一个明确的替换。")
    for start, end, new in reversed(spans):
        original = original[:start] + new + original[end:]
    return original


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
    return list(explicit_headers(question))


def missing_table_headers(text: str, required: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    headers = {header for header, _ in markdown_tables(text)}
    return [header for header in required if header not in headers]


def make_write_report(task_dir: Path, session: ResearchSession | None = None, *, question: str = "", contract: TaskContract | None = None) -> Tool:
    """session：本任务的笔记等状态，用于自动检查报告引用。不传则只检查计划。"""

    initial_contract = contract or extract_contract(question)

    @tool(name="write_report")
    def write_report(
        content: Annotated[str, Field(description="完整的 Markdown 报告内容；patch 模式留空")] = "",
        filename: Annotated[str, Field(description="文件名，例如 report.md")] = "report.md",
        mode: Annotated[
            Literal["overwrite", "append", "patch"],
            Field(description="overwrite 新稿；append 分段追加；审稿后优先 patch 精确替换原文，保留无关内容"),
        ] = "overwrite",
        complete: Annotated[bool, Field(description="是否为完整报告。分段写作的中间段用 false，最后一段用 true；完整稿须满足用户指定表头并接受审稿")] = True,
        replacements: Annotated[list[ReportReplacement], Field(max_length=16, description="patch 模式的原文/新文替换列表；定位均基于修订前原稿")] = [],
        base_sha256: Annotated[str, Field(description="patch 模式必填：最新审稿反馈中的报告 SHA-256")] = "",
        brief_extension_reason: Annotated[str, Field(max_length=600, description="简述稿明显超过默认软目标时，说明具体必答项为何需要这些内容；非硬上限豁免，泛称全面完整不充分")] = "",
        claims: Annotated[list[Claim] | None, Field(max_length=8, description="选择1-8项关键结论：quote逐字摘自本稿；kind为direct/calculated/inference/unknown；supports引用本次user-1、history-1或工具返回的证据ID及逐字excerpt。推断填assumptions，历史填limits。修订稿需重交关联，否则旧关联失效；不自动认证语义")] = None,
    ) -> str:
        """把研究报告（Markdown）保存到本地。报告较长时可分多次写：先 overwrite 写开头，再 append 追加后续章节。
        返回值附有对报告的自动检查结果。"""
        if mode != "patch" and not content.strip():
            raise ValueError("报告内容为空")
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / safe_filename(filename)
        if mode == "patch":
            if content or not replacements or not complete or not path.is_file() or path.is_symlink():
                raise ValueError("patch 要求已有报告、非空 replacements、complete=true，且 content 留空。")
            full = patch_report(path.read_text(encoding="utf-8"), replacements, base_sha256)
            if not full.strip():
                raise ValueError("报告内容为空")
        else:
            if replacements or base_sha256:
                raise ValueError("replacements/base_sha256 仅用于 patch 模式。")
            full = (path.read_text(encoding="utf-8") + "\n\n" + content
                    if mode == "append" and path.exists() else content)
        active_contract = session.task_contract if session and session.task_contract else initial_contract
        contract_check = check_contract(full, active_contract)
        if complete and contract_check['hard_errors']:
            raise ValueError("尚未保存：任务约定检查失败：" + "；".join(contract_check['hard_errors'])
                             + length_repair_hint(contract_check, full) + "草稿不等于完整交付。")
        if complete and needs_brief_reason(contract_check, active_contract) and len(brief_extension_reason.strip()) < 20:
            raise ValueError("尚未保存：用户要求简述，正文明显超过默认软目标。请先删除重复引文、无关背景或额外分析，保留必答结论、依据和局限。"
                             "默认目标不是用户硬上限；如确实需要较长内容，可在 brief_extension_reason 中说明具体必答项与必要性（至少20字符），由审稿核对，不能只称更全面。")
        missing = missing_table_headers(full, list(active_contract.required_headers)) if complete else []
        if missing:
            raise ValueError("尚未保存：完整报告缺少用户指定表头（原文及列顺序必须一致）："
                             + "；".join(" | ".join(header) for header in missing)
                             + "。请修正后重试；分段写作中间段请设 complete=false，最后一段设 true。")
        errors = arithmetic_errors(full) + scaling_explanation_errors(full) if complete else []
        csv_path = task_dir / "costs.csv"
        if complete and csv_path.is_file() and not csv_path.is_symlink():
            with csv_path.open(encoding="utf-8-sig", errors="replace") as stream:
                sample = stream.read(12001)
            if len(sample) <= 12000:
                errors.extend(report_cost_errors(full, sample))
                errors.extend(price_ratio_errors(full, sample))
        if errors:
            raise ValueError("尚未保存：数值检查失败，请核对计算结果：" + "；".join(errors))
        ledger = session.evidence if session else None
        if claims and ledger is None:
            raise ValueError('本次没有可用证据账本，不能提交自造证据ID')
        mapping = ledger.prepare(path, full, claims, complete=complete) if ledger else None
        if mode == "append" and path.exists():
            with path.open("a", encoding="utf-8") as f:
                f.write("\n\n" + content)
            full = path.read_text(encoding="utf-8")
            saved = f"已追加到报告：{path}（本次 {len(content)} 字符，文件共 {len(full)} 字符）"
        else:
            path.write_text(full, encoding="utf-8")
            saved = f"报告已保存：{path}（{len(full)} 字符）"
        check = inspect_report(full, task_dir, session, contract=active_contract)
        save_check(path, check)
        save_contract_check(path, full, active_contract, extension_reason=brief_extension_reason)
        evidence_feedback = ''
        if mapping is not None:
            write_json(path.with_suffix('.evidence.json'),mapping)
            evidence_feedback = (f"\n【关键结论依据】状态 {mapping['status']}，关联{len(mapping['claims'])}项；"
                '只校验原句、证据摘录与版本，不认证蕴含或真实性。'
                + ('请用claims参数关联关键结论；修订后旧关联不能继续作当前依据。' if mapping['status']!='mapped' else '其余未关联文本未认证。'))
        scope_feedback = ""
        if contract_check['observations']:
            scope_feedback = "\n【范围核对线索】" + str(contract_check['observations']) + "\n线索不等于已判违规；结合任务与资料核对，软目标不作为硬上限，不为润色无限改稿。"
        repair = structure_repair_feedback(full, active_contract, check) if complete else ''
        return saved + ("\n【分段草稿】尚未完成，不执行语义审稿；最后一段须 complete=true。" if not complete else "") + "\n\n" + check.feedback() + ('\n' + repair if repair else '') + scope_feedback + evidence_feedback

    return write_report
