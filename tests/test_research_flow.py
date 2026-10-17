"""研究流程的集成测试：剧本化模型 + 假搜索后端 + 模拟网站，走完 搜索 → 阅读 → 写报告。"""

from datetime import date

import httpx
from fakes import ScriptedLLM, completion
from test_fetch import ARTICLE
from test_search import FakeBackend

from agent.loop import Agent
from tools import default_registry
from tools.search import SearchResult


def test_search_fetch_write_report(tmp_path):
    backend = FakeBackend([SearchResult("评测", "https://a.com/p", "摘要")])
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, text=ARTICLE, headers={"content-type": "text/html"})
        )
    )
    registry = default_registry(tmp_path / "task", search_backend=backend, http_client=client)
    report = "# 评测报告\n\n## 摘要\n都支持 function calling [1]。\n\n## 参考资料\n[1] 评测 https://a.com/p"
    llm = ScriptedLLM(
        [
            completion(tool_calls=[("c1", "web_search", {"query": "国产大模型 Agent 评测"})]),
            completion(tool_calls=[("c2", "fetch_url", {"url": "https://a.com/p"})]),
            completion(tool_calls=[("c3", "write_report", {"content": report})]),
            completion("报告已完成：三款模型都支持 function calling。"),
        ]
    )

    res = Agent(llm, registry).run("对比国产大模型的 Agent 能力")

    assert res.stop_reason == "final_answer"
    assert [name for name, _ in res.tool_calls] == ["web_search", "fetch_url", "write_report"]
    assert (tmp_path / "task" / "report.md").read_text(encoding="utf-8") == report

    # 模型在第 3 次请求时应该能看到网页正文
    tool_msgs = [m for m in llm.requests[2]["messages"] if m["role"] == "tool"]
    assert "<web_content>" in tool_msgs[-1]["content"]

    # 默认 system prompt 注入了今天的日期
    assert date.today().isoformat() in llm.requests[0]["messages"][0]["content"]


def test_default_registry_tools(tmp_path):
    names = default_registry(tmp_path, search_backend=FakeBackend()).names
    assert names == [
        "web_search",
        "fetch_url",
        "save_note",
        "read_notes",
        "write_report",
        "calculator",
        "days_between",
        "get_current_time",
    ]
