"""固定题集的客观检查；Decimal 核对每一分项，模型审稿状态独立呈现。"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from agent.quality import inspect_report
from agent.review import fingerprint
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
    headers, rows = None, []
    for line in report.splitlines():
        if not line.strip().startswith("|"):
            headers = None
            continue
        cells = [c.strip().strip("`* ") for c in line.strip().strip("|").split("|")]
        if set(TABLE_COLUMNS).issubset(cells):
            headers = cells
        elif headers and len(cells) == len(headers) and not all(re.fullmatch(r"[-: ]+", c) for c in cells):
            rows.append(dict(zip(headers, cells)))
    return rows


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
    session = session_from_events(events)
    quality = inspect_report(report, task_dir, session)
    checks.append(check("mechanical_quality", bool(report) and not quality.issues,
                        "；".join(i.message for i in quality.issues)))
    try:
        review = json.loads(report_path.with_suffix(".review.json").read_text(encoding="utf-8"))
        if not isinstance(review, dict):
            raise ValueError("审稿记录格式错误")
        review_ok = review.get("status") == "reviewed" and review.get("issues") == [] and review.get("report_sha256") == fingerprint(report)
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
        unsupported = [line for line in report.splitlines() if re.search(pattern, line)
                       and not re.search(r"不能|无法|不足|不支持|不应|不代表|未能|编造|不予采用|不成立|能否|是否|[？?]|若要|如果要|需补齐", line)]
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
            "checks": checks, "review_status": review_status, "task_dir": str(task_dir.resolve()),
            "assessment_scope": "固定规则和已收集证据检查；不等价于独立事实认证。自由文本结论仍需人工抽查。"}
