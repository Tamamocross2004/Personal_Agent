# Personal Research Agent

从零手写（不依赖 LangChain）的个人研究助理 Agent：给它一个问题，它自己搜资料、读网页、记笔记、必要时跑代码，最后输出 Markdown 报告。

完整路线图见 [ROADMAP.md](ROADMAP.md)。

后续已规划“指定文件夹资料助手”（阶段15—19）：目录只读浏览、多格式解析、增量索引、带引用问答和结果导出；当前尚未实施，详见路线图。阶段12的未验收状态保留。

已接入可选的本地原始文档 RAG：桌面“原始资料知识库”导入资料，研究时勾选“知识库”；CLI 使用 `main.py --knowledge`。安装、命令、云端嵌入用量及版本边界见 [RAG 使用说明](docs/rag-guide.md)。这与历史研究记忆分别管理，不改变阶段 12 尚未通过的状态。

知识库混合/向量检索已接入可开关的 cross-encoder 重排，候选默认20个；嵌入、重排、聊天用量分别记录。实测协议与结果见 [重排实测记录](docs/rerank-verification-20261021.md)。

完整100,902条公开中文短文本、固定100题的配对实验中，开启重排后Hit@4由52%增至63%，nDCG@4由0.4547增至0.5744，平均检索由1.197秒增至2.045秒。原标注、退步题和中断记录均保留，详见[十万条语料重排对照](docs/large-rerank-verification-20261021.md)；此隔离基准不放宽正式桌面的导入限制。

后续已按顺序完成[有界读取与200组返回等价核对](docs/rag-performance-20261021.md)、[官方长文档检索](docs/rag-long-verification-20261021.md)、[另外100题的召回配置对照](docs/rag-recall-verification-20261021.md)及[五题端到端诊断](docs/rag-endtoend-verification-20261021.md)。扩大候选未提升这批题的最终命中率；五题端到端均未获得完整正常交付，数字/引用校验、工具协调和审稿预算仍有问题。913项工程回归通过不替代内容验收，阶段12仍未通过。

2026-10-21 已完成通用数字/标识符/引用定位、引用结构反馈和交付预算修复，932项回归通过。另冻结四道新题单次实测：1题正常结束并完成审稿，3题预算收尾并保留未审稿草稿，篇幅组织、引用协调及部分语义表述仍有问题；没有重跑覆盖旧失败或宣布阶段12通过。见[修复与新题诊断](docs/report-mechanisms-verification-20261021.md)。

后续已补报告错误汇总、拒稿整理时保留引用问题、真实工具计数与读取范围快照。另四题单次实测：1题正常完整审稿、1题预算收尾中保存并完整审稿、2题关联或审稿未完成。实测后另修通用标题分隔符与候选/保存稿哈希区分，没有重跑改成绩；阶段12仍未验收。见[报告提交修复与验证](docs/delivery-fix-verification-20261021.md)。

最终版本另用全新Git/PostgreSQL资料做四题单次复检：1题正常完整审稿、1题预算收尾完整审稿、1题未保存、1题partial。965项工程回归通过；新发现审稿混淆历史快照与当前计数、摘要截取与原始截断，并仍有定位符误拦和引用提交往返。生产代码及标准未变，阶段12仍未验收。见[新资料复检](docs/generalization-verification-20261021.md)。

## 当前进度

最新通用修复已补实际送达快照的时间边界、标题支持的定位符数字、重复片段复用/检索增量、拒稿整理和预算停止答复来源。978项回归通过；另三份新官方文档、四道新题单次实测：1题正常完整审稿、2题partial、1题修订后stale/not_reviewed。审稿仍混淆未展示与未送达，范围表述与关联修订仍有问题，阶段12仍未验收。[修复与新题测试](docs/boundary-fix-verification-20261021.md)

- [x] 阶段 0：环境搭建 + LLM 客户端（多厂商切换、重试、token 统计）
- [x] 阶段 1：最小 Agent 循环（工具注册表 + tool calling 循环 + 基础工具）
- [x] 阶段 2：搜索 / 读网页 / 写报告（ddgs + trafilatura，分段阅读，报告落盘）
- [x] 阶段 3：错误处理与健壮性（运行日志与回放、循环保护、token 预算、异常回复处理、Jina 备用通道）
- [x] 阶段 4：笔记与上下文管理（save_note / read_notes、旧网页原文压缩、运行对比）
- [x] 阶段 5：规划与反思（按维度规划、进度跟踪、报告自动检查、步数提醒与收尾阶段；2026-10-18 复测结果见 ROADMAP）
- [x] 阶段 6：代码执行（run_python、超时清理、计算与绘图；验收记录见 ROADMAP）
- [x] 阶段 7：报告质量（来源分级、引用/图片检查、独立审稿与修订；结果见 ROADMAP）
- [x] 阶段 8：长期记忆（SQLite/FTS5、中英文检索、历史背景注入与管理命令；验收见 ROADMAP）
- [x] 阶段 9：评估（固定题集、离线回归、真实评估、结果复查和基线对比；持续迭代）
- [x] 阶段 10：任务约定、范围与篇幅控制（2026-10-19 完成范围验收；边界与复测见下方记录）
- [x] 阶段 11：关键结论证据索引、假设记录与版本绑定（2026-10-19；仅认证选定关联可定位）
- [ ] 阶段 12：独立题集与内容稳定性验收（已完成三轮评估，尚未通过）
- [x] 阶段 13：本机单任务检查点、显式恢复与累计预算（首版工程检查通过，范围见恢复说明）

每个阶段完成后打一个 git 标签（`stage-0`、`stage-1`…），可以用 `git diff stage-0 stage-1` 查看某一阶段具体改了什么。

