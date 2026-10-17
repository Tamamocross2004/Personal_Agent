"""web_search：联网搜索。

搜索服务做成可替换的"后端"（SearchBackend）：Agent 只认识 web_search 这个工具，
背后用 DuckDuckGo、博查还是 Tavily，换一个后端类即可，Agent 和 prompt 都不用改。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Protocol

from pydantic import Field

from tools.registry import Tool, tool


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchBackend(Protocol):
    name: str

    def search(self, query: str, max_results: int) -> list[SearchResult]: ...


class DDGSBackend:
    """基于 ddgs 库（原 duckduckgo_search）。免费、无需 Key；它会聚合多个搜索引擎的结果。"""

    name = "ddgs"

    def __init__(self, region: str = "wt-wt", timeout: int = 15):
        # region: wt-wt 不限地区；cn-zh 偏向中文结果
        self.region = region
        self.timeout = timeout

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        from ddgs import DDGS  # 延迟导入：没装 ddgs 时其它功能（和测试）仍可用

        raw = DDGS(timeout=self.timeout).text(query, region=self.region, max_results=max_results)
        return [
            SearchResult(
                title=(r.get("title") or "").strip(),
                url=(r.get("href") or r.get("url") or "").strip(),
                snippet=(r.get("body") or "").strip(),
            )
            for r in raw
            if r.get("href") or r.get("url")
        ]


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
