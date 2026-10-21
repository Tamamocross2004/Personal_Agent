# 最终版本新资料复检

复检已完成，但完整交付仍不稳定，阶段12仍未验收。生产代码沿用214d960时的最终版本，运行指纹始终为`0fe3d660b9bfff9c44924047fb746e77e876b7ed5961bbdcbfc29bdbe3af8701`。本轮只增加复检题集和记录，没有修改源码、标准或失败产物；问题由编码助手设计和核对，不是独立人工验收。

## 冻结与执行

[预定方案](generalization-plan-20261021.md)、[来源指纹](generalization-sources-20261021.json)和[四道新题](../knowledge/generalization_cases.json)在嵌入调用前提交为`4de229d`；独立库构建后，[研究冻结协议](generalization-freeze-20261021.json)在聊天调用前提交为`fb0abf9`。

新资料为Git v2.47.0的[gitignore](https://raw.githubusercontent.com/git/git/v2.47.0/Documentation/gitignore.txt)、[git-rm](https://raw.githubusercontent.com/git/git/v2.47.0/Documentation/git-rm.txt)，以及PostgreSQL 17的[事务教程](https://www.postgresql.org/docs/17/tutorial-transactions.html)、[隔离级别](https://www.postgresql.org/docs/17/transaction-iso.html)。共46822字符、91片段。Git原字节不改；HTML沿用已有正文提取，不围绕答案裁剪。六个事先选定原文锚点全部定位。

每题只执行一次，12步、80000等效token；项目`.env` Key、deepseek-flash、记忆关闭、任务不使用网页搜索/抓取。嵌入和重排仍为云端API，诊断显式开启20候选重排，生产默认未改。新旧题不是配对，不计算所谓提升幅度，也不以该小规模诊断证明检索普遍效果。

## 原始结果

| 题目 | 停止状态 | 报告与审稿 | 等效token | 工具错误返回 |
|---|---|---|---:|---:|
| 建议与执行状态、280字报告 | final_answer | mapped，4项选定结论完整审稿 | 17402 | 1 |
| Git忽略与索引、跨文档 | budget | mapped，5项选定结论完整审稿 | 59566 | 3 |
| 文档默认值与实际部署 | budget | 未保存报告，未审稿 | 54345 | 2 |
| 保存点机制与运行统计 | budget | mapped，7项关联，partial审稿 | 63138 | 2 |

第一题初稿290字符被拒，最终274字符，原280上限不变；建议没有被写成已批准或已执行，负责人信息保持未知。第二题在预算收尾中完成模型审稿，仍不是正常结束。第三题两次提交均被拒，最终答复为空，不能把trace中的候选稿算成报告交付。第四题保留partial及3项原始问题，不因本次只读核对而更改状态。

965项全量工程回归通过，耗时87.32秒。四题全部实际执行不等于四题通过。3题的budget停止均发生在80000实际等效token之前：输入预估、输出和审稿预留会提前收尾；它们不是账单硬上限或交付保证。

## 内容和机制发现

1. **运行快照正确，审稿混淆时点。** 第四题原始`read_notes`快照是10条证据（用户材料1、文档9）、2个文档版本、1次检索成功返回。记录该工具的`execution-1`之后，账本才成为11条。报告的10与所引快照一致；审稿却说快照是11，最终答复随之“纠正”为11。原始execution记录`truncated=false`、`delivery_truncated=false`，包含计数字段；审稿包只截取所选摘录附近上下文，没有把这些字段送入，审稿又把上下文缺失说成原始返回截断。离线重建证实这一区别，见机器记录中的`review_context_diagnostic`。当前运行状态不能自动推翻历史快照；上下文未展示应保留未知。
2. **引用提交仍耗费收尾空间。** 第三题最后的claim原句与正文不逐字一致，`quote_missing`拒绝有依据；前面8次检索工具请求仅6次需要付费检索，已累积59条文档记录。它在写稿前已消耗大量上下文，预算预留最终拦截后续答复。其候选稿还有“知识库无连接串……”的全库缺失声明，虽然随后补充检索局限，仍存在局部过强表述。
3. **数字校验仍有通用定位符误拦。** 第二题首次拒稿把`gitignore(5)`的手册章节编号5当成无依据事实数字；这不能通过给命令名加白名单解决。其余来源ID不存在、原句不匹配、摘录不在实际记录、execution误标direct等拒绝分别保留，不能一概视为校验器错误。
4. **完整模型审稿仍可能漏掉条件歧义。** Git稿把默认移除条件与`--cached`的特殊条件并列叙述，没有明确两者适用关系，可能被理解成暂存内容与磁盘一致时仍须强制。编码助手将其标为需澄清，而不是声称已执行命令证明错误。报告正确写明仅读片段、未执行命令；不能因5条选定结论被判supported就认证全文。

86条送达文档记录逐字核对页码、区间、文本与哈希均一致；三份已保存报告的版本、依据关联和审稿哈希一致。两份实际快照对照各自返回时刻的trace，工具请求/返回、文档记录、版本数和快照总证据数一致。文档定位正确不等于语义正确，9条记录也不等于读过全部文档。

## 用量和保留

研究聊天39次，输入457503（缓存369280）、输出19276 token，合计194451等效token；8条工具错误返回。研究嵌入9次、145输入token，重排9次、30604输入token。另一次性建库嵌入6次、14589输入token；建库与研究合计嵌入15次、14734输入token，分别计账。新增未知用量0；这些是返回计量，不是人民币报价。

[逐题机器记录与编码助手意见](generalization-verification-20261021.json)、[完整性与密钥扫描记录](generalization-closeout-20261021.json)。此前1089份登记产物保持原哈希；本轮全部原始调用、拒稿、报告、审稿、用量、回放均保留，未重跑覆盖。研究目录为`workspace/generalization-live-20261021`，原始来源和建库记录为`workspace/generalization-source-20261021`。

后续优先解决：按来源/时点组织审稿上下文、明确摘要截取与真实送达截断、减少无新增依据的检索及提交往返、通用定位符数字分类、默认/特殊条件和有限检索范围表述。修复应使用新的正反例和新资料，不提高阈值或篡改历史分数。本轮只复检，未实施这些修复。

只读回放，不调用模型：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m agent.replay workspace/generalization-live-20261021/savepoint-and-activity --full
.\.venv\Scripts\python.exe -X utf8 -m agent.replay workspace/generalization-live-20261021/default-versus-deployment --full
```
