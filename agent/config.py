"""配置加载。

API Key 默认只从项目 .env 读取，其他参数仍可由环境变量覆盖；代码里不写死 Key。
国产主流大模型都提供 OpenAI 兼容接口，因此"切换厂商"只是换 base_url + api_key + model。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from pydantic import Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ProviderName = Literal["deepseek", "qwen", "glm", "kimi", "custom"]


@dataclass(frozen=True)
class ProviderPreset:
    base_url: str
    default_model: str
    key_field: str  # Settings 中存放该厂商 Key 的字段名


# 默认模型名仅作起点 —— 各家迭代很快，请以官方文档为准，并用 LLM_MODEL 覆盖。
PROVIDERS: dict[str, ProviderPreset] = {
    "deepseek": ProviderPreset("https://api.deepseek.com", "deepseek-flash", "deepseek_api_key"),
    "qwen": ProviderPreset(
        "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus", "dashscope_api_key"
    ),
    "glm": ProviderPreset("https://open.bigmodel.cn/api/paas/v4/", "glm-4-plus", "zhipu_api_key"),
    "kimi": ProviderPreset("https://api.moonshot.cn/v1", "moonshot-v1-32k", "moonshot_api_key"),
    "custom": ProviderPreset("http://localhost:8000/v1", "", "custom_api_key"),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource | Callable[[], dict[str, Any]], ...]:
        def project_api_keys() -> dict[str, Any]:
            values = dotenv_settings()
            # 缺失项也显式置空，禁止回退到环境变量或其他隐式密钥源。
            return {name: values.get(name) or "" for name in settings_cls.model_fields
                    if name.endswith("_api_key")}

        # 程序显式传参仍优先，供测试/受控调用；其余参数保持原环境变量优先级。
        return init_settings, project_api_keys, env_settings, dotenv_settings, file_secret_settings

    llm_provider: ProviderName = "deepseek"
    llm_model: str | None = None
    llm_base_url: str | None = None

    deepseek_api_key: str = ""
    dashscope_api_key: str = ""
    zhipu_api_key: str = ""
    moonshot_api_key: str = ""
    custom_api_key: str = ""
    siliconflow_api_key: str = ""

    knowledge_enabled: bool = False
    knowledge_path: Path = PROJECT_ROOT / 'workspace' / 'knowledge'
    embedding_base_url: str = 'https://api.siliconflow.cn/v1'
    embedding_model: str = 'Pro/BAAI/bge-m3'
    embedding_dimensions: int = Field(default=1024, ge=2, le=8192)
    embedding_timeout: float = Field(default=30, gt=0, le=120)
    knowledge_chunk_size: int = Field(default=700, ge=200, le=2000)
    knowledge_chunk_overlap: int = Field(default=120, ge=0, le=400)
    knowledge_build_token_budget: int = Field(default=300000, ge=1000, le=2000000)
    knowledge_rerank_enabled: bool = False
    rerank_base_url: str = 'https://api.siliconflow.cn/v1'
    rerank_model: str = 'BAAI/bge-reranker-v2-m3'
    rerank_timeout: float = Field(default=30, gt=0, le=120)
    knowledge_rerank_candidates: int = Field(default=20, ge=8, le=80)
    knowledge_lexical_candidates: int = Field(default=40, ge=8, le=200)
    knowledge_vector_candidates: int = Field(default=40, ge=8, le=200)
    knowledge_search_token_budget: int = Field(default=30000, ge=1000, le=200000)

    llm_temperature: float = 0.3
    llm_timeout: float = 60.0
    llm_max_retries: int = 3
    # 客户端输出上限；研究模式另受agent_call_max_tokens和剩余估算预算约束。
    llm_max_tokens: int | None = None

    # ---- Agent 运行保护（阶段 3）----
    agent_max_steps: int = 30  # 记笔记会占用步数，阶段 4 实测 20 步不够
    # 单个任务的"等效 token"上限：未命中缓存的输入 × 1 + 命中缓存的输入 × 0.1 + 输出 × 2（权重见下）
    agent_token_budget: int = 300_000
    agent_call_max_tokens: int = Field(default=4096, ge=64, le=32768)
    token_weight_cached: float = 0.1
    token_weight_output: float = 2.0
    tool_timeout: float = 60.0  # 单次工具执行的最长秒数
    code_exec_timeout: float = Field(default=30.0, gt=0)  # 入口同时限制在通用工具时限以内
    report_review_enabled: bool = True
    report_review_max_calls: int = Field(default=2, ge=1, le=5)
    report_review_max_tokens: int = Field(default=2048, ge=256, le=8192)

    # 阶段 8：本地研究记忆，读取与写入可整体关闭
    memory_enabled: bool = True
    memory_db_path: Path = PROJECT_ROOT / "workspace" / "memory.sqlite3"
    memory_recall_limit: int = Field(default=3, ge=1, le=5)
    memory_context_max_chars: int = Field(default=6000, ge=1500, le=20000)

    # ---- 上下文管理（阶段 4）----
    # 估算的上下文超过这个 token 数时，把旧的网页原文 / 搜索摘要移出上下文。设为 0 关闭压缩
    # 默认 6 万：压缩只作为"上下文太大"的保护，不作为省钱手段。
    # 有前缀缓存的厂商（DeepSeek 等）上，只追加的历史几乎全部命中缓存、非常便宜，频繁压缩反而更贵（见 ROADMAP 阶段 4）。
    # 没有自动缓存的厂商可以调低（如 15000）。用 python -m agent.simulate 在真实运行上离线评估。
    context_compact_threshold: int = 60_000
    context_keep_recent_turns: int = 2  # 最近几轮的工具结果不压缩

    # ---- 读网页 ----
    # 直接下载失败（403、需 JS 渲染、PDF、网络不通等）时的备用通道：
    #   jina：通过 r.jina.ai 读取。注意：要读的 URL 会发送给第三方服务 Jina
    #   none：不使用备用通道
    fetch_fallback: Literal["jina", "none"] = "jina"
    jina_api_key: str = ""  # 可选。不填为匿名访问（有频率限制）


@dataclass(frozen=True)
class LLMConfig:
    """解析完成、可直接用来创建客户端的配置。"""

    provider: str
    base_url: str
    api_key: str
    model: str
    temperature: float
    timeout: float
    max_retries: int
    max_tokens: int | None = None
    thinking: Literal['enabled', 'disabled'] | None = None


def load_llm_config(
    provider: str | None = None,
    model: str | None = None,
    settings: Settings | None = None,
) -> LLMConfig:
    """合并预设、Settings 和命令行覆盖；默认密钥仅由项目 .env 提供。"""
    s = settings or Settings()
    name = provider or s.llm_provider
    if name not in PROVIDERS:
        raise ValueError(f"未知厂商 {name!r}，可选：{', '.join(PROVIDERS)}")
    preset = PROVIDERS[name]

    api_key = getattr(s, preset.key_field)
    if not api_key:
        env_name = preset.key_field.upper()
        raise ValueError(f"未配置项目 API Key {env_name}。请在本项目 .env 中填写（参考 .env.example）；不再使用全局环境变量中的 Key。")

    # .env 里的 LLM_MODEL / LLM_BASE_URL 是针对 LLM_PROVIDER 写的；
    # 如果命令行临时切换到别的厂商，就不再沿用它们，改用该厂商预设。
    same_provider = name == s.llm_provider
    env_model = s.llm_model if same_provider else None
    env_base_url = s.llm_base_url if same_provider else None

    final_model = model or env_model or preset.default_model
    if not final_model:
        raise ValueError("未指定模型名，请设置 LLM_MODEL 或使用 --model。")

    return LLMConfig(
        provider=name,
        base_url=env_base_url or preset.base_url,
        api_key=api_key,
        model=final_model,
        temperature=s.llm_temperature,
        timeout=s.llm_timeout,
        max_retries=s.llm_max_retries,
        max_tokens=s.llm_max_tokens,
        thinking='disabled' if name == 'deepseek' and final_model == 'deepseek-flash' else None,
    )
