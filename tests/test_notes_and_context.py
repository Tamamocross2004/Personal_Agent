"""阶段 4：笔记工具、上下文估算与压缩、运行对比。"""

import json

import httpx
import pytest
from fakes import ScriptedLLM, completion
from test_fetch import ARTICLE

from agent.compare import collect
from agent.compare import main as compare_main
from agent.context import (
    COMPACTED_MARKER,
    ContextCompactor,
    estimate_messages,
    estimate_tokens,
)
from agent.loop import Agent
from agent.trace import TRACE_FILE, TraceWriter, fanout
from tools import ResearchSession, ToolRegistry, default_registry
from tools.fetch import Page
from tools.notes import NOTES_FILE, make_notes_tools
from tools.search import SearchResult
from tools.session import normalize_url

# ---------------------------------------------------------------------------
# URL 规范化与 session
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "a,b",
    [
        ("https://www.Example.com/a/", "https://example.com/a"),
        ("https://example.com/a#section", "https://example.com/a"),
        ("HTTPS://example.com/a?x=1", "https://example.com/a?x=1"),
    ],
)
def test_normalize_url_equivalent(a, b):
    assert normalize_url(a) == normalize_url(b)


def test_normalize_url_keeps_query_difference():
    assert normalize_url("https://a.com/p?id=1") != normalize_url("https://a.com/p?id=2")


def make_session():
    s = ResearchSession()
    # 请求地址与重定向后的最终地址不同
    s.pages["http://a.com/old"] = Page("https://www.a.com/new/", "新页面", None, "正文")
    return s


def test_session_find_page_by_requested_or_final_url():
    s = make_session()
    assert s.find_page("http://a.com/old").title == "新页面"
    assert s.find_page("https://a.com/new").title == "新页面"
    assert s.find_page("https://b.com") is None


# ---------------------------------------------------------------------------
# 笔记工具
# ---------------------------------------------------------------------------


@pytest.fixture
def notes(tmp_path):
    s = make_session()
    reg = ToolRegistry(make_notes_tools(tmp_path, s))
    return reg, s, tmp_path


def test_save_and_read_notes(notes):
    reg, s, tmp = notes
    r = reg.execute("save_note", {"content": "GLM-5.3 Terminal-Bench 3.0 得分 28.3", "source_url": "https://a.com/new", "topic": "基准"})
    assert not r.is_error and "#1" in r.content
    reg.execute("save_note", {"content": "第二条", "source_url": "http://a.com/old"})

    out = reg.execute("read_notes", "{}").content
    assert "共 2 条笔记，来自 1 个网页" in out
    assert "### #1 基准" in out and "来源：新页面 — https://www.a.com/new/" in out
    assert "28.3" in out
    # 同时落盘
    md = (tmp / NOTES_FILE).read_text(encoding="utf-8")
    assert md.count("### #") == 2
    assert s.has_note_for("http://a.com/old") and s.has_note_for("https://www.a.com/new/")


def test_note_source_must_be_fetched(notes):
    reg, s, _ = notes
    r = reg.execute("save_note", {"content": "摘要里看到的数字", "source_url": "https://codingplan.org/plans/zhipu"})
    assert r.is_error and "不是本次任务中用 fetch_url 成功读过的网页" in r.content
    assert s.notes == []


@pytest.mark.parametrize("content,msg", [("   ", "为空"), ("x" * 3001, "过长")])
def test_note_content_validation(notes, content, msg):
    reg, _, _ = notes
    r = reg.execute("save_note", {"content": content, "source_url": "https://a.com/new"})
    assert r.is_error and msg in r.content


def test_read_notes_empty(notes):
    reg, _, _ = notes
    assert reg.execute("read_notes", "").content == "还没有任何笔记。"


def test_fetch_and_notes_share_session(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=ARTICLE, headers={"content-type": "text/html"})))
    session = ResearchSession()
    reg = default_registry(tmp_path, search_backend=object(), http_client=client, session=session)  # type: ignore[arg-type]
    assert reg.execute("save_note", {"content": "x", "source_url": "https://a.com/p"}).is_error
    reg.execute("fetch_url", {"url": "https://a.com/p"})
    assert not reg.execute("save_note", {"content": "x", "source_url": "https://a.com/p"}).is_error
    assert len(session.notes) == 1


