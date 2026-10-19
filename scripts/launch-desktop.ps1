$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$electronPath = Join-Path $projectRoot 'desktop\node_modules\electron\dist\electron.exe'
if (-not (Test-Path -LiteralPath $electronPath)) { throw '请先运行 scripts/install-desktop.ps1。' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'desktop\dist\index.html'))) { throw '请先构建界面。' }
# 某些编辑器以 Node 模式运行 Electron；该变量不能传给桌面应用。
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
Start-Process -FilePath $electronPath -ArgumentList ('"' + (Join-Path $projectRoot 'desktop') + '"') -WorkingDirectory $projectRoot
