import json

import pytest
from PIL import Image

from agent.quality import inspect_report, source_kind
from tools.fetch import Page
from tools.session import Note, ResearchSession


def evidence(url="https://help.aliyun.com/zh/model-studio/model-pricing"):
    session = ResearchSession()
    session.pages[url] = Page(url=url, title="价格", date=None, text="价格说明")
    session.notes.append(Note(1, "输入价格 2 元，输出 8 元", url, "价格"))
    return session


def report(body="价格为 2 元[1]。", reference="[1] https://help.aliyun.com/zh/model-studio/model-pricing"):
    return f"# 测试报告\n\n## 摘要\n{body}\n\n## 方法与范围\n仅检查示例。\n\n## 结论\n结果见摘要。\n\n## 局限性\n数据范围有限。\n\n## 参考资料\n{reference}"


@pytest.mark.parametrize("url,kind", [
    ("https://help.aliyun.com/zh/model-studio/model-pricing", "official_candidate"),
    ("https://developer.aliyun.com/article/1761145", "community"),
    ("https://deepseekagent.io/zh/model", "third_party"),
    ("https://api-docs.deepseek.com.evil.test/pricing", "unknown"),
    ("https://unknown.test/官方文档", "unknown"),
])
def test_source_kind_conservative(url, kind):
    assert source_kind(url) == kind


def test_complete_report_passes_and_api_endpoints_are_not_evidence(tmp_path):
    text = report("价格为 2 元[1]。API 地址 `https://api.deepseek.com`，另一地址 https://api.deepseek.com/anthropic。")
    check = inspect_report(text, tmp_path, evidence())
    assert not check.issues
    assert len(check.sources) == 1


def test_api_endpoint_in_reference_still_requires_note(tmp_path):
    check = inspect_report(report("见[1]。", "[1] https://api.deepseek.com"), tmp_path, evidence())
    assert any(i.category == "source" for i in check.issues)


def test_numbered_references_missing_duplicate_unused(tmp_path):
    text = report("参见[1][9]。", "[1] https://a.test\n[1] https://b.test\n2. https://c.test")
    messages = "\n".join(i.message for i in inspect_report(text, tmp_path, None).issues)
    assert "[9] 没有对应" in messages
    assert "[1] 重复" in messages
    assert "[2] 未被正文引用" in messages


def test_reference_definitions_and_code_examples(tmp_path):
    text = report("参见[1]。\n```python\nx = '[999] https://never-read.test'\n```", "[1]: https://help.aliyun.com/zh/model-studio/model-pricing")
    assert not inspect_report(text, tmp_path, evidence()).issues


def test_numbered_local_calculation_artifacts_are_valid_references(tmp_path):
    text = report("单价依据[1]，计算产物见[2]。", "[1] https://help.aliyun.com/zh/model-studio/model-pricing\n[2] 本地计算产物 `costs.csv`、`costs.png`。")
    check = inspect_report(text, tmp_path, evidence())
    assert not check.issues
    assert check.references["2"] == []


@pytest.mark.parametrize("url", ["https://developer.aliyun.com/article/1761145", "https://deepseekagent.io/zh/model"])
def test_community_and_third_party_cannot_be_labeled_official(tmp_path, url):
    check = inspect_report(report("官方定价为 2 元[1]。", f"1. 文章 {url}"), tmp_path, evidence(url))
    assert any(i.category == "source_identity" for i in check.issues)


def test_existing_relative_image_and_reference_image(tmp_path):
    Image.new("RGB", (4, 4)).save(tmp_path / "costs.png")
    text = report("价格为 2 元[1]。\n![成本](costs.png)\n![成本][plot]\n[plot]: costs.png")
    assert not inspect_report(text, tmp_path, evidence()).issues


def test_explicit_nonofficial_caveat_not_reported_as_official_claim(tmp_path):
    url = "https://developer.aliyun.com/article/1761145"
    check = inspect_report(report("社区文章不能称为官方定价[1]。", f"1. 社区 {url}"), tmp_path, evidence(url))
    assert not any(i.category == "source_identity" for i in check.issues)


def test_injected_fixture_flags_three_mechanical_problem_types(tmp_path):
    from pathlib import Path
    text = (Path(__file__).parent / "fixtures" / "report_quality_bad.md").read_text(encoding="utf-8")
    check = inspect_report(text, tmp_path, evidence("https://developer.aliyun.com/article/1761145"))
    assert {i.category for i in check.issues} >= {"citation", "source_identity", "image"}


@pytest.mark.parametrize("target,expected", [("missing.png", "不存在"), ("../outside.png", "越出"), ("broken.png", "无法解码")])
def test_missing_escaping_and_corrupt_images(tmp_path, target, expected):
    (tmp_path / "broken.png").write_text("not an image")
    check = inspect_report(report(f"价格为 2 元[1]。\n![成本]({target})"), tmp_path, evidence())
    assert any(expected in i.message for i in check.issues)


def test_final_plan_checks_can_be_refreshed_without_changing_report(tmp_path):
    from agent.loop import Agent
    from fakes import ScriptedLLM, completion
    from tools.registry import ToolRegistry
    from tools.plan import make_update_plan
    from tools.report import make_write_report
    plan = {"tasks": [{"title": "价格"}]}
    llm = ScriptedLLM([
        completion(json.dumps(plan)),
        completion(tool_calls=[("w", "write_report", {"content": report()})]),
        completion(tool_calls=[("p", "update_plan", {"task_id": "task-1", "status": "done"})]),
        completion("完成"),
        completion("计划已完成，依据尚未关联。"),
    ])
    session=evidence()
    registry = ToolRegistry([make_write_report(tmp_path, session), make_update_plan(tmp_path)])
    Agent(llm, registry, task_dir=tmp_path, research_session=session).run("报告")
    check = json.loads((tmp_path / "report.quality.json").read_text(encoding="utf-8"))
    assert check["issues"] == []
