# 验收问题修复与完整复检（2026-10-18）

## 结论

程序回归 **413 passed**，离线固定评估 **8/8**。v2 题集独立三轮全部执行、无跳过；原始评分 **24/27**，修正两项评分误判后只读复查 **26/27（96.30%）**。仍有一次审稿不收敛、额度耗尽，**未达到 27/27 的稳定性门槛**，不能声称完整验收通过。

## 修复范围

- `agent/markdown_tables.py` 使用显式依赖 markdown-it-py 统一正文表格解析；保存表头、报告与 CSV 对齐、数值评分共用，支持可选外侧竖线、转义竖线、加粗/代码表头、多个表格，排除代码块示例。
- 自由文本评分按逐个命题的局部从句、引述后回指、条件讨论、否定列表与表格列语境判定。不能用对 B 的否定掩盖对 A 的肯定；引号本身不表示否定，转折后的肯定、双重否定仍失败。原 v1 两份失败报告、无外侧竖线追问，以及本次两份误判报告均作为回归样本保留。有限规则仍不能认证任意自然语言语义。
- 审稿补入本轮成功执行的 calculator/days_between/run_python 输入与结果，含调用 ID；最多 20 条、合计输入/输出 16000 字符，每项输入/输出各最多 4000 字符，明确截断与省略数，不把失败结果当证据。
- 审稿明确只有 report 是定位对象，笔记/历史/计算结果只是证据。混合有效与越界问题时保留有效意见并标为 partial；纯越界仍失败，在原有次数/预算内纠正，不删除错误后冒充通过。
- 研究与审稿提示强调歧义跨章节一致，不为迎合前轮意见补造事实；区分“支持协议”与“协议是否关闭资源”。提示不能保证模型始终遵守。
- 题集 v2 将明确样本题改为每版本 51 次、合计 102 次，并新增 ambiguous_evidence，要求保留原材料“共 51 次”及分数含义的歧义。不能用 v2 新题重评 v1 回答，也不能将新旧总通过率直接视为模型能力提升。

## 方法与版本

实际执行冻结在 `b58ce83fd0959b3089abc0b6dc8d09770f865d5f`；三轮运行时工作区干净，代码、题集、评分器、配置哈希一致。单测先为 404 项；三轮结束后仅修改评分器及其回归，最终为 413 项，运行提示和审稿实现未再改动。最终评分修复提交为 `01fe870`。

| 指纹 | 真实执行 | 最终只读评分 |
|---|---|---|
| code_sha256 | b8847e129ec282f2a307ee5731b5f533dfe204bf3c4439b6320606ac3e77675e | 07450447cad266eb503d852f07b0b3f36af208f5df5e8308aec170de551c8aeb |
| suite_sha256 | b478e191150525ccc0505333a55b28e6ca24008cc19e599b19a9f1a9847bc8ae | 同左 |
| grader_sha256 | fd23d2b2770baf0dad803ff27e8cb01ac699a60828755ff3ef4c4ca950dbb8c8 | fd33d37c377b97ad1cf232fa779e12f6d28d1f93df188ec7e2484a9d3dfbd59c |

只读评分在评分修复提交前测量，元数据为 b58ce83 + dirty，完整代码哈希对应最终评分代码；source_code_sha256 保留实际执行版本，不能把复查当成重新运行模型。

DeepSeek/deepseek-chat，temperature 0.3，每题 15 步/80000 等效 tokens，每轮独立 400000 软预算，最多 2 次审稿，缓存/输出权重 0.1/2。三轮分别使用新进程、新目录和专用记忆库，未以 --repeat 3 共享整套预算。

```powershell
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m agent.evaluate offline --out workspace/evaluations/fix-final-offline
foreach ($roundNumber in 1..3) {
  .venv\Scripts\python.exe -m agent.evaluate live --provider deepseek --model deepseek-chat --max-steps 15 --budget 80000 --suite-budget 400000 --review-calls 2 --repeat 1 --out "workspace/evaluations/fix-release-round$roundNumber"
}
foreach ($roundNumber in 1..3) {
  .venv\Scripts\python.exe -m agent.evaluate rescore "workspace/evaluations/fix-release-round$roundNumber/results.json" --out "workspace/evaluations/fix-final-assessment-round$roundNumber"
}
```

