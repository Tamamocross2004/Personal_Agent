"""把模型接口的错误分类，决定 Agent 该怎么应对。

网络、限流、5xx 已经由 LLMClient 自动重试，能走到这里的都是"重试也没用"的错误。
不同厂商对同一类错误的报错文字差别很大（中英文都有），只能靠关键词匹配：

- context_overflow：对话历史超过模型上下文上限 → 没法继续，只能结束（阶段 4 会从根源上压缩上下文）
- content_filter：  触发厂商内容审核（常见于网页内容里有敏感词）→ 可以删掉最近的网页内容后重试
- auth：            Key 错误 / 无权限
- error：           其它（模型名不存在、参数错误等）
"""

from __future__ import annotations

import re
from typing import Literal

import openai

ApiErrorKind = Literal["context_overflow", "content_filter", "auth", "error"]

_CONTENT_FILTER = re.compile(
    r"content[\s_-]?(exists[\s_-]?)?risk"  # DeepSeek: Content Exists Risk
    r"|content[\s_-]?filter"  # 通用 / Kimi
    r"|data_inspection_failed"  # 通义千问
    r"|\b1301\b"  # 智谱 GLM 敏感内容错误码
    r"|inappropriate|sensitive"
    r"|敏感|不安全|违规|审核",
    re.IGNORECASE,
)

_CONTEXT_OVERFLOW = re.compile(
    r"context[\s_-]?(length|window)"
    r"|maximum.{0,40}(tokens?|length)"
    r"|(too long|exceed\w*).{0,40}(tokens?|length|context|limit)"
    r"|(tokens?|input|prompt).{0,30}(too long|exceed\w*)"
    r"|range of input length"  # 通义千问
    r"|超出.{0,10}(长度|上下文|限制)|上下文.{0,10}(过长|超)"
    r"|长度.{0,10}超出",
    re.IGNORECASE,
)


def classify_api_error(e: Exception) -> ApiErrorKind:
    if isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return "auth"
    text = f"{e} {getattr(e, 'body', '') or ''}"
    if isinstance(e, (openai.BadRequestError, openai.UnprocessableEntityError)) or getattr(
        e, "status_code", None
    ) in (400, 413, 422):
        if _CONTENT_FILTER.search(text):
            return "content_filter"
        if _CONTEXT_OVERFLOW.search(text):
            return "context_overflow"
    return "error"
