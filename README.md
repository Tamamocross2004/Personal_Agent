# Personal Research Agent

从零手写（不依赖 LangChain）的个人研究助理 Agent：给它一个问题，它自己搜资料、读网页、记笔记、必要时跑代码，最后输出 Markdown 报告。

完整路线图见 [ROADMAP.md](ROADMAP.md)。

## 当前进度

- [x] 阶段 0：环境搭建 + LLM 客户端（多厂商切换、重试、token 统计）
- [x] 阶段 1：最小 Agent 循环（工具注册表 + tool calling 循环 + 基础工具）
- [x] 阶段 2：搜索 / 读网页 / 写报告（ddgs + trafilatura，分段阅读，报告落盘）
- [x] 阶段 3：错误处理与健壮性（运行日志与回放、循环保护、token 预算、异常回复处理、Jina 备用通道）
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
python main.py "DeepSeek 最新模型支持哪些功能？"
python main.py                         # 研究模式交互（每个问题独立运行）
python main.py --chat                  # 纯对话模式（阶段 0，无工具）
python main.py --provider qwen "..."   # 临时切换厂商
python main.py --max-steps 30 --budget 500000 "..."   # 调整步数 / token 预算
python -m agent.replay                 # 回放最近一次任务的运行日志（不花钱）
python -m agent.replay <任务目录> --full

# 5. 测试（无需 API Key）
pytest
```

运行时终端会逐步展示 Agent 的执行过程：

```
──────────── Step 1 ────────────
→ 调用 web_search(query='DeepSeek GLM Kimi Agent 对比', max_results=5)
← 搜索「DeepSeek GLM Kimi Agent 对比」的结果（共 5 条）：…
──────────── Step 2 ────────────
→ 调用 fetch_url(url='https://blog.example.com/llm-comparison')
← URL: … 标题: … 发布日期: 2026-08-13 正文: 第 0–2911 字符…
→ 调用 fetch_url(url='https://zhuanlan.zhihu.com/p/…')
← ERROR: FetchError: HTTP 403：页面无法访问，请换一个来源
──────────── Step 3 ────────────
→ 调用 write_report(content='# DeepSeek、GLM、Kimi Agent 能力对比\n\n## 摘要…(共 3120 字符), filename='report.md')
← 报告已保存：workspace/20261017-132319-对比-DeepSeek-GLM-Kimi-的-Agent-能/report.md
──────────── Step 4 ────────────
╭──────────── 最终回答 ────────────╮
│ ……                                │
╰──────────────────────────────────╯
```

紫色 `⚠` 是系统干预（预算提醒、拦截重复调用、截断处理等），每次模型调用下方的灰色小字是本次的 token、缓存命中量和耗时。

每个问题有自己的目录 `workspace/<时间>-<问题摘要>/`（已被 git 忽略）：

| 文件 | 内容 |
|---|---|
| `*.md` | 研究报告 |
| `trace.jsonl` | 运行日志，每行一个事件（模型调用、工具调用、结果、耗时、token……），用 `python -m agent.replay` 回放 |
| `messages.json` | 结束时完整的对话历史，排查“模型为什么这么做”时看这里 |

运行中按 **Ctrl+C** 会立即停止当前任务，日志照常保存。

## 运行保护

| 情况 | Agent 的反应 |
|---|---|
| 步数用完 / 预计下一次调用会超出 token 预算 / 反复重复调用 | 不再提供工具，强制模型基于已有资料收尾；没写报告就把收尾回答存为 `report.md` |
| token 用到预算 80% | 提醒模型尽快写报告 |
| 同一工具、同样参数再次调用 | 成功过的不再执行（失败的允许重试至多 3 次） |
| 工具执行超过 `TOOL_TIMEOUT` 秒 | 放弃本次调用，告诉模型换个方法 |
| 回复被截断 | 写报告被截断时提示分段写（`mode="append"`）；文字回答被截断时让模型接着写 |
| 内容审核拦截 | 移除最近读入的网页内容后重试一次 |
| 上下文超长、Key 错误等 | 干净结束并说明原因 |
| 网页直接下载失败（403、需 JS、PDF、网络不通） | 改用 Jina Reader 读取（`FETCH_FALLBACK=none` 可关闭） |

相关参数都在 `.env` 中（见 `.env.example`）。

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
| `web_search(query, max_results)` | 联网搜索，返回标题 / URL / 摘要 | 模型的知识有截止日期；当前使用 ddgs（DuckDuckGo 等，免费无 Key） |
| `fetch_url(url, start)` | 读取网页正文（去广告导航），长文分段；失败时可经 Jina Reader 读取 | 原始 HTML 太长太脏；分段阅读避免一次撑爆上下文 |
| `write_report(content, filename, mode)` | 把 Markdown 报告存到任务目录，`mode="append"` 可分段追加 | 产出最终成果；文件名经过清洗，只能写进任务目录 |
| `get_current_time(timezone)` | 当前日期、时间、星期 | 模型不知道"今天"是哪天 |
| `days_between(start_date, end_date)` | 两个日期相差的天数 | 大小月、闰年容易算错 |
| `calculator(expression)` | 安全计算数学表达式 | 模型心算不可靠；基于 AST 白名单实现，不使用 `eval` |

### 如何添加一个新工具

写一个带类型标注和 docstring 的函数，加上 `@tool`，再放进 `tools/__init__.py` 的 `default_registry()` 即可。需要依赖外部资源（HTTP 客户端、目录等）的工具，参考 `tools/search.py` 写成 `make_xxx(...)` 工厂函数：

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

### 更换搜索服务

`tools/search.py` 里的 `SearchBackend` 是搜索后端接口，只需实现 `search(query, max_results) -> list[SearchResult]`。写一个新类（例如博查、Tavily），在 `default_registry()` 里换掉 `DDGSBackend()` 即可，Agent 和 prompt 都不用改。

> ddgs 在国内网络下可能不稳定；如果频繁报“搜索服务出错”，可以考虑换成国内的搜索 API。

> 隐私提示：`FETCH_FALLBACK=jina`（默认）时，直接读取失败的网页 URL 会发送给第三方服务 Jina（r.jina.ai）。研究涉及敏感链接时请设为 `none`。

## 目录结构

```
agent/
  config.py    # 配置加载：预设 ← .env ← 命令行
  llm.py       # LLM 客户端：OpenAI 兼容协议、tenacity 重试、用量统计
  loop.py      # ★ Agent 主循环：调用模型 → 执行工具 → 回传结果 → 循环，以及各种运行保护
  errors.py    # 模型接口错误分类（上下文超长 / 内容审核 / 鉴权 / 其它）
  prompts.py   # system prompt 与各种系统提示（研究模式会注入今天的日期）
  trace.py     # 运行日志 trace.jsonl / messages.json
  replay.py    # python -m agent.replay：回放运行日志
  display.py   # 终端展示（实时与回放共用）
  workspace.py # 每个任务的工作目录
tools/
  registry.py  # 工具注册表：@tool 装饰器、JSON Schema 生成、参数校验、错误转结果
  basic.py     # 基础工具：get_current_time / days_between / calculator
  search.py    # web_search + 可替换的搜索后端（当前 ddgs）
  fetch.py     # fetch_url：下载、正文提取、分段、缓存、Jina Reader 备用通道
  report.py    # write_report：安全地写报告文件
main.py        # CLI 入口：研究模式（默认）与纯对话模式（--chat）
tests/         # 用假模型测试，不调用真实 API
  fakes.py     # 剧本化的假模型 ScriptedLLM
workspace/     # 运行产物（已 gitignore）
```
