# 指定文件夹资料助手

阶段15已完成本机只读目录接入；阶段16多格式内容预览接续实现。阶段17的索引同步、18问答及19导出不在本次范围。阶段12仍未验收。

桌面重启后，左侧点击“资料文件夹”→“选择资料目录”→使用系统目录选择器→“扫描目录”。界面显示完整相对路径、格式、大小、修改时间和排除/不支持/超限原因。同名文件按目录区分。没有目录问答、自动建库或云端API调用。

目录授权由原生主进程的单独凭证登记，渲染页面的本地API凭证不能授权任意路径。读取按目录、扫描ID和文件ID定位，不能传任意绝对路径。撤销授权后禁止读取和扫描，已有清单保留，不等于彻底删除历史数据。

默认最多2000项（含目录）、200MB总文件字节、单文件20MB、深度12。达到上限明确标记扫描未完成；不静默截取。排除`.env*`、常见密钥、版本库/依赖、应用索引与任务目录；跳过链接、junction、云端占位、网络路径和多硬链接。目录与文件在使用时重新核对身份；读取前核对实际文件句柄路径及扫描身份，前后变化拒绝混读。这是本功能的读取边界，不是整个应用的操作系统沙箱。

CLI（不调用模型）：

```powershell
.\.venv\Scripts\python.exe -m folder_assistant add "D:\资料"
.\.venv\Scripts\python.exe -m folder_assistant list
.\.venv\Scripts\python.exe -m folder_assistant scan <返回的目录ID>
.\.venv\Scripts\python.exe -m folder_assistant show <目录ID>
.\.venv\Scripts\python.exe -m folder_assistant revoke <目录ID>
```

授权与扫描清单默认在`workspace/folders/catalog.sqlite3`，不提交用户文件。`--state-dir`可给CLI指定隔离状态目录。

阶段15验证：目录边界/文件变化/多硬链接/容量/原生授权5项通过；与既有桌面接口合计28项通过；TypeScript/Vite构建通过；真实Electron入口在临时目录验证原生选择器、扫描、排除项、渲染端不能伪造授权、撤销及源文件不变。截图`workspace/stage15-folder-ui-20261021.png`已查看，无页面错误。测试中修正Windows DirEntry缓存属性缺少身份/硬链接数的问题，使用实际lstat；初次失败日志保留。Electron测试的隔离服务启动夹具也修正了uvicorn调用形式，未关闭用户桌面或修改正式目录授权。
