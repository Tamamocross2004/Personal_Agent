"""一次研究任务内，各工具共享的状态。

fetch_url 读过哪些网页、记了哪些笔记 —— 笔记工具要校验"来源是否真的读过"，
上下文压缩要知道"哪些网页已经记过笔记"，所以这些状态放在一个对象里，由 default_registry 创建并注入各工具。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

if TYPE_CHECKING:
    from tools.fetch import Page
    from agent.task_contract import TaskContract


def normalize_url(url: str) -> str:
    """用于比较的规范形式：协议和域名小写、去掉 #片段、去掉末尾斜杠、去掉 www.。"""
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = p.path.rstrip("/")
    return urlunsplit((p.scheme.lower(), host, path, p.query, ""))


@dataclass
class Note:
    id: int
    content: str
    source_url: str
    source_title: str
    topic: str = ""


@dataclass
class ResearchSession:
    pages: dict[str, Page] = field(default_factory=dict)  # 请求的 URL → 页面（即 fetch_url 的缓存）
    notes: list[Note] = field(default_factory=list)
    task_contract: TaskContract | None = None
    evidence: object | None = None  # Agent创建的本次证据账本，不从历史文件恢复。
    failed_fetches: dict[str, str] = field(default_factory=dict)  # 已实际返回FetchError的操作目标，非引用来源
    tool_activity: dict[str, dict[str, int]] = field(default_factory=dict)

    def document_characters(self) -> int:
        """已送达且保留的同版本原文区间并集；重复和重叠不算新增。"""
        groups={}
        for record in self.evidence.records.values() if self.evidence else []:
            if record['kind']!='document': continue
            key=(record.get('document_id'),record.get('file_sha256'),record.get('generation'),record.get('page'))
            groups.setdefault(key,[]).append((record['start'],record['end']))
        total=0
        for intervals in groups.values():
            end=0
            for left,right in sorted(intervals):
                total+=max(0,right-max(end,left)); end=max(end,right)
        return total

    def activity_summary(self, *, snapshot_call_id: str | None = None) -> dict:
        records=list(self.evidence.records.values()) if self.evidence else []
        docs=[r for r in records if r['kind']=='document']
        return {'snapshot':{'kind':'tool_return' if snapshot_call_id else 'current_review_state',
                    'tool_call_id':snapshot_call_id,
                    'cutoff':'after_tool_return_before_own_execution_record' if snapshot_call_id else 'at_summary_creation',
                    'last_retained_evidence_id':records[-1]['id'] if records else None},
                'tool_requests':{name:dict(counts) for name,counts in list(self.tool_activity.items())[:24]},
                'omitted_tools':max(0,len(self.tool_activity)-24),
                'retained_evidence':{'records':len(records),'document_records':len(docs),
                    'document_versions':len({(r.get('document_id'),r.get('file_sha256'),r.get('generation')) for r in docs}),
                    'ledger_omitted_records':self.evidence.omitted if self.evidence else 0},
                'limits':'截至此快照的本任务工具请求与返回；returned_error也可能是参数校验或重复拦截，并非工具函数实际执行失败。'
                         'requested未被returned_ok/returned_error/uncertain覆盖的部分尚未返回；不统计服务商内部重试。'
                         '文档仅计已保留送达片段，不是全库清单、完整阅读或无遗漏证明；不证明生产业务状态。'}

    def find_page(self, url: str) -> Page | None:
        """按规范化 URL 查找读过的页面（同时匹配请求地址和重定向后的最终地址）。"""
        key = normalize_url(url)
        for requested, page in self.pages.items():
            if key in (normalize_url(requested), normalize_url(page.url)):
                return page
        return None

    def noted_urls(self) -> set[str]:
        return {normalize_url(n.source_url) for n in self.notes}

    def has_note_for(self, url: str) -> bool:
        page = self.find_page(url)
        keys = {normalize_url(url)} | ({normalize_url(page.url)} if page else set())
        return bool(keys & self.noted_urls())
