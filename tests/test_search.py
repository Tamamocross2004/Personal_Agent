import pytest

from tools.registry import ToolRegistry
from tools.search import SearchResult, format_results, make_web_search


class FakeBackend:
    name = "fake"

    def __init__(self, results=None, error=None):
        self.results = results or []
        self.error = error
        self.calls = []

    def search(self, query, max_results):
        self.calls.append((query, max_results))
        if self.error:
            raise self.error
        return self.results[:max_results]


RESULTS = [
    SearchResult("DeepSeek 文档", "https://api-docs.deepseek.com", "Function Calling 说明"),
    SearchResult("Qwen 评测", "https://example.com/qwen", ""),
]


def test_search_formats_results():
    backend = FakeBackend(RESULTS)
    reg = ToolRegistry([make_web_search(backend)])

    r = reg.execute("web_search", '{"query": "  国产大模型 agent  ", "max_results": 3}')

    assert not r.is_error
    assert backend.calls == [("国产大模型 agent", 3)]
    assert "[1] DeepSeek 文档" in r.content
    assert "URL: https://api-docs.deepseek.com" in r.content
    assert "摘要: Function Calling 说明" in r.content
    assert "[2] Qwen 评测" in r.content
    assert "fetch_url" in r.content  # 提醒模型去读原文


def test_default_max_results_is_5():
    backend = FakeBackend(RESULTS)
    ToolRegistry([make_web_search(backend)]).execute("web_search", '{"query": "x"}')
    assert backend.calls == [("x", 5)]


def test_no_results_suggests_retry():
    out = format_results("冷门词", [])
    assert "没有找到结果" in out and "换个关键词" in out


def test_backend_error_becomes_tool_error():
    reg = ToolRegistry([make_web_search(FakeBackend(error=TimeoutError("slow")))])
    r = reg.execute("web_search", '{"query": "x"}')
    assert r.is_error
    assert "搜索服务（fake）出错" in r.content and "TimeoutError" in r.content


@pytest.mark.parametrize(
    "args,msg",
    [
        ('{"query": "   "}', "不能为空"),
        ('{"query": "x", "max_results": 0}', "max_results"),
        ('{"query": "x", "max_results": 50}', "max_results"),
    ],
)
def test_invalid_arguments(args, msg):
    reg = ToolRegistry([make_web_search(FakeBackend(RESULTS))])
    r = reg.execute("web_search", args)
    assert r.is_error and msg in r.content


def test_schema_bounds_visible_to_model():
    props = make_web_search(FakeBackend()).schema()["function"]["parameters"]["properties"]
    assert props["max_results"]["minimum"] == 1
    assert props["max_results"]["maximum"] == 10


# ---------- DDGSBackend 重试与地区轮换（注入假的 text 函数，不访问网络） ----------

from tools.search import DDGS_WEB_ENGINES, DDGSBackend  # noqa: E402


class FakeText:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, query, region, max_results, backend):
        self.calls.append((region, backend))
        item = self.outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


HIT = [{"title": "t", "href": "https://x.com", "body": "b"}]


def make_backend(outcomes, **kw):
    fake = FakeText(outcomes)
    sleeps = []
    return DDGSBackend(text_fn=fake, sleep=sleeps.append, **kw), fake, sleeps


def test_ddgs_success_first_try_uses_web_engines_only():
    backend, fake, sleeps = make_backend([HIT])
    assert backend.search("hello", 5) == [SearchResult("t", "https://x.com", "b")]
    assert fake.calls == [("wt-wt", DDGS_WEB_ENGINES)]
    assert "wikipedia" not in DDGS_WEB_ENGINES
    assert sleeps == []


def test_ddgs_chinese_query_prefers_cn_region_and_rotates_on_retry():
    backend, fake, sleeps = make_backend([Exception("No results found."), [], HIT])
    assert backend.search("国产大模型", 5)[0].url == "https://x.com"
    assert [r for r, _ in fake.calls] == ["cn-zh", "wt-wt", "cn-zh"]
    assert sleeps == [1.0, 2.0]


def test_ddgs_gives_clear_error_after_all_attempts():
    backend, fake, _ = make_backend([Exception("boom")] * 3)
    with pytest.raises(RuntimeError, match="尝试 3 次均未得到结果"):
        backend.search("x", 5)
    assert len(fake.calls) == 3


def test_ddgs_skips_results_without_url():
    backend, _, _ = make_backend([[{"title": "no url"}], HIT])
    assert len(backend.search("x", 5)) == 1


def test_ddgs_drops_relative_redirect_urls():
    raw = [
        {"title": "知乎", "href": "/clev?event=Sp_x9&sc=abc", "body": ""},
        {"title": "ok", "href": "https://ok.com", "body": ""},
    ]
    backend, _, _ = make_backend([raw])
    assert [r.url for r in backend.search("x", 5)] == ["https://ok.com"]
