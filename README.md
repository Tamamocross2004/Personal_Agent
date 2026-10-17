# Personal Research Agent

从零手写（不依赖 LangChain）的个人研究助理 Agent：给它一个问题，它自己搜资料、读网页、记笔记、必要时跑代码，最后输出 Markdown 报告。

完整路线图见 [ROADMAP.md](ROADMAP.md)。

## 当前进度

- [x] 阶段 0：环境搭建 + LLM 客户端（多厂商切换、重试、token 统计）
- [ ] 阶段 1：最小 Agent 循环（tool calling）
- [ ] 阶段 2：搜索 / 读网页 / 写报告
- [ ] 阶段 3：错误处理与健壮性
- [ ] 阶段 4：笔记与上下文管理
- [ ] 阶段 5：规划与反思
- [ ] 阶段 6：代码执行
- [ ] 阶段 7：报告质量
- [ ] 阶段 8：长期记忆
- [ ] 阶段 9：评估

## 快速开始

```bash
# 1. 创建虚拟环境（Python 3.10+）
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS / Linux / Git Bash:
# source .venv/bin/activate

# 2. 安装依赖（含测试依赖）
pip install -e ".[dev]"

# 3. 配置密钥
cp .env.example .env      # Windows 可用: copy .env.example .env
# 编辑 .env，填入 LLM_PROVIDER 和对应的 API Key

# 4. 运行
python main.py "用三句话介绍一下你自己"
python main.py                         # 多轮对话
python main.py --provider qwen "你好"  # 临时切换厂商

# 5. 测试（无需 API Key）
pytest
```

## 支持的模型厂商

| provider | 厂商 | Key 变量 | 默认模型 |
|---|---|---|---|
| `deepseek` | DeepSeek | `DEEPSEEK_API_KEY` | `deepseek-chat` |
| `qwen` | 阿里云百炼 / 通义千问 | `DASHSCOPE_API_KEY` | `qwen-plus` |
| `glm` | 智谱 | `ZHIPU_API_KEY` | `glm-4-plus` |
| `kimi` | 月之暗面 | `MOONSHOT_API_KEY` | `moonshot-v1-32k` |
| `custom` | 任意 OpenAI 兼容服务 | `CUSTOM_API_KEY` | 需设置 `LLM_MODEL` |

> 默认模型名只是起点，各家更新很快，请以官方文档为准，用 `LLM_MODEL` 或 `--model` 覆盖。

## 目录结构

```
agent/
  config.py   # 配置加载：预设 ← .env ← 命令行
  llm.py      # LLM 客户端：OpenAI 兼容协议、tenacity 重试、用量统计
main.py       # CLI 入口
tests/        # 用假客户端测试，不调用真实 API
workspace/    # 运行产物（已 gitignore）
```
