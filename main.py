"""命令行入口。

用法：
    python main.py "对比一下 DeepSeek 和通义千问的 Agent 能力"   # 研究模式：单个问题
    python main.py                                     # 研究模式：交互式，每个问题独立运行
    python main.py --chat                              # 阶段 0 的纯对话模式（无工具）
    python main.py --provider qwen "..."               # 临时切换厂商
    python main.py --max-steps 40 --budget 400000 "..."

每个问题的产物保存在 workspace/<时间>-<问题>/ 目录下：
    *.md           研究报告
    notes.md       研究笔记
    trace.jsonl    运行日志（python -m agent.replay <目录> 回放）
    messages.json  完整对话历史
"""

from __future__ import annotations

import argparse
import logging
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable

import openai
from rich.logging import RichHandler
from rich.markdown import Markdown
from rich.panel import Panel

from agent.config import PROVIDERS, Settings, load_llm_config
from agent.context import ContextCompactor
from agent.display import console, show_event, usage_table
from agent.llm import LLMClient
from agent.loop import FINALIZE_REASONS, Agent, AgentEvent, AgentResult, TokenWeights
from agent.memory import MemoryStore, format_context, save_research
from agent.prompts import CHAT_SYSTEM_PROMPT, research_system_prompt
from agent.quality import inspect_report, save_check
from agent.review import save_review
from agent.report_state import PROVENANCE, fallback_document
from agent.task_contract import extract_contract, save_contract_check
from agent.trace import MESSAGES_FILE, TRACE_FILE, TraceWriter, fanout, save_messages
from agent.workspace import new_task_dir
from tools import ResearchSession, default_registry
from tools.notes import NOTES_FILE
from agent.checkpoint import CheckpointStore, CheckpointError

FAILED_STOPS = ("context_overflow", "content_filter", "error", "needs_verification")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="个人研究助理 Agent")
    p.add_argument("question", nargs="?", help="要提问的问题；省略则进入交互模式")
    p.add_argument("--chat", action="store_true", help="纯对话模式（不使用工具，阶段 0 行为）")
    p.add_argument("--no-memory", action="store_true", help="本次不读取或写入长期记忆")
    p.add_argument("--knowledge", action="store_true", help="启用本地原始文档知识库检索")
    p.add_argument("--provider", choices=list(PROVIDERS), help="覆盖 .env 中的 LLM_PROVIDER")
    p.add_argument("--model", help="覆盖模型名")
    p.add_argument("--max-steps", type=int, help="最大循环步数（默认取 .env 的 AGENT_MAX_STEPS，30）")
    p.add_argument("--budget", type=int, help="等效 token 预算（默认取 .env 的 AGENT_TOKEN_BUDGET，300000）")
    p.add_argument("-v", "--verbose", action="store_true", help="打印调试日志（含重试信息）")
    return p.parse_args(argv)


def read_input() -> str | None:
    """读取一行输入；遇到 exit/quit/Ctrl+C/EOF 返回 None。"""
    while True:
        try:
            q = console.input("[bold green]你> [/]").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not q:
            continue
        if q.lower() in {"exit", "quit"}:
            return None
        return q


# ---------------------------------------------------------------------------
# 研究模式
# ---------------------------------------------------------------------------


