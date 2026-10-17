import pytest

from agent.config import PROVIDERS, Settings, load_llm_config


def make_settings(**kw) -> Settings:
    # _env_file=None：测试不读取本地 .env，保证结果可复现
    return Settings(_env_file=None, **kw)


def test_default_provider_uses_preset():
    cfg = load_llm_config(settings=make_settings(deepseek_api_key="sk-test"))
    assert cfg.provider == "deepseek"
    assert cfg.base_url == PROVIDERS["deepseek"].base_url
    assert cfg.model == PROVIDERS["deepseek"].default_model
    assert cfg.api_key == "sk-test"


def test_missing_key_raises_helpful_error():
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        load_llm_config(settings=make_settings())


def test_unknown_provider():
    with pytest.raises(ValueError, match="未知厂商"):
        load_llm_config(provider="nope", settings=make_settings(deepseek_api_key="x"))


def test_cli_model_overrides_env():
    s = make_settings(deepseek_api_key="x", llm_model="from-env")
    assert load_llm_config(settings=s).model == "from-env"
    assert load_llm_config(model="from-cli", settings=s).model == "from-cli"


def test_switching_provider_ignores_env_model_and_base_url():
    s = make_settings(
        llm_provider="deepseek",
        llm_model="deepseek-chat",
        llm_base_url="https://proxy.example.com",
        dashscope_api_key="sk-qwen",
    )
    cfg = load_llm_config(provider="qwen", settings=s)
    assert cfg.model == PROVIDERS["qwen"].default_model
    assert cfg.base_url == PROVIDERS["qwen"].base_url
    assert cfg.api_key == "sk-qwen"


def test_custom_provider_requires_model():
    s = make_settings(llm_provider="custom", custom_api_key="x")
    with pytest.raises(ValueError, match="模型名"):
        load_llm_config(settings=s)
