# 审稿收敛修复与复检（2026-10-18）

## 结果

单测 **433 passed**、离线 **8/8**。上一轮失败的联网题定向三次 **3/3**；另执行完整三轮 9 题，27 份最终报告全部 reviewed、issues=[]，审稿哈希与报告一致，**没有 skipped_limit、failed、partial 或跳过任务**。两次审稿上限、步数和预算未放宽。

完整三轮原始自动评分均为 8/9（共 **24/27**）。三项失败来自小样本题的条件标题/引述与否定列表误判；三轮结束后只修改评分器并补反例，对全部产物只读复查为 **27/27**。因此本轮达到了这份固定题集的自动通过门槛，不能把 24→27 宣称为模型能力提高，也不能保证未来模型输出总能收敛。

## 根因与改动

上一轮失败中，审稿建议新增默认值和小节定位，复审又因笔记没有这些内容要求删除。独立上下文缺少实际已读原文、只收到当前全文和前轮意见，修订又采用整篇重写，容易制造无关变动。

- `source_readings` 保留本轮成功 fetch_url 的实际返回片段及调用 ID/参数；失败调用不作证据，最多 8 条、合计内容与参数 20000 字符，单条内容最多 8000、参数 2000，截断/省略明确标记。不额外抓取网页，不把未返回的缓存全文当成已读内容。
- `revision` 提供同一文件上一稿与当前稿的有界行差异，包含上一稿哈希；差异总计最多 6000 字符，超额省略明确标记。审稿仍收到当前完整报告（原有 40000 字符边界），不能用差异替代全文检查。
- 审稿提示区分原文与笔记摘要、行为主体与条件，先核对已解决问题及实质新增错误，不把等价措辞和可选定位细节反复列入问题。修改建议不得补造默认值、版本或小节名，非必要无据细节可删除。没有自动丢弃 warning，也没有把无效审稿转成通过。
- 报告工具增加 patch 模式。base_sha256 必须匹配当前稿；old/new 替换列表全部基于同一原稿定位，匹配次数须与 expected_occurrences 相同（默认 1），禁止重叠；全部校验后才保存。错误批次不改变报告与质量记录。修订仍经过表头、数值、引用等检查，并触发独立复审。
- 评分器处理引述命题后“所需条件”等议题、比较/排序命题所需测试结果，以及“不可以说”等否定形态；双重否定和转折后肯定仍须失败。只属于有限语境规则，不是通用语义认证。

## 验证与版本

运行实现提交 `e724aa1d20b66d76b93a3e4007b894ef9c3f7b64`，定向及三轮运行期间不改代码；三轮身份和配置一致，工作区干净。定向验证与完整三轮分开统计，不拼接为三轮结果。三轮后仅修改评分器与其测试，提交 `ef3f16b`；最终单测由 425 项增至 433 项。复查在该提交前执行，元数据记录 e724aa1 + dirty 和完整哈希，source_code_sha256 保留实际执行代码。

| 指纹 | 真实执行 | 最终只读评分 |
|---|---|---|
| code_sha256 | 983295e82b023c850e5c380b71b9e85690438efbad64dbd44684262585235e95 | aeacb88e77e92819fcc15436239efcebd4a6d773f3569270fa0c9e1f0c08d792 |
| suite_sha256 | b478e191150525ccc0505333a55b28e6ca24008cc19e599b19a9f1a9847bc8ae | 同左 |
| grader_sha256 | fd33d37c377b97ad1cf232fa779e12f6d28d1f93df188ec7e2484a9d3dfbd59c | 11ac595da65429406c5d832061bf273e640e4a7b63fbdb8692a2fb6b7548e1ec |

相同 v2 题集、DeepSeek/deepseek-chat、temperature=0.3，每题 15 步/80000 等效 tokens、最多 2 次审稿，每轮独立 400000 软预算、独立记忆库，缓存/输出权重 0.1/2。评分只读复查无新模型调用，原始与复查逐题 metrics 完全一致。

