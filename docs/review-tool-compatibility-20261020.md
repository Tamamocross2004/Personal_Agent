# 阶段12复核工具优化

日期：2026-10-20。基线：阶段14提交 `31a5642`。本次只优化评估记录、人工表导出及只读复核；未修改研究提示、Agent循环、题集、数值评分、预算或审稿上限。

## 已修复

1. 原结果的跳过项没有 `artifact_sha256`，旧模板却写成 `{}`，此前复核按 `None != {}` 拒绝。现在仅对没有任务目录的待执行/跳过记录兼容缺失、`null`、`{}`。有目录的任务仍要求哈希映射，并重新核对实际文件；空目录的 `{}` 与未记录产物的 `null` 区分处理。
2. `pending` 是合法待填写状态，输出四种结论的计数和 `review_status`；不自动补核对人或结论。全部填写完成也不等于验收通过；助手复核、存在fail/uncertain、原自动门槛未达等情况仍为 `not_accepted`。
3. 新增 `template` 命令，从旧结果导出新表，带原始结果摘要、自动状态、任务路径、原产物哈希和冻结rubric。导出先核对题集摘要与当前产物，不调用模型、不重评分、不覆盖旧文件。新表在复核时检查绑定的原始结果；旧表继续兼容，但没有新增的结果摘要绑定。
4. 输入错误指出轮次/题目；拒绝重复或空原始记录、布尔型轮次、非法哈希、缺失身份等。复核或导出的输入校验先于输出目录创建，失败不留下空目录，已有目录不覆盖。
5. 未来评估在调用研究前记录任务目录；中断时保留部分文件的哈希和interrupted状态，不把已经开始的任务只记为没有产物的pending。模型调用与预算计算方式未改变。

## 验证

全量 **800 passed，49.08秒**；新增23个参数化测试样本。专项34项通过，涵盖旧空值组合、真实产物版本不能绕过、源结果绑定、未填表、错误输出路径及实际部分文件的中断记录。

在第四批真实记录上，只读执行：

| 输入/操作 | 结果 |
|---|---|
| 重新导出 | 新表39项pending，核对人/类型全空；成功退出0 |
| 未修改的原始旧模板 | 正常写出39项pending，not_accepted，退出1 |
| 新绑定模板 | 39项pending，not_accepted，退出1 |
| 现有人工副本 | 39项pending，未代填，not_accepted，退出1 |
| 现有助手意见 | 25 pass / 2 fail / 12 uncertain，human_review_complete=false，not_accepted，退出1 |

退出1表示复核尚未验收，非模板解析失败；非法输入或输出冲突退出2。原始自动分数仍为37/39。运行前后核对 **456个文件**（451项任务产物及原结果、报告、模板、人工/助手副本），哈希全部一致。所有这些文件未被改写，没有重新运行模型或重评分，API调用为零。

日志与完整性基线在 `workspace/review-tool-*.log`、`workspace/review-tool-baseline-20261020.json`。追踪的[机器记录](review-tool-evaluation-20261020.json)保存源文件与证据哈希。

## 使用

已生成的可填写新表：`workspace/evaluations/stage12-independent4-review-tools-export-20261020/review-template.json`。现有人工副本也可以继续使用；两者均未代填。按原文、rubric及实际产物填写，未执行项不能据此声称报告内容正确；题目有歧义或证据不足时保留uncertain。

自行导出时，用尚不存在的新目录：

```powershell
.\.venv\Scripts\python.exe -m agent.validation --suite agent/validation_independent4_cases.json template workspace/evaluations/stage12-independent4/results.json --out workspace/evaluations/independent4-my-review-template
```

填写后只读检查：

```powershell
.\.venv\Scripts\python.exe -m agent.validation review workspace/evaluations/stage12-independent4/results.json workspace/evaluations/stage12-independent4-review-tools-export-20261020/review-template.json --out workspace/evaluations/independent4-my-reviewed
```

每次输出使用新目录。可逐步填写，未填项保留pending及空身份；正式结论填写pass/fail/uncertain、findings、reviewer、reviewer_kind。不要改ID、轮次、原始结果摘要或哈希。`review`不要求加载当前题集或模型配置；`template`需要指定与原始冻结摘要相符的题集。

## 边界

本次没有解决报告的语义问题，也没有完成外部人工复核或新独立评估。**阶段12仍未通过，不打stage-12标签**。复核工具不能验证填写者真实身份，摘要绑定也不是签名或身份认证。

现有检查点指纹包含 `agent/*.py`，因此本次修改validation.py也会触发旧检查点的源码版本不匹配；没有改变恢复校验或迁移旧检查点。查看完成报告和只读复核不受此限制。旧冻结清单不能用于混入新代码的模型运行，后续真实评估须重新冻结并使用新保留题。
