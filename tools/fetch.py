"""fetch_url：读取网页正文。

网页 HTML 里大部分是导航、广告、脚本，直接塞给模型既浪费 token 又干扰判断。
这里的流程是：下载 → 判断类型 → 提取正文（trafilatura，失败时用 BeautifulSoup 兜底）→ 分段返回。

两个对 Agent 很重要的设计：
1. 分段读取：长文章按 chunk_chars 切段，模型用 start 参数"翻页"，而不是一次塞满上下文；
   已下载的页面会缓存，翻页时不重复请求。
2. 内容隔离：正文包在 <web_content> 标签里。网页内容是不可信的数据，可能藏着
   "忽略之前的指令……"之类的提示注入；system prompt 会告诉模型标签里的内容只是资料、不是指令。

阶段 3：备用通道 Jina Reader（https://r.jina.ai/<URL>）
直接下载失败（403 反爬、超时 / 网络不通、需要 JS 渲染、PDF）时，改由 Jina 的服务器去打开网页并转成 Markdown。
代价：URL 会发送给第三方；匿名访问有频率限制。所以只在直接下载失败时才用，并在结果里注明来源通道。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlparse

import re

import httpx
from pydantic import Field

from tools.registry import Tool, tool

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024  # 超过 5MB 的页面不处理
MIN_USEFUL_CHARS = 200  # 提取结果比这还短，多半是提取失败（或需要 JS 渲染的页面）
JINA_ENDPOINT = "https://r.jina.ai/"


@dataclass
class Page:
    url: str
    title: str
    date: str | None
    text: str
    via: str | None = None  # 经由哪个备用通道读取（None 表示直接下载）


class FetchError(Exception):
    """fallback_ok：这种失败换备用通道是否可能成功（URL 写错、文件过大之类就没必要）。"""

    def __init__(self, message: str, fallback_ok: bool = True):
        super().__init__(message)
        self.fallback_ok = fallback_ok


def download(client: httpx.Client, url: str) -> tuple[bytes, str, str]:
    """下载页面，返回 (内容, content-type, 最终 URL)。限制大小，防止下载超大文件。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise FetchError(f"URL 无效：{url!r}（只支持 http/https 链接）", fallback_ok=False)

    with client.stream("GET", url) as resp:
        if resp.status_code >= 400:
            raise FetchError(f"HTTP {resp.status_code}：页面无法访问，请换一个来源")
        ctype = resp.headers.get("content-type", "").lower()
        chunks, size = [], 0
        for chunk in resp.iter_bytes():
            size += len(chunk)
            if size > MAX_DOWNLOAD_BYTES:
                raise FetchError("页面过大（超过 5MB），请换一个来源", fallback_ok=False)
            chunks.append(chunk)
        return b"".join(chunks), ctype, str(resp.url)


def extract(content: bytes, ctype: str, url: str) -> Page:
    """把原始内容变成干净正文。"""
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        raise FetchError("暂不支持直接解析 PDF，请寻找该内容的网页版本")
    if ctype.startswith("text/plain") or "json" in ctype or "markdown" in ctype:
        return Page(url, "", None, content.decode("utf-8", errors="replace"))
    if ctype and "html" not in ctype and "xml" not in ctype:
        raise FetchError(f"不支持的内容类型：{ctype}", fallback_ok=False)

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
    text = text.strip()
    missing = _recover_tables(content, text)
    if missing:
        text += "\n\n## （以下表格由备用解析器补充提取；空白格表示与左侧单元格合并）\n\n" + "\n\n".join(missing)
    return Page(url, title, date, text)


