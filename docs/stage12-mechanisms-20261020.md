# 阶段12：通用证据机制改进

本轮按用户要求，只增加可以独立验证的工程能力，不以已知保留题的正确句式调整提示或评分规则。不修改验收门槛，不增加预算、审稿次数，不覆盖此前35/39和38/39的结果。

## 改动范围

- `agent/artifacts.py`：共用JSON产物读取器。仅接受任务目录内普通JSON文件名，排除内部记录和符号链接；最多读取65536字节、展示4000字符，完整样本才提供顶层类型及至多32个字段类型。非法编码、非标准NaN/Infinity等不认证为有效JSON。未展示部分不推断字段。
- `read_evidence(artifact_filename="output.json")`：显式读取并登记`artifact`来源，保存原始文件字节SHA-256、样本及类型。相同快照复用ID，改写后重新登记；不会扫描所有任务文件，不调用模型或执行文件。
- `observed`结论可以引用实际文件快照；`direct`和`calculated`不能用文件快照替代资料原文或成功计算。默认`artifact_version=current`，写稿、复用旧关联和离线审计时检查当前文件哈希，改写或删除使当前内容关联失效。显式`snapshot`只描述记录时的历史快照，必须有limits说明历史范围、仅用observed并全部关联artifact；不能证明当前内容。重读旧ID返回记录时间及current/changed/unavailable版本状态，不把仍存在的历史观察抹去。两种语义的正文是否正确仍需审稿。
- 审稿JSON样本与文件证据使用同一读取器，携带字节哈希。不读取内部记录，超过上限的文件明确标为未检查，不把执行成功当文件内容证明。
- 网页结论增加可选`scope=excerpt/whole_document`。声明全文范围时，所有关联网页必须在各自同一URL、同一页面文本哈希下完整送达，版本未知不能认证；范围并集去重。在线验证和离线审计共用覆盖计算，旧版未填scope的关联仍可读取。
- 桌面observed标签改为“工具 / 文件实读”。

## 能力边界

文件内容存在、类型正确不证明业务数值正确，也不证明真实库存、服务状态或外部副作用。全文送达不证明结论成立。`scope`是作者声明，不能机械发现所有正文中隐含的范围扩大；未填scope不代表已读全文。定位及哈希通过仍不认证语义。

本轮没有增加已知费用题、比较题的答案模板或特定错误词组拦截，没有实施新的语义问题分类或模型自报“已修复”即结案的机制。已有历史规则不在本轮大范围删除，避免未经验证的行为变化。

## 验证约定

`tests/test_artifact_evidence.py`使用不同类型和内容检查契约：真实文件类型、改写/删除、路径边界、符号链接、读取上限、非法编码/JSON、阅读范围并集与版本、旧关联兼容及脚本化Agent全流程；不会把模型回答的同义措辞当测试答案。

`agent/validation_mechanism_cases.json`是开发集：配置导出、文件两次写入后的最终版本、完整短文范围，以及网络/计算/预算故障，共6题。实现通过检查并提交后冻结，只跑一轮真实集成检查，不作为新保留集，不把单轮或这6题通过称作阶段12验收。使用DeepSeek-flash和项目`.env`配置，现有15步、80000等效token、2次审稿上限不变，预算故障为1。

