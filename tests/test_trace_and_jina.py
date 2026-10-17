"""Jina Reader 备用通道、trace 日志与回放的测试。"""

import json

import httpx
import pytest
from fakes import ScriptedLLM, completion
from test_fetch import ARTICLE

from agent.loop import Agent
from agent.replay import find_trace
from agent.replay import main as replay_main
from agent.trace import TRACE_FILE, TraceWriter, fanout, read_trace, save_messages
from tools.fetch import JINA_ENDPOINT, make_fetch_url
from tools.registry import ToolRegistry

TARGET = "https://www.zhihu.com/p/1"
JINA_URL = JINA_ENDPOINT + TARGET
JINA_BODY = (
    "Title: 国产大模型核心能力评测\n\n"
    f"URL Source: {TARGET}\n\n"
    "Published Time: 2026-05-14T08:00:00.000Z\n\n"
    "Markdown Content:\n"
    "# 国产大模型核心能力评测\n\n基于 Artificial Analysis 的 Agentic 指数横向对比……"
)


def make_tool(routes, fallback="jina", api_key=""):
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        item = routes.get(str(request.url))
        if item is None:
            return httpx.Response(404)
        if isinstance(item, Exception):
            raise item
        return item

    client = httpx.Client(transport=httpx.MockTransport(handler))
    reg = ToolRegistry([make_fetch_url(client, fallback=fallback, jina_api_key=api_key)])
    return reg, seen


def fetch(reg, url):
    return reg.execute("fetch_url", {"url": url})


def jina_ok():
    return httpx.Response(200, text=JINA_BODY, headers={"content-type": "text/plain"})


# ---------------------------------------------------------------------------
# Jina Reader
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "direct",
    [
        httpx.Response(403),
        httpx.ConnectTimeout("t"),
        httpx.ConnectError("被墙"),
        httpx.Response(200, content=b"%PDF-1.4", headers={"content-type": "application/pdf"}),
        httpx.Response(200, text="<html><body><div id=app></div></body></html>", headers={"content-type": "text/html"}),
    ],
    ids=["403", "timeout", "network", "pdf", "js-page"],
)
def test_falls_back_to_jina(direct):
    reg, seen = make_tool({TARGET: direct, JINA_URL: jina_ok()})
    r = fetch(reg, TARGET)

    assert not r.is_error, r.content
    assert "标题: 国产大模型核心能力评测" in r.content
    assert "发布日期: 2026-05-14" in r.content
    assert "经第三方 Jina Reader 读取" in r.content  # 如实告诉模型来源通道
    assert "Agentic 指数" in r.content and "<web_content>" in r.content
    jina_req = seen[-1]
    assert jina_req.headers["X-Retain-Images"] == "none"
    assert "Authorization" not in jina_req.headers


def test_jina_api_key_sent_when_configured():
    reg, seen = make_tool({TARGET: httpx.Response(403), JINA_URL: jina_ok()}, api_key="jina_xxx")
    fetch(reg, TARGET)
    assert seen[-1].headers["Authorization"] == "Bearer jina_xxx"


def test_direct_success_does_not_touch_jina():
    reg, seen = make_tool({TARGET: httpx.Response(200, text=ARTICLE, headers={"content-type": "text/html"})})
    r = fetch(reg, TARGET)
    assert not r.is_error and "Jina" not in r.content
    assert [str(x.url) for x in seen] == [TARGET]


def test_fallback_disabled():
    reg, seen = make_tool({TARGET: httpx.Response(403), JINA_URL: jina_ok()}, fallback="none")
    r = fetch(reg, TARGET)
    assert r.is_error and "HTTP 403" in r.content
    assert len(seen) == 1


def test_no_fallback_for_invalid_url():
    reg, seen = make_tool({})
    assert "URL 无效" in fetch(reg, "not-a-url").content
    assert seen == []


def test_both_channels_fail_reports_both_reasons():
    jina_err = httpx.Response(
        403, json={"readableMessage": "AbuseAlleviationError: Anonymous access blocked"}
    )
    reg, _ = make_tool({TARGET: httpx.Response(403), JINA_URL: jina_err})
    r = fetch(reg, TARGET)
    assert r.is_error
    assert "HTTP 403" in r.content and "备用通道也失败" in r.content and "AbuseAlleviationError" in r.content


def test_jina_result_is_cached():
    reg, seen = make_tool({TARGET: httpx.Response(403), JINA_URL: jina_ok()})
    fetch(reg, TARGET)
    reg.execute("fetch_url", {"url": TARGET, "start": 10})
    assert len(seen) == 2  # 第二次走缓存


# ---------------------------------------------------------------------------
# trace 日志与回放
# ---------------------------------------------------------------------------


@pytest.fixture
def traced_run(tmp_path):
    from tools import tool

    @tool
    def echo(text: str) -> str:
        """原样返回。"""
        return text

    task_dir = tmp_path / "20261017-120000-问题"
    tracer = TraceWriter(task_dir / TRACE_FILE)
    seen = []
    llm = ScriptedLLM([completion(tool_calls=[("c1", "echo", {"text": "你好"})]), completion("完成")])
    agent = Agent(llm, ToolRegistry([echo]), system_prompt="SYS", on_event=fanout(seen.append, tracer, None))
    result = agent.run("问题")
    tracer.close()
    save_messages(task_dir / "messages.json", result.messages)
    return task_dir, seen, result


def test_trace_contains_every_event_in_order(traced_run):
    task_dir, seen, _ = traced_run
    lines = (task_dir / TRACE_FILE).read_text(encoding="utf-8").splitlines()
    records = [json.loads(x) for x in lines]
    assert [r["kind"] for r in records] == [e.kind for e in seen]
    assert all("ts" in r for r in records)
    assert "你好" in lines[[r["kind"] for r in records].index("tool_result")]  # 中文不转义，可直接阅读


def test_read_trace_roundtrip(traced_run):
    task_dir, seen, _ = traced_run
    events = read_trace(task_dir / TRACE_FILE)
    assert [(e.kind, {k: v for k, v in e.data.items() if k != "ts"}) for e in events] == [
        (e.kind, e.data) for e in seen
    ]


def test_messages_saved(traced_run):
    task_dir, _, result = traced_run
    saved = json.loads((task_dir / "messages.json").read_text(encoding="utf-8"))
    assert saved == result.messages


def test_find_trace(traced_run, tmp_path):
    task_dir, _, _ = traced_run
    assert find_trace(str(task_dir)) == task_dir / TRACE_FILE
    assert find_trace(str(task_dir / TRACE_FILE)) == task_dir / TRACE_FILE
    assert find_trace(None, root=tmp_path) == task_dir / TRACE_FILE
    with pytest.raises(FileNotFoundError):
        find_trace(None, root=tmp_path / "empty")


def test_replay_runs(traced_run, capsys):
    task_dir, _, _ = traced_run
    assert replay_main([str(task_dir), "--full"]) == 0
    out = capsys.readouterr().out
    assert "echo" in out and "完成" in out and "正常完成" in out


def test_replay_missing_file():
    assert replay_main(["/no/such/dir/trace.jsonl"]) == 1


def test_trace_written_incrementally_before_close(tmp_path):
    """即使进程中途被杀，已写的事件也在磁盘上（每行 flush）。"""
    from agent.loop import AgentEvent

    t = TraceWriter(tmp_path / "t" / TRACE_FILE)
    t(AgentEvent("step", {"step": 1}))
    assert (tmp_path / "t" / TRACE_FILE).read_text(encoding="utf-8").count("\n") == 1
    t.close()
