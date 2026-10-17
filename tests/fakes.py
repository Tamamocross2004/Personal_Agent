"""测试用的假模型：按剧本依次返回预设的回复，不发网络请求。"""

from __future__ import annotations

import copy
import json
from typing import Any

from openai.types.chat import ChatCompletion


def completion(
    content: str | None = None,
    tool_calls: list[tuple[str, str, Any]] | None = None,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> ChatCompletion:
    """构造一个 ChatCompletion。

    tool_calls: [(id, 工具名, 参数)]，参数可以是 dict（自动转 JSON）或原始字符串（用于模拟坏 JSON）。
    """
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = [
            {
                "id": cid,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False),
                },
            }
            for cid, name, args in tool_calls
        ]
    return ChatCompletion.model_validate(
        {
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "fake-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls" if tool_calls else "stop",
                    "message": message,
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
    )


class ScriptedLLM:
    """满足 Agent 所需的 ChatModel 协议。记录每次请求，便于断言模型"看到"了什么。"""

    def __init__(self, responses: list[ChatCompletion]):
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def chat(self, messages, tools=None, **kwargs):
        # 深拷贝：Agent 会继续往同一个 list 里追加消息
        self.requests.append({"messages": copy.deepcopy(messages), "tools": tools})
        if not self.responses:
            raise AssertionError("剧本里的回复已用完，Agent 调用模型的次数比预期多")
        return self.responses.pop(0)
