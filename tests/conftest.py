import pytest

from agent.config import Settings


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch):
    """测试与本机环境隔离。

    非密钥参数仍允许系统环境变量覆盖 .env，因此每个测试前清掉同名变量。
    API Key 的项目文件限制由配置回归单独验证。
    """
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
