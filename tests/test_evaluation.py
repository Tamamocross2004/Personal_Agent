import csv
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.eval_checks import CSV_COLUMNS, assess_run, numeric_checks
from agent.evaluate import compare_results, load_suite, main, parse_junit, select_cases, summary
from agent.review import fingerprint


def numeric_fixture(tmp_path, *, wrong_report=False, wrong_csv=False):
    # 人工计算的固定答案，与实现的 Decimal 公式独立：0.4+0.08=0.48；1.8+0.27=2.07。
    rows = [["EvalAlpha", 200000, 10000, 2, 8, 0.4, 0.08, 0.48],
            ["EvalBeta", 200000, 10000, 9, 27, 1.8, 0.27, 2.07]]
    if wrong_csv:
        rows[0][5] = 0.2  # 总额仍正确，不能因此把整行当正确。
    with (tmp_path / "costs.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(CSV_COLUMNS)
        writer.writerows(rows)
    value = "0.20" if wrong_report else "0.40"
    return ("# 测试报告\n\n## 摘要\n固定虚构数据。\n\n## 方法与范围\n输入20万、输出1万。\n\n"
            "## 费用\n| model | input_cost | output_cost | total_cost |\n|---|---|---|---|\n"
            f"| EvalAlpha | {value} | 0.08 | 0.48 |\n| EvalBeta | 1.80 | 0.27 | 2.07 |\n\n"
            "## 结论\n只报告测试金额。\n\n## 局限性\n不是产品事实。\n\n## 参考资料\n用户给定数据。")


@pytest.mark.parametrize("wrong_report,wrong_csv", [(True, False), (False, True)])
def test_component_error_is_detected_even_if_total_correct(tmp_path, wrong_report, wrong_csv):
    report = numeric_fixture(tmp_path, wrong_report=wrong_report, wrong_csv=wrong_csv)
    checks = numeric_checks(tmp_path, report, {"input_tokens": 200000, "output_tokens": 10000})
    failures = [c["name"] for c in checks if not c["passed"]]
    assert failures == [f"{'report' if wrong_report else 'csv'}_EvalAlpha_input_cost"]
    assert all(c["passed"] for c in checks if c["name"].endswith("total_cost"))


def test_csv_and_report_must_match_fixed_inputs(tmp_path):
    report = numeric_fixture(tmp_path)
    assert all(c["passed"] for c in numeric_checks(tmp_path, report, {"input_tokens": 200000, "output_tokens": 10000}))
    # CSV 和报告相互一致但与题目用量不符，仍必须失败。
    checks = numeric_checks(tmp_path, report, {"input_tokens": 100000, "output_tokens": 10000})
    assert any(not c["passed"] and c["name"].endswith("input_tokens") for c in checks)


def evaluation_task(tmp_path, status="reviewed", stale=False, stop="final_answer", wrong_report=False):
    report = numeric_fixture(tmp_path, wrong_report=wrong_report)
    (tmp_path / "report.md").write_text(report, encoding="utf-8")
    (tmp_path / "report.review.json").write_text(json.dumps({"status": status, "issues": [],
             "report_sha256": "old" if stale else fingerprint(report)}), encoding="utf-8")
    events = [{"kind": "run_start", "question": "fixture"},
              {"kind": "tool_call", "id": "code", "name": "run_python", "arguments": "{\"code\":\"print(0)\"}"},
              {"kind": "tool_result", "id": "code", "name": "run_python", "is_error": False, "content": "退出码: 0"},
              {"kind": "llm_call", "prompt_tokens": 100, "cached_tokens": 40, "completion_tokens": 20},
              {"kind": "llm_call", "purpose": "report_review", "prompt_tokens": 50, "cached_tokens": 0, "completion_tokens": 10},
              {"kind": "run_end", "stop_reason": stop, "steps": 3, "seconds": 1.5, "equivalent_tokens": 321}]
    (tmp_path / "trace.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return {"id": "cost_components", "numeric": {"input_tokens": 200000, "output_tokens": 10000}}


def test_assessor_uses_actual_budget_metrics_and_includes_review_calls(tmp_path):
    case = evaluation_task(tmp_path)
    result = assess_run(case, tmp_path)
    assert result["status"] == "pass"
    assert result["metrics"]["equivalent_tokens"] == 321  # 不用默认权重重新估算。
    assert result["metrics"]["review_calls"] == 1
    assert result["metrics"]["input_tokens"] == 150


@pytest.mark.parametrize("status,stale,stop", [("skipped_limit", False, "final_answer"),
    ("failed", False, "final_answer"), ("reviewed", True, "final_answer"), ("reviewed", False, "max_steps")])
def test_incomplete_or_stale_reviews_and_forced_finalization_never_pass(tmp_path, status, stale, stop):
    case = evaluation_task(tmp_path, status=status, stale=stale, stop=stop)
    result = assess_run(case, tmp_path)
    assert result["status"] == "fail"
    if stale:
        assert result["review_status"] == "stale"


def test_model_clean_review_cannot_override_wrong_component(tmp_path):
    case = evaluation_task(tmp_path, wrong_report=True)
    result = assess_run(case, tmp_path)
    assert result["review_status"] == "reviewed"
    assert result["status"] == "fail"
    assert any(not c["passed"] and c["name"] == "report_EvalAlpha_input_cost" for c in result["checks"])


def test_case_selection_includes_memory_dependency_and_rejects_unknown():
    suite = load_suite()
    assert [c["id"] for c in select_cases(suite, "live", ["memory_followup"])] == ["memory_seed", "memory_followup"]
    with pytest.raises(ValueError):
        select_cases(suite, "live", ["not-a-case"])


def test_junit_failure_skip_and_missing_are_not_passes(tmp_path):
    xml = tmp_path / "junit.xml"
    xml.write_text('<testsuites><testsuite><testcase classname="tests.test_example" name="test_ok" time=".1"/>'
                   '<testcase classname="tests.test_example" name="test_bad[x]" time=".2"><failure/></testcase>'
                   '<testcase classname="tests.test_example" name="test_skip"><skipped/></testcase></testsuite></testsuites>')
    cases = [{"id": name, "title": name, "selectors": [f"tests/test_example.py::test_{name}"]} for name in ("ok", "bad", "skip", "missing")]
    results = parse_junit(xml, cases)
    assert [r["status"] for r in results] == ["pass", "fail", "skipped", "error"]
    assert results[0]["metrics"]["equivalent_tokens"] is None


def baseline():
    return {"mode": "live", "suite_sha256": "same", "grader_sha256": "grader", "state": "complete", "cases": [
        {"id": "one", "status": "pass", "metrics": {"seconds": 1, "steps": 3, "equivalent_tokens": 100}},
        {"id": "two", "status": "pass", "metrics": {"seconds": 2, "steps": 4, "equivalent_tokens": 200}}]}


def test_compare_flags_regression_and_different_suites():
    first, second = baseline(), baseline()
    second["cases"][1]["status"] = "fail"
    result = compare_results(first, second)
    assert result["regressions"] == [("two", 1)]
    assert result["deltas"]["pass_rate"] == -.5
    second["suite_sha256"] = "changed"
    assert compare_results(first, second)["comparable"] is False
    second["mode"] = "offline"
    with pytest.raises(ValueError):
        compare_results(first, second)


def test_pending_cases_reduce_rate_instead_of_disappearing():
    data = baseline()
    data["cases"][1]["status"] = "pending"
    assert summary(data)["pass_rate"] == .5
    assert summary(data)["unfinished"] == 1


def test_unfinished_runs_do_not_claim_regression():
    first, second = baseline(), baseline()
    second["state"] = "interrupted"
    second["cases"][0]["status"] = "pending"
    result = compare_results(first, second)
    assert not result["comparable"] and not result["regressions"]


def test_different_graders_are_not_compared_as_model_regression():
    first, second = baseline(), baseline()
    second["grader_sha256"] = "new-grader"
    assert not compare_results(first, second)["comparable"]


def test_invalid_review_record_never_passes(tmp_path):
    case = evaluation_task(tmp_path)
    (tmp_path / "report.review.json").write_text("[]", encoding="utf-8")
    result = assess_run(case, tmp_path)
    assert result["status"] == "fail" and result["review_status"] == "missing_or_invalid"


@pytest.mark.parametrize("statement,passed", [
    ("结论：A最稳定。", False),
    ("能否认定产品A在A/B/C中最稳定？", True),
    ("任何‘A最稳定’的说法都属于编造数据，本报告不予采用。", True),
    ("若要回答A/B/C谁最稳定，需补齐同口径测试。", True),
])
def test_questions_and_rejected_claims_are_not_affirmative_winners(tmp_path, statement, passed):
    evaluation_task(tmp_path)
    path = tmp_path / "report.md"
    text = path.read_text(encoding="utf-8") + "\n" + statement
    path.write_text(text, encoding="utf-8")
    path.with_suffix(".review.json").write_text(json.dumps({"status": "reviewed", "issues": [], "report_sha256": fingerprint(text)}), encoding="utf-8")
    result = assess_run({"forbidden_claims": ["A.{0,8}(?:最稳定|最强)"]}, tmp_path)
    assert result["status"] == ("pass" if passed else "fail")


def test_rescore_does_not_rerun_model_or_modify_original_artifacts(tmp_path, monkeypatch):
    import hashlib
    from agent import llm
    task = tmp_path / "task"
    task.mkdir()
    evaluation_task(task)
    original = {p.name: p.read_bytes() for p in task.iterdir()}
    source = tmp_path / "old-results.json"
    checksum = hashlib.sha256(json.dumps(load_suite(), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    source.write_text(json.dumps({"mode": "live", "suite_sha256": checksum, "code_sha256": "old-code", "cases": [
        {"id": "cost_components", "title": "成本", "status": "fail", "task_dir": str(task)}]}), encoding="utf-8")
    monkeypatch.setattr(llm, "LLMClient", lambda *a, **k: pytest.fail("must not call API"))
    out = tmp_path / "rescored"
    assert main(["rescore", str(source), "--out", str(out)]) == 0
    data = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert data["source_code_sha256"] == "old-code" and data["summary"]["passed"] == 1
    assert {p.name: p.read_bytes() for p in task.iterdir()} == original


def test_rescore_rejects_different_question_set(tmp_path):
    source = tmp_path / "old.json"
    source.write_text(json.dumps({"mode": "live", "suite_sha256": "changed-prompts", "cases": [{"id": "cost_components"}]}))
    out = tmp_path / "out"
    assert main(["rescore", str(source), "--out", str(out)]) == 2
    assert not out.exists()


def test_inspect_is_readonly_and_does_not_call_api(tmp_path, monkeypatch):
    from agent import llm
    task = tmp_path / "task"
    task.mkdir()
    evaluation_task(task)
    before = {p.name: p.read_bytes() for p in task.iterdir()}
    monkeypatch.setattr(llm, "LLMClient", lambda *a, **k: pytest.fail("inspect must not call API"))
    out = tmp_path / "assessment"
    assert main(["inspect", str(task), "--case", "cost_components", "--out", str(out)]) == 0
    assert {p.name: p.read_bytes() for p in task.iterdir()} == before
    assert json.loads((out / "results.json").read_text(encoding="utf-8"))["summary"]["passed"] == 1


def test_cli_rejects_existing_output_without_overwriting(tmp_path):
    out = tmp_path / "existing"
    out.mkdir()
    (out / "keep").write_text("keep")
    assert main(["offline", "--out", str(out)]) == 2
    assert (out / "keep").read_text() == "keep"


@pytest.mark.parametrize("suite_budget,seed_status,expected_calls,exit_code", [
    (400000, "pass", 2, 0), (5, "pass", 1, 1), (400000, "fail", 1, 1)])
def test_live_uses_isolated_memory_and_task_paths(tmp_path, monkeypatch, suite_budget, seed_status, expected_calls, exit_code):
    import main as entry
    import agent.config
    import agent.llm
    import agent.evaluate as evaluate
    seen = []
    settings = agent.config.Settings(_env_file=None, deepseek_api_key="never-log-this-key")
    monkeypatch.setattr(agent.config, "Settings", lambda: settings)
    monkeypatch.setattr(agent.llm, "LLMClient", lambda cfg: object())
    def fake_research(client, s, prompt, steps, budget, *, task_dir):
        seen.append((s.memory_enabled, s.memory_db_path, task_dir))
        return SimpleNamespace(equivalent_tokens=10, steps=1, stop_reason="final_answer")
    monkeypatch.setattr(entry, "research", fake_research)
    monkeypatch.setattr(evaluate, "assess_run", lambda *args: {"status": seed_status, "metrics": {"equivalent_tokens": 10}})
    out = tmp_path / "live"
    assert main(["live", "--case", "memory_followup", "--out", str(out), "--suite-budget", str(suite_budget)]) == exit_code
    assert len(seen) == expected_calls and all(enabled for enabled, _, _ in seen)
    assert all(db.is_relative_to(out) and task.is_relative_to(out) for _, db, task in seen)
    assert "never-log-this-key" not in (out / "results.json").read_text(encoding="utf-8")