2026-10-18 审稿收敛修复：单测 **433 passed**、离线 **8/8**；原失败联网题另测 **3/3**，完整三轮原始 **24/27**，修正条件/否定语境误判后只读复查 **27/27**。27 份最终报告均完成审稿、无问题项；两次审稿上限未放宽。见 [最新复检记录](docs/review-convergence-20261018.md)，此前失败基线见 [上一轮记录](docs/fix-acceptance-20261018.md)。

阶段 10 最新记录：单测 **579 passed**，离线 **8/8 + 5/5 + 1/1**；冻结版本原 v2 一轮 **9/9**、范围题三轮各 **7/7**、补充题 **2/2**，合计原始 **32/32**，无需重评分。15 份明确篇幅上限全部满足，32 份最终稿均完成审稿、无问题项且哈希匹配。本次使用项目 `.env` Key；指定方法的计算和报告检查、长度修订反馈、费用基准权重已补齐。详见 [阶段 10 收尾验收](docs/stage10-scope-acceptance-20261019.md)。此前未通过的 [10 月 18 日记录](docs/stage10-acceptance-20261018.md) 和本轮开发失败均保留；固定题集通过不认证任意语义或全部统计解释。

2026-10-19 短报告后续优化：缺章稿先修结构再消耗审稿次数，正常结束前最多提醒一次修复，保持原步数和预算。单测 **594 passed**，用户原始220字提问三次均通过（197、216、218字），兼容性自动结果原v2 **9/9**、范围 **7/7**。当时回放另发现两处数值解释漏检，**不能称全部报告内容通过验收**；后续已针对已知算式及型号内部比例句式补修复，见 [数值修复](docs/numeric-followup-fix-20261019.md) 和 [阶段 11 验收](docs/stage11-acceptance-20261019.md)。见 [结构修复与剩余问题](docs/short-report-structure-fix-20261019.md)。

阶段 11 最新记录：单测 **662 passed**，证据/原 v2/范围离线 **5/5、8/8、5/5**。冻结实现真实模型证据两轮各 **6/6**、原v2 **9/9**、范围三轮各 **7/7**；结果与人工核对边界见 [阶段 11 验收](docs/stage11-acceptance-20261019.md) 和 [机器记录](docs/stage11-evaluation-20261019.json)。证据与审稿绑定同一报告版本；`mapped` 仅表示选定原句与所存依据可定位，类型标注和语义支持仍需核对。

阶段 12 首轮评估已完成、尚未验收：新增开发/保留题集、冻结配置、三轮评估、故障注入与核对入口 `python -m agent.validation`。单测718 passed；新题三轮正常任务10/10、10/10、9/10，故障题均2/3；自动35/39，回放另发现语义和来源关联问题。详情见 [阶段12结果](docs/stage12-results-20261020.md) 与 [预定门槛及命令](docs/stage12-plan.md)。本批已揭盲，后续修复需新保留集；不把自动分数当内容验收。

阶段12通用机制改进：支持JSON产物的实际读取、字段类型与文件版本证据，以及网页结论声明范围的覆盖检查。只改通用机制，不为已知题目添加答案模板；开发集检查不代表独立验收。见[实施及验证记录](docs/stage12-mechanisms-20261020.md)。

阶段12最新独立三轮（第四批）：正常8/10、10/10、10/10，故障各3/3，原始37/39（1失败、1依赖跳过），**未达预定门槛**；755项测试通过。运行期间未改实现/提示/评分，保留题目歧义与助手发现，尚无外部人工结论。见[结果说明](docs/stage12-independent4-results-20261020.md)及[人工核对入口](docs/stage12-independent4-human-review-20261020.md)。

复核工具后续优化：兼容跳过项的无产物空值，新增绑定原始结果的模板导出、pending进度和逐题错误提示，中断评估保留部分产物版本。800项测试通过；第四批456个原文件哈希不变，人工表仍39项pending，原始37/39及未验收结论不变。没有新增API消耗，见[优化记录](docs/review-tool-compatibility-20261020.md)。

按用户选择，助手已另存39项复核意见：24 pass、2 fail、13 uncertain，无待填写项；标明coding_assistant，不冒充人工盲审，不再等待用户填表。原始结果和阶段12未通过状态保留。见[本轮复核](docs/assistant-review-followup-20261020.md)。

接续优化完成：自动保存未完成结果时明确程序保存事实与停止前模型原文，记录来源/文件版本并接入桌面和回放；原文不改写。813项测试、构建与15组隔离界面流程通过，没有真实模型调用。见[来源与状态说明](docs/fallback-save-provenance-20261020.md)。

2026-10-20继续修复：新增真实工具返回observed/execution、失败URL操作说明、逐项历史limits反馈、已读范围和JSON字段类型审稿，修正引用ID/数组数字误拦。全量735 passed、前端构建通过；冻结另一批三轮正常各10/10，故障3/3、3/3、2/3，原始38/39，一份网络报告skipped_limit且内容核对仍有问题，**阶段12仍未验收**。后续数字格式修正仅离线及只读验证，未再跑新三轮。见[修复复检](docs/stage12-fixes-20261020.md)与[机器记录](docs/stage12-fix-evaluation-20261020.json)。原始35/39与所有失败均保留。

2026-10-19 应用户要求提前增加独立桌面首版：Electron + React + TypeScript，FastAPI 复用现有研究入口。支持会话、真实执行事件、报告/CSV/图片、证据与质量面板、排队和停止；API Key 仍只读取项目 `.env`。使用与部署见 [桌面应用说明](docs/desktop-app.md)，早期检查见 [桌面验收](docs/desktop-acceptance-20261019.md)。阶段13提供本机检查点显式恢复。

阶段13首版：检查点、累计预算、CLI查看/停止/恢复及桌面恢复入口完成；776项测试、真实子进程终止与隔离界面检查通过。只自动恢复已提交边界，结果不确定时停止待核对，旧任务无检查点不能直接恢复。此工程交付不改变阶段12未通过状态。见[恢复说明与离线演示](docs/stage13-recovery-20261020.md)。

