// Open in SSMS (whssms.js), the paths warehouse-ssms leaves out: with SSMS not found, Copy that the
// clipboard refuses, Get SSMS, and a chosen folder that doesn't hold SSMS (nothing is remembered or
// started). The engine's connection is mocked and the shell's answers are given by the test; what the
// person sees and what the shell is asked is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const REF = { workspace_id: "w1", item_id: "wh1", kind: "Warehouse", name: "WH_Gold" };
const CONN = { server: "abc.datawarehouse.fabric.microsoft.com", database: "WH_Gold", user: "chris@contoso.com" };

let app;
const page = () => app.page;
const dialog = () => page().getByRole("dialog", { name: "SSMS isn't on this PC" });
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast")?.textContent || ""), re.source);

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { wh_connection: () => CONN } });
  // The shell: SSMS is nowhere, and the folder picker answers C:\Tools. Other commands go on as before.
  await page().evaluate(() => {
    const real = window.__TAURI__.core.invoke;
    window.__shell = [];
    window.__TAURI__.core.invoke = (cmd, args) => {
      if (cmd === "ssms_find" || cmd === "pick_folder" || cmd === "open_in_ssms") {
        window.__shell.push([cmd, args]);
        return Promise.resolve(cmd === "pick_folder" ? "C:\\Tools" : null);
      }
      return real(cmd, args);
    };
  });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  await page().evaluate(() => {
    closeDialog(); closeDialog();
    window.__shell.length = 0;
    try { localStorage.removeItem("fd.ssms.folder"); } catch { /* fine */ }
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
  });
  app.errors.length = 0;
});

test("with SSMS not found, Copy that the clipboard refuses says so, and Get SSMS opens Microsoft's page", { skip }, async () => {
  await page().evaluate((ref) => { whOpenSsms(ref); }, REF);
  await dialog().waitFor();
  assert.strictEqual(await dialog().getByRole("textbox", { name: "Server name" }).inputValue(), CONN.server);
  await page().evaluate(() => {
    Object.defineProperty(navigator, "clipboard", { value: { writeText: async () => { throw new Error("denied"); } }, configurable: true });
    window.__openLink = window.openLink; window.__links = []; window.openLink = (u) => window.__links.push(u);
  });
  await dialog().getByRole("button", { name: "Copy server name" }).click();
  await toastSays(/^Couldn't copy the server name\.$/);
  await dialog().getByRole("link", { name: "Get SSMS" }).click();
  assert.deepStrictEqual(await page().evaluate(() => { window.openLink = window.__openLink; return window.__links; }),
    ["https://learn.microsoft.com/ssms/install/install"]);
  assert.deepStrictEqual(app.errors, []);
});

test("a chosen folder without SSMS says so; nothing is remembered or started", { skip }, async () => {
  await page().evaluate((ref) => { whOpenSsms(ref, "SELECT 1"); }, REF);
  await dialog().getByRole("button", { name: "Choose SSMS's folder…" }).click();
  await dialog().getByText("There's no SSMS.exe signed by Microsoft in C:\\Tools.", { exact: false }).waitFor();
  assert.ok(!(await dialog().getByRole("button", { name: "Choose SSMS's folder…" }).isDisabled()), "it can be chosen again");
  assert.deepStrictEqual(await page().evaluate(() => window.__shell.map(([c, a]) => [c, a.chosen ?? a.start ?? null])),
    [["ssms_find", null], ["pick_folder", null], ["ssms_find", "C:\\Tools"]]);
  assert.strictEqual(await page().evaluate(() => localStorage.getItem("fd.ssms.folder")), null);
  assert.deepStrictEqual(app.errors, []);
});
