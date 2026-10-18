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


def test_required_header_rejects_translated_or_reordered_revision_without_mutating(tmp_path):
    question = "报告英文表头 model | input_cost | output_cost | total_cost，行名沿用历史型号。"
    reg = ToolRegistry([make_write_report(tmp_path, question=question)])
    good = "| model | input_cost | output_cost | total_cost |\n|---|---|---|---|\n| A | 1 | 2 | 3 |"
    assert not reg.execute("write_report", {"content": good}).is_error
    quality = (tmp_path / "report.quality.json").read_bytes()
    for bad in [good.replace("input_cost", "输入费"), good.replace("model | input_cost", "input_cost | model"), "```markdown\n" + good + "\n```"]:
        result = reg.execute("write_report", {"content": bad})
        assert result.is_error and "尚未保存" in result.content
        assert (tmp_path / "report.md").read_text(encoding="utf-8") == good
        assert (tmp_path / "report.quality.json").read_bytes() == quality


def test_required_headers_support_partial_append_and_reject_incomplete_final(tmp_path):
    from tools.report import required_table_headers
    assert required_table_headers("英文表头 `model` | `cost`。") == [("model", "cost")]
    assert required_table_headers("比较 A | B，报告用中文。") == []
    reg = ToolRegistry([make_write_report(tmp_path, question="表头 model | cost")])
    assert reg.execute("write_report", {"content": "# 完整报告"}).is_error
    assert not (tmp_path / "report.md").exists()
    assert not reg.execute("write_report", {"content": "# 草稿", "complete": False}).is_error
    assert reg.execute("write_report", {"content": "## 结论", "mode": "append"}).is_error
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "# 草稿"
    assert not reg.execute("write_report", {"content": "| model | cost |\n|---|---|\n| A | 1 |", "mode": "append"}).is_error


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


# ---------------------------------------------------------------------------
# 阶段 5：保存报告后的自动检查
# ---------------------------------------------------------------------------


def _session_with_note(url="https://a.com/x"):
    from tools.fetch import Page
    from tools.session import Note, ResearchSession

    s = ResearchSession()
    s.pages["https://a.com/old"] = Page(url=url, title="A", date=None, text="t")  # 请求地址重定向到了 url
    s.notes.append(Note(1, "要点", url, "A"))
    return s


def test_report_urls():
    from tools.report import report_urls

    text = "见 [1](https://a.com/x)。另见 <https://b.com/y>，以及 https://c.com/z. 重复 https://a.com/x"
    assert report_urls(text) == ["https://a.com/x", "https://b.com/y", "https://c.com/z"]


def test_write_report_checks_unnoted_urls_and_unfinished_plan(tmp_path):
    import json

    from tools import ToolRegistry
    from tools.report import make_write_report

    (tmp_path / "plan.json").write_text(
        json.dumps({"tasks": [{"id": "task-1", "title": "价格", "status": "done"},
                              {"id": "task-2", "title": "生态", "status": "pending"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    reg = ToolRegistry([make_write_report(tmp_path, _session_with_note())])
    r = reg.execute("write_report", {"content": "[1] https://a.com/old\n[2] https://www.a.com/x/\n[3] https://b.com/y"})
    assert not r.is_error and "报告已保存" in r.content
    assert "【自动检查】发现以下问题" in r.content
    assert "task-2 生态（pending）" in r.content and "task-1" not in r.content
    # 跳转前地址、www/末尾斜杠不同的写法都算笔记来源；只有 b.com 没记过笔记
    assert "https://b.com/y" in r.content and "a.com" not in r.content.split("不在笔记来源中")[1]


def test_write_report_check_passes(tmp_path):
    from tools import ToolRegistry
    from tools.report import make_write_report

    reg = ToolRegistry([make_write_report(tmp_path, _session_with_note())])
    r = reg.execute("write_report", {"content": "[1] https://a.com/x"})
    assert "【自动检查】通过" in r.content


def test_write_report_check_uses_whole_file_in_append_mode(tmp_path):
    from tools import ToolRegistry
    from tools.report import make_write_report

    reg = ToolRegistry([make_write_report(tmp_path, _session_with_note())])
    reg.execute("write_report", {"content": "第一部分 https://b.com/y"})
    r = reg.execute("write_report", {"content": "## 参考资料\n[1] https://a.com/x", "mode": "append"})
    assert "https://b.com/y" in r.content  # 前一部分里的问题仍然会报出来
