# 阶段12继续修复与第二批保留评估约定

用户在首批评估后要求继续修复。本轮保留原始35/39、原始产物和助手核对，不覆盖旧结果。

## 修复及开发范围

- 将实际工具派发返回记录为execution来源，observed仅可引用该来源；失败不成为calculation或web证据。用户要求不能代替执行记录，单次记录不能证明全任务次数或未发生的行为。
- URL失败说明仅在真实FetchError记录、非参考资料、非编号引用及无明确金额断言时作操作目标处理；引用来源仍须成功读取和记笔记。离线评分从真实trace重建同一状态。修正中文左括号被读入URL的边界问题。
- direct选定原句出现摘录未提供的数字时拒绝，明确错误claim索引及成功计算ID；这是有限数字出现性检查，不是语义认证。每条历史引用（包括unknown）的limits缺失一次列全，不自动补造日期或假设。
- 网页来源记录全文字符数、已送达区间及版本，向审稿/只读证据入口提供去重后的覆盖量；缓存全文不计已读。金额计算建议Decimal，不改独立答案的精度或容差。
- 补充审稿对费用/单价、加权、必要条件、引述、复合句和执行事实的约束，桌面显示observed标签。预算、步数和审稿上限保持不变。

14项新离线回归覆盖失败URL及反例、中文括号、执行来源类型、成功/失败隔离、direct新数字、历史逐项反馈和网页覆盖去重。全量732 passed。桌面TypeScript检查与Vite构建通过。

旧题仅用于开发：`agent/validation_fix_cases.json`选8题，首候选6/8（新增严格关联未收敛、浮点JSON尾差、中文括号），第二候选8/8。两次真实采样全部保留，不能当独立保留集。本轮提示及规则修正都在新保留题首次运行前结束。

## 第二批保留题

`agent/validation_holdout2_cases.json`，schema version=1，cohort=`stage12-fix-fresh-holdout-2`。在忽略目录先写定，再提交为版本化题集；新题未参与本轮开发运行。

正常10题：校准与温度的必要条件、分母给定但事件口径未知、宣传绝对保证与条件事实、千/十万单位及三位小数费用、含下降分项的加权账单、另一长文的多个条件与例外、一方有日期的冲突、新对象Cedar记忆建档/追问、Python zip惰性与strict错误边界。故障3题采用另一地址、算式与预算任务。预先写明rubric及独立JSON答案；题目由编码助手设计，不是外部独立出题或人工盲审。

门槛沿用首批：冻结后完整三轮，**每轮正常至少9/10、故障3/3**；每题15步、80000等效token，每轮500000软预算，审稿最多2次；预算故障为1，仍可能因收尾越过软限。DeepSeek-flash、thinking disabled、temperature 0.3、项目`.env` Key。所有失败保留，不据失败改实现/题目/评分，不用重采样替换。

另按预定rubric回放正文、选定证据和实际工具结果，编码助手意见与外部人工核对分开。自动通过不等于全文语义正确；未达门槛或有内容问题时不打stage-12标签。若新题被用于下一次修复，转为开发资料，需再建未用于修复的新题。

## 命令

下列run真实调用模型；freeze/rescore/review不调用模型，所有输出须新路径。

```powershell
.\.venv\Scripts\python.exe -m agent.validation --suite agent/validation_holdout2_cases.json freeze --split holdout --out workspace/stage12-fix-holdout2-lock.json
.\.venv\Scripts\python.exe -m agent.validation --suite agent/validation_holdout2_cases.json run --lock workspace/stage12-fix-holdout2-lock.json --rounds 3 --out workspace/evaluations/stage12-fix-holdout2
.\.venv\Scripts\python.exe -m agent.validation --suite agent/validation_holdout2_cases.json rescore workspace/evaluations/stage12-fix-holdout2/results.json --out workspace/evaluations/stage12-fix-holdout2-rescore
```

使用review-template.json逐题记录pass/fail/uncertain、findings及reviewer_kind，保留产物哈希。任何只读复评另存，原始分数不改；新旧题目不同不构成模型能力提升的因果A/B比较。
