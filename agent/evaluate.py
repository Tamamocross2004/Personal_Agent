"""固定题集评估。offline 不调用 API；live 显式触发，使用独立任务目录和记忆数据库。"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agent.config import PROJECT_ROOT, PROVIDERS
from agent.eval_checks import CSV_COLUMNS, assess_run

CASE_FILE = Path(__file__).with_name("eval_cases.json")


def load_suite(path: Path | None = None) -> dict:
    suite = json.loads((path or CASE_FILE).read_text(encoding="utf-8"))
    for mode in ("offline", "live"):
        if not suite[mode]:
            raise ValueError("题集不能为空")
        ids = [case["id"] for case in suite[mode]]
        if len(ids) != len(set(ids)):
            raise ValueError("题集 ID 重复")
        for case in suite[mode]:
            if case.get("depends_on") and case["depends_on"] not in ids[:ids.index(case["id"])]:
                raise ValueError("依赖必须在当前题目前定义")
    return suite


def select_cases(suite: dict, mode: str, ids: list[str] | None) -> list[dict]:
    wanted = set(ids or [case["id"] for case in suite[mode]])
    known = {case["id"] for case in suite[mode]}
    if wanted - known:
        raise ValueError("未知题目：" + ", ".join(sorted(wanted - known)))
    for case in reversed(suite[mode]):
        if case["id"] in wanted and case.get("depends_on"):
            wanted.add(case["depends_on"])
    return [case for case in suite[mode] if case["id"] in wanted]


def new_output(path: Path | None) -> Path:
    if path is None:
        stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
        path = PROJECT_ROOT / "workspace" / "evaluations" / f"{stamp}-{uuid.uuid4().hex[:6]}"
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=False)  # 不覆盖之前的基线或用户文件
    return path


def metadata(mode: str, suite: dict, cases: list[dict]) -> dict:
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=PROJECT_ROOT, text=True))
    except (OSError, subprocess.CalledProcessError):
        revision, dirty = "unknown", None
    checksum = hashlib.sha256(json.dumps(suite, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    code_digest = hashlib.sha256()
    files = [PROJECT_ROOT / "main.py", CASE_FILE]
    for directory in ("agent", "tools", "tests"):
        files.extend((PROJECT_ROOT / directory).glob("*.py"))
    files.extend((PROJECT_ROOT / 'agent').glob('*.json'))
    files.extend((PROJECT_ROOT / "tests" / "fixtures").rglob("*.*"))
    for path in sorted(set(files)):
        code_digest.update(path.relative_to(PROJECT_ROOT).as_posix().encode())
        code_digest.update(path.read_bytes())
    return {"schema_version": 1, "mode": mode, "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "suite_version": suite["version"], "suite_sha256": checksum,
            "git_revision": revision, "git_dirty": dirty, "code_sha256": code_digest.hexdigest(),
            "grader_sha256": hashlib.sha256((Path(__file__).with_name("eval_checks.py").read_bytes()
                                             + Path(__file__).with_name("quality.py").read_bytes()
                                             + Path(__file__).with_name("numeric_evidence.py").read_bytes()
                                             + Path(__file__).with_name("markdown_tables.py").read_bytes()
                                             + Path(__file__).with_name("task_contract.py").read_bytes()
                                             + Path(__file__).with_name("analysis_scope.py").read_bytes()
                                             + Path(__file__).with_name("evidence.py").read_bytes())).hexdigest(), "state": "running",
            "definitions": cases,
            "cases": [{"id": c["id"], "title": c["title"], "status": "pending"} for c in cases]}


def summary(data: dict) -> dict:
    cases = data["cases"]
    passed = sum(c["status"] == "pass" for c in cases)
    def total(key):
        values = [c.get("metrics", {}).get(key) for c in cases]
        known = [v for v in values if v is not None]
        return round(sum(known), 3) if known else None
    return {"selected": len(cases), "passed": passed, "failed": sum(c["status"] in ("fail", "error") for c in cases),
            "unfinished": sum(c["status"] not in ("pass", "fail", "error") for c in cases),
            "pass_rate": passed / len(cases) if cases else 0,
            "steps": total("steps"), "seconds": total("seconds"), "equivalent_tokens": total("equivalent_tokens"),
            "review_calls": total("review_calls")}


def write_results(out: Path, data: dict) -> None:
    data["summary"] = summary(data)
    temporary = out / "results.json.tmp"
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(out / "results.json")
    s = data["summary"]
    lines = ["# Agent 固定题集评估", "", f"模式：{data['mode']}；状态：{data['state']}；时间：{data['created_at']}",
             f"代码：{data['git_revision']}（未提交改动：{data['git_dirty']}）；题集 SHA-256：{data['suite_sha256']}",
             f"评分器 SHA-256：{data.get('grader_sha256', 'unknown')}；执行方式：{data.get('execution', data['mode'])}", "",
             f"通过 {s['passed']}/{s['selected']}，失败 {s['failed']}，未完成/跳过 {s['unfinished']}。", "",
             "通过仅表示本题客观检查及规定的审稿状态满足要求，不是通用能力或事实真实性认证。", "",
             "| 题目 | 状态 | 步数 | 秒 | 等效 token | 审稿状态 |", "|---|---|---:|---:|---:|---|"]
    for case in data["cases"]:
        m = case.get("metrics", {})
        lines.append(f"| {case['id']} | {case['status']} | {m.get('steps', '—')} | {m.get('seconds', '—')} | {m.get('equivalent_tokens', '—')} | {case.get('review_status', '—')} |")
    for case in data["cases"]:
        problems = [c for c in case.get("checks", []) if not c["passed"]]
        if problems or case.get("error_type"):
            lines.extend(["", f"## {case['id']} 检查详情", ""])
            lines.extend(f"- {c['name']}：{c.get('detail', '')}" for c in problems)
            if case.get("error_type"):
                lines.append(f"- 运行错误：{case['error_type']}")
    lines.extend(["", "离线集不调用模型，token 成本不可用；耗时来自测试。真实评估按 trace 实际记录统计，等效 token 不是人民币价格。",
                  "联网结果会随来源和厂商变化；自由文本结论仍需人工抽查。未运行、跳过、审稿耗尽、陈旧审稿不能作为通过。"])
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_junit(path: Path, cases: list[dict]) -> list[dict]:
    tests = list(ET.parse(path).getroot().iter("testcase"))
    records = []
    for case in cases:
        selected = []
        missing = []
        for selector in case["selectors"]:
            file, separator, function = selector.partition("::")
            module = Path(file).stem
            matches = [test for test in tests if test.attrib.get("classname", "").split(".")[-1] == module
                       and (not separator or test.attrib.get("name", "").split("[")[0] == function)]
            if not matches:
                missing.append(selector)
            selected.extend(matches)
        failed = [test for test in selected if test.find("failure") is not None or test.find("error") is not None]
        skipped = [test for test in selected if test.find("skipped") is not None]
        status = "error" if missing else "fail" if failed else "skipped" if skipped else "pass"
        records.append({"id": case["id"], "title": case["title"], "status": status,
                        "metrics": {"seconds": round(sum(float(t.attrib.get("time", 0)) for t in selected), 3),
                                    "equivalent_tokens": None},
                        "checks": [{"name": t.attrib.get("name", ""), "passed": t not in failed and t not in skipped,
                                    "detail": "测试失败或跳过" if t in failed or t in skipped else ""} for t in selected]
                                  + [{"name": selector, "passed": False, "detail": "JUnit 没有该测试结果"} for selector in missing]})
    return records


def run_offline(out: Path, data: dict, cases: list[dict]) -> None:
    selectors = list(dict.fromkeys(s for case in cases for s in case["selectors"]))
    with (out / "pytest.log").open("w", encoding="utf-8") as log:
        process = subprocess.run([sys.executable, "-m", "pytest", "-q", *selectors, "--junitxml", str(out / "junit.xml")],
                                 cwd=PROJECT_ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=180)
    data["pytest_exit_code"] = process.returncode
    if (out / "junit.xml").is_file():
        data["cases"] = parse_junit(out / "junit.xml", cases)
    if process.returncode not in (0, 1):
        data["state"] = "runner_error"
    else:
        data["state"] = "complete"


def run_live(out: Path, data: dict, cases: list[dict], args) -> None:
    from agent.config import Settings, load_llm_config
    from agent.llm import LLMClient
    from main import research

    settings = Settings()
    cfg = load_llm_config(provider=args.provider, model=args.model, settings=settings)
    # 只记录白名单参数，绝不输出 Settings/model 配置中的 API Key。
    data["config"] = {"provider": cfg.provider, "model": cfg.model, "max_steps": args.max_steps,
                      "per_case_budget": args.budget, "suite_budget": args.suite_budget,
                      "review_max_calls": args.review_calls, "repeat": args.repeat,
                      "temperature": cfg.temperature, "thinking": cfg.thinking,
                      "token_weights": {"cached": settings.token_weight_cached, "output": settings.token_weight_output}}
    data["cases"] = [{"id": c["id"], "title": c["title"], "repeat": repeat, "status": "pending"}
                     for repeat in range(1, args.repeat + 1) for c in cases]
    used = 0
    for entry in data["cases"]:
        case = next(c for c in cases if c["id"] == entry["id"])
        if used >= args.suite_budget:
            entry["status"] = "skipped_budget"
            write_results(out, data)
            continue
        dependency = case.get("depends_on")
        if dependency and not any(c["id"] == dependency and c["repeat"] == entry["repeat"] and c["status"] == "pass" for c in data["cases"]):
            entry["status"] = "skipped_dependency"
            write_results(out, data)
            continue
        task = out / "cases" / f"{entry['repeat']:02d}-{case['id']}"
        task_settings = settings.model_copy(update={"memory_enabled": case.get("memory", False),
                    "memory_db_path": out / f"memory-{entry['repeat']:02d}.sqlite3",
                    "report_review_enabled": True, "report_review_max_calls": args.review_calls})
        prompt = case["prompt"] + "\n保存为 report.md，包含摘要、方法与范围、正文、结论、局限性、参考资料；修正审稿提出的实质问题。"
        if case.get("numeric"):
            prompt += "\ncosts.csv 必须使用英文列：" + ",".join(CSV_COLUMNS) + "。所有数值列只填数字；报告对应金额单元格只写数值，单位统一元。"
        try:
            result = research(LLMClient(cfg), task_settings, prompt, args.max_steps,
                              min(args.budget, args.suite_budget - used), task_dir=task)
            used += result.equivalent_tokens
            entry["metrics"] = {"equivalent_tokens": result.equivalent_tokens, "steps": result.steps}
            entry.update(assess_run(case, task))
            if result.stop_reason == "interrupted":
                data["state"] = "interrupted"
                write_results(out, data)
                return
        except Exception as exc:
            entry.update(status="error", error_type=type(exc).__name__, task_dir=str(task))
        write_results(out, data)
    data["state"] = "complete"


def compare_results(first: dict, second: dict) -> dict:
    if first["mode"] != second["mode"]:
        raise ValueError("不能直接比较离线与真实模型评估")
    before, after = summary(first), summary(second)
    a = {(c["id"], c.get("repeat", 1)): c for c in first["cases"]}
    b = {(c["id"], c.get("repeat", 1)): c for c in second["cases"]}
    comparable = (first["suite_sha256"] == second["suite_sha256"] and a.keys() == b.keys()
                  and bool(first.get("grader_sha256")) and first.get("grader_sha256") == second.get("grader_sha256")
                  and first.get("state") == "complete" and second.get("state") == "complete")
    return {"comparable": comparable, "note": "相同题集、评分器与选择；退化为候选，仍需核对配置、网络和采样差异" if comparable else "题集/评分器/选择不同或运行未完成，不能据此判断退化",
            "before_identity": {k: first.get(k) for k in ("git_revision", "code_sha256", "source_code_sha256", "execution", "config", "state")},
            "after_identity": {k: second.get(k) for k in ("git_revision", "code_sha256", "source_code_sha256", "execution", "config", "state")},
            "before": before, "after": after,
            "regressions": [key for key in sorted(a.keys() & b.keys()) if a[key]["status"] == "pass" and b[key]["status"] in ("fail", "error")] if comparable else [],
            "deltas": {key: after[key] - before[key] if after[key] is not None and before[key] is not None else None
                       for key in ("pass_rate", "steps", "seconds", "equivalent_tokens")}}


def positive(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return number


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, default=CASE_FILE, help='版本化题集 JSON；默认保持原 v2 题集')
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="列出固定题集，不调用 API")
    for mode in ("offline", "live"):
        p = sub.add_parser(mode)
        p.add_argument("--case", action="append", dest="case_ids", help="只运行指定题，可重复指定；自动包含依赖")
        p.add_argument("--out", type=Path, help="新的输出目录，不能已存在")
        if mode == "live":
            p.add_argument("--provider", choices=list(PROVIDERS))
            p.add_argument("--model")
            p.add_argument("--max-steps", type=positive, default=15)
            p.add_argument("--budget", type=positive, default=80000)
            p.add_argument("--suite-budget", type=positive, default=400000, help="等效 token 软预算，收尾可能略超")
            p.add_argument("--review-calls", type=int, choices=range(1, 6), default=2)
            p.add_argument("--repeat", type=int, choices=range(1, 4), default=1)
    p = sub.add_parser("inspect", help="只读评估已生成的任务，不调用 API")
    p.add_argument("task_dir", type=Path)
    p.add_argument("--case", required=True, dest="case_id")
    p.add_argument("--out", type=Path)
    p = sub.add_parser("compare", help="比较两份 results.json，不调用 API")
    p.add_argument("first", type=Path)
    p.add_argument("second", type=Path)
    p = sub.add_parser("rescore", help="按当前评分器复查整份真实评估，不重跑模型、不修改原任务")
    p.add_argument("results", type=Path)
    p.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    out = data = None
    try:
        suite = load_suite(args.suite)
        if args.command == "list":
            for mode in ("offline", "live"):
                for case in suite[mode]:
                    print(f"{mode:7} {case['id']:22} {case['title']}")
            return 0
        if args.command == "compare":
            result = compare_results(json.loads(args.first.read_text(encoding="utf-8")), json.loads(args.second.read_text(encoding="utf-8")))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["comparable"] else 2
        source = None
        if args.command == "rescore":
            source = json.loads(args.results.read_text(encoding="utf-8"))
            if source.get("mode") != "live" or not source.get("cases"):
                raise ValueError("rescore 仅复查真实评估，离线集应重新运行")
            current_suite = hashlib.sha256(json.dumps(suite, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            if source.get("suite_sha256") != current_suite:
                raise ValueError("原结果题集版本不同，不能用新题目重评旧回答")
        mode = "live" if args.command in ("inspect", "rescore") else args.command
        ids = [c["id"] for c in source["cases"]] if source else [args.case_id] if args.command == "inspect" else args.case_ids
        cases = select_cases(suite, mode, ids)
        if args.command == "inspect":
            cases = [c for c in cases if c["id"] == args.case_id]
        out = new_output(args.out)
        data = metadata(mode, suite, cases)
        data["execution"] = args.command
        write_results(out, data)
        start = time.perf_counter()
        if args.command == "offline":
            run_offline(out, data, cases)
        elif args.command == "live":
            run_live(out, data, cases, args)
        elif args.command == "rescore":
            data["source_results"] = str(args.results.resolve())
            data["source_code_sha256"] = source.get("code_sha256")
            data["config"] = {k: source.get("config", {}).get(k) for k in ("provider", "model", "max_steps", "per_case_budget", "suite_budget", "review_max_calls", "repeat", "temperature", "thinking", "token_weights")}
            data["cases"] = []
            for old in source["cases"]:
                record = {"id": old["id"], "title": old["title"], "repeat": old.get("repeat", 1), "source_status": old["status"]}
                if old.get("task_dir"):
                    try:
                        case = next(c for c in cases if c["id"] == old["id"])
                        record.update(assess_run(case, Path(old["task_dir"])))
                    except (OSError, ValueError, KeyError) as exc:
                        record.update(status="error", error_type=type(exc).__name__)
                else:
                    record["status"] = old["status"] if old["status"].startswith("skipped") else "unavailable"
                data["cases"].append(record)
            data["state"] = "complete"
        else:
            data["cases"][0].update(assess_run(cases[0], args.task_dir))
            data["state"] = "complete"
        data["wall_seconds"] = round(time.perf_counter() - start, 3)
        write_results(out, data)
        print(f"评估报告：{out / 'report.md'}")
        print(json.dumps(data["summary"], ensure_ascii=False))
        if data["state"] == "interrupted":
            return 130
        if data["state"] == "runner_error":
            return 2
        return 0 if data["state"] == "complete" and data["summary"]["passed"] == len(data["cases"]) else 1
    except KeyboardInterrupt:
        if data and out:
            data["state"] = "interrupted"
            write_results(out, data)
        return 130
    except (OSError, ValueError, ET.ParseError, subprocess.SubprocessError, KeyError) as exc:
        if data and out:
            data["state"] = "runner_error"
            data["error_type"] = type(exc).__name__
            write_results(out, data)
        print(f"评估未完成：{type(exc).__name__}。请检查参数、配置及输出日志。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
