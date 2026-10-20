# 阶段12冻结评估约定

2026-10-20结果：三轮已完成，未达到门槛；见[本次结果](stage12-results-20261020.md)。v1保留题已揭盲，以下重跑命令仅用于复现本次历史题；后续修复必须另建新保留集。

本阶段评估内容质量与泛化，不增加步数、审稿次数，也不实现阶段13的中断恢复。2026-10-20开始；阶段11已知失败用于开发，不计为独立验收题。

## 题集与隔离

`agent/validation_cases.json` v1：开发5题，覆盖资源关闭的保证边界、缺失不等于零、原始资料与计算分类、费用加权与规模、必要条件。保留13题，其中正常10题（自然语言授权、歧义、引述反驳、计价单位、加权变化、长文条件、来源冲突、真实记忆建档与追问、真实联网文档），故障3题（网络不可用、计算工具失败、预算不足）。各题独立写出核对依据，费用JSON使用预先计算的Decimal答案，不由被测模型生成答案。

固定网页是虚构`.invalid`资料，由httpx MockTransport送入实际fetch提取、定位、笔记与证据路径；不会访问该域名。网络故障也关闭Jina。工具故障仅替换本次registry里的calculator，不影响正式应用。真实联网文档单独一题，不把固定网页当作外网可用性证明。每轮独立记忆SQLite数据库，不接触用户长期记忆；建档失败则同轮追问记录skipped_dependency，不偷换背景。

## 预先确定的门槛

- 冻结代码、所有题目、评分器与运行配置后完整三轮；每轮正常任务自动通过率至少90%（至少9/10）；故障状态正确性100%（3/3）。失败后继续跑完整矩阵，全部样本保留，不重采样替换。
- 每题15步、80000等效token（预算故障为1）、每轮500000软预算、最多2次审稿；DeepSeek-flash、temperature=0.3、thinking=disabled，项目`.env` Key。不记录或展示Key。预算是现有软保护，规划/收尾可能越过给定值，不声称硬费用上限。
- 正常任务必须有最终报告、正常结束、当前版本机械检查和无问题审稿；需要证据的题同时核对关联版本。故障任务分别检查真实错误是否发生、未确认项是否披露；预算不足必须保留budget及未认证审稿，不当正常任务成功。
- 每份产物还须按rubric核对正文、结论、条件、数字、证据类型及摘录。无实质内容错误或未决分歧才可验收。编码助手回放记录与外部人工核对分开，不能冒充外部人工盲审；本工具不能验证声明的核对人身份。
- 三轮期间不改代码、提示、题集或评分规则。保留题若被用于修复，整批标为已揭盲/开发资料，未来须另建新保留集。新增任务来自本次设计，独立性指未用于本次修复，不表示来自外部独立出题者。
- 未执行、被跳过或审稿不足均不算通过；原始分数不可覆盖。评分修正仅通过rescore新目录只读输出，核对原产物哈希，不能据重评分宣称模型能力提升或替换三轮原始门槛。

## 统一入口

冻结文件和输出目录必须使用新路径；先运行pytest，再提交实现，再冻结。freeze/复评不发送模型请求，run发送真实请求并消耗项目额度。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m agent.validation freeze --split holdout --out workspace/stage12-holdout-lock.json
.\.venv\Scripts\python.exe -m agent.validation run --lock workspace/stage12-holdout-lock.json --rounds 3 --out workspace/evaluations/stage12-holdout-v1
.\.venv\Scripts\python.exe -m agent.validation rescore workspace/evaluations/stage12-holdout-v1/results.json --out workspace/evaluations/stage12-rescore-v1
```

阅读输出report.md/results.json和逐题目录，用`agent.replay`及`agent.evidence`离线核对。复制review-template.json到新文件，逐题填写verdict(pass/fail/uncertain)、findings、reviewer和reviewer_kind(human/coding_assistant)，保留原始哈希。

2026-10-20工具更新：旧无产物空值已兼容；可用 `template 原results.json --out 新目录` 从旧结果重新导出带原始结果摘要的新表，须在命令前用 `--suite` 指定对应冻结题集。未填写项可保留pending，复核输出进度且保持not_accepted；输入错误退出2，不创建空输出目录。导出成功退出0不代表质量通过。原始分数和已有人工表均不改写，详见[工具优化与命令](review-tool-compatibility-20261020.md)。

```powershell
.\.venv\Scripts\python.exe -m agent.validation review workspace/evaluations/stage12-holdout-v1/results.json workspace/stage12-review.json --out workspace/evaluations/stage12-reviewed-v1
```

`run`的退出码0仅代表三轮自动门槛，不代表阶段验收；只有原始三轮门槛通过且每题外部人工核对无实质问题，review才写accepted。没有外部核对或门槛未达到，保留not_accepted，不打stage-12验收标签。
