"""报告的机械检查：引用结构、来源身份线索、图片文件；不代替事实核验。"""

from __future__ import annotations

import json
import hashlib
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit
from agent.task_contract import TaskContract, check_contract

from agent.planner import load_plan, unfinished_tasks
if TYPE_CHECKING:
    from tools.session import ResearchSession

URL = re.compile(r"https?://[^\s<>()\[\]\"'`，。；、）】》]+")
REFERENCE = re.compile(r"^\s*(?:\[(\d+)\]:?|([0-9]+)[.、])\s*(.*)$")
CITATION = re.compile(r"\[(\d+)\](?!:)")
IMAGE = re.compile(r"!\[[^\]]*\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)")
HEADING = re.compile(r"^#{1,6}\s*(参考资料|参考文献|References|Sources)\s*$", re.I | re.M)


def without_code(text: str) -> str:
    text = re.sub(r"(?ms)^\s*(`{3,}|~{3,}).*?^\s*\1\s*$", "", text)
    return re.sub(r"`+[^`\n]*`+", "", text)


def source_kind(url: str) -> str:
    """保守域名规则：官方仅为候选，不把公司域名下的社区当官方文档。"""
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    if host in {"developer.aliyun.com", "blog.csdn.net", "juejin.cn", "zhuanlan.zhihu.com", "medium.com"}:
        return "community"
    if host in {"deepseekagent.io", "therouter.ai", "blog.sandbase.ai", "datalearner.com"}:
        return "third_party"
    official = {"deepseek.com", "api-docs.deepseek.com", "kimi.com", "platform.kimi.com",
                "help.aliyun.com", "docs.bailian.console.aliyun.com", "qwen.ai", "docs.bigmodel.cn",
                "docs.python.org", "pandas.pydata.org", "matplotlib.org"}
    return "official_candidate" if host in official else "unknown"


SOURCE_LABELS = {"official_candidate": "已知官方文档域名（规则匹配，非内容真实性认证）",
                 "community": "社区内容（不能据此声称官方）", "third_party": "第三方",
                 "unknown": "发布主体未核实"}


@dataclass
class QualityIssue:
    category: str
    message: str
    severity: str = "warning"


@dataclass
class QualityCheck:
    issues: list[QualityIssue] = field(default_factory=list)
    references: dict[str, list[str]] = field(default_factory=dict)
    sources: list[dict] = field(default_factory=list)
    images: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    def feedback(self) -> str:
        if not self.issues:
            return "【自动检查】通过：计划、引用结构和本地图片检查未发现问题；不代表事实核验或语义审稿通过。"
        return "【自动检查】发现以下问题（分段写作时可在完整报告写完后修正）：\n" + "\n".join(
            f"- [{i.category}] {i.message}" for i in self.issues
        )


def structure_repair_feedback(text: str, contract: TaskContract, check: QualityCheck) -> str:
    """缺章是可修订的警告；计量余量不证明任意新稿一定放得下。"""
    missing = [i.message for i in check.issues if i.category == 'structure']
    if not missing:
        return ''
    measures = check_contract(text, contract)['measurements']
    units = {'characters': '可见非空白字符', 'han_characters': '汉字', 'english_words': '英文单词'}
    counts = [f"{m['scope']}/{units[m['unit']]}：当前{m['actual']}，上限{m['maximum']}，"
              f"剩余{max(0, m['maximum']-m['actual'])}"
              for m in measures if 'maximum' in m]
    return ('【结构修订】' + '；'.join(missing)
            + f"\n当前报告 SHA-256：{hashlib.sha256(text.encode('utf-8')).hexdigest()}。"
            + ('\n按本次计量：' + '；'.join(counts) + '。' if counts else '')
            + '\n先复用内容相符的现有章节标题、压缩重复解释，再用 write_report 修正文件。'
              '短稿每节一句即可，不能只补空标题、编造来源或删掉必答理由。'
              '用户硬上限优先；余量不保证能直接追加，须检查修订后的全文。'
              '不要未经修订尝试就断言补齐章节必然超限；确有格式冲突时如实说明。')


def urls(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0).rstrip(".,;:!?") for m in URL.finditer(text)))


