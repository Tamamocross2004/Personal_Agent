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
const fieldPlanMode = process.argv.includes("--field-plan");
const completionMode = process.argv.includes("--completion") || fieldPlanMode;
if (completionMode)
  fs.appendFileSync(
    path.join(docs, "readme.txt"),
    "\n" +
      Array.from({ length: 14 }, (_, i) => `持续读取记录${i + 1}。`).join("\n"),
  );
if (process.argv.includes("--reading"))
  fs.appendFileSync(
    path.join(docs, "readme.txt"),
    "\n" + Array.from({ length: 8 }, (_, i) => `续读记录${i + 1}。`).join("\n"),
  );
fs.writeFileSync(path.join(docs, ".env"), "SENSITIVE=must-not-read");
const batchingMode = process.argv.includes("--batching");
const scopeMode = process.argv.includes("--scope");
const projectMode = process.argv.includes("--projects");
const diagnosticsMode = process.argv.includes("--diagnostics");
const contextMode = process.argv.includes("--context");
const fullReadingMode =
  process.argv.includes("--full-reading") || completionMode;
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
const original=cp.spawn;cp.spawn=function(command,args,options){let log;if(args?.includes('desktop_app.server')){const parent=args[args.indexOf('--parent')+1];args=['-X','utf8',${JSON.stringify(path.join(project, "tests/folder_workflow_server.py"))},${JSON.stringify(fixture)},'--parent',parent,${JSON.stringify(batchingMode ? "--batching" : "--normal")}];log=fs.openSync(${JSON.stringify(path.join(fixture, "backend.log"))},'a');options={...options,stdio:['ignore','pipe',log]};}const child=original(command,args,options);if(log!==undefined)fs.closeSync(log);return child;};
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
    await page
      .getByRole("button", { name: "文件夹资料助手", exact: true })
      .click();
    await page
      .getByRole("button", { name: "① 添加资料文件夹", exact: true })
      .click();
    await page
      .getByRole("button", { name: "② 扫描文件清单", exact: true })
      .click();
    await page.getByRole("cell", { name: "readme.txt", exact: true }).waitFor();
    await page.getByRole("cell", { name: ".env", exact: true }).waitFor();
    assert.match(await page.locator(".folder-file-list").innerText(), /已排除/);
    if (process.argv.includes("--experience")) {
      const list = page.locator(".folder-file-list");
      await list
        .getByRole("combobox", { name: "按文件状态筛选" })
        .selectOption("ready");
      assert.equal(
        await list.getByRole("cell", { name: ".env", exact: true }).count(),
        0,
      );
      await list
        .getByRole("combobox", { name: "按文件状态筛选" })
        .selectOption("");
      await list.getByRole("textbox", { name: "搜索文件名" }).fill("absent");
      assert.equal(
        await list
          .getByRole("cell", { name: "readme.txt", exact: true })
          .count(),
        0,
      );
      await list.getByRole("textbox", { name: "搜索文件名" }).fill("");
    }
    if (process.argv.includes("--index")) {
      await page
        .getByRole("button", {
          name: "同步资料索引（调用嵌入服务）",
          exact: true,
        })
        .click();
      await page.getByText(/操作状态：completed/).waitFor();
      await page
        .getByRole("textbox", { name: "资料检索问题" })
        .fill("本地资料");
      await page.getByRole("button", { name: "检索资料", exact: true }).click();
      await page
        .locator(".folder-retrieval")
        .getByText("本地资料原文。", { exact: true })
        .waitFor();
      assert.match(
        await page.locator(".folder-index-panel").innerText(),
        /未变化 1/,
      );
    }
    if (projectMode) {
      await page.locator(".folder-projects > summary").click();
      const region = page.getByRole("region", {
        name: "持续资料项目",
        exact: true,
      });
      await region
        .getByRole("textbox", { name: "项目名称", exact: true })
        .fill("本地资料台账");
      await region
        .getByRole("textbox", { name: "固定整理目标", exact: true })
        .fill("整理本地资料原文，保留来源引用");
      await region
        .getByRole("textbox", { name: "项目结果字段", exact: true })
        .fill("摘录");
      await region
        .getByRole("button", { name: "创建资料项目（本地）", exact: true })
        .click();
      await region
        .getByRole("button", {
          name: "保存项目设置（本地，下次处理生效）",
          exact: true,
        })
        .waitFor();
      const pid = await region
        .getByRole("combobox", { name: "资料项目", exact: true })
        .inputValue();
      const connection = await page.evaluate(() => window.desktop.connection());
      const get = async (url) =>
        (
          await fetch(connection.url + "/api" + url, {
            headers: { Authorization: "Bearer " + connection.token },
          })
        ).json();
      const settled = async (old) => {
        for (let n = 0; n < 100; n++) {
          const p = await get("/folder-projects/" + pid);
          if (p.head && p.head !== old && !p.pending) return p;
          await page.waitForTimeout(200);
        }
        throw Error("project did not finish");
      };
      const run = () =>
        region
          .getByRole("button", {
            name: "执行项目操作（按预览范围，可能调用模型）",
            exact: true,
          })
          .click();
      await run();
      const first = await settled(null);
      await region.getByText(/可用结构覆盖1\/1段/).waitFor();
      const initial = await get("/folder-project-versions/" + first.head);
      await region
        .getByRole("combobox", { name: "项目操作", exact: true })
        .selectOption("revise");
      await region
        .getByRole("checkbox", { name: "readme.txt", exact: true })
        .check();
      await region
        .getByRole("textbox", { name: "项目修订要求", exact: true })
        .fill("重新摘录选定文件的原文，其他结果保留");
      await run();
      const revision = await settled(first.head);
      await page.waitForFunction(
        (id) =>
          document
            .querySelector(".project-version > p")
            ?.textContent.includes(id),
        revision.head,
      );
      assert.deepEqual(
        await get("/folder-project-versions/" + first.head),
        initial,
      );
      await region
        .getByRole("textbox", { name: "项目追问", exact: true })
        .fill("本地资料原文");
      if (contextMode) {
        const preview = region.getByLabel("追问发送预览", { exact: true });
        await preview.locator("summary").click();
        await preview.getByText(/最近对话：0轮（未开启）/).waitFor();
        assert.equal(
          await region
            .getByRole("checkbox", {
              name: "带上最近两轮对话（会重发历史文字）",
              exact: true,
            })
            .isChecked(),
          false,
        );
        await preview.scrollIntoViewIfNeeded();
        await page.screenshot({
          path: path.join(
            project,
            "workspace/stage27-followup-preview-20261023.png",
          ),
        });
      }
      await region
        .getByRole("button", {
          name: "追问当前版本（调用模型，不改结果）",
          exact: true,
        })
        .click();
      await region
        .getByRole("button", { name: /本地资料原文.*completed/ })
        .click();
      await region.getByText(/保存的追问：/).waitFor();
      if (contextMode) {
        const sent = region.getByLabel("追问实际发送构成", { exact: true });
        await sent.locator("summary").click();
        await sent.getByText(/原结果背景：1批/).waitFor();
        await region
          .getByRole("textbox", { name: "项目追问", exact: true })
          .fill("本地资料");
        await region
          .getByRole("checkbox", {
            name: "带上最近两轮对话（会重发历史文字）",
            exact: true,
          })
          .check();
        const preview = region.getByLabel("追问发送预览", { exact: true });
        await preview.locator("summary").click();
        await preview.getByText(/最近对话：1轮（已显式开启）/).waitFor();
        await region
          .getByRole("checkbox", {
            name: "带上最近两轮对话（会重发历史文字）",
            exact: true,
          })
          .uncheck();
        await preview.locator("summary").click();
        await preview.getByText(/最近对话：0轮（未开启）/).waitFor();
      }
      assert.equal((await get("/folder-projects/" + pid)).head, revision.head);
      // Only the test harness edits its synthetic source; the application stays read-only.
      assert.equal(
        fs.readFileSync(path.join(docs, "readme.txt"), "utf8"),
        "本地资料原文。",
      );
      fs.writeFileSync(
        path.join(docs, "readme.txt"),
        "本地资料原文。\n本地资料更新记录。",
      );
      before["readme.txt"] = require("node:crypto")
        .createHash("sha256")
        .update(fs.readFileSync(path.join(docs, "readme.txt")))
        .digest("hex");
      await page
        .getByRole("button", { name: "② 扫描文件清单", exact: true })
        .click();
      await page.getByText(/有尚未同步的资料变化/).waitFor();
      await page
        .getByRole("button", {
          name: "同步资料索引（调用嵌入服务）",
          exact: true,
        })
        .click();
      await page.waitForFunction(
        (g) =>
          Array.from(document.querySelectorAll(".folder-index-panel > p")).some(
            (p) =>
              p.textContent.trim().startsWith("当前索引：") &&
              !p.textContent.includes(g),
          ),
        initial.generation,
      );
      await region
        .getByRole("combobox", { name: "项目操作", exact: true })
        .selectOption("update");
      await region.getByText(/新增0，变化1，删除0/).waitFor();
      // This protocol fake emits only the first evidence row; keep each batch to one segment.
      await region
        .getByRole("spinbutton", { name: "项目每批片段上限", exact: true })
        .fill("1");
      await run();
      const updated = await settled(revision.head);
      await region.getByText(/可用结构覆盖2\/2段/).waitFor();
      const latest = await get("/folder-project-versions/" + updated.head);
      if (contextMode) {
        const job = await get("/folder-jobs/" + latest.job_id);
        assert.ok(
          job.batches.every(
            (b) =>
              b.request.transmission.project_history.history_characters === 0,
          ),
        );
        const preview = region.getByLabel("首批发送预览", { exact: true });
        // Completed update now has no new work; inspect the saved actual requests.
        assert.ok(
          job.batches.every(
            (b) =>
              JSON.parse(b.request.messages[1].content).project_context
                .previous_result_snippets.length === 0,
          ),
        );
        await region
          .getByRole("combobox", { name: "项目操作", exact: true })
          .selectOption("revise");
        await region
          .getByRole("checkbox", { name: "readme.txt", exact: true })
          .check();
        await region
          .getByRole("textbox", { name: "项目修订要求", exact: true })
          .fill("重新摘录原文");
        await preview.locator("summary").click();
        await preview.scrollIntoViewIfNeeded();
        await page.screenshot({
          path: path.join(
            project,
            "workspace/stage27-operation-preview-20261023.png",
          ),
        });
        await region
          .getByRole("combobox", { name: "项目操作", exact: true })
          .selectOption("update");
      }
      assert.equal(latest.parent_id, revision.head);
      assert.deepEqual(
        await get("/folder-project-versions/" + first.head),
        initial,
      );
      await region
        .getByRole("button", { name: "导出本版汇总结果（本地）", exact: true })
        .click();
      await region.getByText(/项目结果已导出：/).waitFor();
      await region.locator(".project-group .folder-citation").first().click();
      await region
        .locator(".project-citation")
        .getByText("本地资料原文。", { exact: true })
        .waitFor();
      const output = path.join(
        fixture,
        "state",
        "folders",
        "project-outputs",
        updated.head,
      );
      assert.equal(
        fs.readdirSync(output).filter((n) => !n.startsWith(".")).length,
        7,
      );
      assert.match(
        fs.readFileSync(path.join(output, "comparison.csv"), "utf8"),
        /本地资料更新记录/,
      );
      await region
        .getByRole("combobox", { name: "项目结果版本", exact: true })
        .selectOption(first.head);
      await region.getByText(/正在查看历史版本/).waitFor();
      assert.equal(
        await region
          .getByRole("button", {
            name: "执行项目操作（按预览范围，可能调用模型）",
            exact: true,
          })
          .isEnabled(),
        false,
      );
      await region
        .getByRole("combobox", { name: "项目结果版本", exact: true })
        .selectOption(updated.head);
      await region.getByText(/可用结构覆盖2\/2段/).waitFor();
    }
    if (process.argv.includes("--questions")) {
      if (process.argv.includes("--strength")) {
        const control = page.getByRole("combobox", {
          name: "模型强度",
          exact: true,
        });
        await control
          .locator('option[value="high"]:not([disabled])')
          .waitFor({ state: "attached" });
        await control.selectOption("high");
      }
      if (process.argv.includes("--experience")) {
        await page
          .getByRole("combobox", { name: "问答文件范围" })
          .selectOption("selected");
        const scope = page.locator(".folder-questions fieldset");
        await scope.getByRole("textbox", { name: "搜索文件名" }).fill("readme");
        await scope.getByRole("button", { name: "选择筛选结果" }).click();
        await scope.getByRole("textbox", { name: "搜索文件名" }).fill("hidden");
        assert.match(await scope.innerText(), /已选 1 项/);
        assert.match(await scope.innerText(), /本次所选：readme.txt/);
      }
      await page
        .getByRole("textbox", { name: "文件夹资料问题" })
        .fill(
          fieldPlanMode
            ? "本地资料是什么？按字段摘录整理。"
            : "本地资料是什么？",
        );
      await page
        .getByRole("button", { name: "开始资料短答（调用模型）", exact: true })
        .click();
      await page
        .locator(".folder-answer")
        .getByText("测试替身仅验证引用协议", { exact: true })
        .waitFor();
      assert.match(
        await page.locator(".folder-answer").innerText(),
        /未正式审稿/,
      );
      if (diagnosticsMode) {
        await page
          .getByLabel("输出诊断", { exact: true })
          .getByText("结构检查完成", { exact: true })
          .waitFor();
        await page
          .getByLabel("输出诊断", { exact: true })
          .scrollIntoViewIfNeeded();
        await page.screenshot({
          path: path.join(project, "workspace/stage26-checked-20261023.png"),
        });
      }
      if (process.argv.includes("--strength")) {
        assert.match(
          await page.locator(".folder-answer").innerText(),
          /本任务模型强度：高（max）/,
        );
        await page
          .getByRole("combobox", { name: "模型强度", exact: true })
          .selectOption("low");
        assert.match(
          await page.locator(".folder-answer").innerText(),
          /本任务模型强度：高（max）/,
        );
      }
      await page.locator(".folder-answer .folder-citation").first().click();
      await page
        .locator(".folder-citation-preview")
        .getByText("本地资料原文。", { exact: true })
        .first()
        .waitFor();
      if (fullReadingMode) {
        const connection = await page.evaluate(() =>
          window.desktop.connection(),
        );
        const get = async (url) =>
          (
            await fetch(connection.url + "/api" + url, {
              headers: { Authorization: "Bearer " + connection.token },
            })
          ).json();
        const parentId = await page
          .getByRole("combobox", { name: "历史资料问答" })
          .inputValue();
        const parent = await get("/folder-jobs/" + parentId);
        const region = page.getByLabel("从局部问答创建全量整理", {
          exact: true,
        });
        const previewButton = region.getByRole("button", {
          name: "预览原范围的全量整理（本地）",
          exact: true,
        });
        const startButton = region.getByRole("button", {
          name: "按原范围新建全量整理（调用模型）",
          exact: true,
        });
        await previewButton.click();
        await startButton.waitFor();
        assert.match(await region.innerText(), /原问题：本地资料是什么/);
        assert.deepEqual(await get("/folder-jobs/" + parentId), parent);
        assert.equal(
          (await get("/folders/" + parent.folder_id + "/jobs")).filter(
            (j) => j.kind === "answer",
          ).length,
          1,
        );
        await region
          .getByLabel("新建全量整理最多批数", { exact: true })
          .fill("2");
        await startButton.waitFor({ state: "detached" });
        await page
          .getByRole("textbox", { name: "文件夹资料问题" })
          .fill("新草稿不替换保存的原问题");
        if (completionMode) {
          await page
            .getByRole("combobox", { name: "资料处理方式" })
            .selectOption("exhaustive");
          await page
            .getByRole("combobox", { name: "分批策略" })
            .selectOption("fixed");
          await page
            .getByRole("spinbutton", { name: "每批片段上限", exact: true })
            .fill("1");
          await region
            .getByRole("checkbox", {
              name: "原范围持续整理至末尾",
              exact: true,
            })
            .check();
          if (!fieldPlanMode)
            await region
              .getByRole("checkbox", {
                name: "原范围先规划字段意图",
                exact: true,
              })
              .uncheck();
          await region
            .getByRole("spinbutton", {
              name: "原范围持续整理调用上限",
              exact: true,
            })
            .fill("20");
        }
        await previewButton.click();
        await startButton.waitFor();
        await region.getByText(/固定索引/).waitFor();
        await app.evaluate(({ BrowserWindow }) =>
          BrowserWindow.getAllWindows().forEach((w) =>
            w.webContents.invalidate(),
          ),
        );
        await region.screenshot({
          path: path.join(
            project,
            fieldPlanMode
              ? "workspace/stage31-full-reading-preview-20261023.png"
              : completionMode
                ? "workspace/stage30-full-reading-preview-20261023.png"
                : "workspace/stage29-full-reading-preview-20261023.png",
          ),
        });
        await startButton.click();
        await page
          .locator(".folder-answer")
          .getByText(/从局部问答 .* 新建的全量整理/)
          .waitFor();
        await page
          .locator(".folder-coverage")
          .getByText(/该范围提取文字已全部送达/)
          .waitFor();
        const nextId = await page
          .getByRole("combobox", { name: "历史资料问答" })
          .inputValue();
        const next = await get("/folder-jobs/" + nextId);
        assert.notEqual(nextId, parentId);
        assert.equal(next.inputs.full_reading_from, parentId);
        assert.equal(next.inputs.query, parent.inputs.query);
        assert.equal(next.inputs.generation, parent.inputs.generation);
        assert.deepEqual(next.inputs.file_ids, parent.inputs.file_ids);
        assert.equal(next.inputs.max_batches, 2);
        assert.equal(next.inputs.output_tokens, 1200);
        assert.equal(next.inputs.budget, 10000);
        assert.equal(
          next.usage_events.filter((e) => e.operation === "llm").length,
          fieldPlanMode ? 16 : completionMode ? 15 : 1,
        );
        if (completionMode) {
          assert.equal(next.inputs.read_to_end, true);
          assert.equal(next.inputs.batch_call_limit, 20);
          assert.equal(next.coverage.stop_reason, "end_of_extraction");
          assert.equal(next.coverage.next_offset, 15);
          assert.equal(next.inputs.budget, 10000);
        }
        if (fieldPlanMode) {
          assert.equal(next.inputs.plan_fields, true);
          assert.equal(next.field_plan.status, "ready");
          assert.equal(next.inputs.field_specs[0].mode, "verbatim");
          assert.deepEqual(
            JSON.parse(next.field_plan.request.messages[1].content),
            { question: parent.inputs.query },
          );
          const planView = page.getByLabel("已保存的字段意图规划", {
            exact: true,
          });
          await planView.locator("summary").first().click();
          await planView
            .getByText("摘录：原样摘录文字", { exact: true })
            .waitFor();
          assert.equal(
            next.usage_events.filter((e) => e.purpose === "field_plan").length,
            1,
          );
        }
        assert.deepEqual(await get("/folder-jobs/" + parentId), parent);
        fs.writeFileSync(
          path.join(fixture, "full-reading-records.json"),
          JSON.stringify({ parent, next }, null, 2),
          { flag: "wx" },
        );
      }
    }
    if (process.argv.includes("--export")) {
      await page
        .getByRole("button", {
          name: "⑤ 导出摘要 / 对照表 / 来源清单（本地）",
          exact: true,
        })
        .click();
      await page.getByText(/已导出至 /).waitFor();
      const outputRoot = path.join(fixture, "state", "folders", "outputs");
      const tasks = fs.readdirSync(outputRoot);
      assert.equal(tasks.length, 1);
      assert.equal(
        fs
          .readdirSync(path.join(outputRoot, tasks[0]))
          .filter((n) => !n.startsWith(".")).length,
        7,
      );
      assert.match(
        fs.readFileSync(
          path.join(outputRoot, tasks[0], "comparison.csv"),
          "utf8",
        ),
        /本地资料原文/,
      );
      if (diagnosticsMode)
        assert.match(
          fs.readFileSync(
            path.join(outputRoot, tasks[0], "summary.md"),
            "utf8",
          ),
          /输出诊断/,
        );
    }
    if (process.argv.includes("--reading")) {
      await page
        .locator(".folder-citation-preview")
        .getByRole("button", {
          name: "查看前后相邻原文（本地，不发送模型）",
          exact: true,
        })
        .click();
      await page
        .locator(".folder-citation-preview")
        .getByText("续读记录1。", { exact: true })
        .waitFor();
      await page
        .getByRole("combobox", { name: "资料处理方式" })
        .selectOption("exhaustive");
      await page
        .getByRole("button", {
          name: "查看读取计划与首批原文（本地）",
          exact: true,
        })
        .click();
      await page
        .locator(".folder-reading-preview")
        .getByText(/9个提取块/)
        .waitFor();
      await page.getByRole("spinbutton", { name: "最多整理批数" }).fill("1");
      if (
        !(await page
          .locator(".folder-budget-settings")
          .evaluate((el) => el.open))
      )
        await page
          .getByText("输出额度与分批设置（下次新任务）", { exact: true })
          .click();
      await page
        .getByRole("combobox", { name: "分批策略" })
        .selectOption("fixed");
      if (batchingMode) {
        await page.locator(".folder-budget-settings").scrollIntoViewIfNeeded();
        await page.screenshot({
          path: path.join(
            project,
            diagnosticsMode
              ? "workspace/stage26-settings-20261023.png"
              : scopeMode
                ? "workspace/stage24-settings-20261022.png"
                : "workspace/stage23-settings-20261022.png",
          ),
        });
      }
      await page
        .getByRole("button", { name: "开始分批整理（调用模型）", exact: true })
        .click();
      if (batchingMode) {
        await page
          .locator(".folder-coverage")
          .getByText(/剩余9段/)
          .waitFor();
        await page
          .getByRole("button", {
            name: "拆小重做未完成批次（新任务，重新计费）",
            exact: true,
          })
          .waitFor();
        const connection = await page.evaluate(() =>
          window.desktop.connection(),
        );
        const getJobs = async () => {
          const folders = await (
            await fetch(connection.url + "/api/folders", {
              headers: { Authorization: "Bearer " + connection.token },
            })
          ).json();
          return await (
            await fetch(
              connection.url +
                "/api/folders/" +
                folders.folders[0].id +
                "/jobs",
              { headers: { Authorization: "Bearer " + connection.token } },
            )
          ).json();
        };
        const parent = (await getJobs()).find((j) => j.coverage);
        assert.equal(parent.coverage.next_offset, 0);
        if (diagnosticsMode) {
          await page
            .getByLabel("输出诊断", { exact: true })
            .getByText("输出达到上限", { exact: true })
            .waitFor();
          assert.equal(
            parent.batches[0].output_diagnostics.code,
            "output_limit",
          );
          assert.equal(
            parent.batches[0].output_diagnostics.validation,
            "checked",
          );
          await page
            .getByLabel("输出诊断", { exact: true })
            .scrollIntoViewIfNeeded();
          await page.screenshot({
            path: path.join(
              project,
              "workspace/stage26-output-limit-20261023.png",
            ),
          });
        }
        if (scopeMode) {
          const scope = page.getByRole("region", {
            name: "范围与任务链",
            exact: true,
          });
          await scope.getByText(/已返回但响应未完成6段/).waitFor();
          await scope.getByText(/拆小重做：本次待处理区间 \[0, 6\)/).waitFor();
          assert.match(
            await scope.innerText(),
            /聊天模型；不调用检索嵌入或重排/,
          );
        }
        await page
          .getByRole("button", {
            name: "拆小重做未完成批次（新任务，重新计费）",
            exact: true,
          })
          .scrollIntoViewIfNeeded();
        await page.screenshot({
          path: path.join(
            project,
            diagnosticsMode
              ? "workspace/stage26-retry-20261023.png"
              : scopeMode
                ? "workspace/stage24-retry-20261022.png"
                : "workspace/stage23-retry-20261022.png",
          ),
        });
        await page
          .getByRole("combobox", { name: "分批策略" })
          .selectOption("adaptive");
        await page
          .getByRole("spinbutton", { name: "每次输出Token上限" })
          .fill("2400");
        await page.getByRole("spinbutton", { name: "最多整理批数" }).fill("2");
        await page
          .getByRole("button", {
            name: "拆小重做未完成批次（新任务，重新计费）",
            exact: true,
          })
          .click();
        await page
          .locator(".folder-coverage")
          .getByText(/本次拆小重做范围已处理/)
          .waitFor();
        const after = await getJobs(),
          retry = after.find((j) => j.inputs.retry_split);
        assert.deepEqual(
          after.find((j) => j.id === parent.id),
          parent,
        );
        assert.equal(retry.inputs.range_end, 6);
        assert.equal(retry.batches.length, 2);
        assert.equal(retry.coverage.next_offset, 6);
        assert.ok(
          retry.batches.every((b) => b.next_offset - b.start_offset <= 3),
        );
        if (scopeMode) {
          const scope = page.getByRole("region", {
            name: "范围与任务链",
            exact: true,
          });
          await scope.getByText(/曾失败后有可用结构：6段/).waitFor();
          await scope.getByText(/继续整理：本次待处理区间 \[6, 9\)/).waitFor();
          await scope.getByText(/查看前序任务与失败尝试（2个任务）/).click();
          assert.match(await scope.innerText(), /length/);
          assert.deepEqual(
            (await getJobs()).find((j) => j.id === parent.id),
            parent,
          );
        }
      }
      await page
        .locator(".folder-coverage")
        .getByText(/剩余3段/)
        .waitFor();
      await page
        .getByRole("button", {
          name: "继续整理剩余范围（新任务，使用上方预算并调用模型）",
          exact: true,
        })
        .click();
      await page
        .locator(".folder-coverage")
        .getByText(/剩余0段/)
        .waitFor();
      if (scopeMode) {
        const scope = page.getByRole("region", {
          name: "范围与任务链",
          exact: true,
        });
        await scope.getByText(/有可用结构9段/).waitFor();
        const latest = await page
          .getByRole("combobox", { name: "历史资料问答" })
          .inputValue();
        const summary = scope.getByText(/查看前序任务与失败尝试（3个任务）/);
        if (!(await summary.locator("..").evaluate((el) => el.open)))
          await summary.click();
        await scope.getByRole("button", { name: /^原任务 / }).click();
        await scope.getByText(/已返回但响应未完成6段/).waitFor();
        await page
          .getByRole("combobox", { name: "历史资料问答" })
          .selectOption(latest);
        await scope.getByText(/有可用结构9段/).waitFor();
      }
      assert.match(
        await page.locator(".folder-coverage").innerText(),
        /该范围提取文字已全部送达/,
      );
      assert.match(
        await page.locator(".folder-coverage").innerText(),
        /前次结果保留/,
      );
      await page
        .getByRole("button", {
          name: "⑤ 导出摘要 / 对照表 / 来源清单（本地）",
          exact: true,
        })
        .click();
      await page.getByText(/已导出至 /).waitFor();
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
    // Hidden offscreen Electron needs a fresh compositor frame after earlier captures.
    await app.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows().forEach((w) => w.webContents.invalidate()),
    );
    await page.screenshot({
      timeout: 60000,
      path: path.join(
        project,
        "workspace",
        fieldPlanMode
          ? "stage31-folder-workflow-20261023.png"
          : completionMode
            ? "stage30-folder-workflow-20261023.png"
            : fullReadingMode
              ? "stage29-folder-workflow-20261023.png"
              : contextMode
                ? "stage27-folder-projects-20261023.png"
                : diagnosticsMode
                  ? "stage26-folder-workflow-20261023.png"
                  : projectMode
                    ? "stage25-folder-projects-20261022.png"
                    : scopeMode
                      ? "stage24-folder-workflow-20261022.png"
                      : batchingMode
                        ? "stage23-folder-workflow-20261022.png"
                        : parseMode
                          ? "folder-workflow-current-multiformat.png"
                          : process.argv.includes("--strength")
                            ? "model-strength-folder-20261022.png"
                            : process.argv.includes("--reading")
                              ? "stage22-folder-workflow.png"
                              : process.argv.includes("--experience")
                                ? "stage21-folder-workflow.png"
                                : "folder-workflow-current.png",
      ),
    });
    await page
      .getByRole("button", { name: "取消目录授权", exact: true })
      .click();
    await page.waitForFunction(
      () =>
        document.querySelector('select[aria-label="资料目录"]').value === "",
    );
    if (process.argv.includes("--export")) {
      const response = await fetch(conn.url + "/api/folders", {
        headers: { Authorization: "Bearer " + conn.token },
      });
      const folder = (await response.json()).folders[0];
      await page
        .getByRole("combobox", { name: "资料目录", exact: true })
        .selectOption(folder.id);
      await page
        .locator(".folder-answer")
        .getByText("测试替身仅验证引用协议", { exact: true })
        .waitFor();
      await page.locator(".folder-answer .folder-citation").first().click();
      await page
        .locator(".folder-citation-preview")
        .getByText(
          process.argv.includes("--reading") ? "续读记录6。" : "本地资料原文。",
          { exact: true },
        )
        .first()
        .waitFor();
      assert.equal(
        await page
          .getByRole("button", {
            name: "开始资料短答（调用模型）",
            exact: true,
          })
          .isEnabled(),
        false,
      );
    }
    if (!process.argv.includes("--reading") && !projectMode && !completionMode)
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
          ...(projectMode
            ? [
                "persistent-project-create",
                "versioned-revision",
                "version-bound-followup",
                "incremental-source-update",
                "immutable-old-edition",
                "project-export-citations",
                "historical-version-readonly",
              ]
            : []),
          ...(scopeMode
            ? [
                "lineage-failure-visible",
                "deduplicated-recovered-coverage",
                "next-operation-services-and-bounds",
                "read-only-scope",
              ]
            : []),
          ...(batchingMode
            ? [
                "output-controls",
                "explicit-split-bounded-range",
                "parent-and-fees-preserved",
                "continue-after-split",
              ]
            : []),
          ...(contextMode
            ? [
                "transmission-preview",
                "explicit-dialogue-opt-in",
                "update-no-history",
                "saved-request-manifest",
              ]
            : []),
          ...(fullReadingMode
            ? [
                "local-full-reading-preview",
                "changed-controls-invalidate-preview",
                "pinned-original-query-and-scope",
                "explicit-new-paid-operation",
                "original-answer-and-usage-unchanged",
              ]
            : []),
          ...(completionMode
            ? [
                "explicit-continuous-opt-in",
                "fifteen-calls-one-budget",
                "batch-limit-replaced-by-explicit-call-limit",
                "source-and-old-answer-unchanged",
              ]
            : []),
          ...(fieldPlanMode
            ? [
                "query-only-field-planning",
                "planning-counts-in-total-calls-and-budget",
                "saved-intents-and-source-quotes",
                "field-plan-not-evidence",
              ]
            : []),
          "native-picker-capability",
          "scan",
          "excluded-secret",
          "renderer-cannot-grant",
          "revoke",
          "source-unchanged",
          ...(process.argv.includes("--index")
            ? ["sync", "keyword-preview"]
            : []),
          ...(process.argv.includes("--questions")
            ? ["answer", "pinned-citation"]
            : []),
          ...(process.argv.includes("--export")
            ? ["export-roundtrip", "revoked-history-read-only"]
            : []),
          ...(process.argv.includes("--experience")
            ? ["file-filters", "hidden-selection-preserved"]
            : []),
          ...(process.argv.includes("--reading")
            ? [
                "local-neighbors",
                "reading-plan",
                "bounded-partial",
                "explicit-continuation",
                "full-input-coverage",
              ]
            : []),
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
