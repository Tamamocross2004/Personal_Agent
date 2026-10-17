from datetime import datetime

import pytest

from agent.workspace import new_task_dir, slugify
from tools.registry import ToolRegistry
from tools.report import make_write_report, safe_filename


@pytest.mark.parametrize(
    "name,expected",
    [
        ("report.md", "report.md"),
        ("对比报告", "对比报告.md"),
        ("../../etc/passwd", "passwd.md"),
        ("..\\..\\Windows\\evil.md", "evil.md"),
        ('a<b>:c"d|e?f*.md', "a_b__c_d_e_f_.md"),
        ("", "report.md"),
        ("...", "report.md"),
        ("REPORT.MD", "REPORT.MD"),
    ],
)
def test_safe_filename(name, expected):
    assert safe_filename(name) == expected


def test_write_report_stays_inside_task_dir(tmp_path):
    task_dir = tmp_path / "task"
    reg = ToolRegistry([make_write_report(task_dir)])

    r = reg.execute("write_report", {"content": "# 标题\n内容", "filename": "../../escape.md"})

    assert not r.is_error, r.content
    assert (task_dir / "escape.md").read_text(encoding="utf-8") == "# 标题\n内容"
    assert not (tmp_path / "escape.md").exists()
    assert "报告已保存" in r.content


def test_write_report_default_name_and_overwrite(tmp_path):
    reg = ToolRegistry([make_write_report(tmp_path)])
    reg.execute("write_report", {"content": "v1"})
    reg.execute("write_report", {"content": "v2"})
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "v2"


def test_write_report_rejects_empty(tmp_path):
    r = ToolRegistry([make_write_report(tmp_path)]).execute("write_report", {"content": "  "})
    assert r.is_error and "为空" in r.content


# ---------- workspace ----------


@pytest.mark.parametrize(
    "q,expected",
    [
        ("对比一下三款国产大模型的 Agent 能力", "对比一下三款国产大模型的-Agent-能力"),
        ("What is MCP?", "What-is-MCP"),
        ("a/b\\c:d", "a-b-c-d"),
        ("？？？", "task"),
    ],
)
def test_slugify(q, expected):
    assert slugify(q) == expected


def test_slugify_truncates():
    assert len(slugify("很长" * 50)) == 30


def test_new_task_dir_is_unique_and_not_created(tmp_path):
    now = datetime(2026, 10, 17, 9, 30, 0)
    a = new_task_dir("问题", root=tmp_path, now=now)
    assert a.name == "20261017-093000-问题"
    assert not a.exists()  # 只计算路径
    a.mkdir()
    b = new_task_dir("问题", root=tmp_path, now=now)
    assert b.name == "20261017-093000-问题-2"


def test_write_report_append_mode(tmp_path):
    reg = ToolRegistry([make_write_report(tmp_path)])
    reg.execute("write_report", {"content": "# 第一部分"})
    r = reg.execute("write_report", {"content": "## 第二部分", "mode": "append"})
    assert "已追加" in r.content
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "# 第一部分\n\n## 第二部分"


def test_write_report_append_to_missing_file_creates_it(tmp_path):
    reg = ToolRegistry([make_write_report(tmp_path)])
    reg.execute("write_report", {"content": "x", "mode": "append"})
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "x"
