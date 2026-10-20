# 自动保存未完成结果的来源与状态

日期：2026-10-20。用户授权助手完成复核后继续下一项任务。本项接续[39项助手复核](assistant-review-followup-20261020.md)，解决程序保存事实与停止前模型文字混杂的问题。

## 行为

原有机制在预算/步数/循环保护结束且没有报告时，将最终回答写入report.md。模型生成回答时保存工具可能确实失败，但程序随后兜底落盘，旧文件却没有说明这个时间差，读者可能将“未保存”误认为当前文件状态。

现在仅在原有自动保存分支执行：

- 报告前增加程序生成的保存说明、停止原因、UTC保存时间及“停止前的模型原文”区分，正文原文保留，包含换行字节。
- 写入report.provenance.json，记录来源、保存时未完成/未审稿、原文摘要及报告字节摘要。版本匹配才显示current；修改/删除报告后stale，损坏来源记录unavailable，旧报告无记录时不推断来源。
- 终端与trace追加带结构字段的notice，发生在原run_end之后；回放可看见后续保存事实。用量不增加，也不冒充write_report工具成功。
- 桌面在当前回答前显示自动保存说明，质量面板列保存来源。当前轮次与成果面板共用详情，避免两个轮询周期短暂显示相反状态。
- 内部来源JSON不能经read_evidence作为业务JSON产物证据；它只说明保存事实，不认证报告结论。

已有工具生成的Markdown不覆盖、不重新标为自动保存。正常研究、预算、审稿上限和模型提示均未改。没有按报告中的关键词寻找或改写已知错误句子，也没有补入题目答案。

## 验证

- 全量 **813 passed，54.05秒**；新增13项专项测试。覆盖实际预算停止、原文/换行保留、未审稿/草稿状态、三类保护原因、正式稿不覆盖、文件变化/损坏、回放、内部JSON读取限制。
- 多文件保存中断保持pending=fallback，不自动恢复；保存边界已提交后中断，恢复不增加模型调用、不重写报告/来源或重复保存notice。
- TypeScript/Vite构建通过。两次独立隔离窗口检查均通过15组流程，末轮额外核对中央/右侧状态一致，page_errors为空。截图已目视检查。
- 演示仅使用ScriptedLLM（两条合成调用记录），**真实模型请求为零**。没有运行新保留集或重评分旧结果。
- 第四批456个原文件与旧助手/人工副本哈希不变，37/39及原正式未验收状态保留；本轮助手39项已填完，不再等待用户操作。

本机证据在workspace：fallback-save-tests-1.log、fallback-save-full-tests-20261020.log、fallback-save-build-release-20261020.log、fallback-save-ui-release-20261020.log、fallback-save-release-20261020-ui-results.json、fallback-save-release-20261020-fallback-current.png、fallback-save-replay-20261020.log。首轮界面记录fallback-save-ui-20261020.log与截图也保留。版本与产物摘要见[机器记录](fallback-save-evaluation-20261020.json)。

## 查看效果

重启桌面应用加载新后端与界面。无需调用API的[演示报告](/D:/code/personal_agent/workspace/evaluations/fallback-save-demo-20261020/report.md)已生成，可直接阅读；回放命令：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m agent.replay workspace/evaluations/fallback-save-demo-20261020
```

开发复检：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_fallback_save.py -q
npm.cmd --prefix desktop run build
npm.cmd --prefix desktop run smoke:acceptance -- --evidence-prefix my-fallback-check
```

evidence-prefix用于另存界面日志数据与截图，不覆盖之前的阶段14证据；运行日志仍由调用者重定向保存。测试使用独立临时workspace及隐藏窗口，未操作用户的生产应用。

## 边界

保存说明与哈希是本地运行记录，不是签名、业务事实认证或完整审稿。模型原文中的其他错误仍可能存在；条件理解、来源充分性等语义问题保留。本次没有用旧题重跑证明质量提高。来源记录缺失的旧报告不补造、不迁移。

新代码继续采用阶段13的保守检查点规则；源码指纹变化使旧检查点拒绝恢复，不绕过或迁移。新增说明可能使未完成稿超出原篇幅约定，仍记录实际约定检查结果，不把它包装为完整交付。没有验收断电、跨机器或长期稳定性。
