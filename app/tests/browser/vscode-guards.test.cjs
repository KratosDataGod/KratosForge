// A VS Code window's request to write to OneLake, in a real browser (see harness.cjs for how to run it):
// the person's answer reaches the engine, and a request that comes while another dialog is open waits
// with a note instead of covering it. The engine's editor calls are mocked.
const { test, before, beforeEach, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const WRITE = { id: "w1", notebook: "Daily sales", window: "C:\\repos\\sales-notebooks", target: "Tables/dbo/orders" };
const PAIR = { id: "p1", window: "C:\\repos\\finance", client: "KratosForge for VS Code 0.1.0", code: "4821" };
// What editor_pending answers now.
const pending = { enabled: true, pairing: [], writes: [] };

let app;
const page = () => app.page;
const calls = (method) => app.calls.filter((c) => c.method === method);
const poll = () => page().evaluate(() => { editorPair.watching = false; return watchEditorRequests(); });
const toastText = () => page().evaluate(() => [...document.querySelectorAll(".toast")].map((t) => t.textContent).join("\n"));

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    editor_pending: () => pending,
    editor_decide_write: () => ({ ok: true }),
  } });
  await page().waitForSelector("#rail-main .rail-item");
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

// Each test starts with nothing pending and no dialog open, so they pass in any order or alone.
beforeEach(async () => {
  if (skip) return;
  pending.writes = [];
  pending.pairing = [];
  await page().evaluate(() => { if (document.getElementById("dialog-root")?.children.length) closeDialog(); });
  await page().waitForFunction(() => !document.getElementById("dialog-root")?.children.length);
});

// Waits until editor_decide_write has been called at least n times.
async function decided(n) {
  for (const end = Date.now() + 5000; calls("editor_decide_write").length < n && Date.now() < end;) await new Promise((r) => setTimeout(r, 10));
}

test("a write from VS Code asks here, names the notebook, window and target, and Deny says no", { skip }, async () => {
  const start = calls("editor_decide_write").length;
  pending.writes = [WRITE];
  await poll();
  const dialog = page().getByRole("dialog", { name: "VS Code wants to write to OneLake" });
  await dialog.waitFor();
  const text = await dialog.textContent();
  assert.match(text, /A cell in Daily sales, run from the VS Code window C:\\repos\\sales-notebooks, writes Tables\/dbo\/orders\./);
  assert.match(text, /waits up to 5 minutes/);
  await dialog.getByRole("button", { name: "Deny" }).click();
  await dialog.waitFor({ state: "detached" });
  await decided(start + 1);
  assert.deepStrictEqual(calls("editor_decide_write").at(-1).params, { ask_id: "w1", approve: false });
});

test("the same request isn't asked twice, and a new one can be allowed once", { skip }, async () => {
  const start = calls("editor_decide_write").length;
  const dialog = page().getByRole("dialog", { name: "VS Code wants to write to OneLake" });
  // Answer a request here first, so this test doesn't lean on another one.
  pending.writes = [{ ...WRITE, id: "w2a" }];
  await poll();
  await dialog.getByRole("button", { name: "Deny" }).click();
  await dialog.waitFor({ state: "detached" });
  await decided(start + 1);
  await poll();
  assert.strictEqual(await page().getByRole("dialog").count(), 0, "w2a was answered, so it isn't shown again");
  pending.writes = [{ ...WRITE, id: "w2" }];
  await poll();
  await dialog.getByRole("button", { name: "Allow once" }).click();
  await dialog.waitFor({ state: "detached" });
  await decided(start + 2);
  assert.deepStrictEqual(calls("editor_decide_write").at(-1).params, { ask_id: "w2", approve: true });
});

test("a request while another dialog is open waits, with a note that says how to answer it", { skip }, async () => {
  await page().evaluate(() => showDialog("Something else", h("p", {}, "Busy"), [btn("Done", null, () => closeDialog(), "tool")]));
  const other = page().getByRole("dialog", { name: "Something else" });
  await other.waitFor();
  pending.writes = [{ ...WRITE, id: "w3" }];
  await poll();
  assert.match(await toastText(), /VS Code is waiting to write to OneLake\. Close this dialog to answer\./);
  pending.writes = [];
  pending.pairing = [PAIR];
  await poll();
  assert.match(await toastText(), /A VS Code window is asking to pair\. Close this dialog to answer\./);
  assert.strictEqual(await page().getByRole("dialog").count(), 1, "the open dialog isn't covered");
  await other.getByRole("button", { name: "Done" }).click();
  await other.waitFor({ state: "detached" });
  await poll();
  await page().getByRole("dialog", { name: "Let VS Code run notebooks on this PC?" }).waitFor();
  assert.deepStrictEqual(app.errors, []);
});