# ---------------------------------------------------------------------------
# token 估算
# ---------------------------------------------------------------------------


def test_estimate_tokens_chinese_vs_english():
    assert estimate_tokens("") == 0
    assert 75 <= estimate_tokens("中" * 100) <= 80  # ~1.3 字 / token
    assert 28 <= estimate_tokens("a" * 100) <= 33  # ~3.3 字符 / token


def test_estimate_messages_counts_tool_call_arguments():
    m = {"role": "assistant", "content": None, "tool_calls": [{"id": "c", "type": "function", "function": {"name": "write_report", "arguments": "中" * 300}}]}
    assert estimate_messages([m]) > 200


# ---------------------------------------------------------------------------
# 上下文压缩
# ---------------------------------------------------------------------------

PAGE = "URL: https://a.com/p\n标题: 某评测\n正文: 第 0–6000 字符，共 9000 字符\n<web_content>\n" + "内容" * 2000 + "\n</web_content>"
SEARCH = "搜索「大模型」的结果（共 2 条）：\n\n[1] 标题一\n    URL: https://a.com/1\n    摘要: " + "摘要" * 300 + "\n[2] 标题二\n    URL: https://a.com/2\n    摘要: 很短\n\n提示：……"


def history(n_old_turns=2):
    """system, user, 然后若干轮 (assistant 调用 → tool 结果)。"""
    msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "q"}]
    for i in range(n_old_turns):
        msgs.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"s{i}", "type": "function", "function": {"name": "web_search", "arguments": '{"query": "x"}'}},
            {"id": f"f{i}", "type": "function", "function": {"name": "fetch_url", "arguments": '{"url": "https://a.com/p"}'}},
            {"id": f"c{i}", "type": "function", "function": {"name": "calculator", "arguments": '{"expression": "1+1"}'}},
        ]})
        msgs.append({"role": "tool", "tool_call_id": f"s{i}", "content": SEARCH})
        msgs.append({"role": "tool", "tool_call_id": f"f{i}", "content": PAGE})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "1+1 = 2"})
    return msgs


def test_no_compaction_below_threshold():
    msgs = history(4)
    assert ContextCompactor(threshold=10**9).maybe_compact(msgs, estimate_messages(msgs)) is None


def test_compacts_old_fetch_and_search_but_keeps_recent_turns():
    msgs = history(4)
    before = estimate_messages(msgs)
    res = ContextCompactor(threshold=100, keep_recent_turns=2, min_saving=1).maybe_compact(msgs, before)

    assert res is not None
    assert res.by_tool == {"web_search": 2, "fetch_url": 2}  # 4 轮里只压前 2 轮
    assert res.tokens_after < before * 0.6
    tools = [m for m in msgs if m["role"] == "tool"]
    # 前两轮：被压缩；后两轮：原样保留
    assert tools[0]["content"].startswith(COMPACTED_MARKER) and tools[1]["content"].startswith(COMPACTED_MARKER)
    assert tools[6]["content"] == SEARCH and tools[7]["content"] == PAGE
    # 计算结果太短，不压缩
    assert tools[2]["content"] == "1+1 = 2"
    # 协议完整：每个 tool_call_id 仍然有对应的 tool 消息
    ids = [c["id"] for m in msgs if m["role"] == "assistant" for c in m["tool_calls"]]
    assert ids == [m["tool_call_id"] for m in tools]
    # 估算值与实际一致
    assert abs(res.tokens_after - estimate_messages(msgs)) < 5


def test_compacted_fetch_stub_content():
    msgs = history(3)
    ContextCompactor(threshold=1, keep_recent_turns=1).maybe_compact(msgs, 10**6)
    stub = msgs[4]["content"]
    assert "https://a.com/p（某评测），第 0–6000 字符，共 9000 字符" in stub
    assert "还没有记笔记" in stub and "再次调用 fetch_url" in stub


def test_compacted_fetch_stub_mentions_notes_when_noted():
    msgs = history(3)
    ContextCompactor(threshold=1, keep_recent_turns=1, has_note=lambda url: url == "https://a.com/p").maybe_compact(msgs, 10**6)
    assert "要点已记录在笔记中" in msgs[4]["content"]


