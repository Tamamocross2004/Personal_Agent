# Personal Research Agent

从零手写（不依赖 LangChain）的个人研究助理 Agent：给它一个问题，它自己搜资料、读网页、记笔记、必要时跑代码，最后输出 Markdown 报告。

完整路线图见 [ROADMAP.md](ROADMAP.md)。

## 当前进度

- [x] 阶段 0：环境搭建 + LLM 客户端（多厂商切换、重试、token 统计）
- [x] 阶段 1：最小 Agent 循环（工具注册表 + tool calling 循环 + 基础工具）
- [ ] 阶段 2：搜索 / 读网页 / 写报告
- [ ] 阶段 3：错误处理与健壮性
- [ ] 阶段 4：笔记与上下文管理
- [ ] 阶段 5：规划与反思
- [ ] 阶段 6：代码执行
- [ ] 阶段 7：报告质量
- [ ] 阶段 8：长期记忆
- [ ] 阶段 9：评估

每个阶段完成后打一个 git 标签（`stage-0`、`stage-1`…），可以用 `git diff stage-0 stage-1` 查看某一阶段具体改了什么。

## 快速开始

```bash
# 1. 创建虚拟环境（Python 3.10+）
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS / Linux / Git Bash:
# source .venv/bin/activate

# 2. 安装依赖（含测试依赖）。依赖有更新时重新执行一次即可
python -m pip install -e ".[dev]"

# 3. 配置密钥
cp .env.example .env      # Windows 可用: copy .env.example .env
# 编辑 .env，填入 LLM_PROVIDER 和对应的 API Key

# 4. 运行
python main.py "现在距离 2027 年元旦还有多少天？如果每天读 3 篇论文，到那时一共能读多少篇？"
python main.py                         # Agent 交互模式（每个问题独立运行）
python main.py --chat                  # 纯对话模式（阶段 0，无工具）
python main.py --provider qwen "..."   # 临时切换厂商
python main.py --max-steps 5 "..."     # 调整最大循环步数（默认 10）

# 5. 测试（无需 API Key）
pytest
```

运行时终端会逐步展示 Agent 的执行过程：

```
──────────── Step 1 ────────────
→ 调用 get_current_time()
← 2026-10-17 12:01:09 星期六（Asia/Shanghai，UTC+08:00）
──────────── Step 2 ────────────
→ 调用 days_between(start_date='2026-10-17', end_date='2027-01-01')
← 从 2026-10-17 到 2027-01-01 相差 76 天
──────────── Step 3 ────────────
→ 调用 calculator(expression='76 * 3')
← 76 * 3 = 228
──────────── Step 4 ────────────
╭──────────── 最终回答 ────────────╮
│ 距离 2027 年元旦还有 76 天……       │
╰──────────────────────────────────╯
```

黄色 `→` 是模型请求的工具调用，绿色 `←` 是工具结果，红色表示工具出错（错误会回传给模型，让它自行修正）。

## 支持的模型厂商

| provider | 厂商 | Key 变量 | 默认模型 |
|---|---|---|---|
| `deepseek` | DeepSeek | `DEEPSEEK_API_KEY` | `deepseek-chat` |
| `qwen` | 阿里云百炼 / 通义千问 | `DASHSCOPE_API_KEY` | `qwen-plus` |
| `glm` | 智谱 | `ZHIPU_API_KEY` | `glm-4-plus` |
| `kimi` | 月之暗面 | `MOONSHOT_API_KEY` | `moonshot-v1-32k` |
| `custom` | 任意 OpenAI 兼容服务 | `CUSTOM_API_KEY` | 需设置 `LLM_MODEL` |

> 默认模型名只是起点，各家更新很快，请以官方文档为准，用 `LLM_MODEL` 或 `--model` 覆盖。
> 注意：系统环境变量的优先级高于 `.env`，如果改了 `.env` 不生效，检查是否设置了同名系统变量。

## 可用工具

| 工具 | 作用 | 为什么需要 |
|---|---|---|
| `get_current_time(timezone)` | 当前日期、时间、星期 | 模型不知道"今天"是哪天 |
| `days_between(start_date, end_date)` | 两个日期相差的天数 | 大小月、闰年容易算错 |
| `calculator(expression)` | 安全计算数学表达式 | 模型心算不可靠；基于 AST 白名单实现，不使用 `eval` |

### 如何添加一个新工具

写一个带类型标注和 docstring 的函数，加上 `@tool`，再放进 `tools/__init__.py` 的 `default_registry()` 即可：

```python
from typing import Annotated
from pydantic import Field
from tools.registry import tool

@tool
def word_count(text: Annotated[str, Field(description="要统计的文本")]) -> int:
    """统计文本的字符数。"""   # ← 这段描述会发给模型，决定它何时调用这个工具
    return len(text)
```

注册表会自动根据签名生成 JSON Schema，并在执行前用 pydantic 校验模型给的参数。

## 目录结构

```
agent/
  config.py    # 配置加载：预设 ← .env ← 命令行
  llm.py       # LLM 客户端：OpenAI 兼容协议、tenacity 重试、用量统计
  loop.py      # ★ Agent 主循环：调用模型 → 执行工具 → 回传结果 → 循环
  prompts.py   # system prompt 等提示词
tools/
  registry.py  # 工具注册表：@tool 装饰器、JSON Schema 生成、参数校验、错误转结果
  basic.py     # 基础工具：get_current_time / days_between / calculator
main.py        # CLI 入口：Agent 模式（默认）与纯对话模式（--chat）
tests/         # 用假模型测试，不调用真实 API
  fakes.py     # 剧本化的假模型 ScriptedLLM
workspace/     # 运行产物（已 gitignore）
```
