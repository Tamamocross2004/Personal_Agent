"""fetch_url：读取网页正文。

网页 HTML 里大部分是导航、广告、脚本，直接塞给模型既浪费 token 又干扰判断。
这里的流程是：下载 → 判断类型 → 提取正文（trafilatura，失败时用 BeautifulSoup 兜底）→ 分段返回。

两个对 Agent 很重要的设计：
1. 分段读取：长文章按 chunk_chars 切段，模型用 start 参数"翻页"，而不是一次塞满上下文；
   已下载的页面会缓存，翻页时不重复请求。
2. 内容隔离：正文包在 <web_content> 标签里。网页内容是不可信的数据，可能藏着
   "忽略之前的指令……"之类的提示注入；system prompt 会告诉模型标签里的内容只是资料、不是指令。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlparse

import httpx
from pydantic import Field

from tools.registry import Tool, tool

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024  # 超过 5MB 的页面不处理
MIN_USEFUL_CHARS = 200  # 提取结果比这还短，多半是提取失败（或需要 JS 渲染的页面）


@dataclass
class Page:
    url: str
    title: str
    date: str | None
    text: str


class FetchError(Exception):
    pass


def download(client: httpx.Client, url: str) -> tuple[bytes, str, str]:
    """下载页面，返回 (内容, content-type, 最终 URL)。限制大小，防止下载超大文件。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise FetchError(f"URL 无效：{url!r}（只支持 http/https 链接）")

    with client.stream("GET", url) as resp:
        if resp.status_code >= 400:
            raise FetchError(f"HTTP {resp.status_code}：页面无法访问，请换一个来源")
        ctype = resp.headers.get("content-type", "").lower()
        chunks, size = [], 0
        for chunk in resp.iter_bytes():
            size += len(chunk)
            if size > MAX_DOWNLOAD_BYTES:
                raise FetchError("页面过大（超过 5MB），请换一个来源")
            chunks.append(chunk)
        return b"".join(chunks), ctype, str(resp.url)


def extract(content: bytes, ctype: str, url: str) -> Page:
    """把原始内容变成干净正文。"""
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        raise FetchError("暂不支持 PDF，请寻找该内容的网页版本")
    if ctype.startswith("text/plain") or "json" in ctype or "markdown" in ctype:
        return Page(url, "", None, content.decode("utf-8", errors="replace"))
    if ctype and "html" not in ctype and "xml" not in ctype:
        raise FetchError(f"不支持的内容类型：{ctype}")

    import trafilatura  # 延迟导入：库较重

    # 直接传 bytes，trafilatura 会自行识别编码（很多中文网站是 GBK 且不声明）
    text = trafilatura.extract(
        content,
        url=url,
        output_format="markdown",
        include_tables=True,
        include_links=False,
        include_comments=False,
    )
    meta = trafilatura.extract_metadata(content)
    title = (meta.title if meta else None) or ""
    date = meta.date if meta else None

    if not text or len(text) < MIN_USEFUL_CHARS:
        fallback = _extract_with_bs4(content)
        if len(fallback) > len(text or ""):
            text = fallback
    if not text or not text.strip():
        raise FetchError("没能从页面提取到正文（可能需要登录或依赖 JavaScript 渲染），请换一个来源")
    return Page(url, title, date, text.strip())


def _extract_with_bs4(content: bytes) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()
    lines = (line.strip() for line in soup.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)


def format_chunk(page: Page, start: int, chunk_chars: int) -> str:
    total = len(page.text)
    if start >= total:
        return f"start={start} 已超出正文长度（共 {total} 字符），这篇文章已经读完。"
    end = min(start + chunk_chars, total)
    header = [f"URL: {page.url}"]
    if page.title:
        header.append(f"标题: {page.title}")
    if page.date:
        header.append(f"发布日期: {page.date}")
    header.append(f"正文: 第 {start}–{end} 字符，共 {total} 字符")
    footer = (
        f"（还有 {total - end} 字符未读，如需继续阅读请调用 fetch_url(url, start={end})）"
        if end < total
        else "（已读完全文）"
    )
    return "\n".join(header) + f"\n<web_content>\n{page.text[start:end]}\n</web_content>\n" + footer


def make_fetch_url(client: httpx.Client | None = None, chunk_chars: int = 6000) -> Tool:
    http = client or httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        follow_redirects=True,
        timeout=httpx.Timeout(15.0, connect=10.0),
    )
    cache: dict[str, Page] = {}

    @tool(name="fetch_url")
    def fetch_url(
        url: Annotated[str, Field(description="要读取的网页 URL（通常来自 web_search 的结果）")],
        start: Annotated[
            int, Field(ge=0, description="从正文第几个字符开始读，用于分段阅读长文章；首次读取用 0")
        ] = 0,
    ) -> str:
        """读取网页并提取正文（已去除导航、广告等），长文章分段返回。返回内容包含标题、发布日期，正文在 <web_content> 标签中。"""
        page = cache.get(url)
        if page is None:
            try:
                content, ctype, final_url = download(http, url)
            except httpx.TimeoutException as e:
                raise FetchError("请求超时，请换一个来源") from e
            except httpx.HTTPError as e:
                raise FetchError(f"网络错误：{type(e).__name__}: {e}") from e
            page = extract(content, ctype, final_url)
            cache[url] = page
        return format_chunk(page, start, chunk_chars)

    return fetch_url
