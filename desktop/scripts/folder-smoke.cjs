const { _electron: electron } = require("playwright");
const fs = require("node:fs"),
  path = require("node:path"),
  os = require("node:os"),
  assert = require("node:assert/strict");
const project = path.resolve(__dirname, "../.."),
  fixture = fs.mkdtempSync(path.join(os.tmpdir(), "agent-folders-"));
const docs = path.join(fixture, "资料 空间");
fs.mkdirSync(docs);
fs.writeFileSync(path.join(docs, "readme.txt"), "本地资料原文。");
fs.writeFileSync(path.join(docs, ".env"), "SENSITIVE=must-not-read");
const wrapper = path.join(fixture, "harness.cjs");
fs.writeFileSync(
  wrapper,
  `
const electron=require('electron'),cp=require('node:child_process'),fs=require('node:fs');
electron.app.disableHardwareAcceleration();electron.app.setPath('userData',${JSON.stringify(path.join(fixture, "user-data"))});
const Native=electron.BrowserWindow;electron.BrowserWindow=class extends Native{constructor(o){super({...o,show:false,webPreferences:{...o.webPreferences,offscreen:true,backgroundThrottling:false}});}};
electron.dialog.showOpenDialog=async()=>({canceled:false,filePaths:[${JSON.stringify(docs)}]});
electron.dialog.showErrorBox=(title,message)=>console.error(title,message);
const original=cp.spawn;cp.spawn=function(command,args,options){let log;if(args?.includes('desktop_app.server')){const parent=args[args.indexOf('--parent')+1];args=['-X','utf8',${JSON.stringify(path.join(project, "tests/folder_ui_server.py"))},${JSON.stringify(fixture)},'--parent',parent];log=fs.openSync(${JSON.stringify(path.join(fixture, "backend.log"))},'a');options={...options,stdio:['ignore','pipe',log]};}const child=original(command,args,options);if(log!==undefined)fs.closeSync(log);return child;};
require(${JSON.stringify(path.join(project, "desktop/electron/main.cjs"))});`,
);
(async () => {
  let app;
  const errors = [];
  try {
    const env = { ...process.env };
    delete env.ELECTRON_RUN_AS_NODE;
    app = await electron.launch({
      executablePath: require("electron"),
      args: [wrapper],
      cwd: project,
      env,
    });
    const page = await app.firstWindow();
    page.setDefaultTimeout(20000);
    page.on("pageerror", (e) => errors.push(e.message));
    await page.getByRole("button", { name: "资料文件夹", exact: true }).click();
    await page
      .getByRole("button", { name: "选择资料目录", exact: true })
      .click();
    await page.getByRole("button", { name: "扫描目录", exact: true }).click();
    await page.getByRole("cell", { name: "readme.txt", exact: true }).waitFor();
    await page.getByRole("cell", { name: ".env", exact: true }).waitFor();
    assert.match(await page.locator(".folder-file-list").innerText(), /已排除/);
    const conn = await page.evaluate(() => window.desktop.connection());
    const denied = await fetch(conn.url + "/api/folders", {
      method: "POST",
      headers: {
        Authorization: "Bearer " + conn.token,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ path: docs }),
    });
    assert.equal(denied.status, 403);
    await page.screenshot({
      path: path.join(project, "workspace", "stage15-folder-ui-20261021.png"),
    });
    await page
      .getByRole("button", { name: "取消目录授权", exact: true })
      .click();
    await page.waitForFunction(
      () =>
        document.querySelector('select[aria-label="资料目录"]').value === "",
    );
    assert.equal(
      fs.readFileSync(path.join(docs, "readme.txt"), "utf8"),
      "本地资料原文。",
    );
    assert.deepEqual(errors, []);
    console.log(
      JSON.stringify({
        passed: true,
        fixture,
        checks: [
          "native-picker-capability",
          "scan",
          "excluded-secret",
          "renderer-cannot-grant",
          "revoke",
          "source-unchanged",
        ],
        errors,
      }),
    );
  } finally {
    if (app) await app.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
