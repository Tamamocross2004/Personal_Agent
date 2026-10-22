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
const parseMode = process.argv.includes("--parse");
if (parseMode)
  require("node:child_process").execFileSync(
    path.join(project, ".venv", "Scripts", "python.exe"),
    ["-X", "utf8", path.join(project, "tests", "folder_ui_fixture.py"), docs],
  );
const before = Object.fromEntries(
  fs.readdirSync(docs).map((n) => [
    n,
    require("node:crypto")
      .createHash("sha256")
      .update(fs.readFileSync(path.join(docs, n)))
      .digest("hex"),
  ]),
);
const wrapper = path.join(fixture, "harness.cjs");
fs.writeFileSync(
  wrapper,
  `
const electron=require('electron'),cp=require('node:child_process'),fs=require('node:fs');
electron.app.disableHardwareAcceleration();electron.app.setPath('userData',${JSON.stringify(path.join(fixture, "user-data"))});
const Native=electron.BrowserWindow;electron.BrowserWindow=class extends Native{constructor(o){super({...o,show:false,webPreferences:{...o.webPreferences,offscreen:true,backgroundThrottling:false}});}};
electron.dialog.showOpenDialog=async()=>({canceled:false,filePaths:[${JSON.stringify(docs)}]});
electron.dialog.showErrorBox=(title,message)=>console.error(title,message);
const original=cp.spawn;cp.spawn=function(command,args,options){let log;if(args?.includes('desktop_app.server')){const parent=args[args.indexOf('--parent')+1];args=['-X','utf8',${JSON.stringify(path.join(project, "tests/folder_workflow_server.py"))},${JSON.stringify(fixture)},'--parent',parent];log=fs.openSync(${JSON.stringify(path.join(fixture, "backend.log"))},'a');options={...options,stdio:['ignore','pipe',log]};}const child=original(command,args,options);if(log!==undefined)fs.closeSync(log);return child;};
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
    await page.getByRole("button", { name: "文件夹资料助手", exact: true }).click();
    await page
      .getByRole("button", { name: "① 添加资料文件夹", exact: true })
      .click();
    await page.getByRole("button", { name: "② 扫描文件清单", exact: true }).click();
    await page.getByRole("cell", { name: "readme.txt", exact: true }).waitFor();
    await page.getByRole("cell", { name: ".env", exact: true }).waitFor();
    assert.match(await page.locator(".folder-file-list").innerText(), /已排除/);
    if (process.argv.includes("--index")) {
      await page.getByRole("button", {name: "同步资料索引（调用嵌入服务）", exact: true}).click();
      await page.getByText(/操作状态：completed/).waitFor();
      await page.getByRole("textbox", {name: "资料检索问题"}).fill("本地资料");
      await page.getByRole("button", {name: "检索资料", exact:true}).click();
      await page.locator(".folder-retrieval").getByText("本地资料原文。", {exact:true}).waitFor();
      assert.match(await page.locator(".folder-index-panel").innerText(), /未变化 1/);
    }
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
    if (parseMode) {
      await page
        .getByRole("button", { name: "预览 说明.docx", exact: true })
        .click();
      await page
        .locator(".folder-preview")
        .getByText("合同样例：交付前须核对清单。", { exact: true })
        .waitFor();
      assert.match(
        await page.locator(".folder-preview").innerText(),
        /表格1 行2 列2/,
      );
      await page
        .getByRole("button", { name: "预览 数据.csv", exact: true })
        .click();
      await page
        .locator(".folder-preview")
        .getByText(/记录2 · 物理行2/)
        .waitFor();
      await page
        .getByRole("button", { name: "预览 分页.txt", exact: true })
        .click();
      await page.getByRole("button", { name: "下一组", exact: true }).click();
      await page
        .locator(".folder-preview")
        .getByText("第51行资料", { exact: true })
        .waitFor();
      await page
        .getByRole("button", { name: "预览 损坏.docx", exact: true })
        .click();
      await page.locator(".folder-preview").getByRole("alert").waitFor();
      await page
        .getByRole("button", { name: "预览 说明.pdf", exact: true })
        .click();
      await page
        .locator(".folder-preview")
        .getByText(/Local PDF source/)
        .waitFor();
      assert.match(await page.locator(".folder-preview").innerText(), /第1页/);
      await page
        .getByRole("button", { name: "预览 台账.xlsx", exact: true })
        .click();
      await page
        .locator(".folder-preview")
        .getByText(/公式：=1\+2/)
        .waitFor();
      assert.match(
        await page.locator(".folder-preview").innerText(),
        /未提供（未知）/,
      );
      await page
        .getByRole("button", { name: "预览 台账.xlsx", exact: true })
        .click();
      await page
        .locator(".folder-preview")
        .getByText(/复用本地解析缓存/)
        .waitFor();
      await page
        .locator(".folder-preview")
        .getByText(/公式：=1\+2/)
        .scrollIntoViewIfNeeded();
    }
    await page.screenshot({
      path: path.join(
        project,
        "workspace",
        parseMode
          ? "stage17-folder-workflow-20261022.png"
          : "stage17-folder-workflow-20261022.png",
      ),
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
    assert.deepEqual(
      Object.fromEntries(
        fs.readdirSync(docs).map((n) => [
          n,
          require("node:crypto")
            .createHash("sha256")
            .update(fs.readFileSync(path.join(docs, n)))
            .digest("hex"),
        ]),
      ),
      before,
    );
    console.log(
      JSON.stringify({
        passed: true,
        multi_format_preview: parseMode,
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
