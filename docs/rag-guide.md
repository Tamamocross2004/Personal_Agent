# 本地原始资料 RAG

本机制检索用户导入的原始资料，独立于 `agent.memory` 保存的历史研究摘要。聊天仍使用项目配置的模型；向量化使用项目 `.env` 的硅基流动 Key。

## 启动与使用

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[desktop,rag]"
.\.venv\Scripts\python.exe -m knowledge status
```

桌面应用重启后，左下角打开“原始资料知识库”，选择 UTF-8 TXT、Markdown 或文本 PDF，点击“导入并构建”。最新构建状态为 `ready` 且当前版本发生切换后可用。预览支持按页阅读，检索预览默认关键词模式。

研究输入栏勾选“知识库”后发送问题，例如“仅根据知识库资料，概述申请流程及例外，不能确定的条件请说明”。历史背景开关控制历史研究摘要，与知识库开关分别工作。模型自行决定是否以及如何调用 `search_knowledge`；开启开关不代表已阅读全文。

CLI 同样可用：

```powershell
.\.venv\Scripts\python.exe -m knowledge import "D:\资料\约定.md" "D:\资料\说明.pdf"
.\.venv\Scripts\python.exe -m knowledge search "申请的例外条件" --mode keyword
.\.venv\Scripts\python.exe -m knowledge search "申请的例外条件" --mode hybrid
.\.venv\Scripts\python.exe main.py --knowledge --no-memory "仅依据知识库解释申请流程和例外，标明依据及未知项。"
.\.venv\Scripts\python.exe -m agent.replay
```

索引维护：`python -m knowledge rebuild` 重建；`python -m knowledge remove <document_id>` 从当前库移除；`python -m knowledge import --replace <文件>` 替换整个库。同名同内容导入复用；同名不同内容拒绝，需明确移除原文档或替换整个库。

## 配置与数据流

`.env.example` 列出了配置。必要项为 `SILICONFLOW_API_KEY`；默认嵌入模型 `Pro/BAAI/bge-m3`，1024 维。Key 只从本项目 `.env` 读取，显式测试参数除外，不回退到操作系统全局 Key 或示例项目。配置变更后重启桌面后端。更换嵌入模型、端点或维度后应重建索引；旧版本仍可显式选择关键词检索。

安装脚本 `scripts/install-desktop.ps1` 包含桌面与 RAG 依赖。正式库默认在 `workspace/knowledge`，SQLite 保存元数据、提取原文、FTS5、向量缓存和嵌入请求日志，Milvus Lite 保存各代际向量索引；均由项目 `.gitignore` 排除。文档原始字节保存在库目录下的 `documents`。

切片默认 700 字符、重叠 120 字符，尽量沿段落/句界切分，保留提取文本的确切区间。PDF 页码为原始 PDF 的一基页码，文本区间对应提取文本，不能当成 PDF 屏幕坐标。首版无 OCR，不解析办公文档、表格、图像和复杂 PDF 排版。

`keyword` 独立使用 FTS5；`vector` 使用 Milvus 召回；`hybrid` 分别召回后用 RRF 融合。混合/向量检索启用重排时，再用 cross-encoder 对融合排名前20个候选重排，之后选最终片段、有界补充相邻上下文。直接命中优先。没有从示例复制退款领域关键词、问答模板或“排序分即置信度”的拒答逻辑。RRF分与重排分都只用于排序，没有答案正确概率的含义；无命中不能证明资料没有相关事实。

## Cross-encoder 重排

默认 `BAAI/bge-reranker-v2-m3`，使用项目 `.env` 中同一硅基流动 Key，通过独立 `/rerank` 接口把问题和候选原文共同送给服务评分。它在选最终4个片段之前执行，不能找回初始召回或前20候选之外的资料。加入/更换重排模型无需重新向量化文档；重排缓存会按模型、端点、问题及有序候选文本哈希区分。

桌面研究输入栏开启“知识库”后，可用“重排”开关控制本轮研究。知识库检索预览也有独立的“本次检索启用重排”开关，该开关只影响预览。关键词模式始终不执行嵌入或重排，显式要求 `keyword` 加 `rerank=true` 会被拒绝。

```powershell
.\.venv\Scripts\python.exe -m knowledge search "申请的例外条件" --mode hybrid --rerank
.\.venv\Scripts\python.exe -m knowledge search "申请的例外条件" --mode hybrid --no-rerank
.\.venv\Scripts\python.exe main.py --knowledge --no-rerank "仅依据知识库解释申请流程。"
```

当前默认关闭（`KNOWLEDGE_RERANK_ENABLED=false`）：本轮小资料实测相关性指标持平、延迟增加，尚无依据让日常查询自动付出额外开销。需要时在桌面勾选、研究CLI加 `--knowledge --rerank`，或将项目 `.env` 配置为 `true` 后重启。其余配置见 `.env.example`：`RERANK_MODEL`、`RERANK_BASE_URL`、`RERANK_TIMEOUT`、`KNOWLEDGE_RERANK_CANDIDATES`。研究工具可显式关闭重排，但不能自行开启本轮已关闭的重排。重排结果校验返回索引完整唯一、分数有限，不把分数强行限制为0到1，也不设基于分数的自动通过/拒答阈值。

失败会明确报错，不重试、不自动降级。已知用量仍计入预算；用量不完整时研究停止为 `needs_verification`。需要改用原排序时必须显式关闭重排，记录中不冒称重排成功。只有最终实际送达的片段登记为原文证据；参与重排的候选不因此被视为全部已读。

固定资料的真实对照命令为 `python -m knowledge.rerank_probe --live --output workspace/rerank-probe-new`，会执行嵌入、重排和两轮聊天研究；使用 `--skip-research` 可仅比较检索。资料、相关文档标注、候选数、top4口径与两臂顺序在请求前保存。该开发诊断不替代独立质量验收，完整结果见 [重排实测记录](rerank-verification-20261021.md)。

## 版本、引用与失败

构建使用新代际，解析、嵌入、向量索引和全文索引完成后才通过 SQLite 事务切换当前版本。失败保留旧版本；请求用量继续保留。并发构建被锁阻止，Milvus 访问也串行释放句柄以兼容本机多进程。

任务第一次检索时保存 `knowledge_snapshot.json` 固定代际；其后新导入、重建或移除不改变该任务使用的原文版本。只有实际送达模型的正文片段登记为 `document` 证据，记录文件名、文件哈希、页码、页文本哈希、代际和提取文本区间。报告中的关键结论仍须通过既有 claims 机制关联真实证据，历史摘要仍不能冒充原始资料。

文件哈希和 `mapped` 说明版本及定位关系，不认证文档内容真实、作者权威、适用于当前情境或结论语义正确。外部文档及文件名均为不可信输入，不能覆盖任务指令。首版没有沙箱或形式化提示注入防护保证。

为保证任务快照可重放，移除资料不物理清除历史代际、原文件或向量缓存；首版尚无垃圾回收/彻底擦除入口。手动删除库会破坏旧任务的后续检索，已经写入任务证据索引的原文仍保留。

上下文超过既有阈值时，旧检索正文也参与压缩，最近几轮受保护，协议中的工具调用与返回仍一一对应。占位提示不冒充摘要或全文阅读证明；原文可用 `read_evidence` 重读，或在同一代际重新检索。

## 调用与预算

嵌入请求前先持久记录 `started`，返回后更新已知输入 token、状态和耗时。按端点、模型、维度和精确文本哈希缓存，缓存命中没有新增嵌入请求。关键词检索不调用嵌入服务。

知识库管理分别显示建库/检索嵌入与重排的累计请求；研究用量面板把本轮嵌入、重排与聊天模型费用分列。嵌入和重排没有配置费率，显示“未计价”，不能理解为免费。真实费用以硅基流动账单为准。

建库使用独立 token 预算；研究检索的已知嵌入/重排输入 token 同时计入研究累计 token 和等效预算（权重1），重排输出按既有输出权重计入预算。知识库管理预览/CLI默认检索预算为30000，可用 `KNOWLEDGE_SEARCH_TOKEN_BUDGET` 配置；研究工具始终继承本轮剩余预算。每次重排前会扣除该次查询已发生的嵌入用量，再检查剩余预算。发送前的预算检查只是估算，实际用量可能更高。两个适配器都不自动重试；请求超时、被中断或响应缺失用量时保留未知记录，研究停止为 `needs_verification`，不能自动重做。任务分别另存 `embedding_usage.jsonl`、`rerank_usage.jsonl`，即使线程晚返回也可核对；用量未知不能按零收费处理。

首版限制：每文件 20MB、100 万提取字符，PDF 最多 500 页；知识库最多 50 份资料、200 万提取字符、5000 片段；单次检索最多 12 片段/5500 正文字符。限制服务于资源边界，不是答案通过门槛。

## 复核

离线测试：`python -m pytest tests/test_knowledge.py tests/test_config.py -q`。显式真实服务诊断：

```powershell
.\.venv\Scripts\python.exe -m knowledge.probe --live --output workspace/rag-probe-new
```

该命令在新目录预先保存虚构资料、问题、模型配置、源码指纹，然后比较三种检索模式并执行一轮研究；产生嵌入与聊天 API 用量，不修改正式库。不能复用已有输出目录，失败记录也不覆盖。它是开发诊断，不是独立保留集，不替代阶段 12 验收，也不证明 RAG 在所有问题上优于原方案。