def _extract_with_bs4(content: bytes) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()
    lines = (line.strip() for line in soup.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# 表格补救
#
# 实测发现：trafilatura 会整张丢掉带合并单元格（colspan / rowspan）的表格，
# 而文档站的"模型 & 价格"之类最关键的信息恰恰常用这种表格（例：DeepSeek 定价页）。
# 这里用 BeautifulSoup 找出正文里没出现的表格，按合并单元格展开成普通 Markdown 表格补在末尾。
# ---------------------------------------------------------------------------

MAX_RECOVERED_TABLES = 5


def _cell_text(cell) -> str:
    return " ".join(cell.get_text(" ", strip=True).split()).replace("|", "/")


def table_to_markdown(table) -> str:
    """把 HTML 表格展开成 Markdown，保证每一列对齐。

    - rowspan（纵向合并）：每一行都重复写上该值，这样每行单独看也能读懂（例如每行都标明"价格"）
    - colspan（横向合并）：值写在第一格，其余格留空。不能重复写——否则相邻两列真实的相同值
      （比如两个模型都"支持"）就和合并单元格分不清了
    """
    grid: dict[tuple[int, int], str] = {}
    for r, tr in enumerate(table.find_all("tr")):
        c = 0
        for cell in tr.find_all(["td", "th"]):
            while (r, c) in grid:  # 跳过被上方 rowspan 占住的格子
                c += 1
            text = _cell_text(cell)
            rs = min(_int_attr(cell, "rowspan"), 50)
            cs = min(_int_attr(cell, "colspan"), 20)
            for dr in range(rs):
                for dc in range(cs):
                    grid[(r + dr, c + dc)] = text if dc == 0 else ""
            c += cs
    if not grid:
        return ""
    n_rows = max(r for r, _ in grid) + 1
    n_cols = max(c for _, c in grid) + 1
    rows = ["| " + " | ".join(grid.get((r, c), "") for c in range(n_cols)) + " |" for r in range(n_rows)]
    rows.insert(1, "|" + "---|" * n_cols)
    return "\n".join(rows)


def _int_attr(cell, name: str) -> int:
    try:
        return max(int(cell.get(name, 1) or 1), 1)
    except ValueError:
        return 1


def _recover_tables(content: bytes, text: str) -> list[str]:
    if b"<table" not in content:
        return []
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content, "lxml")
    # 只在已有的 Markdown 表格行里找：单元格文字散落在正文里（例如 bs4 兜底提取的逐行文本）不算"表格已存在"
    table_rows = " ".join(" ".join(line.split()) for line in text.splitlines() if line.lstrip().startswith("|"))
    out = []
    for table in soup.find_all("table"):
        if table.find_parent("table"):  # 嵌套表格交给外层处理
            continue
        cells = [_cell_text(c) for c in table.find_all(["td", "th"])]
        cells = [c for c in cells if len(c) >= 2]
        if len(cells) < 4:  # 太小的多半是排版用表格
            continue
        present = sum(1 for c in cells if c in table_rows)
        if present / len(cells) >= 0.5:  # 正文里已经有这张表
            continue
        md = table_to_markdown(table)
        if md:
            out.append(md)
        if len(out) >= MAX_RECOVERED_TABLES:
            break
    return out


_JINA_FIELD = re.compile(r"^(Title|URL Source|Published Time):\s*(.*)$", re.MULTILINE)


def fetch_via_jina(client: httpx.Client, url: str, api_key: str = "") -> Page:
    """通过 Jina Reader 读取。返回格式：若干 "字段: 值" 行，然后 "Markdown Content:" 后面是正文。"""
    headers = {
        "X-Return-Format": "markdown",
        "X-Retain-Images": "none",  # 去掉图片链接，省 token
        "X-Remove-Selector": "header, nav, footer, aside",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        resp = client.get(JINA_ENDPOINT + url, headers=headers, timeout=httpx.Timeout(45.0, connect=10.0))
    except httpx.TimeoutException as e:
        raise FetchError("Jina 请求超时") from e
    except httpx.HTTPError as e:
        raise FetchError(f"Jina 网络错误：{type(e).__name__}") from e
    if resp.status_code != 200:
        try:
            msg = resp.json().get("readableMessage") or resp.text
        except ValueError:
            msg = resp.text
        raise FetchError(f"Jina 返回 HTTP {resp.status_code}：{msg[:200]}")

    raw = resp.text
    head, sep, body = raw.partition("Markdown Content:")
    if not sep:
        head, body = "", raw
    fields = dict(_JINA_FIELD.findall(head))
    text = body.strip()
    if not text:
        raise FetchError("Jina 也没能提取到正文")
    return Page(
        url=fields.get("URL Source") or url,
        title=fields.get("Title", ""),
        date=fields.get("Published Time") or None,
        text=text,
        via="Jina Reader",
    )


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
    if page.via:
        header.append(f"读取方式: 直接下载失败，经第三方 {page.via} 读取（排版可能含少量导航残留）")
    header.append(f"正文: 第 {start}–{end} 字符，共 {total} 字符")
    footer = (
        f"（还有 {total - end} 字符未读，如需继续阅读请调用 fetch_url(url, start={end})）"
        if end < total
        else "（已读完全文）"
    )
    return "\n".join(header) + f"\n<web_content>\n{page.text[start:end]}\n</web_content>\n" + footer


def make_fetch_url(
    client: httpx.Client | None = None,
    chunk_chars: int = 6000,
    fallback: str = "none",
    jina_api_key: str = "",
    pages: dict[str, Page] | None = None,
) -> Tool:
    """pages：页面缓存，传入 ResearchSession.pages 即可与笔记等工具共享"读过哪些网页"。"""
    http = client or httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        follow_redirects=True,
        timeout=httpx.Timeout(15.0, connect=10.0),
    )
    cache: dict[str, Page] = pages if pages is not None else {}

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
                page = _fetch_direct(url)
            except FetchError as e:
                if fallback != "jina" or not e.fallback_ok:
                    raise
                try:
                    page = fetch_via_jina(http, url, jina_api_key)
                except FetchError as je:
                    raise FetchError(f"{e}；备用通道也失败：{je}。请换一个来源") from je
            cache[url] = page
        return format_chunk(page, start, chunk_chars)

    def _fetch_direct(url: str) -> Page:
        try:
            content, ctype, final_url = download(http, url)
        except httpx.TimeoutException as e:
            raise FetchError("请求超时，请换一个来源") from e
        except httpx.HTTPError as e:
            raise FetchError(f"网络错误：{type(e).__name__}: {e}") from e
        return extract(content, ctype, final_url)

    return fetch_url
