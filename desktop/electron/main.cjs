const { app, BrowserWindow, ipcMain, shell, dialog } = require("electron");
const { spawn } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");
const crypto = require("node:crypto");
const root = path.resolve(__dirname, "../..");
let backend = null,
  mainWindow = null,
  connection = null,
  quitting = false;
const token = crypto.randomBytes(32).toString("hex");
app.setName("Personal Agent");
if (!app.requestSingleInstanceLock()) app.quit();
app.on("second-instance", () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  }
});
function allowed(event) {
  return (
    mainWindow &&
    event.sender === mainWindow.webContents &&
    event.senderFrame === mainWindow.webContents.mainFrame
  );
}
ipcMain.handle("connection", (event) => {
  if (!allowed(event)) throw Error("Unauthorized");
  return connection;
});
ipcMain.handle("open-external", async (event, url) => {
  if (!allowed(event)) return;
  const u = new URL(url);
  if (["https:", "http:"].includes(u.protocol))
    await shell.openExternal(u.href);
});
ipcMain.handle("open-task", async (event, folder) => {
  if (!allowed(event) || typeof folder !== "string") return;
  const workspace = fs.realpathSync(path.join(root, "workspace"));
  const real = fs.realpathSync(folder);
  const relative = path.relative(workspace, real);
  if (
    !relative ||
    relative.startsWith("..") ||
    path.isAbsolute(relative) ||
    !fs.statSync(real).isDirectory()
  )
    throw Error("Invalid task directory");
  await shell.openPath(real);
});
function startBackend() {
  return new Promise((resolve, reject) => {
    const python = path.join(
      root,
      ".venv",
      process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
    );
    if (!fs.existsSync(python))
      return reject(Error("未找到项目Python环境，请先执行桌面安装脚本。"));
    const logdir = path.join(root, "workspace");
    fs.mkdirSync(logdir, { recursive: true });
    const log = fs.openSync(path.join(logdir, "desktop-service.log"), "a");
    backend = spawn(
      python,
      [
        "-m",
        "desktop_app.server",
        "--ui-dir",
        path.join(root, "desktop", "dist"),
      ],
      {
        cwd: root,
        windowsHide: true,
        env: {
          ...process.env,
          PERSONAL_AGENT_DESKTOP_TOKEN: token,
          PYTHONIOENCODING: "utf-8",
        },
        stdio: ["ignore", "pipe", log],
      },
    );
    fs.closeSync(log);
    let buffer = "";
    const timeout = setTimeout(
      () =>
        reject(Error("本地服务启动超时，请查看workspace/desktop-service.log")),
      25000,
    );
    backend.on("error", (e) => {
      clearTimeout(timeout);
      reject(e);
    });
    backend.stdout.on("data", (chunk) => {
      buffer += chunk.toString();
      let index;
      while ((index = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, index);
        buffer = buffer.slice(index + 1);
        try {
          const value = JSON.parse(line);
          if (value.event === "ready") {
            clearTimeout(timeout);
            connection = { url: `http://127.0.0.1:${value.port}`, token };
            resolve(connection);
          }
        } catch {}
      }
    });
    backend.on("exit", (code) => {
      clearTimeout(timeout);
      if (!connection)
        reject(Error("服务启动失败，请查看workspace/desktop-service.log"));
      else if (!quitting) {
        dialog.showErrorBox(
          "本地服务已停止",
          `研究不会自动重启。退出码：${code}`,
        );
        app.quit();
      }
    });
  });
}
app.whenReady().then(async () => {
  try {
    if (!fs.existsSync(path.join(root, "desktop/dist/index.html")))
      throw Error("界面尚未构建，请先执行桌面安装脚本。");
    await startBackend();
    for (let attempt = 0; attempt < 100; attempt++) {
      try {
        const response = await fetch(connection.url + "/api/config", {
          headers: { Authorization: "Bearer " + token },
        });
        if (response.ok) break;
      } catch {}
      if (attempt === 99) throw Error("本地服务未就绪");
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    mainWindow = new BrowserWindow({
      width: 1440,
      height: 940,
      minWidth: 1050,
      minHeight: 700,
      title: "Personal Agent",
      backgroundColor: "#151617",
      webPreferences: {
        preload: path.join(__dirname, "preload.cjs"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    });
    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
      if (/^https?:\/\//.test(url)) shell.openExternal(url);
      return { action: "deny" };
    });
    mainWindow.webContents.on("will-navigate", (event, url) => {
      if (!url.startsWith(connection.url + "/")) event.preventDefault();
    });
    await mainWindow.loadURL(connection.url);
  } catch (error) {
    dialog.showErrorBox(
      "Personal Agent 启动失败",
      String(error.message || error),
    );
    app.quit();
  }
});
app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => {
  quitting = true;
  if (backend && backend.exitCode === null) {
    if (process.platform === "win32")
      require("node:child_process").spawnSync(
        "taskkill",
        ["/PID", String(backend.pid), "/T", "/F"],
        { windowsHide: true },
      );
    else backend.kill("SIGTERM");
  }
});
