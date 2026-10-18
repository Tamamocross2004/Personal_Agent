"""从当前请求保守提取明确约束；不推测任意自然语言的完整意图。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from markdown_it import MarkdownIt
from pydantic import BaseModel, ConfigDict, Field


class LengthRule(BaseModel):
    model_config = ConfigDict(frozen=True)
    maximum: int = Field(gt=0, le=1000000)
    unit: Literal["characters", "han_characters", "english_words"]
    scope: Literal["body", "full"] = "full"
    explicit_text: str


class TaskContract(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: int = 1
    request_sha256: str
    request_excerpt: str
    required_answers: tuple[str, ...] = ()
    evidence_scope: Literal["provided_only", "unspecified"] = "unspecified"
    network_allowed: bool | None = None
    statistics: Literal["forbidden", "requested", "unspecified"] = "unspecified"
    length_rules: tuple[LengthRule, ...] = ()
    soft_body_target: int | None = None
    required_headers: tuple[tuple[str, ...], ...] = ()
    output_files: tuple[str, ...] = ()
    ambiguities: tuple[str, ...] = ()
    counting_policy: str = ("字/字符按 Markdown 可见文本的非空白 Unicode 字符计，含标点、数字、英文；"
        "中文字符只计汉字；英文单词按字母词计（含词内连字符/撇号）。标题、表格、代码均计入；"
        "仅明确要求正文时排除参考资料，全文限制包含参考资料；URL 隐藏目标不计、可见 URL 计入。")

    def prompt(self) -> str:
        return ("【本次任务约定】以下仅为当前用户请求的有限规则提取，不来自网页或历史。"
                "原始问题仍有效；未识别项不代表允许忽略。明确约束优先，软目标不能冒充硬上限。\n"
                + json.dumps(self.model_dump(exclude={'request_excerpt'}), ensure_ascii=False))

    @property
    def active(self) -> bool:
        return bool(self.required_answers or self.evidence_scope != 'unspecified' or self.network_allowed is not None
                    or self.statistics != 'unspecified' or self.length_rules or self.soft_body_target
                    or self.required_headers or self.output_files or self.ambiguities)


def instruction_text(question: str) -> str:
    """排除明确的数据引述与代码示例；不能识别所有未标记的嵌入资料。"""
    text = re.sub(r"(?ms)^\s*(`{3,}|~{3,}).*?^\s*\1[^\n]*$", "", question)
    text = re.sub(r"(?m)^\s*>.*$", "", text)
    text = re.sub(r'"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’|「[^」\n]*」', "", text)
    text = re.sub(r"(?<!\w)'[^'\n]*'(?!\w)", "", text)
    text = re.sub(r"`+([^`\n]+)`+", lambda m: m[1] if re.fullmatch(r"[A-Za-z_][\w]*(?:\.(?:md|csv|png|json))?", m[1]) else "", text)
    return text


def explicit_headers(question: str) -> tuple[tuple[str, ...], ...]:
    field = r"`?[A-Za-z_][A-Za-z0-9_]*`?"
    pattern = rf"(?:表头|列名)[^|\n]{{0,24}}?({field}(?:\s*\|\s*{field})+)"
    return tuple(dict.fromkeys(tuple(c.strip().strip('`') for c in m.split('|'))
                              for m in re.findall(pattern, instruction_text(question))))


def _choice(text: str, pattern: str, positive: str, negative: str, ambiguities: list[str], label: str):
    matches = list(re.finditer(pattern, text))
    if not matches:
        return None
    values = [negative if m.lastgroup == "deny" else positive for m in matches]
    if len(set(values)) > 1:
        last = matches[-1]
        prefix = re.split(r"[。；;\n]", text[:last.start()])[-1]
        if not re.search(r"改为|更正|现在|最终|本次要求", prefix):
            ambiguities.append(f"{label}出现冲突表述；按较保守约束执行，原始要求须由审稿核对。")
            return negative
    return values[-1]


def extract_contract(question: str) -> TaskContract:
    text = instruction_text(question)
    text = re.sub(r"(?:不是|并非)(不需要联网|不允许联网|不可以联网|不联网)", lambda m: {
        '不需要联网': '需要联网', '不允许联网': '允许联网', '不可以联网': '可以联网', '不联网': '可以联网'}[m[1]], text)
    ambiguities = []
    network = _choice(text,
        r"(?P<deny>无需联网|不需(?:要)?联网|不必联网|不要联网|禁止联网|不联网|不允许联网|不可(?:以)?联网|不准联网|不得联网|别联网|禁止上网|不要上网|无需上网)|(?P<allow>允许联网|可以联网|需要联网|请联网)",
        "allow", "deny", ambiguities, "联网要求")
    statistics = _choice(text,
        r"(?P<deny>(?:不要|禁止|无需|不做|不进行|不开展|(?:不得|不允许)(?:进行|做|开展)?)(?:任何)?(?:额外)?(?:统计分析|统计检验|置信区间))"
        r"|(?P<allow>(?:请|需要|要求|允许)(?:进行|做|计算|开展)?(?:统计分析|统计检验|置信区间|Fisher|Wilson))",
        "requested", "forbidden", ambiguities, "统计分析要求")
    lengths = []
    # 只支持阿拉伯数字的明确上限；约数、区间和模糊数量保留给语义核对。
    units = r"中文字符|汉字|英文单词|单词|字符|字|words?\b"
    pattern = (rf"(?:(?P<bound>不超过|不得超过|不要超过|不能超过|请勿超过|不多于|最多|至多|上限(?:为|是)?|限制(?:在|为)|控制在|no more than|at most)\s*"
               rf"(?P<n>\d{{1,7}})\s*(?:个)?(?P<unit>{units})|(?P<n2>\d{{1,7}})\s*(?:个)?(?P<unit2>{units})\s*(?:以内|以下))")
    for match in re.finditer(pattern, text, re.I):
        before = text[max(0, match.start()-16):match.start()]
        if re.search(r"(?:不要求|无需|取消|不限|不必|不是|没有必要|不需要)[^，,。；;]{0,12}$", before):
            continue
        if re.match(r"\s*(?:左右|上下|前后)", text[match.end():]):
            ambiguities.append('近似长度目标未转换为硬上限，请核对原始要求。')
            continue
        n, unit = int(match['n'] or match['n2']), (match['unit'] or match['unit2']).lower()
        if not 0 < n <= 1000000:
            ambiguities.append("存在未支持的长度上限，请核对原始问题。")
            continue
        kind = "han_characters" if unit in ("中文字符", "汉字") else "english_words" if unit in ("英文单词", "单词", "word", "words") else "characters"
        local = re.split(r"[。；;\n]", text[:match.start()])[-1]
        scope_mark = re.search(r"(正文|全文|全篇|总计)[^。；;]{0,24}$", local)
        scope = "body" if scope_mark and scope_mark[1] == "正文" else "full"
        rule = LengthRule(maximum=n, unit=kind, scope=scope, explicit_text=match[0])
        same = [r for r in lengths if r.scope == scope and r.unit == kind]
        if same and re.search(r"改为|更正|最终|现在", local):
            lengths = [r for r in lengths if (r.scope, r.unit) != (scope, kind)]
        elif same and any(r.maximum != n for r in same):
            ambiguities.append("同一计量口径有多个长度上限；同时核对，不擅自选择宽松上限。")
        lengths.append(rule)
    if re.search(r"(?:[一二三四五六七八九十百千]+|\d+\s*[-–至]\s*\d+)\s*(?:字|字符|词)", text):
        ambiguities.append("中文数字/长度区间未转换为硬上限，须按原始要求核对。")
    provided = bool(re.search(r"(?:只|仅)(?:能)?(?:根据|依据|基于|使用)[^。；;\n]{0,20}(?:给定|提供|上述|这些|以下|原文|资料|材料)", text))
    answers = tuple(m.strip() for m in re.findall(r"(?:必答问题|必须回答|必答)[：:]\s*([^。；;\n]+)", text))
    files = tuple(dict.fromkeys(name for clause in re.split(r"[。；;\n]", text) if re.search(r'保存为|导出|生成|文件名', clause)
                               for name in re.findall(r"\b[\w-]+\.(?:md|csv|png|json)\b", clause)))
    styles = [m for m in re.finditer(r'简述|简短|简要|简洁|详细|全面|深入', text)
              if not re.search(r'不要|无需|不用|不必', text[max(0,m.start()-4):m.start()])]
    brief = bool(styles and styles[-1][0] in ('简述','简短','简要','简洁'))
    return TaskContract(request_sha256=hashlib.sha256(question.encode()).hexdigest(), request_excerpt=question[:2000],
        required_answers=answers, evidence_scope="provided_only" if provided else "unspecified",
        network_allowed=None if network is None else network == "allow", statistics=statistics or "unspecified",
        length_rules=tuple(lengths), soft_body_target=500 if brief and not lengths else None,
        required_headers=explicit_headers(question), output_files=files, ambiguities=tuple(dict.fromkeys(ambiguities)))


def visible_text(report: str, scope: str = "full") -> str:
    from html import unescape
    pieces = []
    tokens = MarkdownIt("commonmark").enable("table").parse(report)
    reference_level = None
    for index, token in enumerate(tokens):
        if scope == "body" and token.type == "heading_open" and token.level == 0:
            level = int(token.tag[1:])
            if reference_level and level <= reference_level:
                reference_level = None
            title = tokens[index+1].content.strip()
            if re.fullmatch(r"参考资料|参考文献|References|Sources", title, re.I):
                reference_level = level
        if reference_level:
            continue
        if token.type in ("fence", "code_block"):
            pieces.append(token.content)
        elif token.type == "html_block":
            pieces.append(unescape(re.sub(r"<[^>]*>", "", token.content)))
        elif token.type == "inline":
            line = []
            for child in token.children or []:
                if child.type in ("text", "code_inline"):
                    line.append(child.content)
                elif child.type == "image":
                    line.append(child.content)
                elif child.type in ("softbreak", "hardbreak"):
                    line.append(" ")
            pieces.append("".join(line))
    return "\n".join(pieces)


def length_count(text: str, unit: str) -> int:
    if unit == "han_characters":
        return len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U000323af]", text))
    if unit == "english_words":
        return len(re.findall(r"[A-Za-z]+(?:['’\-][A-Za-z]+)*", text))
    return sum(not c.isspace() for c in text)


def analysis_signals(report: str) -> list[dict]:
    """只标记可定位的结果/假设线索，供审稿核对；不靠关键词硬判全部语义违规。"""
    signals = []
    tokens = MarkdownIt("commonmark").enable("table").parse(report)
    for token in tokens:
        if token.type != "inline":
            continue
        for sentence in re.split(r"[。！？；;\n]", token.content):
            if re.search(r"未做|未进行|不做|不进行|未计算|不能|无法|无需|若要|如果|假设", sentence):
                continue
            if re.search(r"(?:Fisher|t检验|卡方|精确检验).{0,70}(?:p\s*[=＝<>]|检验结果)|(?:Wilson|置信区间).{0,50}\d\s*[%％]", sentence, re.I):
                signals.append({"kind": "statistical_result", "quote": sentence.strip()[:600]})
            if re.search(r"(?:样本|观测|调用|试验|测试).{0,12}(?:均|相互|彼此|互相|为|是).{0,6}独立|独立同分布|随机抽样", sentence):
                signals.append({"kind": "assumption", "quote": sentence.strip()[:600]})
    return signals[:12]


def check_contract(report: str, contract: TaskContract) -> dict:
    measures, hard_errors, observations = [], [], []
    for rule in contract.length_rules:
        actual = length_count(visible_text(report, rule.scope), rule.unit)
        measures.append({**rule.model_dump(), "actual": actual})
        if actual > rule.maximum:
            hard_errors.append(f"{rule.scope}/{rule.unit} 实际 {actual}，超过明确上限 {rule.maximum}（{rule.explicit_text}）。")
    if contract.soft_body_target:
        actual = length_count(visible_text(report, "body"), "characters")
        measures.append({"scope": "body", "unit": "characters", "actual": actual, "soft_target": contract.soft_body_target})
        if actual > contract.soft_body_target:
            observations.append({"kind": "soft_length", "message": f"正文 {actual} 个可见非空白字符，超过默认简述目标 {contract.soft_body_target}；这不是用户硬上限。"})
    for signal in analysis_signals(report):
        if signal['kind'] == 'assumption' or (contract.statistics != 'requested' and (
                contract.statistics == "forbidden" or contract.soft_body_target or contract.evidence_scope == "provided_only")):
            observations.append({**signal, "message": "核对是否进行了被禁止/未要求的分析，以及前提是否由资料支持；引述或讨论方法本身不等于实际分析。"})
    return {"request_sha256": contract.request_sha256,
            "report_sha256": hashlib.sha256(report.encode()).hexdigest(),
            "hard_errors": hard_errors, "measurements": measures, "observations": observations,
            "semantic_scope": "范围、遗漏和假设须结合原始问题与证据审稿，观察线索不直接等同违规。"}


def save_contract_check(path: Path, report: str, contract: TaskContract) -> dict:
    result = check_contract(report, contract)
    path.with_suffix('.contract.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
