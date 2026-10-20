# 逐条结论、前提与审稿缺口

日期：2026-10-20。接续自动保存来源修复；本项改善依据组织与审稿反馈，不认证任意语义蕴含，不修改旧题答案或放宽验收。

## 改动

- `Claim`增加`reasoning`和最多4项`premises`。推断可以说明从已有前提到结论的步骤，不再强制新增假设；旧`assumptions`仍支持。`evidence`前提须有本次真实摘录，`assumption`和`unknown`不附来源，不能冒充资料事实。原有报告逐字定位、来源/文件版本、历史限制及数值约束同样检查前提中的引用。
- 一项结论有多个判断时，作者可逐字选择不同子句分开关联，不要求在正文新增审计表或扩充报告。程序不凭标点或关键词判定句子是否正确。
- 共享证据包保留原关联索引，包含前提的真实上下文。整体超出原14000字符包上限时省略整项，仍明确记录省略，不能把缺少的来源当已读。
- 审稿返回`claim_checks`：逐项给出支持/矛盾/依据不足/不确定、所指字段、理由、证据缺口和建议。它是模型意见，引用定位和`mapped`继续不认证结论成立。
- 关联字段中的实质问题通过程序绑定的报告原句进入原有修订路径，不要求审稿编造正文引文。缺少逐项意见或包省略项记为`partial`，不视为空问题成功，也不额外触发无限重试。
- 关联变化（即使正文相同）也重新绑定审稿。桌面展示推导、前提、来源和模型依据核对；过期版本不继续展示当前审稿意见。

原有8项关键结论上限、15步试跑、每题80000等效token、两次审稿及2048审稿输出上限保持不变。新字段可能增加输入和输出成本，并可能在现有预算下出现审稿不足；如实保留，不据此扩大额度。

## 检查与试跑约定

新增离线契约覆盖：无额外假设的推导、不同前提来源、非法引用写入前拒绝、历史限制、前提版本变化、包省略与索引、缺少逐项意见、关联字段问题进入修订、正文不变而前提修订后的复审及原调用次数限制。合成模型只验证程序机制，不计为语义能力证据。

新增题集`agent/claim_support_smoke_cases.json`预先写明4项任务与rubric：分组条件推导、版本与实例主体、日志检索窗口、汇总计算和实际文件读取。在实现冻结后仅跑一轮；全部原始结果及助手复核另存。题目由本次编码助手设计，不是外部出题或盲审；没有旧版同题对照，不得宣称模型质量或费用改善。

沿用`agent.validation`的冻结、运行和逐份复核入口，不修改评分器，也不将单轮`automatic_gate=false`伪称三轮通过。用户不用填写表，助手在独立副本完成核对。旧阶段12原始37/39及助手24/2/13、未验收结论不变。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_claim_support.py -q
npm.cmd --prefix desktop run build
npm.cmd --prefix desktop run smoke:acceptance -- --evidence-prefix my-claim-support-check
```

首轮桌面检查通过了新增显示与多数流程，但在检查中修改源码后，已有夹具检查点被源码指纹正确拒绝；该轮失败日志保留，停止修改后完整重跑。

冻结前复检：全量837 passed（55.22秒），其中新增24项；TypeScript/Vite构建通过，15组隔离桌面流程通过、page_errors为空，新增前提/推导/模型意见截图已目视核对。第四批456个原文件哈希不变。日志：workspace/claim-support-full-tests-release-20261020.log、claim-support-build-20261020.log、claim-support-ui-release-20261020.log及claim-support-release-20261020-ui-results.json。此前836项测试通过后追加了异稿证据包拒绝测试，最终全量另跑837项；未替换失败日志。

## 冻结后的真实单轮结果

实现冻结在`5e6089a`，使用项目`.env`中的DeepSeek-flash配置，temperature=0.3、thinking=disabled；未在四项运行期间改源码、题目、规则或预算。原始自动4/4，4份最终稿均reviewed、关联包完整；模型返回18条supported意见，但这不替代逐份复读。

|任务|自动|编码助手复核|发现|
|---|---|---|---|
|档案分组与资格|pass|uncertain|主体结论保留未知；“不能反推”与“逆命题不成立”连写可能把未被证明扩大为为假。|
|版本、默认值与实例|pass|pass|三类主体区分，乙使用哪个修订与S默认值未补造；资料与推断分别关联。|
|日志检索窗口|pass|uncertain|“未提供其他”缩成“无其他”可能扩大缺失范围；某项unknown选摘只是用户要求，真实缺口仍需共享原文上下文支持。|
|台账总量与文件|pass|pass|独立复算40/30/10，实际文件三键均为整数；输入/计算/读取分类最终分开，未推成合格或现实库存证明。|

助手复核已完成，无pending；身份为同一开发/出题编码助手，非独立人工或盲审。原始自动分数不改。`agent.validation review`退出1，review_status=complete、acceptance=not_accepted；用户无须填写。单轮不是原三轮门槛，本项完成工程交付，不给阶段12打验收标签。

合计18步、53.34秒任务耗时、70665等效tokens、26次已记录模型调用（含4次审稿）。审稿输入24981 tokens、输出1071 tokens；四份证据包估计共5863 tokens，是包的估计量，不是相对旧版的新增成本。没有同题旧版对照，不声称节省费用或提高成功率，也不把token折算为实际账户扣款。

6条推断实际使用reasoning且assumptions为空，显示模型能够使用新机制；本次没有触发真实模型的负面逐项意见，负面意见进入修订的机制仅由离线测试验证，不能宣称这轮证明漏错减少。模型审稿仍遗漏上述两处不确定解释。总量题初稿把6/4混入direct被已有数值约束拒绝，日志题初稿超长被已有篇幅约束拒绝，随后恢复；这不是新增审稿的功劳，不删失败轨迹。

54个新任务原文件与456个旧文件均核对未变；评分入口`assess/aggregate/number_tree/assess_run`相较修复前AST不变。新增前提定位进入证据核对，因此冻结整体摘要会变化，不把摘要相同作为声称。

本机结果和已填助手表：

- [原始结果](/D:/code/personal_agent/workspace/evaluations/claim-support-smoke-20261020/results.json)
- [助手意见](/D:/code/personal_agent/workspace/evaluations/claim-support-smoke-20261020-assistant-review/review-decisions.json)
- [正式只读复核输出](/D:/code/personal_agent/workspace/evaluations/claim-support-smoke-20261020-assistant-review/checked/review.json)
- [机器记录及版本摘要](claim-support-evaluation-20261020.json)

查看报告与回放无需额外模型调用。例如：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m agent.replay workspace/evaluations/claim-support-smoke-20261020/cases/01-s_chain
```

这四题现已揭盲，后续只能作为开发资料，不再冒充独立验收。本轮不据这些具体措辞继续添加拦截规则。

## 兼容性

旧证据格式可读取，不补造前提或逐项审稿。新的模型审稿缺少`claim_checks`时保留未完成；旧历史审稿不重写。源码变化仍使旧检查点拒绝恢复，首版不迁移。外部人工不再作为开发等待事项，正式阶段12仍按原标准保持未验收。