def research(
    llm: LLMClient, settings: Settings, question: str, max_steps: int, budget: int, *,
    task_dir: Path | None = None,
    historical_context: str | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> AgentResult:
    task_dir=Path(task_dir or new_task_dir(question))
    checkpoint=CheckpointStore(task_dir)
    _check_new_task(task_dir,checkpoint)
    with checkpoint.lease():
        _check_new_task(task_dir,checkpoint)
        return _research(llm,settings,question,max_steps,budget,task_dir=task_dir,
                         historical_context=historical_context,
                         should_stop=lambda:bool(should_stop and should_stop()) or (task_dir/'.desktop-stop').exists(),
                         checkpoint=checkpoint)


def _check_new_task(task_dir,checkpoint):
    if checkpoint.path.exists():
        raise CheckpointError('任务已有检查点；使用恢复入口，不能覆盖旧任务或重置预算')
    if any((task_dir/name).exists() for name in ('trace.jsonl','messages.json','evidence.json')):
        raise CheckpointError('任务已有执行产物且无检查点，不能覆盖旧任务；请新建任务目录')


def resume_research(task_dir:Path, *, should_stop=None, llm=None, clear_stop_marker=True) -> AgentResult:
    checkpoint=CheckpointStore(task_dir)
    checkpoint.read()  # 无检查点的旧任务不创建锁文件或目录，不改变原始产物。
    with checkpoint.lease():
        data=checkpoint.read()
        checkpoint.validate(data)
        if clear_stop_marker:
            with checkpoint.lease(control=True):
                (checkpoint.root/'.desktop-stop').unlink(missing_ok=True)
        settings=Settings(**data['research_settings'])
        if llm is None:
            identity=data['identity']['model']
            llm=LLMClient(load_llm_config(provider=identity['provider'],model=identity['model'],settings=settings))
        options=data['identity']['options']
        return _research(llm,settings,data['question'],options['max_steps'],options['token_budget'],
                         task_dir=Path(task_dir),
                         should_stop=lambda:bool(should_stop and should_stop()) or (checkpoint.root/'.desktop-stop').exists(),
                         checkpoint=checkpoint,resume_state=data)


def _research(
    llm,settings,question,max_steps,budget,*,task_dir,historical_context=None,should_stop=None,
    checkpoint=None,resume_state=None,
) -> AgentResult:
    """运行一次研究任务：独立的工作目录、工具实例和运行日志。"""
    task_dir = task_dir or new_task_dir(question)
    tracer = TraceWriter(task_dir / TRACE_FILE)
    contract = extract_contract(question)
    session = ResearchSession(task_contract=contract)  # 本次请求是唯一约定来源
    memory_store = MemoryStore(settings.memory_db_path) if settings.memory_enabled else None
    memory_context = resume_state['memory_context'] if resume_state else historical_context or ""
    if historical_context and not resume_state:
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "memory_context.json").write_text(json.dumps({"query":question,
            "record_ids":[],"source":"desktop_session","context":historical_context}, ensure_ascii=False,indent=2),encoding="utf-8")
    if memory_store and historical_context is None and not resume_state:
        try:
            records = memory_store.search(question, limit=settings.memory_recall_limit, followup=True)
            memory_context = format_context(records, settings.memory_context_max_chars)
            if records:
                task_dir.mkdir(parents=True, exist_ok=True)
                (task_dir / "memory_context.json").write_text(
                    json.dumps({"query": question, "record_ids": [r["id"] for r in records], "context": memory_context},
                               ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, sqlite3.Error, ValueError) as exc:
            console.print(f"[yellow]历史记忆不可用（{type(exc).__name__}），本次继续研究。[/]")
    compactor = (
        ContextCompactor(
            threshold=settings.context_compact_threshold,
            keep_recent_turns=settings.context_keep_recent_turns,
            has_note=session.has_note_for,
        )
        if settings.context_compact_threshold > 0
        else None
    )
    agent = Agent(
        llm,
        default_registry(
            task_dir,
            fetch_fallback=settings.fetch_fallback,
            jina_api_key=settings.jina_api_key,
            session=session,
            question=question,
            contract=contract,
            code_exec_timeout=min(settings.code_exec_timeout, settings.tool_timeout * 0.8),
            knowledge_settings=settings if settings.knowledge_enabled else None,
        ),
        system_prompt=research_system_prompt(date.today().isoformat(), max_steps),
        max_steps=max_steps,
        token_budget=budget,
        token_weights=TokenWeights(cached=settings.token_weight_cached, output=settings.token_weight_output),
        tool_timeout=settings.tool_timeout,
        compactor=compactor,
        on_event=fanout(show_event, tracer),
        task_dir=task_dir,
        review_reports=settings.report_review_enabled,
        review_max_calls=settings.report_review_max_calls,
        review_max_tokens=settings.report_review_max_tokens,
        research_session=session,
        memory_context=memory_context,
        task_contract=contract,
        should_stop=should_stop,
        checkpoint=checkpoint,
    )
    agent.research_settings={k:v for k,v in settings.model_dump(mode='json').items() if not k.endswith('_api_key')}
    try:
        result = agent.run(question,resume_state=resume_state) if resume_state else agent.run(question)
    finally:
        tracer.close()
    save_messages(task_dir / MESSAGES_FILE, result.messages)
    if result.stop_reason in ('interrupted','needs_verification'):
        console.print(f'[yellow]任务已停止；恢复条件：python -m agent.resume status "{task_dir}"[/]')
        return result

    reports = sorted(p for p in task_dir.glob("*.md") if p.name != NOTES_FILE)
    # 被强制收尾且没写过报告时，收尾回答本身就是（不完整的）报告，替用户存下来
    if not reports and result.stop_reason in FINALIZE_REASONS and result.answer.strip():
        agent._save_checkpoint('postprocess',pending={'kind':'fallback','reason':'兜底报告多文件保存中，需核对未提交写入'},
                               result=checkpoint.read()['result'] if checkpoint and checkpoint.path.exists() else None)
        fallback = task_dir / "report.md"
        document, provenance = fallback_document(result.answer, result.stop_reason)
        # 原文原样保存；字节来源摘要与报告检查使用的换行规范化文本分别记录。
        with fallback.open('w', encoding='utf-8', newline='') as stream:
            stream.write(document)
        checked_text = fallback.read_text(encoding='utf-8')
        save_check(fallback, inspect_report(checked_text, task_dir, session))
        save_contract_check(fallback, checked_text, contract)
        save_review(fallback, checked_text, "not_reviewed", [], reason="程序自动保存停止前的模型原文，保存时未经过报告审稿")
        from agent.evidence import write_json
        if session.evidence:
            write_json(fallback.with_suffix('.evidence.json'),session.evidence.prepare(fallback,checked_text,None,complete=False))
        write_json(task_dir / PROVENANCE, provenance)
        notice = AgentEvent('notice', {'text': '程序已自动保存未完成回答为report.md；正文保留停止前的模型原文，保存时未经过报告审稿。',
                            'action': 'automatic_fallback_saved', 'filename': 'report.md', 'provenance': provenance})
        try:
            tracer(notice)
        finally:
            tracer.close()
        show_event(notice)
        reports = [fallback]
        agent._save_checkpoint('postprocess',result={k:v for k,v in vars(result).items() if k!='messages'})

    for r in reports:
        console.print(f"[bold green]报告：[/]{r}")
    if not reports:
        console.print("[dim]本次没有生成报告文件。[/]")
    if session.notes:
        console.print(f"[dim]笔记：{task_dir / NOTES_FILE}（{len(session.notes)} 条）[/]")
    if memory_store:
        try:
            memory_id = save_research(memory_store, task_dir, question, result, session, reports)
            if memory_id is not None:
                console.print(f"[dim]已保存研究记忆 #{memory_id}（{memory_store.path}）。[/]")
        except (OSError, sqlite3.Error, ValueError) as exc:
            console.print(f"[yellow]记忆保存失败（{type(exc).__name__}），报告和日志已保留。[/]")
    console.print(f'[dim]运行日志：{task_dir}  （回放：python -m agent.replay "{task_dir}"）[/]')
    if result.error and "AuthenticationError" in result.error:
        console.print("[red]鉴权失败：请检查 API Key 是否正确、是否与所选厂商匹配。[/]")
    if result.stop_reason == "context_overflow":
        console.print("[yellow]提示：对话历史超出了模型上下文上限。可以减少 --max-steps，阶段 4 会从根本上解决。[/]")
    if checkpoint and checkpoint.path.exists():
        data=checkpoint.read()
        # 有未确认的模型请求时不将检查点伪装为完成。
        if not data.get('pending'):
            agent._save_checkpoint('postprocess',status='complete',result={k:v for k,v in vars(result).items() if k!='messages'})
    return result


def run_research(llm: LLMClient, settings: Settings, question: str | None, max_steps: int, budget: int) -> int:
    if question:
        result = research(llm, settings, question, max_steps, budget)
        if result.stop_reason == "interrupted":
            return 130  # 约定俗成：被 Ctrl+C 中断的进程退出码
        return 1 if result.stop_reason in FAILED_STOPS else 0
    memory_mode = "历史记忆已启用" if settings.memory_enabled else "历史记忆已关闭"
    console.print(f"[dim]研究模式：每个问题独立运行，{memory_mode}。运行中按 Ctrl+C 中断当前任务，输入 exit 退出。[/]")
    while (q := read_input()) is not None:
        research(llm, settings, q, max_steps, budget)
    return 0


# ---------------------------------------------------------------------------
# 纯对话模式（阶段 0）
# ---------------------------------------------------------------------------


def run_chat(llm: LLMClient, question: str | None) -> int:
    """演示 messages 列表如何承载上下文。"""
    messages: list[dict[str, Any]] = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]

    def answer(q: str) -> None:
        messages.append({"role": "user", "content": q})
        with console.status("思考中…"):
            resp = llm.chat(messages)
        text = resp.choices[0].message.content or ""
        console.print(Panel(Markdown(text), title=f"[bold]{llm.config.model}[/]", border_style="cyan"))
        messages.append({"role": "assistant", "content": text})

    if question:
        answer(question)
        return 0
    console.print("[dim]纯对话模式（无工具），输入 exit 退出。[/]")
    while (q := read_input()) is not None:
        try:
            answer(q)
        except openai.AuthenticationError:
            raise
        except openai.APIError as e:
            messages.pop()  # 失败的这轮不计入历史
            console.print(f"[red]调用失败：{e}[/]")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING,  # 第三方库只显示警告以上，避免刷屏
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False)],
    )
    if args.verbose:
        logging.getLogger("agent").setLevel(logging.DEBUG)

    try:
        settings = Settings()
        if args.knowledge:
            settings = settings.model_copy(update={'knowledge_enabled':True})
        if args.no_memory:
            settings = settings.model_copy(update={"memory_enabled": False})
        cfg = load_llm_config(provider=args.provider, model=args.model, settings=settings)
    except ValueError as e:
        console.print(f"[red]配置错误：{e}[/]")
        return 2

    llm = LLMClient(cfg)
    max_steps = args.max_steps or settings.agent_max_steps
    budget = args.budget or settings.agent_token_budget
    mode = "纯对话" if args.chat else "研究 Agent"
    console.print(f"[dim]厂商：{cfg.provider} | 模型：{cfg.model} | 模式：{mode}[/]")

    code = 0
    try:
        if args.chat:
            code = run_chat(llm, args.question)
        else:
            code = run_research(llm, settings, args.question, max_steps, budget)
    except openai.AuthenticationError:
        console.print("[red]鉴权失败：请检查 API Key 是否正确、是否与所选厂商匹配。[/]")
        code = 1
    except openai.APIError as e:
        console.print(f"[red]调用失败：{type(e).__name__}: {e}[/]")
        code = 1
    except KeyboardInterrupt:
        console.print("[yellow]已退出。[/]")
        code = 130
    finally:
        u = llm.usage
        if u.calls:
            console.print(
                usage_table(
                    [(u.calls, u.prompt_tokens, u.completion_tokens, u.total_tokens, f"{u.total_seconds:.2f}")],
                    title="本次会话 Token 用量",
                )
            )
    return code


if __name__ == "__main__":
    sys.exit(main())