def test_compacted_search_keeps_titles_and_urls_only():
    msgs = history(3)
    ContextCompactor(threshold=1, keep_recent_turns=1).maybe_compact(msgs, 10**6)
    stub = msgs[3]["content"]
    assert "[1] 标题一" in stub and "URL: https://a.com/2" in stub
    assert "摘要" not in stub.split("\n", 1)[1]


def test_compaction_is_idempotent():
    msgs = history(3)
    c = ContextCompactor(threshold=1, keep_recent_turns=1)
    assert c.maybe_compact(msgs, 10**6) is not None
    assert c.maybe_compact(msgs, 10**6) is None  # 已压缩的不再处理


def test_skips_when_saving_too_small():
    msgs = history(3)
    assert ContextCompactor(threshold=1, keep_recent_turns=1, min_saving=10**6).maybe_compact(msgs, 10**6) is None
    assert not msgs[4]["content"].startswith(COMPACTED_MARKER)  # 没有改动任何消息


def test_hysteresis_prevents_compacting_every_turn():
    msgs = history(6)
    c = ContextCompactor(threshold=1000, keep_recent_turns=4, min_saving=1, rearm_ratio=0.5)
    first = c.maybe_compact(msgs, 20_000)
    assert first is not None and first.by_tool == {"web_search": 2, "fetch_url": 2}
    # 又过了一轮：第 3 轮变"旧"了，但上下文只比上次压缩后涨了一点 → 不压
    c.keep_recent_turns = 3
    assert c.maybe_compact(msgs, first.tokens_after + 100) is None
    # 涨够了 rearm_ratio × threshold → 允许再压
    assert c.maybe_compact(msgs, first.tokens_after + 600) is not None
    # reset 后重新计
    c.reset()
    assert c._last_after is None


def test_nothing_to_compact_when_all_turns_recent():
    msgs = history(2)
    assert ContextCompactor(threshold=1, keep_recent_turns=2).maybe_compact(msgs, 10**6) is None


# ---------------------------------------------------------------------------
# 与 Agent 主循环集成
# ---------------------------------------------------------------------------


@pytest.fixture
def site_registry(tmp_path):
    long_article = ARTICLE.replace("<p>第三段", "<p>" + "很长的正文。" * 800 + "</p><p>第三段")
    hits = {"n": 0}

    def handler(req):
        hits["n"] += 1
        return httpx.Response(200, text=long_article, headers={"content-type": "text/html"})

    from test_search import FakeBackend

    session = ResearchSession()
    reg = default_registry(
        tmp_path / "task",
        search_backend=FakeBackend([SearchResult("评测", "https://a.com/p", "摘要")]),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        session=session,
    )
    return reg, session, hits


def test_agent_compacts_and_allows_refetch(site_registry):
    reg, session, hits = site_registry
    llm = ScriptedLLM(
        [
            completion(tool_calls=[("c1", "fetch_url", {"url": "https://a.com/p"})], prompt_tokens=1000),
            completion(tool_calls=[("c2", "save_note", {"content": "要点", "source_url": "https://a.com/p"})], prompt_tokens=5000),
            completion(tool_calls=[("c3", "calculator", {"expression": "1+1"})], prompt_tokens=5100),
            # 第 4 步前触发压缩（c1 已在保护范围之外）；随后再次读取同一网页 → 不应被重复检测拦截
            completion(tool_calls=[("c4", "fetch_url", {"url": "https://a.com/p"})], prompt_tokens=1500),
            completion("done", prompt_tokens=5000),
        ]
    )
    events = []
    compactor = ContextCompactor(threshold=3000, keep_recent_turns=2, min_saving=500, has_note=session.has_note_for)
    res = Agent(llm, reg, system_prompt="SYS", compactor=compactor, on_event=events.append).run("q")

    assert res.stop_reason == "final_answer"
    comps = [e.data for e in events if e.kind == "compaction"]
    assert len(comps) == 1 and comps[0]["by_tool"] == {"fetch_url": 1}
    # 第 4 次请求中，第一次的网页原文已被替换，且提示"已记笔记"
    req4 = llm.requests[3]["messages"]
    first = next(m for m in req4 if m.get("tool_call_id") == "c1")
    assert first["content"].startswith(COMPACTED_MARKER) and "要点已记录在笔记中" in first["content"]
    # 再次读取：成功执行（不是重复拦截），且走缓存没有重新下载
    refetch = [e.data for e in events if e.kind == "tool_result" and e.data["id"] == "c4"][0]
    assert not refetch["is_error"] and "<web_content>" in refetch["content"]
    assert hits["n"] == 1


