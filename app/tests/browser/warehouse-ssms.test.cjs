// Open in SSMS on the Warehouse page, in a real browser (see harness.cjs for how to run it). The engine's
// wh_connection is mocked (test_warehouse_ssms.py tests it) and so is the shell (ssms_link.rs tests the
// command line SSMS gets).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const WS = "11111111-2222-3333-4444-555555555555";
const WH_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
const WH = { kind: "warehouse", workspace_id: WS, workspace_name: "Sales Prod", item_id: WH_ID, name: "WH_Gold" };
const SERVER = "x6eps4xrq2xudenlfv6naeo3i4-abc123.datawarehouse.fabric.microsoft.com";
const EXE = "C:\\Program Files\\Microsoft SQL Server Management Studio 22\\Release\\Common7\\IDE\\SSMS.exe";

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
// The clipboard, as the page writes it.
const copied = () => page().evaluate(() => window.__copied);
const shellCalls = (cmd) => page().evaluate((c) => window.__fdShellCalls.filter((x) => x.cmd === c).map((x) => x.args), cmd);
// What the shell answers: ssms_find finds SSMS only when `found` is set (or in the chosen folder).
const shellAnswers = (found) => page().evaluate(({ found, exe }) => {
  const base = window.__fdBaseInvoke ||= window.__TAURI__.core.invoke;
  window.__TAURI__.core.invoke = async (cmd, args) => {
    const out = await base(cmd, args);
    if (cmd === "ssms_find") return found || args.chosen === "D:\\Tools\\SSMS" ? exe : null;
    if (cmd === "pick_folder") return "D:\\Tools\\SSMS";
    return out;
  };
}, { found, exe: EXE });

before(async () => {
  if (skip) return;
  app = await openApp({
    mocks: {
      sql_queries_list: () => ({ places: [], queries: [] }),
      wh_connection: () => ({ server: SERVER, database: "WH_Gold", user: "dana@contoso.com" }),
    },
  });
  await page().evaluate((wh) => openWarehouseQuery(wh), WH);
  await page().waitForSelector(".warehouse-page");
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

test("without SSMS on the PC, the dialog gives the server name to connect by hand", { skip }, async () => {
  await shellAnswers(false);
  await page().getByRole("button", { name: "Open in SSMS", exact: true }).click();
  const dialog = page().getByRole("dialog", { name: "SSMS isn't on this PC" });
  await dialog.waitFor();
  assert.strictEqual(await dialog.getByRole("textbox", { name: "Server name" }).inputValue(), SERVER);
  assert.match(await dialog.textContent(), /Database WH_Gold · Authentication: Microsoft Entra MFA/);
  assert.strictEqual((await shellCalls("open_in_ssms")).length, 0);
  const call = (await app.calls.filter((c) => c.method === "wh_connection"))[0].params;
  assert.deepStrictEqual({ ...call }, { workspace_id: WS, item_id: WH_ID, kind: "warehouse" });
  await dialog.getByRole("button", { name: "Close", exact: true }).last().click();
});

test("choosing SSMS's folder remembers it and opens SSMS", { skip }, async () => {
  await page().getByRole("button", { name: "Open in SSMS", exact: true }).click();
  const dialog = page().getByRole("dialog", { name: "SSMS isn't on this PC" });
  await dialog.getByRole("button", { name: "Choose SSMS's folder…" }).click();
  await dialog.waitFor({ state: "detached" });
  const opened = await shellCalls("open_in_ssms");
  assert.deepStrictEqual(opened.at(-1), { exe: EXE, server: SERVER, database: "WH_Gold", user: "dana@contoso.com", query: null });
  assert.strictEqual(await page().evaluate(() => localStorage.getItem("fd.ssms.folder")), "D:\\Tools\\SSMS");
  assert.match(await page().locator("#toast").textContent(), /Opened SSMS on WH_Gold\. Sign in there if it asks; KratosForge doesn't pass it a password or token\./);
});

test("the menu opens SSMS with the query, copies the server, and opens VS Code", { skip }, async () => {
  await shellAnswers(true);
  await page().evaluate(() => {
    const v = sqlPage.editor.view;
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: "SELECT TOP 10 * FROM dbo.sales" } });
  });
  await page().getByRole("button", { name: "More SSMS options" }).click();
  const menu = page().getByRole("menu");
  assert.match(await menu.textContent(), new RegExp(`signs in as you with Microsoft Entra\\..*${SERVER.replace(/\./g, "\\.")}`));
  await menu.getByRole("menuitem", { name: "Open in SSMS with this query" }).click();
  await page().waitForFunction(() => window.__fdShellCalls.filter((x) => x.cmd === "open_in_ssms").length >= 2);
  const opened = (await shellCalls("open_in_ssms")).at(-1);
  assert.strictEqual(opened.exe, EXE);
  assert.strictEqual(opened.query, "SELECT TOP 10 * FROM dbo.sales");
  await page().getByRole("button", { name: "More SSMS options" }).click();
  await page().evaluate(() => Object.defineProperty(navigator, "clipboard", { configurable: true,
    value: { writeText: async (t) => { window.__copied = t; } } }));
  await page().getByRole("menu").getByRole("menuitem", { name: "Copy server name" }).click();
  await page().waitForFunction(() => window.__copied);
  assert.strictEqual(await copied(), SERVER);
  await page().getByRole("button", { name: "More SSMS options" }).click();
  await page().getByRole("menu").getByRole("menuitem", { name: "Open in VS Code (MSSQL extension)" }).click();
  await page().waitForFunction(() => window.__fdShellCalls.some((x) => x.cmd === "open_in_mssql"));
  assert.deepStrictEqual((await shellCalls("open_in_mssql"))[0], { server: SERVER, database: "WH_Gold", user: "dana@contoso.com" });
  assert.deepStrictEqual(app.errors, []);
});
