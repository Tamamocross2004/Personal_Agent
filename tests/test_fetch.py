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
    assert "页面日期（元数据，类型未核实）: 2026-06-21" in r.content
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


# ---------- 表格补救 ----------
# 复现 DeepSeek 定价页的真实结构：表格被包在 <div><b>…</b></div> 里，trafilatura 会整张丢掉。

PRICING = """<html><head><title>模型 & 价格</title></head><body><article>
<h1>模型 & 价格</h1>
<p>下表所列模型价格以百万 tokens 为单位。我们将根据模型输入和输出的总 token 数进行计量计费，费用从余额中扣减。</p>
<p>空闲时段价格为高峰时段价格的一半。北京时间周一至周五 9:00-12:00、14:00-18:00 为高峰时段，其余为空闲时段。</p>
<div><b><table>
<tr><td colspan="2">模型</td><td>flash</td><td>pro</td></tr>
<tr><td colspan="2">上下文长度</td><td colspan="2">1M</td></tr>
<tr><td rowspan="2">功能</td><td>Tool Calls</td><td>支持</td><td>支持</td></tr>
<tr><td>图像理解</td><td>支持</td><td>不支持</td></tr>
<tr><td rowspan="2">价格</td><td>输入</td><td>1元</td><td>4.5元</td></tr>
<tr><td>输出</td><td>4元</td><td>13.5元</td></tr>
</table></b></div>
<p>产品价格可能发生变动，请定期查看此页面以获知最新价格信息。</p>
</article></body></html>"""


def test_recovers_table_dropped_by_trafilatura():
    reg, _ = make_tool({"https://a.com/pricing": html(PRICING)})
    c = fetch(reg, "https://a.com/pricing").content
    assert "备用解析器补充提取" in c
    assert "| 模型 |  | flash | pro |" in c
    assert "| 上下文长度 |  | 1M |  |" in c  # colspan：值在首格，其余留空
    assert "| 功能 | Tool Calls | 支持 | 支持 |" in c  # 两列真实相同的值都保留
    assert "| 功能 | 图像理解 | 支持 | 不支持 |" in c  # rowspan：每行重复"功能"
    assert "| 价格 | 输出 | 4元 | 13.5元 |" in c


def test_table_already_extracted_is_not_duplicated():
    simple = ARTICLE.replace(
        "</article>",
        "<table><tr><th>模型</th><th>价格</th></tr><tr><td>flash</td><td>1元</td></tr>"
        "<tr><td>pro</td><td>4元</td></tr></table></article>",
    )
    reg, _ = make_tool({"https://a.com/s": html(simple)})
    c = fetch(reg, "https://a.com/s").content
    assert "flash" in c and "备用解析器" not in c


def test_table_to_markdown_bad_span_attributes():
    from bs4 import BeautifulSoup

    from tools.fetch import table_to_markdown

    t = BeautifulSoup('<table><tr><td colspan="abc">a</td><td rowspan="0">b</td></tr></table>', "lxml").table
    assert table_to_markdown(t) == "| a | b |\n|---|---|"


def test_locate_deep_passage_then_read_uses_same_cache():
    url = "https://a.com/long"
    body = "无关正文\n" * 5000 + "Context manager: commits transactions, does not close connections." + "尾部\n" * 1000
    reg, hits = make_tool({url: html(body, "text/plain")})
    result = reg.execute("fetch_url", {"url": url, "query": "CONTEXT MANAGER"})
    assert not result.is_error
    assert "does not close connections" in result.content
    assert "局部片段" in result.content and "<web_content>" in result.content
    assert len(result.content) < 1800
    offset = body.index("Context manager")
    read = reg.execute("fetch_url", {"url": url, "start": offset, "length": 200})
    assert f"正文: 第 {offset}–{offset + 200} 字符" in read.content
    assert "does not close connections" in read.content
    assert hits["n"] == 1


def test_search_windows_bounded_and_pagination_preserves_offsets():
    import re
    from tools.fetch import Page, format_matches

    body = ("x" * 1500 + "TARGET") * 12
    page = Page("https://a.com", "标题", "2026-01-01", body, "Jina Reader")
    first = format_matches(page, "target", 0)
    assert first.count("<web_content>") == 4
    assert len(first) < 6000
    assert "Jina Reader" in first and "发布日期" in first
    offset = int(re.search(r"start=(\d+)", first).group(1))
    assert body[offset:offset + 6] == "TARGET"
    second = format_matches(page, "target", offset)
    assert f"正文: 第 {offset}–" in second
    assert second.count("<web_content>") == 4


def test_dense_matches_are_bounded_and_missing_query_is_not_evidence_of_absence():
    from tools.fetch import Page, format_matches

    page = Page("https://a.com", "", None, "a+b " * 10000)
    found = format_matches(page, "a+b", 0)
    assert len(found) < 6000 and "还有命中" in found
    assert "未找到" in format_matches(page, "a.*b", 0)  # literal, not regex
    assert "字面未命中" in format_matches(page, "absent", 0)
    assert "未找到" in format_matches(page, "a+b", len(page.text))


@pytest.mark.parametrize("args", [{"length": 0}, {"length": 6001}, {"query": "a" * 121}])
def test_search_arguments_rejected_before_network(args):
    reg, hits = make_tool({})
    assert reg.execute("fetch_url", {"url": "https://a.com", **args}).is_error
    assert hits["n"] == 0