def test_agent_without_compactor_never_compacts(site_registry):
    reg, _, _ = site_registry
    llm = ScriptedLLM(
        [completion(tool_calls=[(f"c{i}", "calculator", {"expression": f"{i}+1"})], prompt_tokens=50_000) for i in range(3)]
        + [completion("done")]
    )
    events = []
    Agent(llm, reg, system_prompt="SYS", on_event=events.append).run("q")
    assert not [e for e in events if e.kind == "compaction"]


# ---------------------------------------------------------------------------
# 运行对比
# ---------------------------------------------------------------------------


def fake_run(task_dir, prompt_tokens, report, fetched=("https://a.com/p",), notes=0, compaction=None):
    t = TraceWriter(task_dir / TRACE_FILE)
    from agent.loop import AgentEvent as E

    t(E("run_start", {"question": "问题"}))
    for i, p in enumerate(prompt_tokens):
        t(E("llm_call", {"prompt_tokens": p, "cached_tokens": p // 2, "completion_tokens": 10}))
    for i, u in enumerate(fetched):
        t(E("tool_call", {"id": f"f{i}", "name": "fetch_url", "arguments": json.dumps({"url": u})}))
        t(E("tool_result", {"id": f"f{i}", "name": "fetch_url", "content": f"URL: {u}\n…", "is_error": False}))
    for i in range(notes):
        t(E("tool_result", {"id": f"n{i}", "name": "save_note", "content": "ok", "is_error": False}))
    if compaction:
        t(E("compaction", {"tokens_before": compaction[0], "tokens_after": compaction[1]}))
    t(E("run_end", {"stop_reason": "final_answer", "steps": len(prompt_tokens), "seconds": 1.5}))
    t.close()
    (task_dir / "report.md").write_text(report, encoding="utf-8")
    (task_dir / NOTES_FILE).write_text("笔记" * 10000, encoding="utf-8")  # 比报告大，不能被当成报告


def test_collect_stats(tmp_path):
    d = tmp_path / "run"
    report = "# 报告\n正文\n## 参考资料\n1. https://a.com/p\n2. https://www.a.com/p/\n3. https://unread.com/x"
    fake_run(d, [100, 300, 200], report, notes=2, compaction=(5000, 2000))
    st = collect(d)
    assert (st.llm_calls, st.input_tokens, st.cached_tokens, st.output_tokens) == (3, 600, 300, 30)
    assert (st.final_context, st.peak_context) == (200, 300)
    assert (st.fetches_ok, st.notes, st.compactions, st.compacted_saved) == (1, 2, 1, 3000)
    assert st.report_chars == len(report)
    assert st.refs == 3 and st.refs_unread == ["https://unread.com/x"]


def test_compare_cli(tmp_path, capsys):
    a, b = tmp_path / "a", tmp_path / "b"
    fake_run(a, [1000, 2000, 3000], "# r\n## 参考资料\nhttps://a.com/p")
    fake_run(b, [1000, 1200, 1300], "# r\n## 参考资料\nhttps://a.com/p", notes=3, compaction=(3000, 1000))
    assert compare_main([str(a), str(b)]) == 0
    out = capsys.readouterr().out
    assert "累计输入 tokens" in out and "-42%" in out  # 6000 → 3500


def test_compare_no_dirs(tmp_path):
    assert compare_main([str(tmp_path / "missing")]) == 1


def test_compare_old_trace_without_cache_field_is_not_zero(tmp_path, capsys):
    """阶段 3 修复之前的日志没有 cached_tokens 字段：应显示"未记录"，而不是 0%。"""
    old = tmp_path / "old"
    t = TraceWriter(old / TRACE_FILE)
    from agent.loop import AgentEvent as E

    t(E("llm_call", {"prompt_tokens": 100, "completion_tokens": 1}))
    t(E("run_end", {"stop_reason": "final_answer", "steps": 1, "seconds": 1.0}))
    t.close()
    assert collect(old).cached_tokens is None
    new = tmp_path / "new"
    fake_run(new, [100], "# r")
    assert compare_main([str(old), str(new)]) == 0
    out = capsys.readouterr().out
    assert "未记录" in out and "未命中缓存" in out


# ---------------------------------------------------------------------------
# 离线模拟（agent.simulate）与默认配置
# 防止再犯阶段 4 的错误：用"累计 token"而不是"按缓存计价的成本"来选压缩阈值
# ---------------------------------------------------------------------------

from agent.simulate import main as simulate_main  # noqa: E402
from agent.simulate import restore_original_history, simulate  # noqa: E402


def research_history(turns=12):
    """模拟一次研究：每轮读一个长网页，历史只追加。"""
    msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "q"}]
    for i in range(turns):
        msgs.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"f{i}", "type": "function", "function": {"name": "fetch_url", "arguments": json.dumps({"url": f"https://a.com/{i}"})}}]})
        msgs.append({"role": "tool", "tool_call_id": f"f{i}", "content": PAGE.replace("https://a.com/p", f"https://a.com/{i}")})
    msgs.append({"role": "assistant", "content": "done"})
    return msgs


