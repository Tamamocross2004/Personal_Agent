"""用假的 OpenAI 客户端测试 LLMClient —— 不需要真实 API Key，也不产生费用。"""

import openai
import pytest
from openai.types.chat import ChatCompletion
from tenacity import wait_none

from agent.config import LLMConfig
from agent.llm import LLMClient

CFG = LLMConfig(
    provider="deepseek",
    base_url="http://fake",
    api_key="sk-fake",
    model="fake-model",
    temperature=0.0,
    timeout=5,
    max_retries=2,
)


def fake_completion(text: str, prompt_tokens: int = 10, completion_tokens: int = 5) -> ChatCompletion:
    return ChatCompletion.model_validate(
        {
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "fake-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": text},
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
    )


def api_error(cls, status: int):
    """构造 openai 异常实例。

    绕过 __init__（它需要 HTTP Response 对象，而不同 openai 版本底层用的 HTTP 库不同），
    重试逻辑只依赖异常类型，所以这样就够了。
    """
    err = cls.__new__(cls)
    Exception.__init__(err, f"fake {status}")
    err.status_code = status
    return err


class FakeOpenAI:
    """模拟 client.chat.completions.create：按顺序返回结果或抛出异常。"""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_client(outcomes) -> tuple[LLMClient, FakeOpenAI]:
    fake = FakeOpenAI(outcomes)
    llm = LLMClient(CFG, client=fake)
    llm._chat_with_retry.retry.wait = wait_none()  # 测试中不真的等待退避
    return llm, fake


def test_ask_returns_text_and_records_usage():
    llm, fake = make_client([fake_completion("你好", 12, 3)])
    assert llm.ask("hi", system="sys") == "你好"
    assert fake.calls[0]["model"] == "fake-model"
    assert fake.calls[0]["messages"][0] == {"role": "system", "content": "sys"}
    assert "tools" not in fake.calls[0]
    assert (llm.usage.calls, llm.usage.prompt_tokens, llm.usage.completion_tokens) == (1, 12, 3)


def test_usage_accumulates_across_calls():
    llm, _ = make_client([fake_completion("a", 10, 1), fake_completion("b", 20, 2)])
    llm.ask("1")
    llm.ask("2")
    assert llm.usage.calls == 2
    assert llm.usage.total_tokens == 33


def test_retries_on_rate_limit_then_succeeds():
    llm, fake = make_client([api_error(openai.RateLimitError, 429), fake_completion("ok")])
    assert llm.ask("hi") == "ok"
    assert len(fake.calls) == 2


def test_gives_up_after_max_retries():
    errs = [api_error(openai.InternalServerError, 500) for _ in range(3)]
    llm, fake = make_client(errs)
    with pytest.raises(openai.InternalServerError):
        llm.ask("hi")
    assert len(fake.calls) == CFG.max_retries + 1


def test_does_not_retry_auth_error():
    llm, fake = make_client([api_error(openai.AuthenticationError, 401)])
    with pytest.raises(openai.AuthenticationError):
        llm.ask("hi")
    assert len(fake.calls) == 1


def test_tools_passed_through_when_given():
    tools = [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}]
    llm, fake = make_client([fake_completion("ok")])
    llm.chat([{"role": "user", "content": "x"}], tools=tools)
    assert fake.calls[0]["tools"] == tools
