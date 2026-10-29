// Browse… beside each folder box (#971): the shell's folder dialog (pick_folder) is mocked, and
// each answer is what the person picked, or null for Cancel (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";

let app;
const page = () => app.page;
// The next folder the dialog answers with (null is Cancel), and the start folders it was asked for.
const answer = (folder) => page().evaluate((f) => { window.__fdPickAnswer = f; }, folder);
const starts = () => page().evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "pick_folder").map((c) => c.args.start));

before(async () => {
  if (skip) return;
  app = await openApp();
  await page().evaluate(() => {
    const invoke = window.__TAURI__.core.invoke;
    window.__TAURI__.core.invoke = async (cmd, args) => {
      if (cmd !== "pick_folder") return invoke(cmd, args);
      window.__fdShellCalls.push({ cmd, args });
      return window.__fdPickAnswer;
    };
  });
});

after(async () => { await app?.close(); });

test("Browse… fills the folder box, Cancel leaves it, and typing still works", { skip }, async () => {
  await page().evaluate(() => gitOpenDialog());
  const box = page().getByRole("textbox", { name: "Folder", exact: true });
  const browse = page().getByRole("button", { name: "Browse for the repository's folder" });
  await answer("C:\\Users\\you\\source\\repos\\sales");
  await browse.click();
  await page().waitForFunction(() => document.activeElement?.value === "C:\\Users\\you\\source\\repos\\sales");
  assert.strictEqual(await box.inputValue(), "C:\\Users\\you\\source\\repos\\sales");
  await answer(null);
  await browse.click();
  await page().waitForFunction(() => window.__fdShellCalls.filter((c) => c.cmd === "pick_folder").length === 2);
  assert.strictEqual(await box.inputValue(), "C:\\Users\\you\\source\\repos\\sales", "Cancel keeps what was there");
  assert.deepStrictEqual(await starts(), [null, "C:\\Users\\you\\source\\repos\\sales"], "the dialog starts in the box's folder");
  await box.fill("D:\\typed");
  assert.strictEqual(await box.inputValue(), "D:\\typed");
  await page().evaluate(() => closeDialog());
  assert.deepStrictEqual(app.errors, []);
});

test("Browse… is off while its dialog is open, so a double click opens one", { skip }, async () => {
  await page().evaluate(() => {
    const invoke = window.__fdInvoke = window.__TAURI__.core.invoke;
    window.__TAURI__.core.invoke = (cmd, args) => cmd === "pick_folder"
      ? (window.__fdShellCalls.push({ cmd, args }), new Promise((r) => { window.__fdPickDone = r; })) : invoke(cmd, args);
    gitOpenDialog();
  });
  const before = (await starts()).length;
  const browse = page().getByRole("button", { name: "Browse for the repository's folder" });
  await browse.dblclick({ force: true });
  assert.strictEqual(await browse.isDisabled(), true);
  assert.strictEqual((await starts()).length, before + 1, "one dialog");
  await page().evaluate(() => window.__fdPickDone("C:\\once"));
  await page().waitForFunction(() => !document.querySelector('button[aria-label="Browse for the repository\'s folder"]').disabled);
  assert.strictEqual(await page().getByRole("textbox", { name: "Folder", exact: true }).inputValue(), "C:\\once");
  await page().evaluate(() => { window.__TAURI__.core.invoke = window.__fdInvoke; closeDialog(); });
  assert.deepStrictEqual(app.errors, []);
});

test("every Source control folder box has Browse…", { skip }, async () => {
  await page().evaluate(() => { gitPage.info = { ...(gitPage.info || {}), clone_folder: "C:\\repos", kinds: { notebooks: { label: "Notebooks", holds: "", for: "" } } }; });
  for (const [open, name] of [["gitInitDialog()", "Browse for the folder"], ["gitCloneDialog()", "Browse for the folder to clone into"],
    ["gitNewDialog()", "Browse for the folder to create it in"]]) {
    await page().evaluate((o) => eval(o), open);
    assert.strictEqual(await page().getByRole("button", { name }).count(), 1, `${open}: ${name}`);
    await page().evaluate(() => closeDialog());
  }
  assert.deepStrictEqual(app.errors, []);
});

test("the metadata project setup page has Browse… for both folders", { skip }, async () => {
  await page().evaluate(() => renderMetaSetup({ default_path: "C:\\Users\\you\\Documents\\metadata", presets: { 3: [{ name: "Bronze" }, { name: "Silver" }, { name: "Gold" }] } }));
  const before = (await starts()).length;
  await answer("E:\\projects\\sales");
  await page().getByRole("button", { name: "Browse for the project's folder" }).click();
  await page().waitForFunction(() => document.getElementById("meta-open-path").value === "E:\\projects\\sales");
  await answer(null);
  await page().getByRole("button", { name: "Browse for the new project's folder" }).click();
  await page().waitForFunction((n) => window.__fdShellCalls.filter((c) => c.cmd === "pick_folder").length === n + 2, before);
  assert.strictEqual((await starts()).at(-1), "C:\\Users\\you\\Documents\\metadata");
  assert.strictEqual(await page().locator("#meta-path").inputValue(), "C:\\Users\\you\\Documents\\metadata");
  assert.deepStrictEqual(app.errors, []);
});

test("a temp folder that policy sets has no Browse…", { skip }, async () => {
  const count = (source) => page().evaluate((source) => {
    const s = Object.fromEntries(SQL_SETTINGS.map(([key, , , min]) => [key, { value: min, source: "default" }]));
    s.sql_temp_dir = { value: "D:\\spill", source };
    s.sql_warehouse_writes = { value: false, source: "default" };
    const root = sqlSettingsSection(s, (key) => fixedBy(s, key) !== null);
    return root.querySelectorAll('button[aria-label="Browse for the temp folder"]').length;
  }, source);
  assert.strictEqual(await count("user"), 1);
  assert.strictEqual(await count("policy"), 0);
  assert.deepStrictEqual(app.errors, []);
});
