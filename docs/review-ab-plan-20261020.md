# 固定报告的审稿方案对照

日期：2026-10-20。按用户授权，验证新增逐项核对是否改善审稿有效性；先写本约定，再冻结/运行。生产Agent、提示、评分与预算均不修改。

## 比较对象

- baseline：`92c612c:agent/review.py`的提示、JSON Schema及解析器。
- challenger：`e5d2575:agent/review.py`的提示、JSON Schema及解析器。
- 两份可信本仓库历史模块从Git对象读取，在当前依赖中隔离加载；不是旧版完整环境或端到端Agent对照。
- 两版收到逐字相同的user消息、报告、真实可定位的资料上下文与旧格式关联，保留新核对所需的claim_index；不提供新premises/reasoning字段。只有system提示/输出Schema/解析不同，不把新前提组织的收益也归因于这个实验。

`experiments/review_ab_cases.json`预先列出4类材料（单向条件、索引范围、版本与测试边界、相互冲突的记录），每类有正确/明确错误/保留未知三份报告，共12份。它们是开发助手设计的诊断样本，方向受到已知缺陷启发，不称外部独立保留集。所有预期/rubric都在运行前写定，不传给模型；报告ID、correct/incorrect/ambiguous标签及核对依据不进入模型输入。

每份报告仅有一项需判断的正文焦点，统一章节/限制/资料包。采用两版都支持的inference及相同“仅给定材料”的适用范围说明，避免把额外新字段或不同资料引入为自变量。机械检查没有预先标出语义错误。

## 运行约定

每版每稿两次，共48次独立上下文调用。固定seed=20261020随机排列24个报告/轮次对，成对交错baseline/challenger，并平衡每版先执行的次数。不让前次回复进入后次上下文。两轮仅观察波动，不是48个独立题目。

使用项目`.env`的DeepSeek-flash Key，temperature=0.3、thinking=disabled、JSON输出、每次最多2048输出tokens，无工具。实验实例关闭SDK/封装重试（max_retries=0），不修改项目配置；网络失败、响应用量缺失或中断立即保存并停止，不重采样补齐。截断/解析失败保留原始回复，不在本实验内纠正审稿或要求Agent修订报告。

累计预算300000等效tokens，沿用项目口径：未命中输入×1、命中输入×0.1、输出×2。每次发出前用估计输入加最大输出检查余量；达到保护则明确skipped_budget。该值是运行保护，不是账户人民币硬上限。缓存未报告时标为未知并以输入全额计预算，不冒充真实零缓存；报告原始输入/缓存/输出量、耗时和返回模型，不从token假定实际账单。

冻结脚本、题集、历史模块摘要、相关生产依赖、参数、请求及顺序；运行期间不改代码或规则。输出目录只新建，原始响应分别保存，逐次状态原子更新；未知结果不当成功。

## 预定判定与汇总

助手逐条读实际回复，填写独立副本并绑定逐次结果摘要；不是人工盲审，用户不需填写。

- 正确稿：有实质误报记false_alarm，无实质问题记clean。可选润色、要求额外研究或不改变事实的类型偏好不能当命中。
- 错误稿：意见实际定位并指出rubric中的关键逻辑/无据前提才记detects_error；没有或只有无关问题记misses_error。不能只按issues非空判命中。
- 未知稿：允许supported，因为其陈述已经恰当限定；保持未知记preserves_ambiguity。无依据要求选一方、给确定结果或无必要扩展研究记forces_resolution。不能因为模型返回uncertain这个单词就判其处理正确。
- 无法可靠判定记inconclusive；失败/未执行/无法读取回复记not_completed。另列格式完整/漏核对/解析错误/截断/未知用量，不能把这些从总分母隐藏。

每版24次，三类各8次；分别报告识错数、正确稿误报数、未知保留数及全部原始状态。比较同一报告/同一重复的成对差异和调用资源。即使全通过，也只说明这12份诊断报告的有限表现，不声称泛化、统计显著、端到端成功率或阶段12验收。

样本均为单条短结论、完整给定材料、人造明确错误或明确保留未知，不覆盖长网页、多结论合并、证据截断或真实复杂研究；容易出现两版都通过的上限效应。若出现该结果，应说明本实验未能区分识错能力，不能继续挑题直到新版获胜。

不因结果不理想修改样本、挑选重跑或加已知答案规则。本实验结束后结合漏报/误报/成本决定下一项；旧阶段12状态不改。

## 命令

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_review_ab.py -q
.\.venv\Scripts\python.exe -X utf8 -m experiments.review_ab freeze --out workspace/review-ab-plan-20261020
.\.venv\Scripts\python.exe -X utf8 -m experiments.review_ab run --lock workspace/review-ab-plan-20261020/lock.json --out workspace/evaluations/review-ab-20261020
```

先提交本方案与脚本，再正式冻结。freeze不请求模型，run才消耗项目API用量；workspace/review-ab-dry-20261020是开发期间仅准备请求的离线试用，不计实验结果。

冻结前检查：新增13项离线测试通过；全量850 passed（79.62秒）。首次全量848 passed后补充原始响应摘要及缺失试次校验，最终另跑850项；旧日志保留。未改agent/tools/main.py/desktop/desktop_app生产文件，运行源码指纹仍为89729932fe7b6011ce047ead97abf0e18c7a104e0c4bd3fe844451f366b65d57；本轮不需要重新构建桌面。
