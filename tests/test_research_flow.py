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
        "update_plan",
        "write_report",
        "run_python",
        "read_evidence",
        "calculator",
        "days_between",
        "get_current_time",
    ]


def test_explicit_offline_question_has_no_network_or_web_note_tools(tmp_path):
    registry = default_registry(tmp_path, search_backend=FakeBackend(), question="固定虚构价格，不需要联网。")
    assert not set(registry.names) & {"web_search", "fetch_url", "save_note"}
    assert {"read_notes", "write_report", "run_python"}.issubset(registry.names)
    assert registry.execute("fetch_url", {"url": "https://example.com"}).is_error


def test_steps_run_out_then_wrapup_writes_real_report(tmp_path):
    """阶段 5 实测的情形：步数用完时还没写报告。
    现在收尾阶段可以 read_notes → write_report，且 write_report 的自动检查会指出未读的引用；不许再搜索。"""
    backend = FakeBackend([SearchResult("评测", "https://a.com/p", "摘要")])
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, text=ARTICLE, headers={"content-type": "text/html"})
        )
    )
    task = tmp_path / "task"
    registry = default_registry(task, search_backend=backend, http_client=client)
    report = "# 报告\n\n## 参考资料\n[1] https://a.com/p\n[2] https://never-read.com/x"
    llm = ScriptedLLM(
        [
            completion(tool_calls=[("c1", "fetch_url", {"url": "https://a.com/p"})]),
            completion(tool_calls=[("c2", "save_note", {"content": "要点", "source_url": "https://a.com/p"})]),
            # ---- 步数用完，进入收尾阶段 ----
            completion(
                tool_calls=[("w1", "web_search", {"query": "还想再搜"}), ("w2", "read_notes", {})]
            ),
            completion(tool_calls=[("w3", "write_report", {"content": report})]),
            completion("总结：已保存报告。"),
        ]
    )
    events = []
    res = Agent(llm, registry, max_steps=2, on_event=events.append).run("问题")

    assert res.stop_reason == "max_steps" and res.answer == "总结：已保存报告。"
    assert (task / "report.md").read_text(encoding="utf-8") == report  # 正式报告，而不是兜底存下的回答
    results = {e.data["id"]: e.data["content"] for e in events if e.kind == "tool_result"}
    assert "收尾阶段不能再调用 web_search" in results["w1"]
    assert "共 1 条笔记" in results["w2"]
    assert "https://never-read.com/x" in results["w3"] and "不在笔记来源中" in results["w3"]
    # 所有请求都发同一份工具说明书、不加 tool_choice → 前缀缓存可以命中
    assert all(r["tools"] == llm.requests[0]["tools"] and "tool_choice" not in r for r in llm.requests)


def test_system_prompt_states_step_budget(tmp_path):
    from agent.prompts import research_system_prompt

    assert "本任务最多 25 轮" in research_system_prompt("2026-10-17", 25)
    llm = ScriptedLLM([completion("hi")])
    Agent(llm, default_registry(tmp_path, search_backend=FakeBackend()), max_steps=12).run("q")
    assert "本任务最多 12 轮" in llm.requests[0]["messages"][0]["content"]
