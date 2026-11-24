// Replay bundles (replay.js), the paths replay-bundle and replay-panel leave out: tables the notebook
// created, files with and without a SHA-256, the kernel's limits, a bundle with nothing to sample, the
// banner's notes and blockers, a delete that partly failed, and keeping two replays' outputs.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const db = { deletes: [] };
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    replay_bundle_preview: () => ({ samples_allowed: true, sample_tables: [], sample_rows: 1000,
      bundle: { failed_cell: 1, cells: [{}], inputs: {}, environment: { python: "3.12" }, error: null, limitations: [] } }),
    replay_delete_outputs: ({ name }) => { db.deletes.push(name); return { deleted: ["a"], problems: ["b is in use"] }; },
    replay_check: () => ({ tables: [], environment: [], notes: [], blockers: [], sandboxes: [] }),
  } });
  app.errors.length = 0;
});
after(async () => { await app?.close(); });
beforeEach(() => { if (!skip) app.errors.length = 0; });

const toastSays = (text) => page().waitForFunction((t) => {
  const el = document.getElementById("toast");
  return !el.hidden && el.textContent.startsWith(t);
}, text, { timeout: 10000 });

test("inputs say which tables the notebook made and which files have no SHA-256; limits are in minutes and GB", { skip }, async () => {
  const out = await page().evaluate(() => ({
    inputs: replayInputLines({ tables: [{ table: "dbo.tmp", created: true }],
      files: [{ path: "Files/a.csv", sha256: "0123456789abcdef" }, { path: "Files/b.csv", sha256: null }] }),
    env: replayEnvironmentLine({ python: "3.12", packages: { pandas: "2.2" },
      kernel: { process: true, limits: { cell_seconds: 1800, memory_bytes: 4 * 2 ** 30 } } }),
  }));
  assert.deepStrictEqual(out.inputs, ["dbo.tmp: created by the notebook, so a replay starts without it",
    "Files/a.csv (SHA-256 01234567…)", "Files/b.csv (changed after it was read, so no SHA-256)"]);
  assert.strictEqual(out.env, "Python 3.12, pandas 2.2, kernel in its own process, 30 min cell limit, 4 GB memory limit");
  assert.deepStrictEqual(app.errors, []);
});

test("a bundle that read no table says there is nothing to sample", { skip }, async () => {
  const p = page();
  await p.evaluate(() => {
    const cell = { type: "code", source: "1/0", meta: {}, status: "error", outputs: [] };
    replayDialog({ name: "Quiet", cells: [cell], lakehouse: null }, cell);
  });
  const dialog = p.getByRole("dialog", { name: "Save a replay bundle" });
  await dialog.getByText("No table was read, so there is nothing to sample.").waitFor();
  assert.ok(await dialog.getByLabel("Include a sample of the input data").isDisabled());
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await dialog.waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});

test("the banner shows the check's notes and blockers", { skip }, async () => {
  const out = await page().evaluate(() => {
    const nb = { name: "R", replay: { notebook: "Sales", failed_cell: 2, saved_at: null }, cells: [] };
    replayView(nb).check = { tables: [], environment: [], notes: ["The bundle has no sample."], blockers: ["Attach a lakehouse first."], sandboxes: [] };
    const nodes = replayBody(nb);
    return nodes.filter((n) => n.matches("span.muted.small, span.error-text")).map((n) => [n.className, n.textContent]);
  });
  assert.deepStrictEqual(out, [["muted small", "The bundle has no sample."], ["error-text", "Attach a lakehouse first."]]);
  assert.deepStrictEqual(app.errors, []);
});

test("a delete that partly failed says what wasn't deleted; Keep warns that two replays' folders stay", { skip }, async () => {
  const p = page();
  await p.evaluate(() => {
    window.__tRnb = { name: "R2", cells: [], replay: { notebook: "Sales", failed_cell: 1 } };
    replayView(window.__tRnb).check = { sandboxes: [{ folder: "replay-1" }, { folder: "replay-2", lakehouse: "SalesLH" }] };
    replayDeleteOutputs(window.__tRnb);
  });
  const confirm = p.getByRole("dialog", { name: "Delete what 2 replays wrote?" });
  assert.match(await confirm.innerText(), /Deletes replay-1, replay-2 in SalesLH:/);
  await confirm.getByRole("button", { name: "Delete" }).click();
  await toastSays("Deleted 1. Couldn't delete: b is in use");
  assert.deepStrictEqual(db.deletes, ["R2"]);

  await p.evaluate(() => {
    replayView(window.__tRnb).check = { sandboxes: [{ folder: "replay-1" }, { folder: "replay-2" }] };
    replayEnd(window.__tRnb, true);
  });
  const keep = p.getByRole("dialog", { name: "Keep the replay outputs?" });
  assert.match(await keep.innerText(), /2 replay output folders stay in the lakehouse's Files\./);
  await keep.getByRole("button", { name: "Cancel" }).click();
  await keep.waitFor({ state: "detached" });
  assert.ok(!app.calls.some((c) => c.method === "replay_end"), "Cancel keeps the replay");
  assert.ok(await p.evaluate(() => !!window.__tRnb.replay));
  await p.evaluate(() => { delete window.__tRnb; });
  assert.deepStrictEqual(app.errors, []);
});
