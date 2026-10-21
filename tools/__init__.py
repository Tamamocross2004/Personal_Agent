"""Agent 可调用的工具。"""

from __future__ import annotations

from pathlib import Path

import httpx

from tools.basic import calculator, days_between, get_current_time
from tools.code_exec import make_run_python
from tools.evidence import make_read_evidence
from tools.fetch import make_fetch_url
from tools.registry import Tool, ToolRegistry, ToolResult, tool
from tools.notes import make_notes_tools
from tools.plan import make_update_plan
from tools.report import make_write_report
from tools.search import DDGSBackend, SearchBackend, make_web_search
from tools.session import ResearchSession
from tools.knowledge import make_search_knowledge
from agent.task_contract import TaskContract, extract_contract


def default_registry(
    task_dir: Path,
    search_backend: SearchBackend | None = None,
    http_client: httpx.Client | None = None,
    fetch_fallback: str = "none",
    jina_api_key: str = "",
    session: ResearchSession | None = None,
    on_plan_update: callable = None,
    code_exec_timeout: float = 30.0,
    question: str = "",
    contract: TaskContract | None = None,
    knowledge_settings=None,
) -> ToolRegistry:
    """为一次研究任务组装全部工具。

    search / fetch / report 依赖外部资源（搜索后端、HTTP 客户端、任务目录），
    所以用工厂函数创建，而不是写死成全局函数 —— 测试时可以注入假的后端。
    session：本任务内工具共享的状态（读过的网页、笔记）。调用方传入后可以在外部读取，例如上下文压缩时。
    """
    session = session if session is not None else ResearchSession()
    # 只依据当前用户明确约束；不从历史背景推断权限。非联网研究不需要伪造网页笔记。
    contract = contract or session.task_contract or extract_contract(question)
    session.task_contract = contract
    offline = contract.network_allowed is False
    notes = make_notes_tools(task_dir, session)
    network_tools = [] if offline else [
        make_web_search(search_backend or DDGSBackend()),
        make_fetch_url(http_client, fallback=fetch_fallback, jina_api_key=jina_api_key, pages=session.pages),
    ]
    return ToolRegistry(
        [
            *network_tools,
            *[t for t in notes if not offline or t.name != "save_note"],
            make_update_plan(task_dir, on_update=on_plan_update),
            make_write_report(task_dir, session, contract=contract),
            make_run_python(task_dir, timeout=code_exec_timeout, contract=contract, session=session),
            make_read_evidence(session),
            calculator,
            days_between,
            get_current_time,
            *([make_search_knowledge(
                knowledge_settings,task_dir,session)] if knowledge_settings is not None else []),
        ]
    )


__all__ = ["Tool", "ToolRegistry", "ToolResult", "ResearchSession", "tool", "default_registry"]
