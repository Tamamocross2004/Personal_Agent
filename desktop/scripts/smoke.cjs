// 运行前关闭已有应用。提交操作由测试拦截，绝不创建研究或调用模型。
const { _electron: electron } = require("playwright");
const assert = require("node:assert/strict");
const path = require("node:path");

(async () => {
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  const desktop = path.resolve(__dirname, "..");
  const application = await electron.launch({
    executablePath: require("electron"),
    args: [desktop],
    cwd: desktop,
    env,
  });
  const errors = [];
  let submissions = 0;
  try {
    const page = await application.firstWindow();
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("request", (request) => {
      if (
        request.method() === "POST" &&
        /\/sessions\/[^/]+\/runs$/.test(request.url())
      )
        submissions++;
    });
    await page.getByRole("button", { name: "新建研究", exact: true }).click();
    await page.locator(".session.selected").waitFor();
    // 足量历史内容才能暴露 Grid 自动最小行高撑出窗口的问题。
    await page.locator(".session-list").evaluate((list) => {
      const template = list.querySelector(".session");
      for (let index = 0; index < 40; index++) {
        const entry = template.cloneNode(true);
        entry.classList.remove("selected");
        entry.dataset.layoutFixture = "true";
        list.appendChild(entry);
      }
    });
    const assertControlsInViewport = async () => {
      const failures = await page.evaluate(() => {
        const selectors = [
          ".sidebar",
          ".main",
          ".composer-area",
          ".composer textarea",
          ".send",
          ".settings-entry",
          ".import-history",
        ];
        const failures = [];
        for (const selector of selectors) {
          const element = document.querySelector(selector),
            rect = element.getBoundingClientRect();
          if (
            rect.top < -0.5 ||
            rect.left < -0.5 ||
            rect.bottom > innerHeight + 0.5 ||
            rect.right > innerWidth + 0.5
          )
            failures.push({
              selector,
              top: rect.top,
              bottom: rect.bottom,
              viewport: innerHeight,
            });
        }
        return failures;
      });
      assert.deepEqual(
        failures,
        [],
        "输入框、发送与设置必须在可见窗口内，不能依赖自动滚动才能点击",
      );
    };
    for (const [width, height] of [
      [1440, 940],
      [1280, 800],
      [1050, 700],
    ]) {
      await application.evaluate(
        ({ BrowserWindow }, size) =>
          BrowserWindow.getAllWindows()[0].setSize(...size),
        [width, height],
      );
      await page.waitForFunction(
        ([width, height]) =>
          Math.abs(innerWidth - width) < 40 &&
          Math.abs(innerHeight - height) < 100,
        [width, height],
      );
      await assertControlsInViewport();
      await page.getByRole("button", { name: "显示或隐藏成果面板" }).click();
      await assertControlsInViewport();
      await page.getByRole("button", { name: "显示或隐藏成果面板" }).click();
    }
    await page.locator(".session-list").hover();
    await page.mouse.wheel(0, 800);
    await page.waitForFunction(
      () => document.querySelector(".session-list").scrollTop > 0,
    );
    await assertControlsInViewport();
    assert.equal(
      await page.locator(".app").evaluate((element) => element.scrollTop),
      0,
    );
    await page
      .locator("[data-layout-fixture]")
      .evaluateAll((entries) => entries.forEach((entry) => entry.remove()));
    await page
      .locator(".session-list")
      .evaluate((list) => (list.scrollTop = 0));
    await page.locator(".suggestions button").nth(1).click();
    const input = page.getByRole("textbox", { name: "研究问题" });
    assert.match(await input.inputValue(), /costs.csv/);
    let intercepted = 0;
    await page.route("**/api/sessions/*/runs", (route) => {
      if (route.request().method() !== "POST") return route.continue();
      intercepted++;
      assert.equal(
        route.request().postDataJSON().question,
        "界面发送检查，不执行研究",
      );
      return route.fulfill({
        status: 400,
        contentType: "application/json",
        body: JSON.stringify({ detail: "测试已拦截提交，未调用模型" }),
      });
    });
    await input.fill("界面发送检查，不执行研究");
    await page.getByRole("button", { name: "发送研究", exact: true }).click();
    await page
      .getByText("Error: 测试已拦截提交，未调用模型", { exact: true })
      .waitFor();
    assert.equal(intercepted, 1);
    await page.locator(".error-banner button").click();
    await input.press("Enter");
    await page
      .getByText("Error: 测试已拦截提交，未调用模型", { exact: true })
      .waitFor();
    assert.equal(intercepted, 2);
    await page.unroute("**/api/sessions/*/runs");
    await input.fill("");
    const title = "桌面界面检查 " + Date.now();
    await page.locator(".session.selected summary").click();
    await page.getByRole("button", { name: "重命名", exact: true }).click();
    await page.getByRole("textbox", { name: "会话名称" }).fill(title);
    await page.getByRole("button", { name: "保存名称" }).click();
    const session = page.locator(".session").filter({ hasText: title });
    await session.locator("summary").click();
    await page.getByRole("button", { name: "归档会话", exact: true }).click();
    await page.getByRole("button", { name: "切换归档" }).click();
    await session.waitFor();
    await page.getByPlaceholder("搜索会话").fill("不会匹配的会话名称");
    assert.equal(await page.locator(".session").count(), 0);
    await page.getByPlaceholder("搜索会话").fill("");
    await page.getByRole("button", { name: "本地工作区" }).click();
    await page.getByRole("heading", { name: "运行设置" }).waitFor();
    await page.locator(".pricing-editor summary").click();
    for (const name of ["未缓存输入费率", "缓存输入费率", "输出费率"]) {
      await page.getByRole("spinbutton", { name, exact: true }).waitFor();
    }
    await page.getByRole("button", { name: "完成", exact: true }).click();
    await page.getByRole("button", { name: "显示或隐藏成果面板" }).click();
    assert.equal(await page.locator(".inspector").count(), 0);
    await page.getByRole("button", { name: "显示或隐藏成果面板" }).click();
    assert.equal(await page.locator(".inspector").count(), 1);
    assert.deepEqual(errors, []);
    assert.equal(submissions, 2);
    console.log(
      "桌面检查通过：长历史/三种窗口尺寸下输入和操作区可见，新建、示例、按钮/Enter发送、重命名、归档、搜索、设置与面板切换；两次提交均已拦截，没有创建研究或调用模型。",
    );
  } finally {
    await application.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
