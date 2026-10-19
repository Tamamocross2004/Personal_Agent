"""LLM 客户端封装。

职责（阶段 0）：
1. 用 OpenAI 兼容协议对接多家国产模型；
2. 对可恢复错误（限流、网络、服务端 5xx）做指数退避重试；
3. 累计 token 用量，方便后续做成本 / 预算控制。

后续阶段会在 chat() 里传入 tools，Agent 主循环直接复用这个类。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import openai
from openai import OpenAI
from openai.types.chat import ChatCompletion
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from agent.config import LLMConfig

logger = logging.getLogger(__name__)

# 只有这些错误值得重试；鉴权失败、参数错误等重试也没用，应立即抛出。
RETRYABLE_ERRORS = (
    openai.RateLimitError,  # 429
    openai.APIConnectionError,  # 网络问题（APITimeoutError 是它的子类）
    openai.InternalServerError,  # 5xx
)


@dataclass
class UsageStats:
    """跨多次调用累计的用量统计。"""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_seconds: float = 0.0
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def record(self, resp: ChatCompletion, seconds: float) -> None:
        self.calls += 1
        self.total_seconds += seconds
        u = resp.usage
        p = u.prompt_tokens if u else 0
        c = u.completion_tokens if u else 0
        self.prompt_tokens += p
        self.completion_tokens += c
        self.history.append(
            {"model": resp.model, "prompt": p, "completion": c, "seconds": round(seconds, 2)}
        )


class LLMClient:
    def __init__(self, config: LLMConfig, client: OpenAI | None = None):
        self.config = config
        # SDK 自带的重试关掉（max_retries=0），统一由下面的 tenacity 控制，逻辑更透明。
        self._client = client or OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout,
            max_retries=0,
        )
        self.usage = UsageStats()

        self._chat_with_retry = retry(
            retry=retry_if_exception_type(RETRYABLE_ERRORS),
            wait=wait_random_exponential(multiplier=1, max=20),
            stop=stop_after_attempt(config.max_retries + 1),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )(self._raw_chat)

    def _raw_chat(self, **kwargs: Any) -> ChatCompletion:
        return self._client.chat.completions.create(**kwargs)

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> ChatCompletion:
        """发送一次对话请求，返回原始 ChatCompletion。

        Agent 循环需要拿到 tool_calls 等完整信息，所以这里不做过多加工。
        """
        params: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            **kwargs,
        }
        if self.config.max_tokens and "max_tokens" not in params:
            params["max_tokens"] = self.config.max_tokens
        if tools:  # 有些厂商对 tools=[] 会报错，空时干脆不传
            params["tools"] = tools
        if self.config.thinking is not None:
            extra = dict(params.get('extra_body') or {})
            if 'thinking' not in extra and 'reasoning_effort' not in params:
                extra['thinking'] = {'type': self.config.thinking}
            params['extra_body'] = extra

        start = time.perf_counter()
        resp = self._chat_with_retry(**params)
        self.usage.record(resp, time.perf_counter() - start)
        return resp

    def ask(self, prompt: str, system: str | None = None) -> str:
        """便捷方法：单轮问答，直接返回文本。"""
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = self.chat(messages)
        return resp.choices[0].message.content or ""
