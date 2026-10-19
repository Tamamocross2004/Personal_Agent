// 运行前关闭已有应用。只操作会话与设置，不提交研究、不调用模型。
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
    await page.getByRole("button", { name: "完成", exact: true }).click();
    await page.getByRole("button", { name: "显示或隐藏成果面板" }).click();
    assert.equal(await page.locator(".inspector").count(), 0);
    await page.getByRole("button", { name: "显示或隐藏成果面板" }).click();
    assert.equal(await page.locator(".inspector").count(), 1);
    assert.deepEqual(errors, []);
    assert.equal(submissions, 0);
    console.log(
      "桌面检查通过：新建、重命名、归档、搜索、设置与面板切换；没有提交研究。",
    );
  } finally {
    await application.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
