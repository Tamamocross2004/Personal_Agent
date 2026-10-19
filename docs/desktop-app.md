# 本地桌面应用

首版采用 Electron + React + TypeScript + Vite，Python FastAPI 复用 `main.research`，SQLite 保存会话与运行索引。Electron 管理回环地址上的后端服务；每轮研究使用独立 Python 进程。API Key 继续只从项目 `.env` 读取，前端只有当前应用的临时访问凭证。

## 当前电脑启动

双击桌面 **Personal Agent**。也可以在项目根目录执行：

```powershell
.\scripts\launch-desktop.ps1
```

这是依赖当前项目目录的本地部署；请保留 `.venv`、`desktop/node_modules` 与 `desktop/dist`。移动项目后重新安装以更新快捷方式。首版没有跨电脑安装包、自动升级或开机自启。

## 从源码安装 / 更新

需要 Windows、项目 Python 虚拟环境以及 Node.js 22.19 或更高版本（本次部署使用 22.23.2）。在根目录执行：

```powershell
.\scripts\install-desktop.ps1
```

脚本安装 `.[desktop]` Python 依赖，执行 `npm ci`，显式下载 Electron 运行时、构建界面并创建当前用户的桌面快捷方式。首次需要下载 Electron，网络需要代理时使用你实际可用的地址，例如本机现有代理：

```powershell
.\scripts\install-desktop.ps1 -ProxyUrl http://127.0.0.1:7890
```

更新源码或修改前端后，关闭应用并重新执行安装脚本。只改前端也可以执行 `npm.cmd --prefix desktop run build` 后重启。正常启动不运行开发服务器。

## 怎样使用

1. 左侧新建研究，输入问题；设置中核对厂商、模型、最大步数和等效 token 预算。默认读取项目配置，目前为 `deepseek-flash`。每次发送都创建新任务并使用新预算。
2. 中间展示真实模型调用、工具参数与返回结果；点击展开查看。最终回答在研究结束后展示。首版没有逐 token 文本流，也不展示模型隐藏思考。
3. 右侧切换报告、文件、依据与质量。CSV 可直接查看，PNG/JPEG/WebP 可预览；点击依据卡片查看保留原文、选定摘录与读取元数据。外部链接在系统浏览器打开，本地 Markdown 链接打开文件预览。
4. “历史背景”用于下一轮。开启后优先使用本会话上一轮已结束报告，作为有日期、未认证的历史资料；没有本会话报告时沿用现有 CLI 的历史召回。关闭后不注入或保存研究记忆。当前问题始终单独提取任务约定。
5. 左侧可搜索、重命名和归档会话。“导入已有任务”只索引 `workspace` 顶层带 trace 的任务，不运行模型、不更改旧报告，不导入评估目录中的批量用例。索引保存在 `workspace/desktop.sqlite3`，原始产物仍在各自任务目录。

同一时间执行一项研究，其他任务排队。选择相应轮次可停止排队或运行中的任务。正常停止等待当前调用返回，在下一次模型/工具调用之前生效；收到的模型用量先记账。再次点击“强制停止”会结束所属工作进程树，已保存内容保留，部分结果可能不完整。强制停止不能撤销已经完成的工具操作或 API 消耗，未返回的 API 用量也可能无法记录。

关闭窗口结束所属后端及工作进程。再次打开不会重跑队列或恢复中断任务；新提交不是检查点恢复。阶段13的恢复与累计预算机制仍待实施。首次启动、查看历史及切换面板均不调用模型。

## 怎样解读质量

“研究结束”只表示执行收到最终回答。质量面板分别列出本轮参数、联网约定、结束原因、报告审稿、问题数与证据关联。`reviewed` 是模型审稿已完成，仍须结合问题列表；`skipped_limit`、`not_reviewed` 或 `stale` 不能解释为通过。`mapped` 表示当前版本的选定关联能定位，语义支持仍为 `not_certified`。

缺失或损坏的产物显示不可用。文本预览限2MB，文件读取限20MB，CSV预览限500行，执行过程最多保留400个事件（展示最近160个相关事件）；完整记录从“打开任务目录”查看。默认报告为 `report.md`，其他命名可从文件面板打开。

应用只监听 `127.0.0.1`，API要求应用临时凭证并拒绝跨来源调用；前端不接收 API Key。生成代码仍使用本项目现有本地执行工具，它不是系统权限沙箱。文件和记忆仍以当前 Windows 用户权限保存在本机。

## 检查与排错

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm.cmd --prefix desktop run build
# 先关闭已有应用；测试拦截提交，不创建研究、不调用模型
npm.cmd --prefix desktop run smoke
```

启动服务日志在 `workspace/desktop-service.log`；每轮完整事件为任务目录的 `trace.jsonl`。该目录中 `.desktop-console.log` 是工作进程输出，`.desktop-request.json`、`.desktop-result.json` 为内部状态，不在界面文件列表展示。不要将工作目录或 `.env` 发布到远程仓库。

设计参考：[Electron进程模型](https://www.electronjs.org/docs/latest/tutorial/process-model)、[React](https://react.dev/)、[Vite](https://vite.dev/guide/)、[FastAPI](https://fastapi.tiangolo.com/)。