def inspect_report(text: str, task_dir: Path, session: ResearchSession | None, *, contract: TaskContract | None = None) -> QualityCheck:
    result = QualityCheck()

    def issue(category, message, severity="warning"):
        result.issues.append(QualityIssue(category, message, severity))

    contract = contract or (session.task_contract if session else None)
    if contract:
        for error in check_contract(text, contract)['hard_errors']:
            issue('task_contract', error, 'error')

    unfinished = unfinished_tasks(load_plan(task_dir))
    if unfinished:
        items = "；".join(f"{t.get('id')} {t.get('title', '')}（{t.get('status')}）" for t in unfinished)
        issue("plan", f"计划中还有未完成的子任务：{items}。用 update_plan 更新，未完成的需说明局限。")

    clean = without_code(text)
    references_start = HEADING.search(clean)
    body_lines, reference_lines = [], []
    definitions = {}
    in_references = references_start is None
    # 同时支持有参考资料标题和旧版无标题的 [1] URL 列表；定义本身不算正文引用。
    for line in clean.splitlines():
        if HEADING.fullmatch(line):
            in_references = True
        match = REFERENCE.match(line)
        if in_references and match and (references_start or urls(match[3])) and not match[3].startswith("("):
            number = match[1] or match[2]
            if number in definitions:
                issue("citation", f"参考资料编号 [{number}] 重复。", "error")
            definitions[number] = match[3]
            result.references[number] = urls(match[3])
            reference_lines.append(line)
        else:
            body_lines.append(line)
    body = "\n".join(body_lines)
    used = set(CITATION.findall(body))
    for number in sorted(used - definitions.keys()):
        issue("citation", f"正文引用 [{number}] 没有对应参考资料。", "error")
    if used:
        for number in sorted(definitions.keys() - used):
            issue("citation", f"参考资料 [{number}] 未被正文引用。")

    reference_urls = {u for values in result.references.values() for u in values}
    if references_start:
        reference_urls.update(urls(clean[references_start.end():]))
    # 行内代码中的端点被 without_code 排除；裸 API 端点仅在正文可作为说明性链接。
    # 若端点列入参考资料仍严格按证据来源校验，避免借例外绕过来源检查。
    evidence_urls = set(reference_urls)
    for url in urls(IMAGE.sub("", body)):
        host = (urlsplit(url).hostname or "").lower()
        if host.startswith("api.") and url not in reference_urls:
            continue
        evidence_urls.add(url)
    for url in sorted(evidence_urls):
        kind = source_kind(url)
        result.sources.append({"url": url, "kind": kind, "label": SOURCE_LABELS[kind]})
    if session is not None:
        missing = [u for u in sorted(evidence_urls) if not session.has_note_for(u)]
        if missing:
            issue("source", "以下 URL 不在笔记来源中（未读原文或未记笔记）：" + "、".join(missing) + "。请删除或先阅读并记笔记。", "error")

    # 只报能定位的身份冲突；未知域名不能自动断言不是官方。
    for line in body_lines + reference_lines:
        if "官方" not in line or re.search(r"非官方|不是官方|并非官方|未核实|非厂商官方|不能.{0,8}官方|不代表官方|不等于官方", line):
            continue
        linked = urls(line) + [u for n in CITATION.findall(line) for u in result.references.get(n, [])]
        disputed = [u for u in linked if source_kind(u) in ("community", "third_party")]
        if disputed:
            issue("source_identity", "将社区/第三方内容称为官方，需追溯原始发布主体：" + "、".join(dict.fromkeys(disputed)))

    if re.search(r"^#\s+", clean, re.M):
        for title, pattern in [("摘要", r"^##\s*摘要"), ("方法/范围", r"^##\s*.*(?:方法|范围|研究口径)"),
                               ("结论", r"^##\s*.*结论"), ("局限性", r"^##\s*.*(?:局限|限制|不确定)"),
                               ("参考资料", r"^##\s*(?:参考资料|参考文献|References|Sources)")]:
            if not re.search(pattern, clean, re.M | re.I):
                issue("structure", f"缺少“{title}”章节。")

    root = task_dir.resolve()
    image_targets = [m[1].strip("<>") for m in IMAGE.finditer(clean)]
    # Markdown 引用式图片：![说明][图1] + [图1]: costs.png
    image_defs = dict(re.findall(r"(?m)^\s*\[([^\]]+)\]:\s*(\S+)", clean))
    for label in re.findall(r"!\[[^\]]*\]\[([^\]]+)\]", clean):
        if label not in image_defs:
            issue("image", f"图片引用 [{label}] 没有路径定义。", "error")
        else:
            image_targets.append(image_defs[label])
    for target in dict.fromkeys(image_targets):
        result.images.append(target)
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            issue("image", f"图片 {target} 不是本地相对路径，未验证其内容。")
            continue
        path = (root / unquote(parsed.path)).resolve()
        if not path.is_relative_to(root):
            issue("image", f"图片路径越出任务目录：{target}。", "error")
        elif not path.is_file():
            issue("image", f"图片文件不存在：{target}。", "error")
        else:
            try:
                from PIL import Image
                with Image.open(path) as image:
                    image.verify()
            except (OSError, ValueError) as exc:
                issue("image", f"图片无法解码：{target}（{type(exc).__name__}）。", "error")
    return result


def save_check(report_path: Path, check: QualityCheck) -> None:
    import hashlib
    data = check.to_dict()
    data["report_sha256"] = hashlib.sha256(report_path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    report_path.with_suffix(".quality.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