以上为实际使用的命令形式；live 在评分最终修正前运行，随后 rescore。输出目录已存在，复现时应改用新的目录名。

## 三轮结果

| 轮次 | 原始通过 | 最终只读复查 | 步数 | 任务耗时合计/秒 | 等效 tokens | 审稿调用 |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 7/9 | 8/9 | 56 | 177.61 | 164967 | 10 |
| 2 | 9/9 | 9/9 | 52 | 173.43 | 169320 | 12 |
| 3 | 8/9 | 9/9 | 48 | 153.98 | 143897 | 10 |
| 合计 | 24/27 | 26/27 | 156 | 505.02 | 478184 | 32 |

最终复查中，official_research 为失败/通过/通过，其余八题均为三轮通过。原始与复查逐题 metrics 完全一致，复查没有新增模型消耗。

两项评分误判分别为第一轮“不能说”控制的否定列表，第三轮“比较性结论无法成立”及“相对判断需要数据”的引述讨论。修复补肯定句反例并复查全部三轮，不只重评失败题；原始失败不改写，不把 24→26 计作模型能力提高。

唯一保留失败：第一轮 official_research 两次有效 JSON 审稿后又修改第三稿，最终状态 skipped_limit。第二次意见涉及第一轮建议新增的默认值缺少笔记依据，以及引用定位措辞；部分意见具有反复和过度追究细节的倾向。没有追加次数、手工置 reviewed 或用后两轮成功替换。本次三轮无“定位原文不在报告中”等审稿结构校验错误，但模型层的意见可靠性与修订收敛仍未解决。

## 人工抽查

- 已阅读三轮联网报告及来源笔记，并检查各轮成功 fetch_url 返回的原文中均有“不关闭连接”的直接依据；三轮均没有再把 Connection 归类为不支持上下文管理器。该核心判断正确不代表所有附加表述均已独立核实；部分报告仍明显超出简述篇幅目标，第三轮关于事务开启主体的表格措辞也可更清晰。
- 已阅读三轮明确/歧义样本报告。明确题各版本 51 次，成功数 49/48，合计 97/102；歧义题保留 51/102 等解释为未知，不把分数确定换成失败次数，不给出跨产品赢家。第一轮明确题的三次成功率 calculator 记录均真实成功，审稿未再误称无计算记录。
- 已目视核对三轮 costs.png，图例、柱高和金额一致；主 CSV/报告均为 EvalAlpha 0.20+0.08=0.28、EvalBeta 0.90+0.27=1.17。输入/输出单价倍数分别为 4.5/3.375，未混用。
- 六个记忆任务的数字核对全部通过，追问均为 EvalAlpha 0.4/0.08/0.48、EvalBeta 1.8/0.27/2.07；输入 100000→200000、输出 10000 不变、固定价格和历史日期一致。每轮专用 SQLite 库均两条记录，追问均召回本轮 id 1；六题搜索/读取网页/网页笔记工具尝试均为 0，未使用日常记忆库。

这些是针对本次产物的抽查，不能承诺模型的其他报告始终无误。保留状态失败，能够避免将未复审的修订稿宣称为通过。

## 原始产物与提交

- 上次失败基线保持不变：`docs/stability-acceptance-20261018.md` 及 `workspace/evaluations/stability-20261018-round1/` 至 round3。
- 早期候选 f80c79a 在旧报告回放暴露新误判后主动中断：`workspace/evaluations/fix-acceptance-round1/INVALIDATED.md`。只完成五题，不计入本次三轮统计，也不拼接使用。
- 实际完整三轮：`workspace/evaluations/fix-release-round1/` 至 round3；原始汇总 `fix-release-aggregate/`，控制台 `workspace/fix-release-round1-console.txt` 至 round3。
- 最终只读复查：`workspace/evaluations/fix-final-assessment-round1/` 至 round3；原始/复查身份、配置、指标一致性与逐题矩阵在 `workspace/evaluations/fix-final-aggregate/summary.json`。
- 最终离线：`workspace/evaluations/fix-final-offline/`。`git diff --check` 通过。
- 提交修复代码、回归样本和本记录；运行产物、个人记忆、.env、虚拟环境遵循现有忽略规则，不纳入推送。远程为用户指定的 `https://github.com/Tamamocross2004/Personal_Agent.git`，推送 main 及阶段标签，保留本地完整提交历史。
