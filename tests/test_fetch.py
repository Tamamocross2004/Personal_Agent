"""fetch_url 测试：用 httpx.MockTransport 模拟网站，不发真实网络请求。"""

import httpx
import pytest

from tools.fetch import MAX_DOWNLOAD_BYTES, make_fetch_url
from tools.registry import ToolRegistry

PARAGRAPH = "大模型的 Agent 能力主要体现在工具调用、多步规划和长上下文理解等方面。" * 6

ARTICLE = f"""<!DOCTYPE html>
<html><head>
  <title>国产大模型 Agent 能力评测</title>
  <meta property="article:published_time" content="2026-06-21">
</head><body>
  <nav>首页 | 新闻 | 登录 | 注册</nav>
  <script>var ad = "广告脚本";</script>
  <article>
    <h1>国产大模型 Agent 能力评测</h1>
    <p>{PARAGRAPH}</p>
    <p>第二段：DeepSeek、通义千问和 GLM 都支持 function calling。{PARAGRAPH}</p>
    <p>第三段：评测结论。{PARAGRAPH}</p>
  </article>
  <footer>版权所有 © 2026</footer>
</body></html>"""


def make_tool(routes, chunk_chars=6000):
    """routes: {url: httpx.Response 或 异常}。返回 (registry, 请求计数)。"""
    hits = {"n": 0}

    def handler(request: httpx.Request):
        hits["n"] += 1
        item = routes.get(str(request.url))
        if item is None:
            return httpx.Response(404)
        if isinstance(item, Exception):
            raise item
        return item

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ToolRegistry([make_fetch_url(client, chunk_chars=chunk_chars)]), hits


def html(body: str | bytes, ctype="text/html; charset=utf-8"):
    content = body.encode("utf-8") if isinstance(body, str) else body
    return httpx.Response(200, content=content, headers={"content-type": ctype})


def fetch(reg, url, start=0):
    import json

    return reg.execute("fetch_url", json.dumps({"url": url, "start": start}))


def test_extracts_article_and_strips_boilerplate():
    reg, _ = make_tool({"https://a.com/p": html(ARTICLE)})
    r = fetch(reg, "https://a.com/p")

    assert not r.is_error, r.content
    assert "标题: 国产大模型 Agent 能力评测" in r.content
    assert "发布日期: 2026-06-21" in r.content
    assert "<web_content>" in r.content and "</web_content>" in r.content
    assert "function calling" in r.content
    assert "广告脚本" not in r.content
    assert "已读完全文" in r.content


def test_paging_and_cache():
    reg, hits = make_tool({"https://a.com/p": html(ARTICLE)}, chunk_chars=100)

    first = fetch(reg, "https://a.com/p")
    assert "正文: 第 0–100 字符" in first.content
    assert "fetch_url(url, start=100)" in first.content

    second = fetch(reg, "https://a.com/p", start=100)
    assert "正文: 第 100–200 字符" in second.content

    beyond = fetch(reg, "https://a.com/p", start=10**6)
    assert "已经读完" in beyond.content

    assert hits["n"] == 1  # 翻页走缓存，只下载一次


def test_gbk_page_without_charset_header():
    # 编码只在 HTML <meta> 里声明、HTTP 头里没有 charset —— 很多老中文站点就是这样
    page = ARTICLE.replace("©", "(c)").replace("<head>", '<head><meta charset="gbk">')
    gbk = page.encode("gbk")
    reg, _ = make_tool({"https://cn.com/p": html(gbk, ctype="text/html")})
    r = fetch(reg, "https://cn.com/p")
    assert not r.is_error, r.content
    assert "工具调用" in r.content


def test_plain_text():
    reg, _ = make_tool({"https://a.com/t.txt": html("纯文本内容", ctype="text/plain")})
    assert "纯文本内容" in fetch(reg, "https://a.com/t.txt").content


@pytest.mark.parametrize(
    "routes,url,msg",
    [
        ({}, "https://a.com/missing", "HTTP 404"),
        ({}, "ftp://a.com/x", "只支持 http/https"),
        ({}, "not a url", "URL 无效"),
        ({"https://a.com/x.pdf": html(b"%PDF-1.4", "application/pdf")}, "https://a.com/x.pdf", "PDF"),
        ({"https://a.com/img": html(b"\x89PNG", "image/png")}, "https://a.com/img", "不支持的内容类型"),
        ({"https://a.com/js": html("<html><body><div id=app></div></body></html>")}, "https://a.com/js", "没能从页面提取到正文"),
        ({"https://a.com/slow": httpx.ReadTimeout("t")}, "https://a.com/slow", "超时"),
        ({"https://a.com/down": httpx.ConnectError("refused")}, "https://a.com/down", "网络错误"),
        (
            {"https://a.com/big": html(b"x" * (MAX_DOWNLOAD_BYTES + 1))},
            "https://a.com/big",
            "页面过大",
        ),
    ],
)
def test_errors_are_reported_not_raised(routes, url, msg):
    reg, _ = make_tool(routes)
    r = fetch(reg, url)
    assert r.is_error
    assert msg in r.content


def test_failed_fetch_is_not_cached():
    routes = {"https://a.com/flaky": httpx.ConnectError("refused")}
    reg, hits = make_tool(routes)
    assert fetch(reg, "https://a.com/flaky").is_error
    routes["https://a.com/flaky"] = html(ARTICLE)
    assert not fetch(reg, "https://a.com/flaky").is_error
    assert hits["n"] == 2
