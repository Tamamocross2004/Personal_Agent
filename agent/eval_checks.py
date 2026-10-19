"""固定题集的客观检查；Decimal 核对每一分项，模型审稿状态独立呈现。"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from agent.quality import inspect_report
from agent.markdown_tables import markdown_tables
from agent.task_contract import check_contract, extract_contract
from agent.review import fingerprint
from agent.numeric_evidence import arithmetic_errors, price_ratio_errors, scaling_explanation_errors
from tools.fetch import Page
from tools.session import Note, ResearchSession

PRICES = {"EvalAlpha": (Decimal(2), Decimal(8)), "EvalBeta": (Decimal(9), Decimal(27))}
CSV_COLUMNS = ("model", "input_tokens", "output_tokens", "input_price", "output_price", "input_cost", "output_cost", "total_cost")
TABLE_COLUMNS = ("model", "input_cost", "output_cost", "total_cost")


def check(name, passed, detail=""):
    return {"name": name, "passed": bool(passed), "detail": detail}


def decimal_value(text: str) -> Decimal:
    try:
        return Decimal(str(text).strip().replace(",", ""))
    except InvalidOperation:
        pass
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", str(text))
    if not match:
        raise ValueError(f"没有可解析数值：{text}")
    return Decimal(match[0].replace(",", ""))


def table_rows(report: str) -> list[dict]:
    return [dict(zip(header, row)) for header, rows in markdown_tables(report)
            if set(TABLE_COLUMNS).issubset(header) for row in rows]


def numeric_checks(task_dir: Path, report: str, specification: dict) -> list[dict]:
    expected = {}
    for model, (input_price, output_price) in PRICES.items():
        ci = input_price * Decimal(specification["input_tokens"]) / Decimal(1000000)
        co = output_price * Decimal(specification["output_tokens"]) / Decimal(1000000)
        expected[model] = {"input_tokens": Decimal(specification["input_tokens"]), "output_tokens": Decimal(specification["output_tokens"]),
                           "input_price": input_price, "output_price": output_price,
                           "input_cost": ci, "output_cost": co, "total_cost": ci + co}
    checks = []
    try:
        with (task_dir / "costs.csv").open(encoding="utf-8-sig") as stream:
            sample = stream.read(12001)
        if len(sample) <= 12000:
            ratio_errors = price_ratio_errors(report, sample)
            checks.append(check("report_price_ratios", not ratio_errors, "；".join(ratio_errors)))
    except OSError:
        pass  # 下方 numeric_csv 负责文件缺失。
    try:
        with (task_dir / "costs.csv").open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not set(CSV_COLUMNS).issubset(reader.fieldnames or []):
                raise ValueError("costs.csv 缺少规定的英文列")
            csv_rows = list(reader)
    except (OSError, ValueError, csv.Error) as exc:
        checks.append(check("numeric_csv", False, str(exc)))
        csv_rows = []
    for label, rows, fields in (("csv", csv_rows, CSV_COLUMNS[1:]), ("report", table_rows(report), TABLE_COLUMNS[1:])):
        for model, values in expected.items():
            matches = [row for row in rows if row.get("model") == model]
            if label == "report":
                # 同一当前数据被正文及 CSV 展示表重复呈现并非矛盾；历史/当前不同数值仍失败。
                distinct = {}
                for row in matches:
                    try:
                        key = tuple(decimal_value(row.get(field, "")) for field in fields)
                    except (ValueError, InvalidOperation):
                        key = tuple(row.get(field, "") for field in fields)
                    distinct.setdefault(key, row)
                matches = list(distinct.values())
            checks.append(check(f"{label}_{model}_unique", len(matches) == 1, f"找到 {len(matches)} 行"))
            if len(matches) != 1:
                continue
            for field in fields:
                try:
                    actual = decimal_value(matches[0].get(field, ""))
                    checks.append(check(f"{label}_{model}_{field}", actual == values[field], f"实际 {actual}；期望 {values[field]}"))
                except (ValueError, InvalidOperation) as exc:
                    checks.append(check(f"{label}_{model}_{field}", False, str(exc)))
    return checks


def _nonasserted_claim(sentence: str, claim: re.Match) -> bool:
    """识别命题的语用范围：局部否定/提问，以及引述后回指该命题的条件或驳斥。"""
    left = max(sentence.rfind(mark, 0, claim.start()) for mark in ("，", ",", "：", ":")) + 1
    right = min((p for mark in ("，", ",") if (p := sentence.find(mark, claim.end())) >= 0), default=len(sentence))
    context = sentence[left:right]
    if re.search(r"能否|是否|[？?]|若要|如果要|需补齐", context):
        return True
    if re.search(r"不能|无法|不足|不支持|不应|不代表|未能|编造|不予采用|不成立|不可|无从", context):
        return True
    before, after = sentence[:claim.start()], sentence[claim.end():]
    if (re.search(r"不对\s*[\"“‘「]?\s*$", before)
            and re.match(r"[\"”’」]?\s*作(?:任何)?(?:倾向性)?(?:暗示|判断|评价|断言)", after)):
        return True
    for quote in re.finditer(r'''["“‘「]([^"“”‘’「」\n]+)["”’」]''', sentence):
        if not (quote.start(1) <= claim.start() and claim.end() <= quote.end(1)):
            continue
        tail = sentence[quote.end():]
        lead = re.split(r"[，,]", tail, maxsplit=1)[0]
        # “判断某命题需要数据”讨论的是证据条件，并未断言该命题成立。
        # 限定为裸判断动词，不能用“已经认定...”掩盖肯定结论。
        if (re.fullmatch(r'\s*(?:若要|如果要|要)?(?:判断|认定|证明|证实|得出|断言)\s*', sentence[:quote.start()])
                and re.match(r'\s*(?:需要|需有|须有|必须有)[^。；;]{0,100}(?:证据|数据)', tail)):
            return True
        # 引述命题后的“所需条件/成立前提”是条件议题，不是命题本身的肯定。
        if re.match(r"\s*(?:所需(?:要)?的?|成立(?:所需)?的?)(?:条件|证据|数据|前提)", tail):
            return True
        # 回指必须出现在引号后第一个从句；不能用另一对象的否定覆盖引述的肯定。
        meta = re.search(r"结论|命题|判断|说法|表述|比较|排序", lead)
        affirmed = re.search(r"正确|准确|已证实|已成立|毫无疑问|事实", lead)
        if meta and not affirmed:
            if re.search(r"缺少|缺乏|尚缺|无对照|未知|不存在|不具备|不成立|(?:无法|不能|无从)(?:成立|比较|排序|谈起|建立)", tail):
                return True
            if re.search(r"(?:需要|要求|前提).*(?:证据|数据|条件|测试结果)", tail):
                return True
        if re.search(r"(?:若|如果).*强行", sentence[:quote.start()]) and re.search(r"无对照|缺少|缺乏", tail):
            return True
    return False


def unsupported_claims(report: str, pattern: str) -> list[str]:
    """有限规则：按句子/转折分开判定，表格否定列保留列语境；不是通用语义认证。"""
    report = re.sub(r"(?ms)^\s*(`{3,}|~{3,}).*?^\s*\1[^\n]*$", "", report)
    lines = report.splitlines()
    table_fragments = []
    # 与保存检查共用语法，包括无外侧竖线的表格；保留否定列的语境。
    from markdown_it import MarkdownIt
    for token in MarkdownIt("commonmark").enable("table").parse(report):
        if token.type == "table_open" and token.map:
            start, end = token.map
            for header, rows in markdown_tables("\n".join(lines[start:end])):
                rejected = {i for i, cell in enumerate(header) if re.search(r"不可|不能|不支持|不应|无法", cell)}
                table_fragments.extend(cell for row in rows for i, cell in enumerate(row) if i not in rejected)
            lines[start:end] = [""] * (end - start)
    findings = []
    rejected_label = re.compile(r"^(?:[-*]\s*)?(?:(?:不能|不可(?:以)?|不应(?:该)?|无法|不足以)(?:说|得出|支持|断言|认定|推导|认为|判断|证明|暗示|成立)(?:的(?:结论|表述|说法|判断))?|不支持的结论)(?:包括|有)?\s*[：:]")
    for line in lines + table_fragments:
        # 否定标签只支配本句内的列表；不能跨句覆盖后面的肯定断言。
        fragments = re.split(r"(?<=[。！？?])", line)
        for fragment in fragments:
            plain = fragment.replace('**', '').strip()
            rejected_list = rejected_label.match(plain)
            if rejected_list and not re.search(r"但是|但|然而|不过", plain):
                continue  # 明确的否定标签支配同一列表中的分号项。
            for sentence in re.split(r"(?<=[。！？?；;])|但是|但|然而|不过", fragment):
                # 同行上一句可先说明已知事实，后一句才开始明确的否定列表。
                # 转折已分开，后面的肯定断言仍须独立检查。
                if rejected_label.match(sentence.replace('**', '').strip()):
                    continue
                if not re.search(pattern, sentence):
                    continue
                normalized = re.sub(r"(?:不是|并非)(?:不能|不可(?:以)?|不应(?:该)?)", "可以", sentence.replace('**', ''))
                claims = list(re.finditer(pattern, normalized))
                if all(_nonasserted_claim(normalized, claim) for claim in claims):
                    continue
                findings.append(sentence.strip())
    return findings


def session_from_events(events: list[dict]) -> ResearchSession:
    session, calls = ResearchSession(), {}
    for event in events:
        if event["kind"] == "tool_call":
            calls[event["id"]] = (event["name"], json.loads(event["arguments"] or "{}"))
        elif event["kind"] == "tool_result" and not event.get("is_error"):
            name, args = calls.get(event["id"], ("", {}))
            if name == "fetch_url":
                requested = args["url"]
                match = re.search(r"^URL: (\S+)", event.get("content", ""), re.M)
                final = match[1] if match else requested
                session.pages[requested] = Page(url=final, title="运行中已读来源", date=None, text="")
            elif name == "save_note":
                page = session.find_page(args["source_url"])
                if page:
                    session.notes.append(Note(len(session.notes) + 1, args["content"], page.url, page.title))
    return session


def assess_run(case: dict, task_dir: Path) -> dict:
    events = [json.loads(line) for line in (task_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if not all(isinstance(event, dict) and isinstance(event.get("kind"), str) for event in events):
        raise ValueError("trace 事件格式错误")
    end = next((event for event in reversed(events) if event["kind"] == "run_end"), {})
    calls = [e for e in events if e["kind"] == "llm_call"]
    tools = Counter(e["name"] for e in events if e["kind"] == "tool_result" and not e.get("is_error"))
    metrics = {"steps": end.get("steps"), "seconds": end.get("seconds"), "equivalent_tokens": end.get("equivalent_tokens"),
               "input_tokens": sum(e.get("prompt_tokens", 0) for e in calls),
               "cached_tokens": sum(e.get("cached_tokens", 0) or 0 for e in calls),
               "output_tokens": sum(e.get("completion_tokens", 0) for e in calls), "llm_calls": len(calls),
               "review_calls": sum(e.get("purpose") == "report_review" for e in calls),
               "tool_errors": sum(e["kind"] == "tool_result" and bool(e.get("is_error")) for e in events),
               "stop_reason": end.get("stop_reason", "missing_run_end")}
    checks = [check("normal_completion", end.get("stop_reason") == "final_answer", metrics["stop_reason"])]
    report_path = task_dir / "report.md"
    checks.append(check("report_exists", report_path.is_file()))
    report = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
    checks.append(check("explicit_arithmetic", not arithmetic_errors(report), "；".join(arithmetic_errors(report))))
    checks.append(check('scaling_explanation', not scaling_explanation_errors(report), '；'.join(scaling_explanation_errors(report))))
    session = session_from_events(events)
    original_question = case.get('prompt') or next((e.get('question', '') for e in events if e['kind'] == 'run_start'), '')
    contract = extract_contract(original_question)
    session.task_contract = contract
    contract_check = check_contract(report, contract)
    checks.append(check('task_contract_hard_limits', not contract_check['hard_errors'], '；'.join(contract_check['hard_errors'])))
    if case.get('forbid_statistical_results'):
        signals = [s for s in contract_check['observations'] if s.get('kind') == 'statistical_result']
        checks.append(check('no_statistical_result_signals', not signals, '；'.join(s['quote'] for s in signals)))
    quality = inspect_report(report, task_dir, session)
    audit = None
    if case.get('require_evidence'):
        from agent.evidence import audit_mapping
        try:
            audit = audit_mapping(task_dir)
            checks.append(check('evidence_current_and_mapped', audit['status']=='mapped' and audit['claims']>0,
                                json.dumps(audit,ensure_ascii=False)))
            for kind in case.get('required_evidence_kinds',[]):
                checks.append(check('evidence_kind_'+kind,kind in audit['claim_kinds']))
            for kind in case.get('required_evidence_source_kinds',[]):
                checks.append(check('evidence_source_'+kind,kind in audit['source_kinds']))
        except (OSError,ValueError,KeyError,TypeError) as exc:
            checks.append(check('evidence_current_and_mapped',False,str(exc)))
    checks.append(check("mechanical_quality", bool(report) and not quality.issues,
                        "；".join(i.message for i in quality.issues)))
    try:
        review = json.loads(report_path.with_suffix(".review.json").read_text(encoding="utf-8"))
        if not isinstance(review, dict):
            raise ValueError("审稿记录格式错误")
        review_ok = review.get("status") == "reviewed" and review.get("issues") == [] and review.get("report_sha256") == fingerprint(report)
        if case.get('require_evidence'):
            bound = audit is not None and review.get('evidence_mapping_sha256')==audit['mapping_sha256']
            checks.append(check('review_matches_evidence_mapping',bound))
        review_status = review.get("status", "unknown") if review.get("report_sha256") == fingerprint(report) else "stale"
    except (OSError, ValueError):
        review_ok, review_status = False, "missing_or_invalid"
    checks.append(check("review_complete_and_clean", review_ok, review_status))
    for tool in case.get("required_tools", []):
        checks.append(check(f"tool_{tool}", tools[tool] > 0))
    checks.append(check("minimum_sources", len(session.noted_urls()) >= case.get("min_sources", 0), str(len(session.noted_urls()))))
    if case.get("require_image"):
        checks.append(check("image_in_report", bool(quality.images)))
    for pattern in case.get("required_patterns", []):
        checks.append(check(f"contains_{pattern}", bool(re.search(pattern, report, re.I))))
    for pattern in case.get("forbidden_claims", []):
        unsupported = unsupported_claims(report, pattern)
        checks.append(check("no_unsupported_winner", not unsupported, "；".join(unsupported)))
    if case.get("numeric"):
        checks.extend(numeric_checks(task_dir, report, case["numeric"]))
    if case.get("require_recall"):
        try:
            snapshot = json.loads((task_dir / "memory_context.json").read_text(encoding="utf-8"))
            recalled = bool(snapshot.get("record_ids")) and "EvalAlpha" in snapshot.get("context", "") and "EvalBeta" in snapshot.get("context", "")
        except (OSError, ValueError):
            recalled = False
        checks.append(check("memory_recall", recalled))
    return {"status": "pass" if all(c["passed"] for c in checks) else "fail", "metrics": metrics,
            "model_identity": {'requested_models': sorted({e['requested_model'] for e in calls if e.get('requested_model')}),
                               'response_models': sorted({e['response_model'] for e in calls if e.get('response_model')}),
                               'all_calls_identified': bool(calls) and all(e.get('requested_model') and e.get('response_model') for e in calls)},
            "checks": checks, "review_status": review_status, "task_dir": str(task_dir.resolve()),
            "assessment_scope": "固定规则和已收集证据检查；不等价于独立事实认证。自由文本结论仍需人工抽查。"}
