import pytest

from agent.config import Settings


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch):
    """测试与本机环境隔离。

    pydantic-settings 除了读 .env，还会读系统环境变量（且优先级更高）。
    如果本机设置过 DEEPSEEK_API_KEY 等变量，测试结果就会受影响，所以每个测试前都清掉。
    """
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