命令（产物目录必须是新路径）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd desktop
npm.cmd run build
cd ..
.\.venv\Scripts\python.exe -m agent.validation --suite agent/validation_mechanism_cases.json freeze --split development --out workspace/stage12-mechanisms-lock.json
.\.venv\Scripts\python.exe -m agent.validation --suite agent/validation_mechanism_cases.json run --lock workspace/stage12-mechanisms-lock.json --rounds 1 --out workspace/evaluations/stage12-mechanisms
```

单轮返回码1是正常的“未满足三轮自动门槛”标记，不等于全部题目失败；逐题结果须另看report.md/results.json。修复后的开发复测必须另存结果，不能替换旧失败结果。

阶段12整体仍未验收。本轮先交付机制和开发集成证据，后续独立保留集完整三轮及外部人工核对仍须单独进行；旧保留集不能重新包装成独立题。

## 本轮检查

- 全量测试：753 passed，135.27秒，含新增18项机制契约检查；日志`workspace/stage12-mechanisms-tests.log`。
- 历史快照契约及通用报告模板修正后：全量755 passed，63.65秒；日志`workspace/stage12-mechanisms-tests-final.log`。新增测试不以m_replace答案措辞作为判断。
- 桌面TypeScript检查和Vite构建通过。未关闭或重启用户正在使用的桌面应用。

## 首轮真实集成检查及通用契约修正

冻结`9f5f6d8`，6道开发题单轮：正常2/3、故障3/3，合计5/6。47步、65次完成的模型调用（含5次审稿）、152362等效token、任务用时合计121.95秒。原始结果位于`workspace/evaluations/stage12-mechanisms`，不覆盖、不重评分替换。

失败为m_replace：最终JSON正确且最终版本已读取，但报告同时描述初次读取的旧版本。首版契约把所有旧快照关联一概判为失效，模型反复重读仍引用该历史记录，最后max_steps、未完成审稿。回放后认定不能仅归因于模型：**“当时读到了什么”与“当前文件是什么”是不同命题，旧文件被改写不应抹去真实历史观察。** 因此以显式current/snapshot字段修正通用契约，并补充跨文件内容的离线测试；不按题目文字识别豁免，不改变默认当前内容检查，不把模型自报“历史”视为语义正确。

其他局限：配置报告用Python解析类型名bool/str/int描述JSON类型，业务值与实际文件一致，但不是JSON原生类型名称；网页报告仍含“未检索其他来源”等未由网页原文证明的执行全称声明；预算题自动pass仅表示真实budget结束且未虚报审稿，其收尾文本有“文件未写入”与兜底文件实际存在的表述差异，不认证内容正确。

首轮自动报告沿用了旧模板的“独立评估/三轮/另有真实联网题”固定文字，本开发集实际仅一轮、无真实外网题；原始report.md不改写。后续只修正通用报告模板为实际轮次数及环境以冻结题集为准，并增加离线契约测试；评分和原始JSON不变。

最终契约只对同一开发集另作单轮回归，使用`workspace/stage12-mechanisms-final-lock.json`与`workspace/evaluations/stage12-mechanisms-final`新路径。此时题目已用于发现缺陷，不能称新独立保留验收，无论复测是否通过均保留两轮原始结果。

## 最终开发回归

冻结`a00d894`，相同6题单轮：正常3/3、故障3/3。43步、58次完成的模型调用（含6次审稿）、130721等效token、任务用时合计112.76秒。m_replace分别用artifact-2/current证明最终文件、artifact-1/snapshot和limits描述旧快照，最终报告完成审稿。预算题保留budget/not_reviewed，自动pass只针对预期未完成状态。两轮均因仅一轮返回退出码1，automatic_gate=false。

两轮144项原始产物哈希与结果记录一致，未覆盖首轮失败、未重评分改写；机器摘要见[版本与逐题记录](stage12-mechanisms-evaluation-20261020.json)。模型请求均由现有评估入口使用项目`.env`的DeepSeek-flash配置，未读取系统Key或更换模型。两轮合计123次完成调用、283083等效token，不包含未记录的服务端尝试，也不是人民币账单。

仍有边界：最终网页报告将“预约取得时段”概括为“先预约”，是否扩大为所有领取者的必要前提尚需人工核对；若干执行次数/未操作声明只引用单次返回或网页，不足以单独证明全程执行情况。预算收尾还触发既有direct数字检查对裸写`user-1`引用的误拦，未在本轮追加针对该文字的例外。以上不因6/6而宣称已解决，不将模型审稿无问题视为独立事实认证。

本轮交付的是通用机制、契约测试及开发集成证据，**阶段12整体仍未验收，不打stage-12标签**。新的独立保留集完整三轮及外部人工核对未在本轮实施。
