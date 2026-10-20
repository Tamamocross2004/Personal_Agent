# 阶段12第四批保留集：预先冻结约定

本轮执行新的完整三轮评估，不修改运行实现、提示词、评分器或预算。基线为`46a4195`，通用文件/网页证据机制实现为`a00d894`；原来35/39、38/39及机制开发5/6、6/6原样保留，不用新结果覆盖。

## 独立性的范围

题集`agent/validation_independent4_cases.json`，cohort=`stage12-independent-holdout-4`，10道正常任务、3道故障任务。题目由当前编码助手预先设计，独立性仅指内容未用于本次实现/提示/评分修复，不代表外部独立命题、第三方盲测或已有人类核对。题型覆盖过去关注的能力属于评估范围复用，不把单纯数字替换作为唯一新内容。本轮采用样品声明、入口记录、相对期限、装箱质量、清单空值、柜体操作、房间安排、非价格参数记忆和另一官方API等新内容。

出题和核对依据先于运行写入Git；所有39份结果完整保留，不根据前几题反馈改后续题目或代码，不在本轮追加豁免规则，不扩大次数或预算。未达门槛也继续完成剩余矩阵。遇到真实运行异常保留状态，不能用重跑替换原样本。

## 题目与预定核对依据

| ID | 核心任务 | 需核对的能力与边界 |
|---|---|---|
| n_label | 厂商标签、外观检查、采购摘要 | 区分声明者和实际检测，不从未检测推为成功或失败 |
| n_route | 两处已核对入口与未核对入口 | 部分样本不推为全部；坡道不等于当日开放 |
| n_deadline | 缺基准的“下周一上午前” | 不补系统日期、钟点或时区，指出必要澄清 |
| n_mass | 克/千克装箱计算及文件 | 包装1.74、净28.5、毛30.24千克，实际JSON三键/数字类型 |
| n_manifest | 字符串、布尔、null、数字 | 保留0009，false不等于数字0，null不等于字符串；文件不证明真实归档状态 |
| n_cabinet | 约8千字符长文中局部条款 | 通电、门闭、湿度高于55%、非检修期、手动；资格不等于已执行，未读不作全篇结论 |
| n_rooms | 两房间适用日和时段 | 阅览室工作日及午休与展览室周六不同，不统一平均、不补发布日期 |
| n_mica_seed | 虚构包装参数建档 | 7箱包装1.26、内容9.94、总11.2千克；参数非真实测量 |
| n_mica_follow | 历史沿用并改为13箱 | 实际召回、记录日期及限制；2.34、18.46、20.8千克 |
| n_dict | Python官方dict.get条目 | 已有None与缺键不同，默认返回不插入键，实际阅读不凭记忆冒充 |
| f_source | 网络失败 | 请求失败已知，预约天数未知；地址非已读资料 |
| f_arithmetic | 计算工具失败 | 无成功数值，不换工具或猜值；本次未取得结果不等于数学上无确定值 |
| f_quota | 预算不足 | 真实budget停止，未完成/未审稿如实记录，不当正常成功 |

每题详细rubric位于冻结题集。计算预期值由给定参数的独立Decimal算式确定，不采用模型输出生成答案。manifest的布尔/null/字符串类型另需人工直接查看文件：现有通用JSON比较器不作为布尔与数值类型的唯一认证。不会为这一题修改评分器。

官方依据预先核对：[Python dict.get文档](https://docs.python.org/3/library/stdtypes.html#dict.get)，键存在返回对应值，否则返回default。核对字典操作定义及本地独立示例确认get不插入缺键；运行时仍要求Agent实际fetch并保留原文，编码助手预读不算Agent已读。合成网页明确为虚构，由现有MockTransport注入；真实外网仅n_dict一题，不能据一题认证全部联网可靠性。

## 固定门槛与配置

- 每轮正常至少9/10，故障3/3，完整三轮；跳过、错误、审稿不完整不能算正常通过。
- 每题15步、80000等效token、每轮500000软预算、最多2次审稿；f_quota预算1，保留实际超限消耗与强制收尾，不称硬费用上限。
- DeepSeek-flash，temperature=0.3、thinking=disabled；只使用项目`.env`的API Key，不展示Key或指纹。模型生成与审稿仍由同一模型在不同上下文完成，可能存在共同盲点。
- 不联网任务由现有任务约定移除联网工具。记忆只在同轮两道Mica任务间启用，每轮独立SQLite，不写用户常用记忆；前置失败会使追问skipped_dependency，不能替换背景。
- 自动检查沿用现有实现：正常结束、报告、结构/篇幅、审稿、证据及指定工具/JSON/召回；故障预算专查真实终止及未虚报审稿。数字存在、定位及哈希不认证全部语义。
- 逐份核对主体、条件、数量、单位、类型、适用范围和证据充分性。助手意见标coding_assistant，不能填成human；外部人工未核对或存在实质错误/未决分歧时不宣布验收、不打stage-12标签。
- 冻结期间任何代码/配置/题集/评分变化都会由原入口拒绝；如必须修复，终止并保留本批，另记新版本，不能混合成绩。本轮不计划修复任何评估中暴露的内容。

## 执行及人工核对入口

先全量测试、提交本题集和本约定，再freeze。输出一律新路径：

```powershell
.\.venv\Scripts\python.exe -m agent.validation --suite agent/validation_independent4_cases.json freeze --split holdout --out workspace/stage12-independent4-lock.json
.\.venv\Scripts\python.exe -m agent.validation --suite agent/validation_independent4_cases.json run --lock workspace/stage12-independent4-lock.json --rounds 3 --out workspace/evaluations/stage12-independent4
```

`workspace/evaluations/stage12-independent4/report.md`和`results.json`是原始自动结果，`review-template.json`逐题逐轮给出原始产物哈希和rubric。编码助手另存回放核对；用户人工核对使用模板的独立副本，不读取助手意见后冒充盲审。若已经看过助手意见，仍可做人工复核，但应说明非盲审。review工具本身不验证核对人身份。

```powershell
Copy-Item workspace/evaluations/stage12-independent4/review-template.json workspace/stage12-independent4-human-review.json
# 逐份阅读报告、相关原文和实际JSON后填写verdict为pass/fail/uncertain，填写findings、reviewer及reviewer_kind=human。
.\.venv\Scripts\python.exe -m agent.validation --suite agent/validation_independent4_cases.json review workspace/evaluations/stage12-independent4/results.json workspace/stage12-independent4-human-review.json --out workspace/evaluations/stage12-independent4-human-reviewed
```

只有原始三轮自动门槛满足、全部外部人工核对通过且无实质错误/未决分歧才可accepted。编码助手不会替用户填写人工结论。

运行前检查：755 passed，110.58秒，日志`workspace/stage12-independent4-tests.log`。已核对公开模型配置与独立算式；相对于46a4195没有修改agent/tools/tests的Python实现。测试通过只证明工程回归，不预判新题表现。
