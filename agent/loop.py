"""Agent 主循环 —— 整个项目的心脏。

    while 没超过步数上限:
        把 [对话历史 + 工具说明书] 发给模型
        if 模型没有要求调用工具:
            它给出的就是最终回答 → 结束
        else:
            逐个执行它要求的工具，把结果作为 role="tool" 消息追加到历史 → 继续循环

阶段 1 的核心细节：
- 模型的回复（含 tool_calls）必须原样追加进历史，紧接着是每个调用对应的 tool 消息；
  tool 消息用 tool_call_id 与调用一一对应，缺一条接口就会报 400。
- 模型可能在一轮里同时要求调用多个工具（parallel tool calls），要逐个执行、逐条回传。
- 工具出错不中断循环：错误信息交给模型，由它决定如何补救。

阶段 3 加上的保护（每一项都对应 tests/test_loop_robustness.py 里的故障测试）：
- 步数上限 / token 预算 / 重复调用检测 → 触发后"强制收尾"（阶段 5 改为收尾阶段：只允许读笔记、写报告、更新计划，
  最多再调用几次模型；请求格式与平时完全相同，以保住前缀缓存）
- token 用到 80% 时提醒模型尽快收尾；剩余 6 步时也提醒一次（阶段 5）
- 单个工具执行超时
- 回复被截断（finish_reason=length）：工具参数不完整就不执行并告知原因；文字回答则让模型接着写
- 空回复：提醒一次
- 内容审核拦截：移除最近一批工具结果（多半是某个网页触发的）后重试一次
- 上下文超长、Key 错误等无法恢复的错误：干净地结束并给出原因
- Ctrl+C：立即停止，但已发生的一切都通过事件写进了日志

阶段 4：上下文管理（agent/context.py）
- 每轮调用前估算上下文大小，超过阈值时把旧的网页原文 / 搜索摘要替换成占位说明
- 被压缩的网页允许再次 fetch_url（否则会被重复调用检测拦下）

无论以哪种方式结束，run() 都会返回 AgentResult，并发出 run_end 事件，不会把异常抛给调用方。
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

import openai
from openai.types.chat import ChatCompletion, ChatCompletionMessage

from agent.context import ContextCompactor, estimate_messages, estimate_tools
from agent.errors import classify_api_error
from agent.planner import format_plan_for_prompt, generate_plan, load_plan, unfinished_tasks
from agent.quality import inspect_report, save_check, structure_repair_feedback
from agent.evidence import EvidenceLedger, mapping_sha, write_json
from agent.review import ReviewValidationError, build_review_messages, fingerprint, parse_verdict, review_feedback, save_review
from agent.task_contract import TaskContract, extract_contract, save_contract_check
from agent.prompts import (
    CONTINUE_PROMPT,
    EMPTY_REPLY_PROMPT,
    REDACTED_TOOL_RESULT,
    REPEAT_BLOCKED_RESULT,
    TRUNCATED_TOOL_CALL_RESULT,
    WRAPUP_LAST_CALL_PROMPT,
    WRAPUP_TOOLS,
    budget_warning_prompt,
    finalize_prompt,
    research_system_prompt,
    steps_warning_prompt,
    wrapup_refused_result,
)
from tools.registry import ToolRegistry, ToolResult

StopReason = Literal[
    "final_answer",  # 正常完成
    "max_steps",  # 步数用完，强制收尾
    "budget",  # token 预算用完，强制收尾
    "loop_detected",  # 反复重复调用，强制收尾
    "interrupted",  # 用户 Ctrl+C
    "context_overflow",  # 上下文超出模型上限
    "content_filter",  # 被内容审核拦截且无法恢复
    "error",  # 其它无法恢复的错误（Key 错误、模型名错误……）
]

# 这几种情况会做"强制收尾"调用，因此 answer 里通常仍有内容
FINALIZE_REASONS = ("max_steps", "budget", "loop_detected")


@dataclass(frozen=True)
class TokenWeights:
    """把 token 折算成"等效 token"的权重：预算按它计算，才能大致反映花费。

    原始 token 总数反映不了花费：有前缀缓存时，大部分输入是命中缓存的部分，价格只有未命中的几十分之一
    （阶段 4 实测 85% 命中，按原始 token 计的预算第 19 步就用完了）。
    默认值比 DeepSeek 真实价格比例（命中:未命中:输出 ≈ 0.02 : 1 : 4）保守：换到别的厂商时不至于严重低估花费。
    """

    uncached: float = 1.0
    cached: float = 0.1
    output: float = 2.0

    def equivalent(self, prompt: int, cached: int, completion: int) -> float:
        return (prompt - cached) * self.uncached + cached * self.cached + completion * self.output


class ChatModel(Protocol):
    """Agent 只依赖这一个方法 —— LLMClient 满足它，测试里的假模型也满足它。"""

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, **kwargs: Any
    ) -> ChatCompletion: ...


@dataclass
class AgentEvent:
    """循环中发生的事件，交给 on_event 回调（终端展示、写 trace 日志都基于它）。

    kind:
      run_start    任务开始         data: question, max_steps, token_budget, token_weights, tools
      step         新一轮开始       data: step
      llm_call     一次模型调用完成 data: step, prompt_tokens, cached_tokens, completion_tokens, equivalent_tokens,
                                          seconds, finish_reason
      thought      调用工具前的说明 data: text
      tool_call    模型请求调用工具 data: id, name, arguments
      tool_result  工具执行结果     data: id, name, content, is_error, seconds
      notice       系统干预说明     data: text   （预算提醒、重复拦截、截断处理……）
      compaction   上下文压缩       data: count, by_tool, tokens_before, tokens_after
      final        最终回答         data: text, stop_reason
      run_end      任务结束         data: stop_reason, steps, total_tokens, equivalent_tokens, seconds, error
    """

    kind: str
    data: dict[str, Any] = field(default_factory=dict)


EventHandler = Callable[[AgentEvent], None]


@dataclass
class AgentResult:
    answer: str
    steps: int  # 调用模型的轮数（不含收尾调用）
    stop_reason: StopReason
    messages: list[dict[str, Any]]  # 完整对话历史，便于调试 / 复盘
    total_tokens: int = 0  # 原始 token 总数（输入 + 输出）
    error: str | None = None
    equivalent_tokens: float = 0.0  # 按缓存折算的等效 token，预算按它计算

    @property
    def tool_calls(self) -> list[tuple[str, str]]:
        """按顺序列出本次运行中所有 (工具名, 参数)。"""
        out = []
        for m in self.messages:
            for c in m.get("tool_calls") or []:
                out.append((c["function"]["name"], c["function"]["arguments"]))
        return out


class Agent:
    def __init__(
        self,
        llm: ChatModel,
        registry: ToolRegistry,
        system_prompt: str | None = None,
        max_steps: int = 30,
        max_tool_result_chars: int = 8000,
        token_budget: int = 300_000,
        budget_warn_ratio: float = 0.8,
        token_weights: TokenWeights | None = None,
        tool_timeout: float = 60.0,
        max_blocked_repeats: int = 3,
        steps_warn_remaining: int = 6,
        wrapup_max_calls: int = 3,
        compactor: ContextCompactor | None = None,
        on_event: EventHandler | None = None,
        task_dir: Path | None = None,
        review_reports: bool = False,
        review_max_calls: int = 2,
        review_max_tokens: int = 2048,
        research_session=None,
        memory_context: str = "",
        task_contract: TaskContract | None = None,
        should_stop: Callable[[], bool] | None = None,
    ):
        self.llm = llm
        self.registry = registry
        self.system_prompt = system_prompt or research_system_prompt(date.today().isoformat(), max_steps)
        self.max_steps = max_steps
        self.max_tool_result_chars = max_tool_result_chars
        self.token_budget = token_budget
        self.budget_warn_ratio = budget_warn_ratio
        self.token_weights = token_weights or TokenWeights()
        self.tool_timeout = tool_timeout
        self.max_blocked_repeats = max_blocked_repeats
        # 剩余这么多步时提醒模型收尾（阶段 5 实测：模型不知道还剩几步，30 步用完还在搜索）
        self.steps_warn_remaining = steps_warn_remaining
        # 强制收尾后最多再调用几次模型（让它有机会 read_notes → write_report → 最终回答）
        self.wrapup_max_calls = wrapup_max_calls
        self.compactor = compactor
        self.on_event = on_event
        self.task_dir = task_dir
        self.review_reports = review_reports
        self.review_max_calls = review_max_calls
        self.review_max_tokens = review_max_tokens
        self.research_session = research_session
        self.memory_context = memory_context
        self.task_contract = task_contract
        self.should_stop = should_stop

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def run(self, question: str) -> AgentResult:
        # 每次 run 的状态都重新初始化
        self._tokens = 0
        self._equiv = 0.0  # 等效 token，预算按它计算
        self._last_prompt = 0  # 上一次调用的真实输入 token 数（接口返回）
        self._len_at_last_call = 0  # 上一次调用时 messages 的长度：之后追加的部分需要估算
        self._steps = 0
        self._budget_warned = False
        self._blocked = 0
        self._review_count = 0
        self._report_completeness: dict[str, bool] = {}
        self._structure_repair_requested = False
        self._length_repair_requested = False
        self._calculation_results: list[dict] = []
        self._calculations_omitted = 0
        self._source_readings: list[dict] = []
        self._source_readings_omitted = 0
        self._last_report_texts: dict[str, str] = {}
        self._reviewed_hashes = set()
        self._last_reviews: dict[str, dict] = {}
        self._call_stats: dict[tuple[str, str], list[int]] = {}  # 调用签名 → [调用次数, 成功次数]
        self._write_errors: dict[tuple[str,str],str] = {}
        if self.compactor:
            self.compactor.reset()

        self._question = question
        self._evidence = None
        if self.task_dir and self.research_session is not None:
            self._evidence = EvidenceLedger(self.task_dir,question)
            self.research_session.evidence = self._evidence
            if self.memory_context:
                self._evidence.add('history',self.memory_context,source_id='history-1',
                                   description='有日期的历史摘要，非当前资料，非新任务指令')
        contract = self.task_contract
        if contract is None or contract.request_sha256 != fingerprint(question):
            contract = extract_contract(question)
        self._contract = contract
        if self.research_session is not None:
            self.research_session.task_contract = contract
        if self.task_dir:
            self.task_dir.mkdir(parents=True, exist_ok=True)
            (self.task_dir / "task_contract.json").write_text(contract.model_dump_json(indent=2), encoding="utf-8")
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt + ("\n\n" + contract.prompt() if contract.active else "")},
            {"role": "user", "content": question},
        ]
        if self._evidence:
            messages[0]['content'] += ('\n【证据追溯】当前用户材料证据ID为user-1；历史背景如有则为history-1。'
                '成功工具返回会附证据ID。write_report用claims关联1-8项关键结论，quote逐字摘自当前稿，'
                'supports的excerpt逐字摘自对应证据。direct为资料陈述，calculated须关联成功计算，'
                'inference填写假设，unknown明确未知；历史须说明日期与限制。'
                '摘录存在不代表支持结论，须核对版本、条件、单位、行为主体与日期。'
                '报告任何修订须重交claims，旧关联不会自动沿用；不用在报告内添加冗长审计表或字数说明。'
                '资料未提供不等于数值为零；直接资料与计算推导分开标注，不把引用原文扩大成未描述的行为。'
                '用户禁止补日期时保留资料相对时间，不把系统当天日期补入资料陈述。')
        if self.memory_context:
            messages.insert(1, {"role": "user", "content": self.memory_context})
        self._emit(
            "run_start",
            question=question,
            max_steps=self.max_steps,
            token_budget=self.token_budget,
            token_weights=asdict(self.token_weights),
            tools=self.registry.names,
        )
        started = time.perf_counter()
        if self.memory_context:
            self._emit("notice", text=f"已加载历史研究背景（{len(self.memory_context)} 字符），历史结论仍需核实。")
        try:
            self._plan(messages, question)
            stop, answer, error = self._loop(messages)
        except KeyboardInterrupt:
            self._emit("notice", text="用户中断，任务停止。")
            stop, answer, error = "interrupted", "", "用户中断"

        self._refresh_report_checks()
        self._emit(
            "run_end",
            stop_reason=stop,
            steps=self._steps,
            total_tokens=self._tokens,
            equivalent_tokens=round(self._equiv),
            seconds=round(time.perf_counter() - started, 2),
            error=error,
        )
        return AgentResult(answer, self._steps, stop, messages, self._tokens, error, round(self._equiv))

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def _loop(self, messages: list[dict[str, Any]]) -> tuple[StopReason, str, str | None]:
        tools = self._tools = self.registry.schemas()
        pending_text: list[str] = []  # 被截断、正在续写的回答
        empty_replies = 0

        for step in range(1, self.max_steps + 1):
            self._steps = step
            self._emit("step", step=step)

            # 估算这次请求的上下文大小；超过阈值就先压缩
            context_tokens = self._estimate_context(messages, tools)
            if self.compactor:
                context_tokens = self._compact(messages, context_tokens)

            # 不是等超了才停，而是预估"再调用一次就会超"时就收尾（收尾调用本身仍会花掉约一次的量）
            if self._equiv + self._project_next_call(context_tokens) >= self.token_budget:
                return self._finalize(messages, "budget")

            # ---- 1. 调用模型 ----
            try:
                resp = self._call_llm(messages, tools, step)
            except openai.APIError as e:
                kind = classify_api_error(e)
                if kind == "content_filter" and self._redact_last_tool_results(messages):
                    self._emit("notice", text="请求被模型服务的内容审核拦截，已移除最近一批工具结果后重试。")
                    continue
                return _stop_for(kind), "", f"{type(e).__name__}: {e}"

            choice = resp.choices[0]
            msg = choice.message
            if choice.finish_reason == "content_filter":  # 生成的内容被拦截
                if self._redact_last_tool_results(messages):
                    self._emit("notice", text="模型输出被内容审核拦截，已移除最近一批工具结果后重试。")
                    continue
                return "content_filter", "", "模型输出被内容审核拦截"
            truncated = choice.finish_reason == "length"

            # ---- 2. 没有工具调用：最终回答（或需要续写 / 重试） ----
            if not msg.tool_calls:
                text = msg.content or ""
                if truncated:
                    messages.append(_assistant_message_to_dict(msg))
                    messages.append({"role": "user", "content": CONTINUE_PROMPT})
                    pending_text.append(text)
                    self._emit("notice", text="回答因输出长度上限被截断，已要求模型接着写。")
                    continue
                if not text.strip() and not pending_text:
                    empty_replies += 1
                    if empty_replies >= 2:
                        return "error", "", "模型连续返回空回复"
                    # 空的 assistant 消息有的厂商不接受，所以不放进历史，只追加提醒
                    messages.append({"role": "user", "content": EMPTY_REPLY_PROMPT})
                    self._emit("notice", text="模型返回了空回复，已提醒它继续。")
                    continue
                messages.append(_assistant_message_to_dict(msg))
                if step < self.max_steps and self._request_structure_repair(messages):
                    pending_text.clear()
                    empty_replies = 0
                    continue
                answer = "".join(pending_text) + text
                self._emit("final", text=answer, stop_reason="final_answer")
                return "final_answer", answer, None

            # ---- 3. 有工具调用：逐个执行，结果写回历史 ----
            empty_replies = 0
            self._execute_calls(messages, msg, truncated)

            # ---- 4. 本轮结束后的检查 ----
            if self._blocked >= self.max_blocked_repeats:
                return self._finalize(messages, "loop_detected")
            self._maybe_warn_budget(messages)
            self._maybe_warn_steps(messages, step)

        return self._finalize(messages, "max_steps")

    # ------------------------------------------------------------------
    # 各个环节
    # ------------------------------------------------------------------

    def _execute_calls(
        self,
        messages: list[dict[str, Any]],
        msg: ChatCompletionMessage,
        truncated: bool,
        allowed: tuple[str, ...] | None = None,
    ) -> None:
        """执行模型这一轮要求的全部工具调用，结果逐条追加到历史。

        allowed：只允许调用这些工具（收尾阶段用）；其它调用不执行，返回说明。
        注意即使拒绝也必须回一条 tool 消息：每个 tool_call 都要有对应的结果，否则下次请求报 400。
        """
        messages.append(_assistant_message_to_dict(msg))
        reports_written = {}
        length_rejected = False
        rejected_length_calls = set()
        length_measurements = []
        if msg.content:
            self._emit("thought", text=msg.content)

        for call in msg.tool_calls or []:
            self._check_stop()
            name, arguments = call.function.name, call.function.arguments
            self._emit("tool_call", id=call.id, name=name, arguments=arguments)
            if truncated:
                # 参数大概率是半截 JSON；即使碰巧能解析，内容也不完整（例如半篇报告）
                result, seconds = ToolResult(TRUNCATED_TOOL_CALL_RESULT, True), 0.0
            elif allowed is not None and name not in allowed:
                result, seconds = ToolResult(wrapup_refused_result(name), True), 0.0
            else:
                result, seconds = self._run_tool(name, arguments)
            content = _truncate(result.content, self.max_tool_result_chars)
            if name == 'write_report' and result.is_error and '超过明确上限' in result.content:
                length_rejected = True
                rejected_length_calls.add(call.id)
                for scope, unit, actual, maximum in re.findall(
                    r'(full|body)/(characters|han_characters|english_words) 实际 (\d+)，超过明确上限 (\d+)', result.content
                ):
                    length_measurements.append({'scope':scope,'unit':unit,'actual':int(actual),
                                                'maximum':int(maximum),'suggested_target':int(int(maximum)*.6)})
            if self._evidence and not result.is_error:
                ids = self._evidence.capture(name,arguments,content,call.id,result.evidence,
                                             min(len(result.content),self.max_tool_result_chars))
                if ids:
                    content += '\n【本次证据ID】' + '、'.join(ids) + '；摘录须来自对应实际片段；需要原文用read_evidence读取。'
                    for source_id in ids:
                        source=self._evidence.records[source_id]
                        preview=source['text'][:100] if source['kind']=='web' else source['text'].split('成功输出：',1)[-1][:120]
                        content += '\n'+json.dumps({'source_id':source_id,'range':[source.get('start'),source.get('end')],
                                                    'preview':preview},ensure_ascii=False)
            if name == "fetch_url" and not result.is_error:
                self._source_readings.append({"tool_call_id": call.id, "arguments": arguments[:2000],
                    "content": content[:8000], "possibly_truncated": len(result.content) > min(self.max_tool_result_chars, 8000) or len(arguments) > 2000})
                while (len(self._source_readings) > 8 or sum(len(i["arguments"]) + len(i["content"]) for i in self._source_readings) > 20000):
                    self._source_readings.pop(0)
                    self._source_readings_omitted += 1
            if name in ("calculator", "days_between", "run_python") and not result.is_error:
                # 独立于主对话压缩；只保留真实成功调用，有界记录并明确截断/省略。
                self._calculation_results.append({"tool_call_id": call.id, "tool": name,
                    "arguments": arguments[:4000], "result": result.content[:4000],
                    "possibly_truncated": len(arguments) > 4000 or len(result.content) > 4000})
                while (len(self._calculation_results) > 20
                       or sum(len(i["arguments"]) + len(i["result"]) for i in self._calculation_results) > 16000):
                    self._calculation_results.pop(0)
                    self._calculations_omitted += 1
            self._emit(
                "tool_result",
                id=call.id,
                name=name,
                content=content,
                is_error=result.is_error,
                seconds=round(seconds, 2),
            )
            messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
            if name == "write_report" and not result.is_error:
                from tools.report import safe_filename
                args = json.loads(arguments)
                filename = safe_filename(args.get("filename", "report.md"))
                complete = args.get("complete", True)
                reports_written[filename] = complete
                self._report_completeness[filename] = complete

        if rejected_length_calls and not reports_written:
            # 失败参数仍在 trace；只移除其对模型的反复锚定，保留同批其他工具的配对消息。
            compacted = []
            for item in messages:
                if item.get('role') == 'tool' and item.get('tool_call_id') in rejected_length_calls:
                    continue
                if item.get('role') == 'assistant' and item.get('tool_calls'):
                    calls = [c for c in item['tool_calls'] if c['id'] not in rejected_length_calls]
                    if not calls:
                        continue
                    item = dict(item)
                    if len(calls) != len(item['tool_calls']):
                        item['content'] = None
                    item['tool_calls'] = calls
                compacted.append(item)
            messages[:] = compacted
            self._last_prompt = 0
            self._len_at_last_call = 0
            messages.append({'role':'assistant','content':
                '【上下文整理】本批超限write_report未保存。被拒写稿参数与反馈已移出模型上下文，完整记录仍在trace。'
                '原任务、其他工具结果和证据索引保留；请重新生成显著更短的完整稿，不能复用被拒稿。'
                '程序计量（不是放宽上限）：'+json.dumps(length_measurements[:4],ensure_ascii=False)})
            self._emit('notice',text='已从模型上下文移除本批超限写稿调用；原始参数/反馈仍保留在trace，证据索引不变。')

        if length_rejected and not reports_written and not self._length_repair_requested:
            self._length_repair_requested = True
            reminder = ('【硬篇幅修订】上一稿未保存。下一稿整体重写为明显更短的正文，按工具反馈的计量单位和余量目标压缩，'
                        '不要重复提交原稿或只删参考资料。保留必要章节，每节只写必要短句，合并重复解释。'
                        '可以缩短结论原句并同步更新claims.quote，不必为了保留旧quote继续保留长句。'
                        'claims、supports.excerpt、assumptions、limits是独立依据字段，不计入报告正文长度；'
                        '来源原文保留在excerpt，不必抄进正文。不得删掉必答事实或修改原上限；仍用原步数、预算和审稿次数。')
            messages.append({'role':'system','content':reminder})
            self._emit('notice',text=reminder)

        for filename, complete in reports_written.items():
            self._review_saved_report(messages, filename, complete=complete)

        if truncated:
            self._emit("notice", text="回复因输出长度上限被截断，工具调用未执行，已告知模型分段处理。")

    def _request_structure_repair(self, messages: list[dict[str, Any]]) -> bool:
        """正常结束前只提醒一次；不增加步数、预算或强制收尾轮数。"""
        if self._structure_repair_requested or not self.task_dir:
            return False
        pending = []
        for filename, complete in self._report_completeness.items():
            path = self.task_dir / filename
            if not complete or filename == 'notes.md' or not path.is_file() or path.is_symlink():
                continue
            report = path.read_text(encoding='utf-8')
            check = inspect_report(report, self.task_dir, self.research_session, contract=self._contract)
            feedback = structure_repair_feedback(report, self._contract, check)
            if self._evidence:
                mapping=self._evidence.prepare(path,report,None,complete=True)
                if mapping['status']!='mapped':
                    feedback += ('\n【依据关联待补】请用write_report的claims关联1-8项关键原句与本次证据ID、逐字摘录。'
                                 '未知用unknown，推断填assumptions，历史填日期及limits；不编造证据。')
            if feedback:
                pending.append(f"文件 {filename}\n{feedback}")
            if len(pending) >= 4:
                break  # 有界反馈；不扫描本次未成功写入的其他 Markdown 文件。
        if not pending:
            return False
        self._structure_repair_requested = True
        feedback = ('【结束前结构核对】本次已保存的完整报告仍有结构或关键依据关联缺项。'
                    '这是本次唯一一次结束前修复提醒，占用现有剩余步数和预算。'
                    '请先在用户篇幅及内容要求内修订，再最终回答；不能只在回答里解释。\n'
                    + '\n'.join(pending))
        messages.append({'role': 'user', 'content': feedback})
        self._emit('notice', text=feedback)
        return True

    def _review_saved_report(self, messages: list[dict[str, Any]], filename: str, *, complete: bool = True) -> None:
        if not self.review_reports or not self.task_dir:
            return
        from tools.report import safe_filename

        path = self.task_dir / safe_filename(filename)
        if not path.is_file() or path.is_symlink():
            return
        report = path.read_text(encoding="utf-8")
        digest = (path.name, fingerprint(report))
        if self._evidence:
            mapping=self._evidence.prepare(path,report,None,complete=complete)
            digest=(*digest,mapping_sha(mapping))
        if complete and digest in self._reviewed_hashes:
            return
        if not complete:
            self._reviewed_hashes.discard(digest)
            record = save_review(path, report, "not_reviewed", [], reason="分段草稿尚未完成")
        elif structure_repair_feedback(report, self._contract, inspect_report(
                report, self.task_dir, self.research_session, contract=self._contract)):
            record = save_review(path, report, "not_reviewed", [], reason="机械结构检查待修复，暂不消耗语义审稿次数")
        elif self._evidence and mapping['status']!='mapped':
            record = save_review(path,report,'not_reviewed',[],reason='关键结论依据待关联；请补交claims后再语义审稿，不消耗次数')
        elif self._review_count >= self.review_max_calls:
            record = save_review(path, report, "skipped_limit", [])
        else:
            review_messages, truncated = build_review_messages(report, self.task_dir, self.research_session,
                                                               question=self._question,
                                                               memory_context=self.memory_context,
                                                               previous_review=self._last_reviews.get(path.name),
                                                               calculation_results=self._calculation_results,
                                                               calculations_omitted=self._calculations_omitted,
                                                               source_readings=self._source_readings,
                                                               source_readings_omitted=self._source_readings_omitted,
                                                               previous_report=self._last_report_texts.get(path.name),
                                                               task_contract=self._contract, report_filename=path.name)
            while True:
                projected = estimate_messages(review_messages) + self.review_max_tokens * self.token_weights.output
                if self._equiv + projected >= self.token_budget:
                    record = save_review(path, report, "skipped_budget", [])
                    break
                self._review_count += 1
                self._reviewed_hashes.add(digest)
                self._emit("notice", text=f"开始报告审稿：{path.name}（{self._review_count}/{self.review_max_calls}），用量计入预算。")
                raw_review = ""
                try:
                    response = self._call_llm(review_messages, None, None,
                                              response_format={"type": "json_object"}, max_tokens=self.review_max_tokens,
                                              purpose="report_review",
                                              evidence_tokens_estimate=json.loads(review_messages[1]['content']).get('evidence_packet_estimated_tokens',0))
                    choice = response.choices[0]
                    raw_review = choice.message.content or ""
                    if choice.finish_reason != "stop":
                        raise ValueError("审稿回复不完整")
                    issues = parse_verdict(raw_review, report)
                    record = save_review(path, report, "partial" if truncated else "reviewed", issues)
                    break
                except (openai.APIError, ValueError, IndexError) as exc:
                    valid_issues = exc.valid_issues if isinstance(exc, ReviewValidationError) else []
                    record = save_review(path, report, "partial" if valid_issues else "failed", valid_issues, error_type=type(exc).__name__,
                                         error_detail=str(exc)[:2000] if isinstance(exc, ValueError) else "模型服务调用失败",
                                         raw_response=raw_review[:12000])
                    if valid_issues:
                        break  # 先落实有效意见，再用剩余次数审查修订稿；不能把 partial 当通过。
                    if not isinstance(exc, ValueError) or self._review_count >= self.review_max_calls:
                        break
                    self._emit("notice", text="审稿回复未通过校验，在原次数和预算上限内要求纠正；失败记录已保留。")
                    review_messages.extend([
                        {"role": "assistant", "content": raw_review[:12000]},
                        {"role": "user", "content": "上一回复未通过校验：" + str(exc)[:2000]
                         + "。请重新审查原报告，返回符合 schema 的完整 JSON；只列实际存在且需要修改的问题，无问题则 issues=[]。"},
                    ])
                finally:
                    # 审稿是独立上下文，不能把其 token 数当作主对话下一轮的基准。
                    self._last_prompt = 0
                    self._len_at_last_call = 0
        # 只携带当前运行、同一文件的上次意见，不加载磁盘上的任意旧记录或失败回复全文。
        self._last_reviews[path.name] = {k: record[k] for k in ("report_sha256", "status", "issues")}
        self._last_report_texts[path.name] = report[:40000]
        feedback = review_feedback(record)
        if record.get('reason'):
            feedback += '\n原因：' + record['reason']
        self._emit("notice", text=feedback)
        messages.append({"role": "user", "content": feedback})

    def _refresh_report_checks(self) -> None:
        """收尾时刷新计划检查；模型可能在写报告之后才标记计划完成。"""
        if not self.task_dir or not self.task_dir.exists():
            return
        for path in self.task_dir.glob("*.md"):
            if path.name == "notes.md" or path.is_symlink():
                continue
            report = path.read_text(encoding="utf-8")
            check = inspect_report(report, self.task_dir, self.research_session, contract=self._contract)
            save_check(path, check)
            contract_check = save_contract_check(path, report, self._contract)
            if self._evidence:
                mapping = self._evidence.prepare(path,report,None,complete=self._report_completeness.get(path.name,False))
                write_json(path.with_suffix('.evidence.json'),mapping)
                if mapping['status']!='mapped':
                    self._emit('notice',text=f"最终报告 {path.name} 关键结论依据状态 {mapping['status']}，未获得当前完整关联；不是事实核验通过。")
            if contract_check['hard_errors']:
                self._emit("notice", text=f"最终报告 {path.name} 仍违反任务硬约束：" + "；".join(contract_check['hard_errors']))
            if check.issues:
                self._emit("notice", text=f"最终报告 {path.name} 仍有 {len(check.issues)} 项机械检查问题，见 {path.stem}.quality.json。")
            if self.review_reports:
                review_path = path.with_suffix(".review.json")
                try:
                    review = json.loads(review_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    review = save_review(path, report, "not_reviewed", [])
                if review.get("report_sha256") != fingerprint(report):
                    review = save_review(path, report, "stale", [])
                elif self._evidence and review['status']=='reviewed' and review.get('evidence_mapping_sha256')!=mapping_sha(mapping):
                    review = save_review(path,report,'stale',[],reason='关键结论依据改变，未重新审稿')
                if review["status"] != "reviewed" or review["issues"]:
                    self._emit("notice", text=f"最终报告 {path.name} 审稿状态：{review['status']}，{len(review['issues'])} 项问题；未获得无问题审稿结论。")

    def _call_llm(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        step: int | None,
        purpose: str = "agent",
        evidence_tokens_estimate: int = 0,
        **kwargs: Any,
    ) -> ChatCompletion:
        self._check_stop()
        started = time.perf_counter()
        self._len_at_last_call = len(messages)
        resp = self.llm.chat(messages, tools=tools, **kwargs)
        u = resp.usage
        p = u.prompt_tokens if u else 0
        c = u.completion_tokens if u else 0
        cached = min(_cached_tokens(u), p)
        equiv = self.token_weights.equivalent(p, cached, c)
        self._tokens += p + c
        self._equiv += equiv
        self._last_prompt = p
        self._emit(
            "llm_call",
            purpose=purpose,
            evidence_packet_estimated_tokens=evidence_tokens_estimate,
            step=step,
            requested_model=getattr(getattr(self.llm, 'config', None), 'model', None),
            response_model=resp.model,
            prompt_tokens=p,
            cached_tokens=cached,
            completion_tokens=c,
            equivalent_tokens=round(equiv),
            seconds=round(time.perf_counter() - started, 2),
            finish_reason=resp.choices[0].finish_reason if resp.choices else None,
        )
        # 已返回的调用先记录实际用量，再响应停止，避免丢失已消耗 token。
        self._check_stop()
        return resp

    def _check_stop(self) -> None:
        if self.should_stop and self.should_stop():
            raise KeyboardInterrupt

    def _plan(self, messages: list[dict[str, Any]], question: str) -> None:
        """任务开始时先拆解计划，一次性写进 system 消息。

        之后 system 消息不再改动（改动开头会让整段历史的前缀缓存失效）；
        计划的进展由 update_plan 工具的返回值带回，只往历史末尾追加。
        规划本身走 _call_llm，所以计入预算并写进 trace。规划失败不影响主流程。
        """
        if not self.task_dir:
            return
        try:
            plan = generate_plan(
                question + ("\n\n" + self.memory_context if self.memory_context else ""),
                lambda m, **kw: self._call_llm(m, None, None, purpose="planning", **kw),
                self.task_dir,
                today=date.today().isoformat(),
                contract=self._contract,
            )
        except openai.APIError as e:
            self._emit("notice", text=f"生成计划失败（{type(e).__name__}），将不带计划继续。")
            return
        finally:
            # 规划请求的 prompt 与主循环无关，基准作废
            self._last_prompt = 0
            self._len_at_last_call = 0
        text = format_plan_for_prompt(plan)
        if text:
            messages[0]["content"] += "\n\n" + text
            self._emit("plan", plan=plan)
            self._emit("notice", text="已生成研究计划（plan.json）。")

    def _estimate_context(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> int:
        """下一次请求的输入 token 估算 = 上一次的真实输入 + 之后新增消息的估算。

        以接口返回的真实数字为基准，只估算增量，比整体估算准得多。
        """
        if not self._last_prompt:
            return estimate_messages(messages) + estimate_tools(tools)
        return self._last_prompt + estimate_messages(messages[self._len_at_last_call :])

    def _compact(self, messages: list[dict[str, Any]], context_tokens: int) -> int:
        result = self.compactor.maybe_compact(messages, context_tokens)  # type: ignore[union-attr]
        if not result:
            return context_tokens
        # 被压缩的网页允许再次读取：清掉它在重复调用检测里的"成功"记录
        for name, arguments in result.compacted:
            self._call_stats.pop((name, _normalize_args(arguments)), None)
        # 历史中间被改动过，"上次真实值 + 增量"的基准失效，下次改为整体估算
        self._last_prompt = 0
        parts = "、".join(f"{n} × {c}" for n, c in result.by_tool.items())
        self._emit(
            "compaction",
            count=len(result.compacted),
            by_tool=result.by_tool,
            tokens_before=result.tokens_before,
            tokens_after=result.tokens_after,
        )
        self._emit(
            "notice",
            text=f"压缩上下文：移出 {parts} 的旧结果，约 {result.tokens_before} → {result.tokens_after} tokens",
        )
        return result.tokens_after

    def _run_tool(self, name: str, arguments: str) -> tuple[ToolResult, float]:
        """执行一次工具调用：先做重复检测，再带超时执行。"""
        sig = (name, _normalize_args(arguments))
        stats = self._call_stats.setdefault(sig, [0, 0])
        calls, successes = stats
        # 同样的调用：成功过一次就不必再做（结果就在历史里）；失败了允许重试，但最多 3 次
        if successes >= 1 or calls >= 3:
            self._blocked += 1
            self._emit("notice", text=f"拦截了一次重复调用：{name}（累计 {self._blocked} 次）")
            feedback = REPEAT_BLOCKED_RESULT
            if name == 'write_report' and not successes and sig in self._write_errors:
                feedback += '\n上次保存拒绝的具体原因：' + self._write_errors[sig]
                feedback += '\n不要重发同一内容；请合并重复解释、重写为明显更短的稿件，保留必答结论、依据和局限。'
            return ToolResult(feedback, True), 0.0

        started = time.perf_counter()
        result = self._execute_with_timeout(name, arguments)
        stats[0] += 1
        if not result.is_error:
            stats[1] += 1
        elif name == 'write_report':
            self._write_errors[sig] = result.content[:1800]
            if len(self._write_errors) > 20:
                self._write_errors.pop(next(iter(self._write_errors)))
        return result, time.perf_counter() - started

    def _execute_with_timeout(self, name: str, arguments: str) -> ToolResult:
        """在后台线程里执行工具，超时就放弃等待。

        Python 无法强制杀死线程，超时的线程会在后台继续跑完（daemon 线程，不会阻止程序退出）。
        这里用"分段等待"而不是一次 wait(timeout)，这样 Windows 上 Ctrl+C 也能及时响应。
        """
        box: dict[str, ToolResult] = {}
        done = threading.Event()

        def target() -> None:
            try:
                box["r"] = self.registry.execute(name, arguments)
            except BaseException as e:  # noqa: BLE001 —— execute 本身不抛异常，这里是双保险
                box["r"] = ToolResult(f"ERROR: {type(e).__name__}: {e}", True)
            finally:
                done.set()

        threading.Thread(target=target, daemon=True, name=f"tool-{name}").start()
        deadline = time.monotonic() + self.tool_timeout
        while not done.wait(0.1):
            if time.monotonic() >= deadline:
                return ToolResult(
                    f"ERROR: 工具执行超时（超过 {self.tool_timeout:g} 秒），已放弃本次调用。请换个参数或来源。",
                    True,
                )
        return box["r"]

    def _project_next_call(self, context_tokens: int) -> float:
        """预估下一次调用的等效 token：上次请求的部分大概率命中缓存，新增部分不会。

        刚压缩过（或第一次调用）时 _last_prompt 为 0，按全部未命中保守估计。不计输出（事先无从知道）。
        """
        w = self.token_weights
        reused = min(self._last_prompt, context_tokens)
        return reused * w.cached + (context_tokens - reused) * w.uncached

    def _maybe_warn_budget(self, messages: list[dict[str, Any]]) -> None:
        if self._budget_warned or self._equiv < self.token_budget * self.budget_warn_ratio:
            return
        self._budget_warned = True
        used = round(self._equiv)
        messages.append({"role": "user", "content": budget_warning_prompt(used, self.token_budget)})
        self._emit("notice", text=f"等效 token 已用 {used}/{self.token_budget}，已提醒模型尽快收尾。")

    def _maybe_warn_steps(self, messages: list[dict[str, Any]], step: int) -> None:
        """剩余步数到达 steps_warn_remaining 时提醒一次，并列出计划中还没完成的子任务。

        提醒追加在历史末尾，不影响前缀缓存。步数上限本身已写在 system prompt 里（开局一次性写入）。
        """
        remaining = self.max_steps - step
        if remaining != self.steps_warn_remaining or remaining <= 0:
            return
        plan = load_plan(self.task_dir) if self.task_dir else None
        unfinished = [f"{t.get('id', '')} {t.get('title', '')}" for t in unfinished_tasks(plan)]
        messages.append({"role": "user", "content": steps_warning_prompt(remaining, unfinished)})
        self._emit("notice", text=f"只剩 {remaining} 步，已提醒模型收尾（未完成子任务 {len(unfinished)} 个）。")

    def _redact_last_tool_results(self, messages: list[dict[str, Any]]) -> bool:
        """把历史末尾那一批工具结果替换成占位说明。返回是否真的替换了内容。

        内容审核多半是刚读进来的网页触发的；已替换过的不会再替换，所以最多重试一次。
        """
        changed = False
        for m in reversed(messages):
            if m["role"] != "tool":
                break
            if m["content"] != REDACTED_TOOL_RESULT:
                m["content"] = REDACTED_TOOL_RESULT
                changed = True
        return changed

    def _finalize(
        self, messages: list[dict[str, Any]], reason: StopReason
    ) -> tuple[StopReason, str, str | None]:
        """强制收尾：进入"收尾阶段"，只允许读笔记、写报告、更新计划，最多再调用 wrapup_max_calls 次模型。

        为什么不直接禁止调用工具：
        - 阶段 5 实测：模型在 30 步内没写报告，强制收尾只能把一篇回答当报告存下，
          而它本来可以再用一两轮 read_notes → write_report 写出正式报告；
        - 前缀缓存：收尾请求和平时的请求必须"长得一样"（同样的工具说明书、不加 tool_choice）。
          实测"不发工具"（4.2 万输入命中 384）和"tools + tool_choice=none"（3.5 万输入命中 640）都几乎不命中，
          推测厂商按 tool_choice 渲染出的提示开头不同。所以收尾阶段照常请求，由代码拒绝不允许的工具。
        收尾轮数用完仍没有文字回答时，才退回到老办法（tool_choice=none，再不行就不发工具）。
        预算不在收尾阶段检查：收尾最多几次调用，超出量有限。
        """
        self._emit("notice", text=f"触发强制收尾（{reason}），进入收尾阶段：只能读笔记、写报告、更新计划。")
        messages.append({"role": "user", "content": finalize_prompt(reason)})
        tools = getattr(self, "_tools", None) or None
        allowed = tuple(n for n in WRAPUP_TOOLS if n in self.registry.names)

        for _ in range(self.wrapup_max_calls):
            try:
                resp = self._call_llm(messages, tools, None)
            except openai.APIError as e:
                return reason, "", f"收尾调用失败：{type(e).__name__}: {e}"
            choice = resp.choices[0]
            msg = choice.message
            if not msg.tool_calls:
                messages.append(_assistant_message_to_dict(msg))
                answer = msg.content or ""
                self._emit("final", text=answer, stop_reason=reason)
                return reason, answer, None
            self._execute_calls(messages, msg, choice.finish_reason == "length", allowed)

        return self._finalize_without_tools(messages, reason)

    def _finalize_without_tools(
        self, messages: list[dict[str, Any]], reason: StopReason
    ) -> tuple[StopReason, str, str | None]:
        """最后手段：禁止调用工具，逼模型直接用文字作答（这次调用基本不命中缓存，但只发生在收尾轮数用完时）。"""
        self._emit("notice", text="收尾轮数已用完，禁止调用工具，要求模型直接作答。")
        messages.append({"role": "user", "content": WRAPUP_LAST_CALL_PROMPT})
        try:
            resp = self._call_llm(messages, getattr(self, "_tools", None) or None, None, tool_choice="none")
            if resp.choices[0].message.tool_calls and not resp.choices[0].message.content:
                raise _ToolChoiceIgnored
        except (openai.BadRequestError, _ToolChoiceIgnored):
            try:
                resp = self._call_llm(messages, None, None)
            except openai.APIError as e:
                return reason, "", f"收尾调用失败：{type(e).__name__}: {e}"
        except openai.APIError as e:
            return reason, "", f"收尾调用失败：{type(e).__name__}: {e}"
        msg = resp.choices[0].message.model_copy(update={"tool_calls": None})
        messages.append(_assistant_message_to_dict(msg))
        answer = msg.content or ""
        self._emit("final", text=answer, stop_reason=reason)
        return reason, answer, None

    def _emit(self, kind: str, **data: Any) -> None:
        if self.on_event:
            self.on_event(AgentEvent(kind, data))


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------


class _ToolChoiceIgnored(Exception):
    """收尾时模型无视 tool_choice=none，仍然只返回了工具调用。"""


def _stop_for(kind: str) -> StopReason:
    return {"context_overflow": "context_overflow", "content_filter": "content_filter"}.get(
        kind, "error"
    )  # type: ignore[return-value]


def _cached_tokens(usage: Any) -> int:
    """输入中命中厂商缓存的 token 数（命中部分通常只收 1/10～1/50 的价格）。

    各家字段不同：OpenAI 标准是 prompt_tokens_details.cached_tokens，DeepSeek 是 prompt_cache_hit_tokens。
    """
    if usage is None:
        return 0
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details else None
    if cached is None:
        cached = (getattr(usage, "model_extra", None) or {}).get("prompt_cache_hit_tokens")
    return int(cached or 0)


def _normalize_args(arguments: str) -> str:
    """参数规范化后再比较：{"a":1,"b":2} 和 {"b": 2, "a": 1} 视为同一个调用。"""
    try:
        return json.dumps(json.loads(arguments or "{}"), sort_keys=True, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        return arguments or ""


def _assistant_message_to_dict(msg: ChatCompletionMessage) -> dict[str, Any]:
    """把 SDK 返回的消息对象转成普通 dict 再放回历史。

    不直接 append 对象、也不用 model_dump() 全量导出，是为了只保留协议需要的字段：
    有些厂商会返回额外字段（如 DeepSeek 推理模型的 reasoning_content），
    原样回传可能导致下一次请求报错。
    """
    out: dict[str, Any] = {"role": "assistant", "content": msg.content}
    if msg.tool_calls:
        out["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.function.name, "arguments": c.function.arguments},
            }
            for c in msg.tool_calls
        ]
    return out


def _truncate(text: str, limit: int) -> str:
    """工具结果过长会撑爆上下文。先简单截断，阶段 4 再做摘要。"""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[结果过长已截断，原文共 {len(text)} 字符]"


def pretty_arguments(arguments: str, max_value_chars: int | None = None) -> str:
    """把 JSON 参数格式化成一行便于阅读的文本；解析失败就原样返回。

    max_value_chars：终端展示时截断过长的参数值（例如 write_report 的整篇报告）。
    """
    try:
        obj = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError:
        return arguments
    if not isinstance(obj, dict):
        return arguments
    parts = []
    for k, v in obj.items():
        text = repr(v)
        if max_value_chars and len(text) > max_value_chars:
            text = text[:max_value_chars] + f"…(共 {len(str(v))} 字符)"
        parts.append(f"{k}={text}")
    return ", ".join(parts)
