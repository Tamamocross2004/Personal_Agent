// 隔离的阶段14端到端验收：真实Electron入口、API、队列、worker与工具；只替换模型。
// 不启动生产workspace调度器，不修改.env，不发送真实API请求，不关闭用户应用。
const { _electron: electron } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

const project = path.resolve(__dirname, "../..");
const fixture = fs.mkdtempSync(path.join(os.tmpdir(), "agent-stage14-"));
const evidence = path.join(project, "workspace");
const prefixAt = process.argv.indexOf("--evidence-prefix");
const evidencePrefix = prefixAt < 0 ? "stage14" : process.argv[prefixAt + 1];
assert.match(evidencePrefix || "", /^[a-z0-9-]+$/);
const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;
let app, page, connection, backendPid;
const errors = [],
  checks = [];
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(fn, message, timeout = 25000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const result = await fn();
    if (result) return result;
    await sleep(100);
  }
  throw Error("timeout: " + message);
}
function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}
async function api(url, method = "GET", body) {
  const response = await fetch(connection.url + "/api" + url, {
    method,
    headers: {
      Authorization: "Bearer " + connection.token,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const value = await response.json();
  if (!response.ok) throw Error(response.status + ": " + JSON.stringify(value));
  return value;
}
async function selectSession(title) {
  await page.locator(".session-title").filter({ hasText: title }).click();
  await page.locator(".artifact-meta").waitFor();
}
async function newTask(question) {
  await page.getByRole("button", { name: "新建研究", exact: true }).click();
  await page.getByRole("textbox", { name: "研究问题" }).fill(question);
  await page.getByRole("button", { name: "发送研究", exact: true }).click();
  return until(
    async () =>
      (await api("/sessions")).find((s) => s.title === question.slice(0, 28)),
    "created session",
  );
}
async function lastRun(sid) {
  return (await api(`/sessions/${sid}/runs`)).at(-1);
}
async function finished(sid) {
  return until(
    async () => {
      const run = await lastRun(sid);
      return (
        run &&
        !["queued", "resuming", "running", "cancelling"].includes(run.state) &&
        run
      );
    },
    "task completion",
    45000,
  );
}
async function quality(text) {
  await page
    .locator(".tabs")
    .getByRole("button", { name: "质量", exact: true })
    .click();
  await page
    .locator(".inspector-body")
    .getByText(text, { exact: true })
    .waitFor();
}
async function resumeSelected(rid) {
  const [response] = await Promise.all([
    page.waitForResponse(
      (r) =>
        r.url().endsWith(`/api/runs/${rid}/resume`) &&
        r.request().method() === "POST",
    ),
    page.getByRole("button", { name: "从检查点恢复", exact: true }).click(),
  ]);
  assert.equal(response.status(), 200);
}

async function launch() {
  const wrapper = path.join(fixture, "electron-harness.cjs");
  // 真实入口保持IPC、认证、父进程参数、启动握手和退出逻辑；只替换服务根目录/模型与窗口可见性。
  fs.writeFileSync(
    wrapper,
    `
const electron=require('electron'),cp=require('node:child_process'),fs=require('node:fs');
electron.app.disableHardwareAcceleration();electron.app.setPath('userData',${JSON.stringify(path.join(fixture, "user-data"))});
const NativeWindow=electron.BrowserWindow;
electron.BrowserWindow=class extends NativeWindow {constructor(options){super({...options,show:false,webPreferences:{...options.webPreferences,offscreen:true,backgroundThrottling:false}});}};
const nativeSpawn=cp.spawn;
cp.spawn=function(command,args,options){
 if(args?.includes('desktop_app.server')){
  const parent=args[args.indexOf('--parent')+1];
  args=['-X','utf8',${JSON.stringify(path.join(project, "tests/desktop_acceptance_server.py"))},${JSON.stringify(fixture)},'--parent',parent];
  options={...options,stdio:['ignore','pipe',fs.openSync(${JSON.stringify(path.join(fixture, "backend.log"))},'a')]};
  const child=nativeSpawn(command,args,options);fs.closeSync(options.stdio[2]);
  fs.writeFileSync(${JSON.stringify(path.join(fixture, "backend-pid.json"))},JSON.stringify({pid:child.pid}));return child;
 }
 return nativeSpawn(command,args,options);
};
require(${JSON.stringify(path.join(project, "desktop/electron/main.cjs"))});
`,
  );
  app = await electron.launch({
    executablePath: require("electron"),
    args: [wrapper],
    cwd: project,
    env,
  });
  page = await app.firstWindow();
  page.setDefaultTimeout(15000);
  page.on("pageerror", (error) => errors.push(error.message));
  await page.getByRole("button", { name: "新建研究", exact: true }).waitFor();
  connection = await page.evaluate(() => window.desktop.connection());
  backendPid = JSON.parse(
    fs.readFileSync(path.join(fixture, "backend-pid.json")),
  ).pid;
  await until(
    () =>
      page.getByRole("button", { name: "新建研究", exact: true }).isEnabled(),
    "UI ready",
  );
}

async function verifyCrashCleanup() {
  const crash = await newTask("不联网，虚构资料：等待停止测试桌面意外退出。");
  const crashRun = await until(async () => {
    const row = await lastRun(crash.id);
    return (
      row &&
      fs.existsSync(path.join(row.task_dir, ".desktop-test-waiting")) &&
      row
    );
  }, "crash worker waiting");
  const worker = JSON.parse(
    fs.readFileSync(path.join(crashRun.task_dir, ".desktop-test-worker.json")),
  ).pid;
  const server = JSON.parse(
    fs.readFileSync(path.join(fixture, "server-ready.json")),
  );
  const identities = {
    electron: app.process().pid,
    nativeMain: await app.evaluate(() => process.pid),
    launcher: backendPid,
    server: server.pid,
    parent: server.parent,
    worker,
  };
  console.log(JSON.stringify({ lifecycle: identities }));
  assert.equal(server.parent, identities.nativeMain);
  // Windows Playwright的process()可能是启动器，必须结束evaluate确认的本窗口主进程。
  process.kill(identities.nativeMain, "SIGKILL");
  try {
    await until(
      () => !alive(server.pid) && !alive(worker),
      "crashed desktop cleans server and worker",
      10000,
    );
    await until(
      () => !alive(backendPid),
      "crashed desktop cleans Python launcher",
      10000,
    );
  } catch (error) {
    console.error(
      JSON.stringify({
        lifecycle_alive: Object.fromEntries(
          Object.entries(identities).map(([k, pid]) => [k, alive(pid)]),
        ),
      }),
    );
    throw error;
  }
  await app.close().catch(() => {});
  app = null;
  await launch();
  assert.equal((await api(`/runs/${crashRun.id}`)).state, "interrupted");
  assert.equal((await api(`/sessions/${crash.id}/runs`)).length, 1);
  await app.close();
  app = null;
  await until(() => !alive(backendPid), "final close cleanup", 10000);
}

(async () => {
  try {
    await launch();
    if (process.argv.includes("--lifecycle-only")) {
      await verifyCrashCleanup();
      console.log(JSON.stringify({ lifecycle: "passed", fixture }));
      return;
    }
    assert.equal((await fetch(connection.url + "/api/config")).status, 401);
    checks.push("native startup, preload and authenticated loopback API");

    await page
      .getByRole("button", { name: "原始资料知识库", exact: true })
      .click();
    await page
      .getByLabel("选择知识文档")
      .setInputFiles({
        name: "界面原文.md",
        mimeType: "text/markdown",
        buffer: Buffer.from(
          '档案保留期限为七个工作日。<script>throw Error("untrusted")</script>',
        ),
      });
    await page.getByRole("button", { name: "导入并构建", exact: true }).click();
    await until(
      async () => {
        const s = await api("/knowledge");
        return !s.busy && s.active?.files?.length === 1;
      },
      "knowledge build",
      45000,
    );
    await page.getByRole("button", { name: "预览", exact: true }).click();
    assert.equal(await page.getByLabel("选择知识文档").inputValue(), "");
    await page
      .locator(".knowledge-modal .source-text")
      .getByText("档案保留期限", { exact: false })
      .waitFor();
    await page.getByLabel("知识库检索问题").fill("档案保留期限");
    await page.getByRole("button", { name: "检索", exact: true }).click();
    await page.locator(".knowledge-hit").waitFor();
    assert.equal((await api("/knowledge")).usage.calls, 1);
    await page.screenshot({
      path: path.join(evidence, evidencePrefix + "-knowledge.png"),
      fullPage: true,
    });
    const oldGeneration = (await api("/knowledge")).active.id;
    await page.getByRole("button", { name: "重建索引", exact: true }).click();
    await until(
      async () => {
        const s = await api("/knowledge");
        return !s.busy && s.active?.id !== oldGeneration;
      },
      "cached rebuild",
      45000,
    );
    assert.equal((await api("/knowledge")).usage.calls, 1);
    await page
      .getByRole("button", { name: "从当前库移除", exact: true })
      .click();
    await until(async () => {
      const s = await api("/knowledge");
      return !s.busy && s.active?.files?.length === 0;
    }, "knowledge remove");
    await page
      .locator(".knowledge-modal")
      .getByRole("button", { name: "关闭", exact: true })
      .click();
    checks.push(
      "knowledge import, escaped preview, independent keyword retrieval, cached rebuild and remove",
    );

    // 模拟一次完整事件帧后的正常EOF，验证cursor续传且不重新提交任务。
    let streamRequests = 0;
    await page.route("**/api/runs/*/events?*", async (route) => {
      streamRequests++;
      if (streamRequests === 1)
        await route.fulfill({
          contentType: "text/event-stream",
          body: 'id: 1\nevent: trace\ndata: {"kind":"notice","text":"模拟事件连接提前结束"}\n\n',
        });
      else await route.continue();
    });
    // 固定延迟创建响应，确保输入发生在响应前，稳定覆盖草稿被异步清空的回归。
    await page.route("**/api/sessions", async (route) => {
      if (route.request().method() === "POST") await sleep(400);
      await route.continue();
    });
    const good = await newTask("不联网，虚构资料：演示报告、CSV和图表。");
    await page.unroute("**/api/sessions");
    const done = await finished(good.id);
    assert.equal(done.state, "completed");
    assert(streamRequests >= 2);
    const detail = await api(`/runs/${done.id}`);
    assert.equal(detail.options.max_steps, 12);
    assert.equal(detail.options.budget, 80000);
    assert.equal(detail.contract.network_allowed, false);
    assert(detail.files.some((f) => f.name === "chart.png"));
    assert.equal((await api(`/sessions/${good.id}/runs`)).length, 1);
    await page.unroute("**/api/runs/*/events?*");
    await page
      .locator(".tabs")
      .getByRole("button", { name: "报告", exact: true })
      .click();
    await page
      .locator(".document")
      .getByRole("heading", { name: "离线界面验收", exact: true })
      .waitFor();
    await until(
      () =>
        page
          .locator(".document img")
          .evaluateAll((imgs) => imgs.some((img) => img.naturalWidth > 0)),
      "report chart",
    );
    fs.writeFileSync(
      path.join(done.task_dir, "chart.png"),
      Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=",
        "base64",
      ),
    );
    await until(
      () =>
        page
          .locator(".document img")
          .evaluateAll((imgs) => imgs.some((img) => img.naturalWidth === 1)),
      "updated image version",
    );
    await page
      .locator(".document")
      .getByRole("link", { name: "data.csv", exact: true })
      .click();
    await page
      .locator(".csv-preview td")
      .getByText("5", { exact: true })
      .waitFor();
    await page
      .locator(".tabs")
      .getByRole("button", { name: "依据", exact: true })
      .click();
    await page
      .locator(".claim")
      .getByText("业务状态未知。", { exact: true })
      .waitFor();
    assert.equal(detail.review.support_review.status, "complete");
    assert.equal(detail.review.support_review.checks.length, 2);
    await page
      .locator(".claim")
      .getByText("推导说明", { exact: true })
      .waitFor();
    await page
      .locator(".claim")
      .getByText("资料前提", { exact: true })
      .waitFor();
    await page
      .locator(".claim")
      .getByText("未知前提", { exact: true })
      .waitFor();
    await page
      .locator(".claim")
      .getByText("模型依据核对 · 支持", { exact: true })
      .first()
      .waitFor();
    await page.screenshot({
      path: path.join(evidence, `${evidencePrefix}-claim-support.png`),
    });
    await page
      .locator(".claim")
      .getByRole("button", { name: "execution-1" })
      .click();
    await page
      .locator(".source-modal .excerpt")
      .getByText("退出码: 0", { exact: true })
      .waitFor();
    await page.keyboard.press("Escape");
    await quality("reviewed");
    await page
      .locator(".inspector-body")
      .getByText("已完成（模型意见）", { exact: true })
      .waitFor();
    await page
      .locator(".tabs")
      .getByRole("button", { name: "用量", exact: true })
      .click();
    await page.locator(".usage-panel").waitFor();
    assert.equal(detail.usage.calls, 5); // planning, code, report, review, final
    await page.screenshot({
      path: path.join(evidence, `${evidencePrefix}-completed.png`),
    });
    checks.push(
      "submit -> subprocess tools -> report/chart/CSV/evidence/review/usage; SSE EOF reconnect",
    );

    // 刷新、连按发送均不创建重复任务。
    await page.reload();
    await selectSession(good.title);
    assert.equal((await api(`/sessions/${good.id}/runs`)).length, 1);
    // 会话管理均只改本地索引；归档后仍能查看原产物。
    const sessionRow = () =>
      page.locator(".session").filter({
        has: page.locator(".session-title").filter({ hasText: good.title }),
      });
    await sessionRow().getByLabel("会话操作").click();
    await sessionRow()
      .getByRole("button", { name: "重命名", exact: true })
      .click();
    await page
      .getByRole("textbox", { name: "会话名称", exact: true })
      .fill("阶段14索引验收");
    await page.getByRole("button", { name: "保存名称", exact: true }).click();
    good.title = "阶段14索引验收";
    await sessionRow().waitFor();
    await page.getByPlaceholder("搜索会话", { exact: true }).fill(good.title);
    assert.equal(await page.locator(".session").count(), 1);
    await page.getByPlaceholder("搜索会话", { exact: true }).fill("");
    await sessionRow().getByLabel("会话操作").click();
    await sessionRow()
      .getByRole("button", { name: "归档会话", exact: true })
      .click();
    await until(
      async () =>
        (await api("/sessions?archived=true")).some((s) => s.id === good.id),
      "archive committed",
    );
    await page.getByTitle("切换归档", { exact: true }).click();
    await selectSession(good.title);
    await sessionRow().getByLabel("会话操作").click();
    await sessionRow()
      .getByRole("button", { name: "取消归档", exact: true })
      .click();
    await until(
      async () => (await api("/sessions")).some((s) => s.id === good.id),
      "unarchive committed",
    );
    await page.getByTitle("切换归档", { exact: true }).click();
    await selectSession(good.title);
    assert.equal(
      (await api(`/runs/${done.id}`)).usage.calls,
      detail.usage.calls,
    );
    checks.push(
      "search, rename, archive/unarchive preserve research and never call model",
    );
    await page.route("**/api/sessions/*/runs", async (route) => {
      if (route.request().method() === "POST") await sleep(400);
      await route.continue();
    });
    await page
      .getByRole("textbox", { name: "研究问题" })
      .fill("不联网，虚构资料：重复点击验收。");
    await page
      .getByRole("button", { name: "发送研究", exact: true })
      .evaluate((b) => {
        b.click();
        b.click();
      });
    await page
      .getByRole("textbox", { name: "研究问题" })
      .fill("下一个尚未发送的问题");
    await until(
      async () => (await api(`/sessions/${good.id}/runs`)).length === 2,
      "one followup created",
    );
    await finished(good.id);
    assert.equal((await api(`/sessions/${good.id}/runs`)).length, 2);
    assert.equal(
      await page.getByRole("textbox", { name: "研究问题" }).inputValue(),
      "下一个尚未发送的问题",
    );
    await page.unroute("**/api/sessions/*/runs");
    checks.push(
      "reload is read-only; synchronous repeated send creates one run",
    );

    const limited = await newTask("不联网，虚构资料：审稿限制场景。");
    assert.equal((await finished(limited.id)).state, "completed");
    await quality("skipped_limit");
    checks.push("review limit remains skipped_limit, not passed");

    // 固定预算通过原始API提交，UI不得放大或重置。
    await page
      .getByRole("button", { name: "deepseek-flash", exact: true })
      .first()
      .click();
    await page.getByLabel("等效 token 预算", { exact: true }).fill("1000");
    await page.getByRole("button", { name: "完成", exact: true }).click();
    const budget = await newTask("不联网，虚构资料：预算不足，自动保存演示。");
    const partial = await finished(budget.id);
    assert.equal(partial.state, "partial");
    assert.equal(partial.options.budget, 1000);
    assert.equal(partial.result.stop_reason, "budget");
    await quality("budget");
    await quality("not_reviewed");
    const saved = await api(`/runs/${partial.id}`);
    assert.equal(saved.provenance.status, "current");
    assert.equal(
      partial.result.answer,
      "离线样例：模型生成此段文字时，程序尚未开始文件保存。",
    );
    await page
      .locator(".runtime-save-note")
      .getByText(/已自动保存未完成结果/)
      .waitFor();
    assert.equal(
      await page.locator(".run-block.focused .state").innerText(),
      "提前收尾",
    );
    await page
      .locator(".tabs")
      .getByRole("button", { name: "报告", exact: true })
      .click();
    await page
      .locator(".document")
      .getByRole("heading", { name: "自动保存的未完成结果", exact: true })
      .waitFor();
    await page.screenshot({
      path: path.join(evidence, `${evidencePrefix}-fallback-current.png`),
    });
    fs.appendFileSync(
      path.join(partial.task_dir, "report.md"),
      "\n\n后续手工修改示例。\n",
    );
    await page
      .locator(".runtime-save-note")
      .getByText("自动保存记录已过期，当前文件版本已变化。", { exact: true })
      .waitFor();
    checks.push(
      "automatic fallback shows current save, original model text and unreviewed state; edit makes provenance stale",
    );
    await page
      .getByRole("button", { name: "deepseek-flash", exact: true })
      .first()
      .click();
    await page.getByLabel("等效 token 预算", { exact: true }).fill("80000");
    await page.keyboard.press("Escape");
    await page.locator(".modal").waitFor({ state: "hidden" });
    checks.push(
      "budget exhaustion remains partial with original limit; Escape closes settings",
    );

    const failed = await newTask("不联网，虚构资料：服务失败场景。");
    const fail = await finished(failed.id);
    assert.equal(fail.state, "needs_verification");
    await quality("needs_verification");
    assert(
      await page
        .getByRole("button", { name: "从检查点恢复", exact: true })
        .isDisabled(),
    );
    const failDetail = await api(`/runs/${fail.id}`);
    assert.equal(failDetail.usage.failed, 1);
    checks.push(
      "unknown API outcome shows needs_verification and failed/unknown usage; resume blocked",
    );

    // 正常停止后，已提交响应可恢复；继承累计用量且不再次请求同一个响应。
    const stopping = await newTask("不联网，虚构资料：等待停止场景。");
    const running = await until(async () => {
      const row = await lastRun(stopping.id);
      return (
        row &&
        fs.existsSync(path.join(row.task_dir, ".desktop-test-waiting")) &&
        row
      );
    }, "worker in model call");
    await page.getByRole("button", { name: "停止", exact: true }).click();
    assert.equal((await finished(stopping.id)).state, "cancelled");
    const before = await api(`/runs/${running.id}`);
    assert(before.checkpoint.resumable);
    await until(
      () =>
        page
          .getByRole("button", { name: "从检查点恢复", exact: true })
          .isEnabled(),
      "resume enabled",
    );
    await resumeSelected(running.id);
    const resumed = await finished(stopping.id);
    assert.equal(resumed.state, "completed");
    const after = await api(`/runs/${running.id}`);
    assert.equal(after.id, before.id);
    assert.equal(after.task_dir, before.task_dir);
    assert.equal(after.usage.calls, before.usage.calls);
    assert.equal(
      after.result.equivalent_tokens,
      before.checkpoint.equivalent_tokens,
    );
    checks.push(
      "cooperative stop and UI resume reuse committed response, same task and cumulative usage",
    );

    await selectSession("可恢复（离线夹具）");
    const recovery = (await api("/sessions")).find(
      (s) => s.title === "可恢复（离线夹具）",
    );
    const recoveryRun = await lastRun(recovery.id);
    const csvBefore = fs.readFileSync(
      path.join(recoveryRun.task_dir, "data.csv"),
    );
    await resumeSelected(recoveryRun.id);
    assert.equal((await finished(recovery.id)).state, "completed");
    assert.deepEqual(
      fs.readFileSync(path.join(recoveryRun.task_dir, "data.csv")),
      csvBefore,
    );
    const trace = fs
      .readFileSync(path.join(recoveryRun.task_dir, "trace.jsonl"), "utf8")
      .trim()
      .split("\n")
      .map(JSON.parse);
    assert.equal(
      trace.filter((e) => e.kind === "tool_call" && e.name === "run_python")
        .length,
      1,
    );
    checks.push(
      "subprocess recovery after a committed tool batch does not repeat that tool",
    );
    await selectSession("损坏检查点（离线夹具）");
    assert(
      await page
        .getByRole("button", { name: "从检查点恢复", exact: true })
        .isDisabled(),
    );
    checks.push("corrupt checkpoint has visible refusal and disabled resume");

    // 缺失文件、错误转义图片链接均必须保留可用界面。
    fs.writeFileSync(
      path.join(done.task_dir, "report.md"),
      "# 文件故障演示\n\n![损坏链接](bad%zz.png)\n\n[缺失CSV](missing.csv)\n",
    );
    await selectSession(good.title);
    await page
      .locator(".run-block")
      .first()
      .getByRole("button", { name: "查看本轮", exact: true })
      .click();
    await page
      .locator(".tabs")
      .getByRole("button", { name: "报告", exact: true })
      .click();
    await page
      .locator(".document")
      .getByRole("link", { name: "缺失CSV", exact: true })
      .click();
    await page
      .locator(".empty-artifact")
      .getByText("文件不存在或越界", { exact: true })
      .waitFor();
    await quality("stale");
    checks.push(
      "missing CSV visible; malformed image URL does not crash; changed report makes review stale",
    );

    await selectSession("长历史（离线夹具）");
    await until(
      () =>
        page
          .locator(".run-block")
          .count()
          .then((n) => n === 35),
      "history rendered",
    );
    await page.locator(".conversation").evaluate((box) => {
      box.scrollTop = 0;
      box.dispatchEvent(new Event("scroll"));
    });
    await page
      .locator(".run-block")
      .first()
      .getByRole("button", { name: "查看本轮", exact: true })
      .click();
    await page
      .locator(".notice-event pre")
      .getByText("历史事件回放检查", { exact: true })
      .waitFor({ state: "attached" });
    await page
      .locator(".tabs")
      .getByRole("button", { name: "质量", exact: true })
      .click();
    assert.equal(
      await page
        .locator(".quality-row")
        .filter({ hasText: "任务步数" })
        .locator("b")
        .innerText(),
      "未记录",
    );
    assert(
      (await page.locator(".conversation").evaluate((box) => box.scrollTop)) <
        100,
    );
    await page.setViewportSize({ width: 1050, height: 700 });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
    );
    await page.screenshot({
      path: path.join(evidence, `${evidencePrefix}-small-window.png`),
    });
    checks.push(
      "long history stays at reader position; 1050x700 has no document overflow",
    );

    // 强制停止只结束隔离服务拥有的worker；正常关闭清理后台。
    const force = await newTask("不联网，虚构资料：等待停止并强制中断。");
    const forceRun = await until(async () => {
      const row = await lastRun(force.id);
      return (
        row &&
        fs.existsSync(path.join(row.task_dir, ".desktop-test-waiting")) &&
        row
      );
    }, "force worker waiting");
    // 标记cancelling后立即强停，避免夹具先响应停止导致竞态。
    await api(`/runs/${forceRun.id}/stop?force=true`, "POST");
    assert.equal((await finished(force.id)).state, "interrupted");
    assert.equal(
      (await api(`/runs/${forceRun.id}`)).checkpoint.resumable,
      false,
    );
    const normalBackend = backendPid;
    await app.close();
    app = null;
    await until(() => !alive(normalBackend), "normal close cleanup", 10000);
    checks.push(
      "force stop preserves uncertain checkpoint; normal desktop exit cleans backend",
    );

    // 重新打开同一隔离索引，并意外杀死自己的Electron主进程，后台应自行退出。
    await launch();
    const sessionsAfter = await api("/sessions");
    assert(sessionsAfter.some((s) => s.id === force.id));
    assert.equal((await api(`/runs/${forceRun.id}`)).state, "interrupted");
    await verifyCrashCleanup();
    checks.push(
      "restart preserves index; unexpected Electron death cleans backend and worker; restart never auto-runs",
    );
    assert.deepEqual(errors, []);
    const result = {
      status: "passed",
      scope: "isolated offline engineering acceptance; no paid model request",
      checks,
      page_errors: errors,
      fixture,
    };
    fs.writeFileSync(
      path.join(evidence, `${evidencePrefix}-ui-results.json`),
      JSON.stringify(result, null, 2),
    );
    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    if (page && !page.isClosed()) {
      console.error((await page.locator("body").innerText()).slice(-12000));
      await page
        .screenshot({
          path: path.join(evidence, `${evidencePrefix}-ui-error.png`),
          timeout: 5000,
        })
        .catch(() => {});
    }
    console.error(JSON.stringify({ checks, errors, fixture }));
    throw error;
  } finally {
    if (app) await app.close().catch(() => {});
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
