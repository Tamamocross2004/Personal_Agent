param([string]$ProxyUrl = '')
$ErrorActionPreference = 'Stop'
if ($ProxyUrl) {
    $env:HTTP_PROXY = $ProxyUrl
    $env:HTTPS_PROXY = $ProxyUrl
}
if ($env:HTTP_PROXY -or $env:HTTPS_PROXY) { $env:ELECTRON_GET_USE_PROXY = 'true' }
$projectRoot = Split-Path -Parent $PSScriptRoot
& node -e "const [major,minor]=process.versions.node.split('.').map(Number);process.exit(major>22||(major===22&&minor>=19)?0:1)"
if ($LASTEXITCODE -ne 0) { throw '桌面依赖需要 Node.js 22.19 或更高版本。' }
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw '请先在项目中创建 .venv 并安装 Python 依赖。' }
Push-Location $projectRoot
try {
    & $pythonPath -m pip install -e '.[desktop]'
    if ($LASTEXITCODE -ne 0) { throw 'Python 桌面依赖安装失败。' }
    Push-Location (Join-Path $projectRoot 'desktop')
    try {
        & npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败。' }
        # Electron 44 按需下载运行时；显式安装以便快捷方式可直接启动。
        & node node_modules/electron/install.js
        if ($LASTEXITCODE -ne 0) { throw 'Electron 运行时下载失败；网络需要代理时使用 -ProxyUrl 参数。' }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw '界面构建失败。' }
    } finally { Pop-Location }
    $desktopPath = [Environment]::GetFolderPath('Desktop')
    $shortcutPath = Join-Path $desktopPath 'Personal Agent.lnk'
    $shellObject = New-Object -ComObject WScript.Shell
    $shortcut = $shellObject.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
    $shortcut.Arguments = '"' + (Join-Path $projectRoot 'scripts\launch-desktop.vbs') + '"'
    $shortcut.WorkingDirectory = $projectRoot
    $shortcut.IconLocation = (Join-Path $projectRoot 'desktop\node_modules\electron\dist\electron.exe') + ',0'
    $shortcut.Description = 'Personal Agent 本地研究工作台'
    $shortcut.Save()
    Write-Output "部署完成：$shortcutPath"
} finally { Pop-Location }