| 测量 | 原始通过 | 只读复查 | 步数 | 任务耗时/秒 | 等效 tokens | 审稿调用 |
|---|---:|---:|---:|---:|---:|---:|
| 定向联网题重复三次 | 3/3 | 不另计 | 23 | 81.22 | 104224 | 6 |
| 完整第一轮 | 8/9 | 9/9 | 54 | 151.39 | 159341 | 12 |
| 完整第二轮 | 8/9 | 9/9 | 55 | 159.61 | 161643 | 11 |
| 完整第三轮 | 8/9 | 9/9 | 58 | 160.53 | 168268 | 11 |
| 完整三轮合计 | 24/27 | 27/27 | 167 | 471.53 | 489252 | 34 |

原始失败均为 limited_evidence：第一轮“所需的条件与缺口”标题，第二轮“不可以说”控制的分号列表和明确否定的比较命题，第三轮“排序命题至少需要可比测试结果”。报告均拒绝跨产品排名，不是肯定赢家。补入原句及肯定、转折、双重否定反例后，复查全部三轮，保留原始失败记录；之前的失败基线也不改写。

## 实际修订与人工检查

- 三次定向联网题都实际调用 patch，修订后在第二次审稿完成。完整三轮另有 8 次 patch 尝试、7 次成功。第三轮一次把 3 处引用错写成预期 5 处，整批拒绝；更正定位参数后成功，没有消耗额外审稿，也没有保存部分修订。
- 新回归覆盖版本过期、原文缺失、重复匹配次数、范围重叠、清空报告、批次拒绝不破坏原文件/质量记录、patch 后表头与数值检查、实际修订再审、网页成功/失败与容量边界、差异容量边界。最终 `git diff --check` 通过。
- 阅读三轮联网报告与审稿历史，核心结论仍有本轮已读原文支持；“不关闭资源”没有再次被写成“不支持协议”。第三轮真实修正了引用编号错位，没有沿用旧稿通过状态。
- 已目视核对三轮费用图，CSV/报告核对正确：输入 100000、输出 10000 时总额 0.28/1.17 元；记忆追问输入翻倍时为 0.48/2.07 元，分项一致。六个记忆任务无搜索/读取网页/网页笔记尝试，三个隔离库各两条记录，追问均召回本轮 id 1。
- 已阅读三轮明确样本与歧义题。歧义题保留测试总次数、分数定义和失败次数为未知。范围软约束仍非完全可靠：第一轮明确样本题自行扩展 Fisher/Wilson 统计分析，其统计前提没有由用户材料独立证明；不应把附加推断视为已验证事实。部分报告也仍长于简述目标。本次固定题集通过与审稿收敛，不等价于所有附加文字获独立事实认证。

## 命令与产物

```powershell
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m agent.evaluate offline --out workspace/evaluations/review-convergence-final-offline
.venv\Scripts\python.exe -m agent.evaluate live --provider deepseek --model deepseek-chat --case official_research --max-steps 15 --budget 80000 --suite-budget 400000 --review-calls 2 --repeat 3 --out workspace/evaluations/review-convergence-targeted
foreach ($roundNumber in 1..3) {
  .venv\Scripts\python.exe -m agent.evaluate live --provider deepseek --model deepseek-chat --max-steps 15 --budget 80000 --suite-budget 400000 --review-calls 2 --repeat 1 --out "workspace/evaluations/review-convergence-round$roundNumber"
}
foreach ($roundNumber in 1..3) {
  .venv\Scripts\python.exe -m agent.evaluate rescore "workspace/evaluations/review-convergence-round$roundNumber/results.json" --out "workspace/evaluations/review-convergence-assessed-round$roundNumber"
}
```

这是先冻结实现运行、后修正评分只读复查的命令形式；重现请换新输出目录。逐题 trace、报告、CSV/图片、审稿历史保留在对应目录，原始/复查一致性汇总在 `workspace/evaluations/review-convergence-aggregate/summary.json`，原始控制台在 `workspace/review-convergence-*-console.txt`。产物按现有规则忽略，代码、测试和复检文档提交并推送到同一远程仓库。上一轮未通过记录见 [fix-acceptance-20261018.md](fix-acceptance-20261018.md)。
