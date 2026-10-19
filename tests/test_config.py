import os

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
    assert cfg.model == 'deepseek-flash' and cfg.thinking == 'disabled'


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


@pytest.mark.parametrize("field", [p.key_field for p in PROVIDERS.values()] + ["jina_api_key"])
def test_project_api_key_overrides_global_key(tmp_path, monkeypatch, field):
    project_env = tmp_path / ".env"
    project_env.write_text(f"{field.upper()}=project-test-key\n", encoding="utf-8")
    monkeypatch.setenv(field.upper(), "global-test-key")

    settings = Settings(_env_file=project_env)

    assert getattr(settings, field) == "project-test-key"
    # 配置加载不修改当前进程环境；其他项目仍可使用自己的配置。
    assert os.environ[field.upper()] == "global-test-key"


@pytest.mark.parametrize("content", [None, "LLM_PROVIDER=deepseek\n", "DEEPSEEK_API_KEY=\n", "DEEPSEEK_API_KEY\n"])
def test_missing_project_key_never_falls_back_to_global(tmp_path, monkeypatch, content):
    project_env = tmp_path / ".env"
    if content is not None:
        project_env.write_text(content, encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "global-test-key")

    settings = Settings(_env_file=project_env)

    assert settings.deepseek_api_key == ""
    with pytest.raises(ValueError, match="项目 API Key DEEPSEEK_API_KEY"):
        load_llm_config(settings=settings)


@pytest.mark.parametrize("field", [p.key_field for p in PROVIDERS.values()] + ["jina_api_key"])
def test_disabled_dotenv_does_not_load_global_keys(monkeypatch, field):
    monkeypatch.setenv(field.upper(), "global-test-key")
    assert getattr(Settings(_env_file=None), field) == ""


def test_noncredential_environment_and_cli_overrides_are_preserved(tmp_path, monkeypatch):
    project_env = tmp_path / ".env"
    project_env.write_text("DEEPSEEK_API_KEY=project-test-key\nLLM_MODEL=project-model\nAGENT_MAX_STEPS=15\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "global-test-key")
    monkeypatch.setenv("LLM_MODEL", "environment-model")
    monkeypatch.setenv("AGENT_MAX_STEPS", "22")

    settings = Settings(_env_file=project_env)

    assert settings.agent_max_steps == 22
    assert load_llm_config(settings=settings).model == "environment-model"
    cli = load_llm_config(model="cli-model", settings=settings)
    assert cli.model == "cli-model"
    assert cli.api_key == "project-test-key"


def test_explicit_programmatic_key_can_be_injected_for_tests(tmp_path, monkeypatch):
    project_env = tmp_path / ".env"
    project_env.write_text("DEEPSEEK_API_KEY=project-test-key\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "global-test-key")
    settings = Settings(_env_file=project_env, deepseek_api_key="explicit-test-key")
    assert load_llm_config(settings=settings).api_key == "explicit-test-key"