阶段14本地桌面首版完成工程验收：777项测试与构建通过；隔离窗口完成提交、报告/图表/证据/用量、停止/恢复、故障及进程退出检查。修复新建会话清空新输入、事件断连不重连、历史滚动和文件预览等问题。模型为离线夹具，不新增API消耗，不代替阶段12内容验收。见[阶段14记录](docs/stage14-desktop-acceptance-20261020.md)。

桌面布局后续修复：历史会话较多时，输入/发送及设置入口曾被裁到窗口外，已固定栏高度并补充三种窗口尺寸与长历史回归，见 [修复记录](docs/desktop-layout-fix-20261019.md)。

桌面调用与成本统计：右侧“用量”查看每轮规划/研究/审稿调用、请求重试、输入/输出token、缓存命中及人民币参考估算；运行设置中可保存费率，新任务冻结费率快照，旧任务明确标注当前费率回溯估算。缺失项显示未计价，不当作零；这是任务级模型token估算，不是账户账单。见 [使用说明](docs/desktop-app.md#调用与成本统计) 与 [验证记录](docs/usage-acceptance-20261019.md)。

## 桌面应用

本机部署后双击桌面 **Personal Agent**，或在项目根目录执行：

```powershell
.\scripts\launch-desktop.ps1
```

首次安装或更新源码：`.\scripts\install-desktop.ps1`（需要项目 `.venv` 和 Node.js 22.19+）。网络需要代理时可传 `-ProxyUrl http://127.0.0.1:7890`，使用你实际可用的地址。界面左侧管理会话，中间发起研究，右侧查看报告、文件、依据与质量；查看旧任务不调用模型。

## 快速开始

```bash
# 1. 创建虚拟环境（Python 3.10+）
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS / Linux / Git Bash:
# source .venv/bin/activate

# 2. 安装依赖（含测试与RAG依赖）。依赖有更新时重新执行一次即可
python -m pip install -e ".[dev,rag]"

# 3. 配置密钥
cp .env.example .env      # Windows 可用: copy .env.example .env
# 编辑 .env，填入 LLM_PROVIDER 和对应的 API Key

# 4. 运行
python main.py "DeepSeek 最新模型支持哪些功能？"
python main.py                         # 研究模式交互（每个问题独立运行）
python main.py --chat                  # 纯对话模式（阶段 0，无工具）
python main.py --provider qwen "..."   # 临时切换厂商
python main.py --no-memory "..."       # 本次不读写长期记忆
python main.py --max-steps 40 --budget 400000 "..."   # 调整步数 / 等效 token 预算（默认 30 步 / 30 万）
python -m agent.replay                 # 回放最近一次任务的运行日志（不花钱）
python -m agent.replay <任务目录> --full
python -m agent.evidence <任务目录>    # 离线核对关键结论关联与版本，不调用 API
python -m agent.compare --last 2       # 对比最近两次运行的 token、缓存、笔记、引用等指标
python -m agent.simulate <任务目录>    # 离线评估不同压缩阈值的成本
python -m agent.cache_probe            # 测试不同请求写法能否命中厂商的前缀缓存（约 5 万输入 token）
python -m agent.memory list            # 查看已保存的研究记忆（不需要 API Key）
python -m agent.evaluate list          # 查看离线和真实模型题集（不调用 API）
python -m agent.evaluate offline       # 离线固定回归，输出 JSON / Markdown 汇总

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
← URL: … 标题: … 页面日期（元数据，类型未核实）: 2026-08-13 正文: 第 0–2911 字符…
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

黄色 `→` 是模型请求的工具调用，绿色 `←` 是工具结果，红色表示工具出错（错误会回传给模型，让它自行修正）。

紫色 `⚠` 是系统干预（预算提醒、拦截重复调用、截断处理等），每次模型调用下方的灰色小字是本次的 token、缓存命中量和耗时。

每个问题有自己的目录 `workspace/<时间>-<问题摘要>/`（已被 git 忽略）：

| 文件 | 内容 |
|---|---|
| `*.md` | 研究报告 |
| `evidence.json` | 本次实际送达的用户材料、网页片段、成功计算及有日期历史的有界索引 |
| `report.evidence.json` | 选定结论原句、依据摘录、类型/假设/限制及报告版本；mapped 不认证事实正确 |
| `notes.md` | 研究笔记（`save_note` 记录的要点和来源） |
| `plan.json` | 研究计划（按问题复杂度拆成 1–6 个子任务，简单问题优先 1–2 个；`update_plan` 更新状态） |
| `task_contract.json` | 从本次请求保守提取的任务约定；原始问题仍有效，历史和网页不增加指令 |
| `trace.jsonl` | 运行日志，每行一个事件（模型调用、工具调用、结果、耗时、token……），用 `python -m agent.replay` 回放 |
| `messages.json` | 结束时保留的模型上下文，可能已整理/压缩；完整原始调用与反馈看 trace.jsonl |
| `memory_context.json` | 本次召回的历史记录 ID 与实际注入的背景；无历史命中时不生成 |
| `<报告名>.quality.json` | 最终机械检查：计划、章节、引用编号、来源及图片，含对应报告文本的 SHA-256 |
| `<报告名>.contract.json` | 篇幅计量、明确长度错误、范围观察和简述扩展理由，关联请求及报告哈希 |
| `<报告名>.review.json` | 最新语义审稿结果、状态及报告文本 SHA-256 |
| `<报告名>.reviews.jsonl` | 历次审稿与跳过记录，避免修订后误用旧审稿结论 |

运行中按 **Ctrl+C** 会立即停止当前任务，日志照常保存。

## 任务范围与篇幅

规划、主循环、报告保存和审稿共享本次任务约定。支持明确的阿拉伯数字上限，例如“全文不超过300字”“正文最多350字符”“全文最多100个英文单词”；明确更正的新要求优先。完整稿超限会在保存和审稿之前被拒绝，反馈实际数量、差额与计量单位，原报告保持原样。追加和局部 patch 也检查合并后的全文。

“字/字符”按 Markdown 可见文本的非空白 Unicode 字符计，含英文、数字和标点；“中文字符”只计汉字；英文单词按字母词计，保留词内连字符和撇号。标题、表格、代码均计入。正文限制排除明确的参考资料章节，全文限制包含参考资料。隐藏链接目标不计，可见 URL 计入。复杂排除条件、中文数字和模糊范围尚不自动转换成硬约束，应结合原始问题核对。

“简短/简述”使用约500个可见正文字符的默认软目标。超过两倍时，完整稿需用 `write_report` 的 `brief_extension_reason` 说明哪些必要内容需要保留（至少20字符），供审稿判断；这项提交策略不是用户字数上限，理由也不代表范围已经合规。详细任务不套用这一软目标。

明确禁止联网时不注册搜索、网页读取和网页笔记工具；本地 Python 仍适用已有执行边界。对于明确指定的 Wilson、Wald 等已知方法，规划会去除额外方法任务，代码在执行前检查方法标识及带计算参数的输出标签，报告保存前检查额外方法的数值结果。明确要求两种方法时同时保留；否定说明、概念提及与概率定义域不等同额外结果。检查使用有限名称与模板，不能识别任意别名或未命名计算，也不是 Python 沙箱。独立性等前提仍形成可定位观察，范围、遗漏和科学解释须结合原问题及计算证据审查。

```powershell
# 本地体验：报告及检查记录在本次任务目录，打开 report.md 和 report.contract.json
python main.py --no-memory "只依据给定虚构资料：甲称X为128K，乙称256K，均未说明版本和条件。简述冲突与未知，不联网、不做统计分析。全文不超过220字。"

# 新题集独立于原 v2；--suite 放在子命令之前，list/offline 不调用模型
python -m agent.evaluate --suite agent/scope_cases.json list
python -m agent.evaluate --suite agent/scope_cases.json offline
```

## 运行保护

| 情况 | Agent 的反应 |
|---|---|
| 步数用完 / 预计下一次调用会超出 token 预算 / 反复重复调用 | 进入收尾阶段：只允许 `read_notes` / `read_evidence` / `write_report` / `update_plan`，最多再调用 3 次模型；仍没写报告就把收尾回答存为 `report.md` |
| token 用到预算 80% | 提醒模型尽快写报告 |
| 只剩 6 步 | 提醒模型停止搜索、开始收尾，并列出计划中未完成的子任务（步数上限也写在 system prompt 里） |
| 调用 `write_report` 后 | 检查计划、章节、引用编号、证据来源和本地图片；默认追加独立模型审稿，将意见交回主循环修订 |
| 同一工具、同样参数再次调用 | 成功过的不再执行（失败的允许重试至多 3 次） |
| 工具执行超过 `TOOL_TIMEOUT` 秒 | 放弃本次调用，告诉模型换个方法 |
| 回复被截断 | 写报告被截断时提示分段写（`mode="append"`）；文字回答被截断时让模型接着写 |
| 内容审核拦截 | 移除最近读入的网页内容后重试一次 |
| 上下文超长、Key 错误等 | 干净结束并说明原因 |
| 网页直接下载失败（403、需 JS、PDF、网络不通） | 改用 Jina Reader 读取（`FETCH_FALLBACK=none` 可关闭） |

相关参数都在 `.env` 中（见 `.env.example`）。

**token 预算按“等效 token”计算**，尽量反映真实花费，而不是原始 token 数：

```
等效 token = 未命中缓存的输入 × 1 + 命中缓存的输入 × 0.1 + 输出 × 2
```

有前缀缓存的厂商上，研究任务 80% 以上的输入都命中缓存（价格只有几十分之一），按原始 token 计预算会过早用完。
默认权重比 DeepSeek 实际价格比例（约 0.02 : 1 : 4）保守，换到其他厂商也不易低估花费；可用 `TOKEN_WEIGHT_CACHED`、`TOKEN_WEIGHT_OUTPUT` 调整。

研究模式会为一次写作和一次审稿预留估算额度，所有规划、审稿和收尾请求都经过预算预检；估算输入与最小输出放不下时停止派发，保留未完成状态。`AGENT_CALL_MAX_TOKENS`默认4096，每次实际输出上限还取客户端配置、用途上限和剩余估算额度中的最小值。分词、缓存和工具结果仍可能超估，实际消耗照实记账，这不是服务商账单硬上限。

收尾阶段保持同样的工具说明书、不加 `tool_choice`，由代码拒绝不允许的工具，以尽量复用缓存前缀。
实测在 DeepSeek 上，“不发工具”和“工具 + `tool_choice="none"`”这两种写法都几乎不命中缓存；`tool_choice="none"` 现在只在收尾轮数用完时作为最后手段。
换厂商时可以先跑 `python -m agent.cache_probe` 看看哪种写法能命中。

## 上下文管理

研究任务的每一轮都要把全部历史重新发给模型，其中网页原文占大头。

1. 模型每读完一个网页就用 `save_note` 记要点，报告以笔记为依据（笔记的来源必须是读过的网页）；
2. 上下文估算超过 `CONTEXT_COMPACT_THRESHOLD`（默认 60000 tokens）时，较早的网页原文会被替换成一行说明（终端显示紫色“压缩上下文”提示），搜索结果只保留标题和 URL；
3. 需要时模型可以再次 `fetch_url` 读原文（有缓存，不会重新下载）。

**压缩是为了控制上下文大小，不是为了省钱。** DeepSeek 等厂商有自动前缀缓存：历史只往后追加时几乎全部命中缓存（命中价约为未命中的 1/50），而压缩会改动历史、让之后的缓存失效。实测在 DeepSeek 上，按 15000 压缩虽然让累计 token 少了一半，按缓存计价的输入成本反而高了 24%～35%。所以默认阈值设得较高，只在上下文真的很大时才起作用；没有自动缓存的厂商可以调低。

在自己的运行上评估不同阈值（离线重放，不花钱）：

```bash
python -m agent.simulate <任务目录>
python -m agent.simulate <任务目录> --thresholds 0 20000 60000 --hit-price 0.02 --miss-price 1
```

详见 ROADMAP 阶段 4。设为 0 可关闭压缩。

## 支持的模型厂商

| provider | 厂商 | Key 变量 | 默认模型 |
|---|---|---|---|
| `deepseek` | DeepSeek | `DEEPSEEK_API_KEY` | `deepseek-flash` |
| `qwen` | 阿里云百炼 / 通义千问 | `DASHSCOPE_API_KEY` | `qwen-plus` |
| `glm` | 智谱 | `ZHIPU_API_KEY` | `glm-4-plus` |
| `kimi` | 月之暗面 | `MOONSHOT_API_KEY` | `moonshot-v1-32k` |
| `custom` | 任意 OpenAI 兼容服务 | `CUSTOM_API_KEY` | 需设置 `LLM_MODEL` |

> 默认模型名只是起点，各家更新很快，请以官方文档为准，用 `LLM_MODEL` 或 `--model` 覆盖。
> API Key 默认只从本项目 `.env` 读取，包括模型厂商与可选 Jina Key；不会被 Windows/进程环境变量覆盖。项目 Key 缺失或留空时不回退全局 Key，模型调用会明确报错。模型、地址、预算等非密钥参数仍保持“环境变量高于 `.env`”，命令行模型/厂商选项优先。程序内显式传入的 Settings 参数仍可用于测试和受控调用。

DeepSeek 默认 `deepseek-flash` 显式发送 `thinking: disabled`，保持本项目原先的非思考运行意图。日志同时记录 `requested_model` 与服务端返回的 `response_model`；评估汇总为 `model_identity`。模型名称不锁定服务端权重版本，旧日志缺少返回模型信息时保持未知，不补写推测值。

## 可用工具

| 工具 | 作用 | 为什么需要 |
|---|---|---|
| `web_search(query, max_results)` | 联网搜索，返回标题 / URL / 摘要 | 模型的知识有截止日期；当前使用 ddgs（DuckDuckGo 等，免费无 Key） |
| `fetch_url(url, start, query, length)` | 读取网页正文；用关键词定位，再按偏移精读；失败时可经 Jina Reader 读取 | 缓存全文，定位时只返回最多 4 个有界片段，减少长文逐页阅读 |
| `save_note(content, source_url, topic)` | 记录从网页提炼的要点 | 网页原文稍后会被移出上下文，笔记摘要与原始证据索引分别有界保留；来源必须是读过的网页 |
| `read_notes()` | 读取全部笔记 | 回顾摘要，关键结论仍需核对原始证据 |
| `read_evidence(source_id, query)` | 重看本次保留证据 ID 与摘录 | 不读取未送达的缓存全文 |
| `write_report(content, filename, mode, complete, replacements, base_sha256, claims)` | 保存报告，append 分段追加，patch 按报告版本精确替换 | 完整稿保存前核对表头和数值；中间草稿 `complete=false` 不消耗审稿次数 |
| `run_python(code)` | 用独立 Python 子进程计算数据、生成 CSV / PNG，返回退出码和有界 stdout/stderr | 复杂计算可复现，报告可引用实际生成的图表 |
| `get_current_time(timezone)` | 当前日期、时间、星期 | 模型不知道"今天"是哪天 |
| `days_between(start_date, end_date)` | 两个日期相差的天数 | 大小月、闰年容易算错 |
| `calculator(expression)` | 安全计算数学表达式 | 模型心算不可靠；基于 AST 白名单实现，不使用 `eval` |

长网页可以直接调用 `fetch_url(url, query="context manager")`，无需先读第一页。关键词按字面匹配、忽略大小写；返回正文字符偏移和附近上下文。
需要更多细节时，用 `fetch_url(url, start=命中附近偏移, length=2000)` 精读，`length` 为 200–6000；继续定位用返回的 `start` 与相同关键词。
同一 URL 的定位与精读共享下载缓存。命中片段不代表已阅读全文，未命中不证明该事实不存在，应换同义词或分段核对。

报告修订保留用户指定的文件名、表头和列顺序；审稿同时接收本次原始问题、有界历史背景及本次运行中同一报告的前轮意见，聚焦有实质影响的事实、证据与计算问题。
历史背景和前轮意见仍是待核对数据，不能当作当前事实或强制修改指令；复审不会载入其他任务的审稿记录。
审稿另接收本轮成功读取的网页原文片段与实际修订差异，减少仅凭笔记摘要误判、反复要求相反修改。网页片段与差异均有容量上限和省略标记，不表示已读完整网页。
修订优先使用 `mode="patch"`：content 留空，base_sha256 使用最新审稿反馈中的哈希，replacements 为 `[{"old":"原文","new":"修正文本","expected_occurrences":1}]`。版本过期、匹配次数不符或范围重叠会整批拒绝；通过后仍执行机械检查及独立审稿，不复用旧稿通过状态。
自动检查和语义审稿均无问题后直接收尾，避免为润色反复改写。审稿失败、额度耗尽或最终文件哈希改变时仍如实标记，不视为通过。
对于问题中“表头/列名”后以 `|` 分隔的明确英文字段（如 `model | input_cost | output_cost | total_cost`），完整报告保存前会核对正文 Markdown 表头及列顺序。
缺失、翻译或重排会拒绝本次保存，保留原文件并返回修正提示；代码块中的示例表格不能代替正文。该规则不推断任意中文或自然语言格式约定。
分段写作中间段用 `complete=false`，最后一次追加用 `complete=true`；草稿标记为未审稿，不能使用旧版本的通过状态。
完整稿还会用 Decimal 核对支持的显式四则算式（含连算、括号、百分数和末位四舍五入），以及规定的 `model | input_cost | output_cost | total_cost` 主表与当前 `costs.csv` 是否一致。
检查失败时保留原文件；历史场景应使用明确不同的列名。该检查只支持有限的数字表达式和不超过 50 行、12,000 字符的单场景 CSV，不核验任意文字结论或数据来源；代码/引用块不按报告算术断言处理。
对 CSV 中已知型号的明确“X 的输入/输出单价为 Y 的 n 倍”陈述也会逐项复算；“输入、输出均为 n 倍”必须两项都成立。该规则只覆盖支持的陈述模板，不等于任意自然语言数值核验。

当前问题明确写出“无需联网”“不需要联网”“不要联网”等常见中文短语时，研究工具集中不提供 `web_search`、`fetch_url` 和网页 `save_note`；用户给定的固定数据直接用于计算。
此限制针对研究工具注册，不是代码执行的网络沙箱；`run_python` 的既有边界见下文。

### 固定题集评估

```powershell
# 不调用 API：8 组固定回归（需要安装 dev 测试依赖）
.\.venv\Scripts\python.exe -m agent.evaluate offline

# 显式触发真实 API：v2 共 9 题完整评估，默认 DeepSeek 或 .env 指定厂商
.\.venv\Scripts\python.exe -m agent.evaluate live --provider deepseek

# 只跑一题；记忆追问会自动先运行 memory_seed
.\.venv\Scripts\python.exe -m agent.evaluate live --provider deepseek --case cost_components
.\.venv\Scripts\python.exe -m agent.evaluate live --provider deepseek --case memory_followup
```

真实题集包含官方文档研究、固定虚构价格的计算/绘图与分项校验、资料冲突、证据不足、来源渠道、记忆基线和追问。
固定价格题给定两款虚构型号及 CSV/报告列格式，用独立 Decimal 公式逐项核对用量、单价、输入/输出/总费用。
即使模型审稿“未发现问题”，总额正确而分项抄错也会判失败。自由文本题的关键词检查仅是必要条件，仍需人工抽查结论。
评分按句子、转折及表格列语境区分条件讨论、否定与肯定结论；同型号在报告重复展示相同费用只算一组数据，数值冲突仍失败，CSV 重复型号仍失败。
显式错误算式不会被无问题模型审稿覆盖。评分器的哈希包含数值检查实现，更新后应先 `rescore` 旧结果，再与新运行比较，不能把纠正误判当成模型通过率提升。

每次评估保存到新的 `workspace/evaluations/<时间>-<ID>/`，含 `results.json`、`report.md`；
离线模式额外保存 `pytest.log`、`junit.xml`，真实模式在 `cases/` 中保留每题的报告、trace、审稿记录等。
结果记录题集和代码 SHA-256、Git 版本与工作区状态、白名单模型参数、逐题检查与指标，不保存 API Key。
评估使用独立任务目录，记忆题每次重复使用自己的数据库，不读取或污染日常研究记忆。

真实评估可设置 `--max-steps 15 --budget 80000 --suite-budget 400000 --review-calls 2 --repeat 1`。
以上为默认值；等效 token 预算是估算软预算，收尾也受预检约束，但实际分词或缓存偏差仍可能超出。每题用量包含规划、研究和独立审稿；不是人民币价格。
`--repeat` 可设 1–3 次，记忆数据库按重复次数隔离。失败依赖、预算耗尽、未运行或中断的题目都会保留，不从通过率分母中消失。

查看旧结果或对比基线都不调用 API：

```powershell
# task_dir 应是该固定题集生成的单题目录；检查过程不改写原报告
.\.venv\Scripts\python.exe -m agent.evaluate inspect <task_dir> --case cost_components

# 评分器修正后，复查整份已有评估，不重跑模型，不改写原任务
.\.venv\Scripts\python.exe -m agent.evaluate rescore <results.json>

# --out 必须是新目录，不能覆盖已有基线
.\.venv\Scripts\python.exe -m agent.evaluate offline --out workspace/evaluations/baseline-a
.\.venv\Scripts\python.exe -m agent.evaluate offline --out workspace/evaluations/baseline-b
.\.venv\Scripts\python.exe -m agent.evaluate compare workspace/evaluations/baseline-a/results.json workspace/evaluations/baseline-b/results.json
```

对比会列出通过率、步数、耗时、等效 token 的差异及候选退化题。
只有完成运行且题集版本、评分器 SHA-256、题目选择和重复次数相同才直接对比；离线与真实结果不混比。
评分器改变时可先用 `rescore` 将两份已有真实结果按同一版本复查；它保留原执行代码的哈希，指标来自原始 trace，不代表重新跑过模型。
如果题目或标准答案已经改变，`rescore` 会拒绝重评，需要重新运行对应题集。
对比不同模型时分别传 `--provider` / `--model`，并核对报告中的预算、温度和审稿配置；联网波动和模型采样可能影响结果。
退出码：0 全部通过，1 有失败/跳过/未完成，2 参数或执行器错误，130 中断。

离线集验证程序机制，不测真实模型语义能力；真实评估也不是独立事实认证。
审稿耗尽（`skipped_limit`）、失败或与报告哈希不匹配都不能算通过，即使已有报告文件。
成本题规定了英文 CSV/表格列；格式不匹配会导致自动检查失败，不能据此直接断言模型算术错误，需查看逐项检查详情。

### 长期研究记忆

研究任务结束后自动将问题、摘要/结论/局限性、来源与发布日期、报告路径、完成及质量状态存入
`workspace/memory.sqlite3`。不额外调用模型总结，也不自动导入以前的任务目录。
“研究完成”与“质量通过”分别记录：正常结束但审稿有问题的报告不会因此变成已验证事实。
中断/失败任务有报告或笔记时以未完成记录保存；没有报告也没有笔记的对话不存储。

下次研究先用 SQLite FTS5 搜索问题和摘要，召回最多 3 条、总计最多约 6000 字符的历史背景。
中文使用相邻双字切分，英文按词检索；普通问题无关键词命中时不注入无关记录。
出现“沿用上次”“上次的型号”等明确追问时，优先带入最近一次研究，因此可连续提问：

```powershell
.\.venv\Scripts\python.exe main.py "比较两个模型输入10万、输出1万token的API成本"
.\.venv\Scripts\python.exe main.py "沿用上次的型号，输入量翻倍而输出不变，费用是多少？"
```

历史背景带日期、来源和质量状态，同时进入规划与研究上下文，作为数据发送给当前 LLM。
它不能替代当前任务的证据；价格、版本等时效事实仍需重新读取来源并记笔记。
第一版不使用向量检索，也不推断或保存用户偏好；中文分词和“上次”的识别均为简单规则。

管理命令不调用 LLM、不需要 API Key：

```powershell
.\.venv\Scripts\python.exe -m agent.memory list
.\.venv\Scripts\python.exe -m agent.memory search "DeepSeek 价格"
.\.venv\Scripts\python.exe -m agent.memory show 1
.\.venv\Scripts\python.exe -m agent.memory delete 1
# 指定另一个数据库：--db 写在子命令之前
.\.venv\Scripts\python.exe -m agent.memory --db workspace/demo.sqlite3 list
```

`delete` 只删除数据库记录和检索索引，保留报告及已有任务中的背景快照。
`MEMORY_ENABLED=false` 或 `--no-memory` 同时关闭本次召回和保存，纯对话 `--chat` 不使用研究记忆。
默认路径位于已被 Git 忽略的 workspace；自定义数据库路径相对于项目根目录。
数据库不可用时提示并继续研究，不影响报告保存。需要 Python 的 SQLite 带 FTS5 支持。

### 报告检查与审稿

报告采用摘要 → 方法与范围 → 分维度正文 → 结论 → 局限性 → 参考资料的结构。
正文用 `[1][2]` 引用，文末支持 `[1] URL`、`1. 标题 URL`、`[1]: URL`。
自动检查会报告未定义/重复/未使用的引用、未记笔记的证据链接、缺失/损坏或越界的本地图片。
行内代码中的 API 地址及正文裸 API 端点不会当作参考资料；列入参考资料的端点仍会核对来源。

笔记附上“官方候选 / 社区 / 第三方 / 发布主体未核实”的域名线索。
规则只覆盖有限的已知域名，不把公司社区文章当官方文档，也不据此认证未知网站的身份。

研究模式默认在保存报告后自动调用独立上下文审稿，依据报告、笔记、CSV 样本与最近绘图代码查找矛盾、
超出证据范围的结论、来源混淆和数据不一致。意见返回主循环，由模型通过 `write_report` 修订实际文件。
审稿调用计入任务用量、预算和 trace（`purpose=report_review`），最多调用两次，可在 `.env` 调整：

```dotenv
REPORT_REVIEW_ENABLED=true
REPORT_REVIEW_MAX_CALLS=2
REPORT_REVIEW_MAX_TOKENS=2048
```

`reviewed` 表示完成本次语义检查；是否发现问题须查看 `issues`，并核对报告 SHA-256。
`skipped_budget`、`skipped_limit`、`failed`、`partial`、`stale`、`not_reviewed` 均不能算完整审稿通过。
任务结束会刷新机械检查，避免报告保存后更新计划导致旧的计划告警残留。
`complete=false` 的分段草稿不消耗审稿次数；完整报告每次审稿及校验失败后的重试都计入同一上限和 token 预算。
“无需修改”却列为 issue、定位不在报告中或 JSON 不合法时，保留原始失败记录，在剩余次数内要求纠正；没有剩余次数不能算通过。
同一回复混有可定位的有效意见和“无需修改”项时，标为 `partial` 并保留有效意见，先让主循环修订，再审稿新版本；不把无效项静默删除后标记通过。
审稿另附程序从完整 CSV 样本复算的费用、型号内部单价比例及相对首个型号的输入/输出单价倍数，用于核对“两个型号的比例相同”等派生结论；不增加默认模型调用次数。

机械检查不验证事实真伪；模型审稿也可能遗漏或误报。审稿不联网、没有独立事实核验能力，
只读取有限的 CSV/代码样本，不读取图像像素，不能保证图表视觉内容与所有数据完全一致。

硬篇幅拒绝后，后续模型上下文会移除本批被拒写稿及回复，保留原任务、其他工具结果与证据索引，并附程序计量重新生成。原始参数/反馈仍在 trace；不自动替用户删稿，也不重置实际用量、步数或重复保护。静态修订提醒每任务最多一次。

### 关键结论与证据

`write_report` 可用 `claims` 提交最多 8 条关键结论：报告原句 `quote`、类型 `kind`、
依据 `supports`（`source_id` 与 `excerpt`），以及 `assumptions`、`limits`。
类型为 `direct`（直接资料）、`calculated`（计算）、`inference`（推断）、`unknown`（未知或冲突）、`observed`（本次工具/文件记录）。
推断可用 `reasoning` 说明已有前提到结论的步骤，无需额外假设时不必编造 `assumptions`。可选 `premises` 区分有真实摘录的 `evidence` 前提与未确认的 `assumption`、`unknown`；复合结论可按子句分别关联。
这些类型由模型填写，有限检查只验证来源类别等约束，不能证明分类和推理正确。
不提供关联仍可保存报告，但完整稿保持 `unmapped`，暂不消耗语义审稿次数。

工具 `read_evidence(source_id="", query="")` 可列出已保留 ID 或重新查看某条实际送达内容；
只能读取本次索引，不读取未展示的缓存全文。网页日期类型未核实，抓取时间单独保存；历史日期不证明当前事实。
改稿未提供新关联时保守使全部旧关联过期；只改关联也会使旧审稿失效，复审仍受原预算和次数上限约束。
摘录允许仅空白差异匹配，最终保存原始子串，不能用相似措辞冒充原文。

新审稿记录附 `support_review`，逐项保留模型对选定结论的支持判断、证据缺口和建议。缺少逐项意见或证据包省略时保持 `partial`；来源可定位不等于模型已核对或结论成立。桌面“依据”与“质量”面板可查看，过期版本不沿用。见[实现与试跑记录](docs/claim-support-20261020.md)。

新增机制并不自动表示审稿更准确：已完成12份固定短报告、两版各两次的48次诊断对照，原文识错打平，新版有3次字段目标校验未完成且等效用量约高17%。生产规则未据实验更改，原始失败与助手意见保留；详见[审稿对照结果](docs/review-ab-results-20261020.md)，不替代阶段12内容验收。

在项目根目录核对已有任务，无需 API Key：

```powershell
.\.venv\Scripts\python.exe -m agent.evidence "workspace/<任务目录>"
```

退出码 0 代表版本与所选关联可定位；`draft`、`unmapped`、`stale` 或损坏返回非零。
查看 `report.evidence.json` 和 `evidence.json` 可逐条复核。所有关联均保留 `semantic_support=not_certified`；
少量关键句被关联不等于整篇事实已覆盖，成功运行代码也不等于任意计算逻辑正确。

### Python 计算与绘图

更新依赖后即可使用：`python -m pip install -e ".[dev]"`，已包含 pandas、matplotlib。

```powershell
.\.venv\Scripts\python.exe main.py "根据搜到的模型 API 价格，计算输入10万、输出1万token的单任务成本，用Python画柱状图并写入报告"
```

每次 `run_python` 保存脚本到任务目录的 `.code_runs/<调用ID>/code.py`，以当前任务目录为 cwd；
变量不跨调用保留，文件会保留。用 `print` 输出结果，`plt.savefig('costs.png')` 保存图表，
报告用 `![成本对比](costs.png)` 引用。默认采用非交互 Agg 后端，不需要打开图形窗口。

`CODE_EXEC_TIMEOUT` 默认为 30 秒，实际时限不超过 `TOOL_TIMEOUT` 的 80%；超时会终止进程树。
stdout/stderr 分别最多返回 2500 字符，过长会标记截断。失败包含退出码和错误信息，模型可修正后重试。
收尾阶段不允许执行代码，计算和绘图需要提前完成。

**这是本地代码执行，不是安全沙箱。** 子进程仅继承少量系统环境变量，不继承 API Key、其它业务环境变量或 PYTHONPATH；
但仍能以当前用户权限读写任务目录以外的文件、访问网络，也可能主动读取 `.env`。只应在可信本地环境使用；
Docker 的文件、网络与资源限制属于后续增强。

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
  context.py   # 上下文估算与压缩
  evidence.py  # 已送达证据索引、选定结论关联、版本审计 CLI
  evidence_cases.json # 阶段11证据开发回归题集（非独立保留集）
  memory.py    # SQLite/FTS5 研究记忆、历史背景格式化与管理 CLI
  evaluate.py  # 离线/真实题集执行、结果保存、只读复查与基线比较
  eval_checks.py # 独立数值校验、引用/图片/审稿/记忆检查与 trace 指标
  eval_cases.json # 版本化固定题集（v2：离线 8 组、真实 9 题，含明确/歧义样本）
  markdown_tables.py # 保存检查、数值核对和评分共用的 Markdown 表格解析
  planner.py   # 开局拆解研究计划（plan.json）
  quality.py   # 引用、来源、结构、本地图片机械检查
  review.py    # 独立报告审稿提示、证据打包、结构校验与结果记录
  errors.py    # 模型接口错误分类（上下文超长 / 内容审核 / 鉴权 / 其它）
  prompts.py   # system prompt 与各种系统提示（研究模式会注入今天的日期）
  trace.py     # 运行日志 trace.jsonl / messages.json
  replay.py    # python -m agent.replay：回放运行日志
  compare.py   # python -m agent.compare：对比多次运行的指标
  simulate.py  # python -m agent.simulate：在真实历史上离线评估压缩阈值（含缓存成本模型）
  cache_probe.py # python -m agent.cache_probe：测试不同请求写法对前缀缓存的影响
  display.py   # 终端展示（实时与回放共用）
  workspace.py # 每个任务的工作目录
tools/
  registry.py  # 工具注册表：@tool 装饰器、JSON Schema 生成、参数校验、错误转结果
  basic.py     # 基础工具：get_current_time / days_between / calculator
  search.py    # web_search + 可替换的搜索后端（当前 ddgs）
  fetch.py     # fetch_url：下载、正文提取、分段、缓存、Jina Reader 备用通道
  evidence.py  # read_evidence：只读本次保留证据及原始摘录
  report.py    # write_report：安全地写报告文件，并自动检查计划完成度与引用来源
  code_exec.py # run_python：独立子进程、精简环境、超时清理、有界输出与产物路径
  plan.py      # update_plan：更新计划中子任务的状态
  notes.py     # save_note / read_notes
  session.py   # 一个任务内各工具共享的状态（读过的网页、笔记）
main.py        # CLI 入口：研究模式（默认）与纯对话模式（--chat）

knowledge/     # 原始资料RAG：有界解析、SQLite/FTS5、Milvus Lite、嵌入缓存与调用日志
               # python -m knowledge 管理；knowledge.probe 显式付费隔离诊断
tests/         # 用假模型测试，不调用真实 API
  fakes.py     # 剧本化的假模型 ScriptedLLM
workspace/     # 运行产物（已 gitignore）
```
