// 隔离服务+隐藏测试窗口，不启动生产应用、不发送真实模型请求。
const { _electron: electron } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { spawn } = require("node:child_process");

(async () => {
  const project = path.resolve(__dirname, "../..");
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), "agent-recovery-smoke-"));
  const token = "stage13-isolated-ui-test";
  const backend = spawn(path.join(project, ".venv/Scripts/python.exe"),
    ["-X", "utf8", "tests/checkpoint_ui_server.py", fixture],
    { cwd: project, windowsHide: true, env: { ...process.env, STAGE13_SMOKE_TOKEN: token }, stdio: ["ignore", "pipe", "pipe"] });
  let app, page;
  try {
    const ready = await new Promise((resolve, reject) => {
      let buffer = "", error = "";
      const timer = setTimeout(() => reject(Error("isolated backend startup timeout: " + error)), 25000);
      backend.stderr.on("data", data => error += data);
      backend.on("exit", code => { clearTimeout(timer); reject(Error("isolated backend exited " + code + ": " + error)); });
      backend.stdout.on("data", data => {
        buffer += data.toString();
        let at;
        while ((at = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, at); buffer = buffer.slice(at + 1);
          try { const value = JSON.parse(line); if (value.ready) { clearTimeout(timer); resolve(value); } } catch {}
        }
      });
    });
    const preload = path.join(fixture, "preload.cjs");
    fs.writeFileSync(preload, `require("electron").contextBridge.exposeInMainWorld("desktop", {connection: async()=>(${JSON.stringify({url:ready.url,token})}),openTask:()=>{},openExternal:()=>{}});`);
    const harness = path.join(fixture, "main.cjs");
    fs.writeFileSync(harness, `const {app,BrowserWindow}=require("electron");app.disableHardwareAcceleration();app.setPath("userData",${JSON.stringify(path.join(fixture,"user-data"))});app.whenReady().then(()=>{let w=new BrowserWindow({show:false,width:1440,height:940,webPreferences:{offscreen:true,backgroundThrottling:false,preload:${JSON.stringify(preload)}}});w.loadURL(${JSON.stringify(ready.url)});});app.on("window-all-closed",()=>app.quit());`);
    const env = {...process.env}; delete env.ELECTRON_RUN_AS_NODE;
    app = await electron.launch({executablePath:require("electron"),args:[harness],cwd:project,env});
    page = await app.firstWindow();
    const errors = []; page.on("pageerror", error => errors.push(error.message));
    await page.locator(".session-title").filter({hasText:"阶段13恢复检查（模拟任务）"}).click();
    await page.locator(".run-block").first().getByRole("button", {name:/正在查看|查看本轮/}).click();
    const button = page.getByRole("button", {name:"从检查点恢复",exact:true});
    await button.waitFor(); assert(await button.isEnabled());
    const read = async () => (await fetch(ready.url+"/api/runs/"+ready.rid,{headers:{Authorization:"Bearer "+token}})).json();
    const before = await read(); assert.equal(before.checkpoint.steps,1); assert.equal(before.checkpoint.equivalent_tokens,40);
    await page.screenshot({path:path.join(project,"workspace/stage13-recovery-before.png")});
    await button.click();
    await page.waitForFunction(() => document.querySelector(".artifact-meta")?.textContent.includes("等待恢复"));
    assert(await button.isDisabled());
    const after = await read(); assert.equal(after.id,before.id); assert.equal(after.task_dir,before.task_dir);
    assert.deepEqual(after.options,before.options); assert.equal(after.checkpoint.equivalent_tokens,40);
    await page.screenshot({path:path.join(project,"workspace/stage13-recovery-queued.png")});
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({resume_button:"passed",same_task:true,budget_preserved:true,page_errors:errors,fixture}));
  } catch (error) {
    if (page) {
      console.error(await page.locator("body").innerText());
      await page.screenshot({path:path.join(project,"workspace/stage13-recovery-ui-error.png"),timeout:5000}).catch(()=>{});
    }
    throw error;
  } finally {
    if (app) await app.close();
    backend.kill();
  }
})().catch(error => { console.error(error); process.exitCode=1; });