def test_simulate_append_only_history_is_fully_cached_except_new_part():
    msgs = research_history(5)
    r = simulate(msgs, threshold=0)
    assert r.compactions == 0
    # 只追加：未命中缓存的总量 ≈ 最后一次请求的大小（每次只有新增部分未命中）
    assert abs(r.uncached_input - r.final_context) < 50
    assert r.total_input > 3 * r.uncached_input


def test_simulate_low_threshold_saves_tokens_but_costs_more_with_prefix_cache():
    msgs = research_history(12)
    none = simulate(msgs, threshold=0)
    low = simulate(msgs, threshold=8_000)
    assert low.compactions >= 2
    assert low.total_input < none.total_input * 0.7  # 累计 token 明显减少……
    assert low.uncached_input > none.uncached_input  # ……但未命中缓存的 token 反而增加
    assert low.cost(0.02, 1.0) > none.cost(0.02, 1.0)  # 按 DeepSeek 计价更贵
    # 没有前缀缓存（命中与未命中同价）时，压缩确实省钱
    assert low.cost(1.0, 1.0) < none.cost(1.0, 1.0)


def test_default_threshold_is_a_size_guard_not_a_cost_optimizer():
    from agent.config import Settings

    s = Settings(_env_file=None)
    assert s.context_compact_threshold >= 40_000
    assert s.agent_max_steps >= 30
    assert ContextCompactor().threshold == s.context_compact_threshold


def test_restore_original_history_from_trace(tmp_path):
    from agent.loop import AgentEvent as E
    from agent.trace import save_messages

    msgs = research_history(3)
    original = msgs[3]["content"]
    ContextCompactor(threshold=1, keep_recent_turns=1, min_saving=1).maybe_compact(msgs, 10**6)
    assert msgs[3]["content"].startswith(COMPACTED_MARKER)
    save_messages(tmp_path / "messages.json", msgs)
    t = TraceWriter(tmp_path / TRACE_FILE)
    t(E("tool_result", {"id": "f0", "name": "fetch_url", "content": original, "is_error": False}))
    t.close()

    restored, n = restore_original_history(tmp_path)
    assert restored[3]["content"] == original
    assert n == 1  # f1 在 trace 里没有记录，无法还原


def test_simulate_cli(tmp_path, capsys):
    from agent.trace import save_messages

    save_messages(tmp_path / "messages.json", research_history(8))
    assert simulate_main([str(tmp_path), "--thresholds", "0", "8000"]) == 0
    out = capsys.readouterr().out
    assert "不压缩" in out and "8000" in out
    assert simulate_main([str(tmp_path / "nope")]) == 1
