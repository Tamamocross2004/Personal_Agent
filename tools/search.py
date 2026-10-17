"""web_search：联网搜索。

搜索服务做成可替换的"后端"（SearchBackend）：Agent 只认识 web_search 这个工具，
背后用 DuckDuckGo、博查还是 Tavily，换一个后端类即可，Agent 和 prompt 都不用改。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Annotated, Any, Callable, Protocol

from pydantic import Field

from tools.registry import Tool, tool

_CJK = re.compile(r"[一-鿿]")


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchBackend(Protocol):
    name: str

    def search(self, query: str, max_results: int) -> list[SearchResult]: ...


# ddgs 的 backend="auto" 总是把 wikipedia、grokipedia 排在最前面并占用并发名额。
# 它们返回的是百科词条而不是网页，而且 wikipedia 在国内无法访问（TLS 握手失败），
# 实测会让中文搜索大面积失败，所以显式指定只用网页搜索引擎。
DDGS_WEB_ENGINES = "duckduckgo,brave,google,startpage,yahoo,mojeek"


class DDGSBackend:
    """基于 ddgs 库（原 duckduckgo_search）。免费、无需 Key；它会聚合多个搜索引擎的结果。

    免费服务不稳定（限流、个别引擎被墙），所以这里做了两件事：
    1. 失败自动重试，并在重试时轮换地区参数：中文查询优先 cn-zh，其它优先 wt-wt（不限地区）；
    2. ddgs 失败时报出的往往是"最后一个出错的引擎"的错误，容易误导，这里换成统一说明。
    """

    name = "ddgs"

    def __init__(
        self,
        engines: str = DDGS_WEB_ENGINES,
        timeout: int = 15,
        attempts: int = 3,
        retry_delay: float = 1.0,
        text_fn: Callable[..., list[dict[str, Any]]] | None = None,  # 测试注入用
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.engines = engines
        self.timeout = timeout
        self.attempts = attempts
        self.retry_delay = retry_delay
        self._text_fn = text_fn
        self._sleep = sleep

    def _text(self, query: str, region: str, max_results: int) -> list[dict[str, Any]]:
        if self._text_fn:
            return self._text_fn(query, region=region, max_results=max_results, backend=self.engines)
        from ddgs import DDGS  # 延迟导入：没装 ddgs 时其它功能（和测试）仍可用

        return DDGS(timeout=self.timeout).text(
            query, region=region, max_results=max_results, backend=self.engines
        )

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        regions = ["cn-zh", "wt-wt"] if _CJK.search(query) else ["wt-wt", "cn-zh"]
        last_err: Exception | None = None
        for i in range(self.attempts):
            if i:
                self._sleep(self.retry_delay * i)
            try:
                raw = self._text(query, regions[i % len(regions)], max_results)
            except Exception as e:  # noqa: BLE001 —— "No results found" 也是以异常形式抛出
                last_err = e
                continue
            results = [
                SearchResult(
                    title=(r.get("title") or "").strip(),
                    url=(r.get("href") or r.get("url") or "").strip(),
                    snippet=(r.get("body") or "").strip(),
                )
                for r in raw
                if r.get("href") or r.get("url")
            ]
            if results:
                return results
        raise RuntimeError(
            f"尝试 {self.attempts} 次均未得到结果（ddgs 免费服务不稳定，可能被限流或部分引擎不可访问）。"
            f"最后一次错误：{type(last_err).__name__ if last_err else '无结果'}"
        )


def format_results(query: str, results: list[SearchResult]) -> str:
    if not results:
        return f"搜索「{query}」没有找到结果。可以换个关键词，或改用英文/更通用的说法再试。"
    lines = [f"搜索「{query}」的结果（共 {len(results)} 条）：", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r.title}")
        lines.append(f"    URL: {r.url}")
        if r.snippet:
            lines.append(f"    摘要: {r.snippet}")
    lines.append("")
    lines.append("提示：摘要只是片段，可能过时或断章取义；重要信息请用 fetch_url 阅读原文后再引用。")
    return "\n".join(lines)


def make_web_search(backend: SearchBackend) -> Tool:
    @tool(name="web_search")
    def web_search(
        query: Annotated[
            str, Field(description="搜索关键词。用简洁的关键词而非完整句子；中英文各搜一次通常效果更好")
        ],
        max_results: Annotated[int, Field(ge=1, le=10, description="返回结果数，1-10")] = 5,
    ) -> str:
        """联网搜索，返回若干条结果的标题、URL 和摘要。用于查找资料来源；需要细节时再用 fetch_url 阅读具体网页。"""
        q = query.strip()
        if not q:
            raise ValueError("query 不能为空")
        try:
            results = backend.search(q, max_results)
        except Exception as e:  # noqa: BLE001 —— 统一包装成对模型友好的提示
            raise RuntimeError(
                f"搜索服务（{backend.name}）出错：{type(e).__name__}: {e}。可以稍后重试或换个关键词。"
            ) from e
        return format_results(q, results)

    return web_search
