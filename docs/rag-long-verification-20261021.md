# 官方长文档检索诊断

协议和题目在`7711183`提交后才调用模型。完整三份资料共884,753提取字符、1,638片段：SQLite FTS5官方文档转TXT、Python Logging Cookbook转TXT、229页GNU Make原始PDF。原始HTML/PDF、转换结果和PDF截图均保留。参考[预定方案](rag-next-plan-20261021.md)和[原始机器记录](rag-long-verification-20261021.json)。

10题两臂全部完成，同一代际和候选池，源码指纹未变。55项相关检查通过，所有实际送达摘录另与原始文档对应页/区间逐字核对。

| 预先指定原文区间的送达情况 | 关闭重排 | 开启重排 |
|---|---:|---:|
| 有核对依据的问题 | 9 | 9 |
| 每题全部指定区间都送达 | 4/9 | 6/9 |
| 指定区间送达数 | 4/11 | 7/11 |

第10题问公司具体服务费用，文档没有公司费用依据，不计入有正标注的召回均值，返回的所有内容仍完整保留，最终是否正确表示未知留到端到端任务核对。

不能把上表称为语义回答通过率：它只检查在运行前固定的某段原文是否送达，并非穷尽同义依据。比如bm25问题的指定概述区间未送达，但实际返回片段103/105明确包含等价的排序方向解释，具备回答依据。本轮保留原计数，不事后增补标注来抬分。跨文档问题则只送达SQLite内容，未送达GNU Make的order-only依据，是实质覆盖不足。

`.PHONY`问题命中了隐式规则条款但没送达预定的两个用途概述区间。中文问题、英文长文、相似示例、同页重复解释和不同来源间的信息分配均比此前短商品标题更复杂。当前结果显示重排改善了部分指定区间送达，但没有解决跨文档覆盖和所有条款完整性；不据此调整题目、chunk、默认候选数或拒答阈值。

实际嵌入113次、352,113输入token；重排10次、39,802输入token、0输出token。无失败、无未知用量，未计货币费用。尚无聊天模型调用；端到端报告质量由预定五个任务单独检查，不能用本次定位计数替代。

复现需要原快照，使用新目录：

```powershell
.\.venv\Scripts\python.exe -m knowledge.long_probe prepare --documents workspace/rag-next-20261021/long-docs --output workspace/rag-long-new
.\.venv\Scripts\python.exe -m knowledge.long_probe retrieve --output workspace/rag-long-new
```

来源：[SQLite FTS5](https://www.sqlite.org/fts5.html)、[Python Logging Cookbook](https://docs.python.org/3.11/howto/logging-cookbook.html)、[GNU Make PDF](https://www.gnu.org/software/make/manual/make.pdf)。这些官方资料的版本快照用于诊断，不能据此声称当前所有版本或私有业务文档表现相同。本项不是阶段12独立验收。
