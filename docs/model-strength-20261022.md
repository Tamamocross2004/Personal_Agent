# 桌面模型强度

研究任务：点击输入框下方模型名称或左侧设置，选择“模型强度（思考）”，完成设置后用于下一轮。文件夹资料助手：在提问区域直接选择。修改选择不会改变已有任务或触发重新调用，续读继承原任务强度。

| 界面选项 | 官方DeepSeek参数 |
| --- | --- |
| 默认 | 沿用原配置；本项目deepseek-flash保持非思考 |
| 低 | thinking=enabled，reasoning_effort=low |
| 中 | thinking=enabled，reasoning_effort=high |
| 高 | thinking=enabled，reasoning_effort=max |

官方三个独立思考档为low/high/max，界面中档明确映射high。当前仅验证deepseek厂商、官方api.deepseek.com接口（含/v1、/beta路径）的deepseek-flash和deepseek-v4-pro；未知模型、其他厂商或代理接口禁用三档选项，后台同样拒绝，不猜测兼容参数或自动切换模型。能力查询只读本地配置，不调用模型。

依据2026-10-22核对的[官方思考说明](https://api-docs.deepseek.com/guides/thinking_mode/)与[Chat Completions参数](https://api-docs.deepseek.com/api/create-chat-completion/)。开启思考后不发送不生效的temperature；工具对话按协议回传服务实际返回的reasoning_content，不伪造或当作正文展示。必要协议内容计入上下文估算，随消息和检查点保留。任务、事件与文件夹导出保存所选强度和实际参数。

强度不会增加最大步数、任务Token预算、批数、文件夹1200输出上限、检索容量或重试次数。思考与正文共用输出额度，可能增加耗时、费用或导致正文截断；高档不保证更准确、不代替审稿。计费仍按服务返回的输入/输出Token，不重复另加思考Token。

研究设置的启动值可在本项目.env设 `LLM_STRENGTH=default|low|medium|high`；文件夹界面从“默认”开始，可独立选择。桌面选择保存在任务记录，不改写.env；重开应用后研究设置重新读取配置。API Key仍仅由本项目.env读取。旧历史没有强度字段则显示“旧记录未保存”，不回填、重验或自动重跑。研究恢复保留原检查点及代码版本匹配规则，不迁移旧检查点。

## 验证

13项强度专项通过：三档SDK实际请求、未知能力拒绝、工具协议回传和上下文计数、API校验及任务快照、worker使用保存的强度、文件夹续读与导出继承。初次工具测试误加了一个规划响应，修正夹具为真实两步协议后通过，生产行为未为夹具改动。

隔离真实Electron研究窗口验证三档选择、未支持厂商禁用、worker事件参数、预算不变和只读历史；文件夹窗口验证高档问答、切换选择后旧答案不变、分批、续读、引用、导出及撤销后只读历史。模型为明确测试替身。研究窗口首轮发现旧控件缺少明确可访问标签，补充标签；之后改用原生disabled属性核对选项状态，失败日志保留，没有放宽后台校验。只关闭测试自身进程，未关闭用户应用。

真实接口先登记计划与运行代码指纹，每档一次短请求，每次最多256输出Token、30秒、无重试。三档均被当前deepseek-flash接受并返回思考内容和正文：低档输入38/输出35，中档38/17，高档38/35，总计201 Token，均正常停止。只验证参数接入，不是质量评测或提升率结论；简单题都回答“4”不能证明高档更好。未估算人民币总费用。

真实脚本 `scripts/model_strength_check.py` 拒绝覆盖既有目录；计划、响应和用量在 `workspace/model-strength-live-20261022/`。

最终全量回归1062项通过（505.43秒），其中13项强度专项；TypeScript/Vite构建与两个隔离桌面流程通过。404份既有产物哈希核对一致，新增代码和真实调用产物未检出项目实际Key。阶段12未验收状态保持不变。机器记录见[验证数据](model-strength-evaluation-20261022.json)，截图与日志在workspace下的model-strength文件中。
