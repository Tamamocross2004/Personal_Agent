# 阶段12：通用证据机制改进

本轮按用户要求，只增加可以独立验证的工程能力，不以已知保留题的正确句式调整提示或评分规则。不修改验收门槛，不增加预算、审稿次数，不覆盖此前35/39和38/39的结果。

## 改动范围

- `agent/artifacts.py`：共用JSON产物读取器。仅接受任务目录内普通JSON文件名，排除内部记录和符号链接；最多读取65536字节、展示4000字符，完整样本才提供顶层类型及至多32个字段类型。非法编码、非标准NaN/Infinity等不认证为有效JSON。未展示部分不推断字段。
- `read_evidence(artifact_filename="output.json")`：显式读取并登记`artifact`来源，保存原始文件字节SHA-256、样本及类型。相同快照复用ID，改写后重新登记；不会扫描所有任务文件，不调用模型或执行文件。
- `observed`结论可以引用实际文件快照；`direct`和`calculated`不能用文件快照替代资料原文或成功计算。写稿、复用旧关联、重读该ID、离线审计时检查当前文件哈希，文件改写或删除使旧关联失效。仅所引用版本受检查，历史未引用快照可保留。
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

单轮返回码1是正常的“未满足三轮自动门槛”标记，不等于全部题目失败；逐题结果须另看report.md/results.json。不因某题失败修改代码并替换本轮结果。

阶段12整体仍未验收。本轮先交付机制和开发集成证据，后续独立保留集完整三轮及外部人工核对仍须单独进行；旧保留集不能重新包装成独立题。

## 本轮检查

- 全量测试：753 passed，135.27秒，含新增18项机制契约检查；日志`workspace/stage12-mechanisms-tests.log`。
- 桌面TypeScript检查和Vite构建通过。未关闭或重启用户正在使用的桌面应用。
