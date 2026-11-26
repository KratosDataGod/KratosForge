// Version history (notebook-versions.js) with the engine's calls mocked, for what
// notebook-versions.test.cjs (real engine) leaves out: versions grouped by day, View, the
// commands with and without a notebook, Cancel paths, saving first when there are unsaved edits,
// Compare with the last commit and an engine that can't read the versions (see harness.cjs).
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";

const daysAgo = (d, hour = 10) => { const t = new Date(); t.setDate(t.getDate() - d); t.setHours(hour, 0, 0, 0); return t.toISOString(); };
let versions;
let compare;
let failList;
let app;
const page = () => app.page;
const calls = (method) => app.calls.filter((c) => c.method === method).map((c) => c.params);

before(async () => {
  if (skip) return;
  app = await openApp({
    mocks: {
      notebook_versions: () => { if (failList) throw new Error("The versions folder can't be read"); return { versions, coalesce_minutes: 5, keep_days: 30 }; },
      notebook_version: () => ({ cells: [{ type: "markdown", source: "# Daily sales" }, { type: "code", source: "" }] }),
      notebook_version_compare: () => compare,
      notebook_checkpoint: ({ label }) => ({ label }),
      notebook_version_restore: () => ({ name: "Daily sales", cells: [{ type: "code", source: "orders = 0" }] }),
      notebook_version_delete: () => ({ deleted: true }),
      save_notebook: () => ({ saved: true }),
      list_notebooks: () => [{ name: "Daily sales" }],
    },
  });
});

after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  failList = false;
  compare = { changes: [] };
  versions = [
    { id: "v3", kind: "auto", at: daysAgo(0), cells: 2, current: true },
    { id: "v2", kind: "checkpoint", label: "Before the refactor", at: daysAgo(1), cells: 1 },
    { id: "v1", kind: "changed outside", at: daysAgo(9), cells: 3 },
  ];
  await page().evaluate(() => {
    document.getElementById("palette")?.remove(); closeDialog();
    loadIntoState({ name: "Daily sales", cells: [{ type: "code", source: "orders = 1" }, { type: "code", source: "total = orders * 2" }] });
    go("notebook");
  });
  app.calls.length = 0;
  app.errors.length = 0;
});

const versionsDialog = () => page().getByRole("dialog", { name: "Versions of Daily sales" });
const openVersions = async () => { await page().evaluate(() => nbVersions()); await versionsDialog().waitFor(); };

async function command(label) {
  await page().keyboard.press("Control+k");
  await page().locator("#palette").waitFor();
  await page().keyboard.type(label);
  await page().waitForFunction((l) => document.querySelector("#palette .palette-item.on")?.textContent.includes(l), label);
  await page().keyboard.press("Enter");
}

test("versions are grouped by day: today, yesterday, then the date", { skip }, async () => {
  await openVersions();
  const days = await versionsDialog().locator(".nbv-h").allTextContents();
  assert.strictEqual(days.length, 3);
  assert.deepStrictEqual(days.slice(0, 2), ["Today", "Yesterday"]);
  assert.ok(!["Today", "Yesterday"].includes(days[2]));
  const text = await versionsDialog().innerText();
  assert.match(text, /one every 5 minutes while you edit, for 30 days/);
  assert.match(text, /Changed outside the app/);
  assert.match(text, /2 cells/);
  assert.match(text, /1 cell\b/);
  // The version that is the same as now can't be compared or restored.
  assert.strictEqual(await versionsDialog().getByRole("button", { name: /^Compare with Saved/ }).isDisabled(), true);
  assert.strictEqual(await versionsDialog().getByRole("button", { name: /^Restore Saved/ }).isDisabled(), true);
  assert.strictEqual(await versionsDialog().getByRole("button", { name: /^Restore Before the refactor/ }).isEnabled(), true);
  assert.deepStrictEqual(calls("notebook_versions"), [{ name: "Daily sales" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("no versions yet says the next save keeps one", { skip }, async () => {
  versions = [];
  await openVersions();
  await versionsDialog().getByText("No versions yet. The next save keeps one.").waitFor();
});

test("versions that can't be read show an error and no dialog", { skip }, async () => {
  failList = true;
  await page().evaluate(() => nbVersions());
  await page().waitForFunction(() => document.getElementById("toast").textContent.startsWith("Couldn't read the versions"));
  assert.strictEqual(await versionsDialog().count(), 0);
});

test("unsaved edits are saved before the versions are listed", { skip }, async () => {
  await page().evaluate(() => { state.notebook.cells[0].source = "orders = 5"; markDirty(); });
  await openVersions();
  const saves = calls("save_notebook");
  assert.strictEqual(saves.length, 1);
  assert.strictEqual(saves[0].cells[0].source, "orders = 5");
  assert.ok(app.calls.findIndex((c) => c.method === "save_notebook") < app.calls.findIndex((c) => c.method === "notebook_versions"));
});

test("View shows the version's cells read only, and Restore from there asks first", { skip }, async () => {
  await openVersions();
  await versionsDialog().getByRole("button", { name: /^View Before the refactor/ }).click();
  const view = page().getByRole("dialog", { name: /^Before the refactor, / });
  await view.getByText("2 cells, as saved. Outputs aren't kept in versions.").waitFor();
  const text = await view.innerText();
  assert.match(text, /Cell 1 \(Markdown\)\n# Daily sales/);
  assert.match(text, /Cell 2\n/);
  assert.deepStrictEqual(calls("notebook_version"), [{ name: "Daily sales", version_id: "v2" }]);
  await view.getByRole("button", { name: "Restore this version" }).click();
  const ask = page().getByRole("dialog", { name: "Restore Before the refactor?" });
  await ask.getByText(/is kept as a version first/).waitFor();
  await ask.getByRole("button", { name: "Cancel" }).click();
  // Cancel goes back to the list, and nothing is restored.
  await versionsDialog().waitFor();
  assert.deepStrictEqual(calls("notebook_version_restore"), []);
  assert.deepStrictEqual(app.errors, []);
});

test("View of the version that's the same as now has no Restore, and Back returns to the list", { skip }, async () => {
  await openVersions();
  await versionsDialog().getByRole("button", { name: /^View Saved/ }).click();
  const view = page().getByRole("dialog", { name: /^Saved, / });
  await view.getByText(/2 cells, as saved/).waitFor();
  assert.strictEqual(await view.getByRole("button", { name: "Restore this version" }).count(), 0);
  await view.getByRole("button", { name: "Back to versions" }).click();
  await versionsDialog().waitFor();
});

test("Restore saves unsaved edits first, then loads the version's cells", { skip }, async () => {
  await openVersions();
  await page().evaluate(() => { state.notebook.cells[0].source = "orders = 9"; markDirty(); });
  await versionsDialog().getByRole("button", { name: /^Restore Changed outside the app/ }).click();
  await page().getByRole("dialog", { name: "Restore Changed outside the app?" }).getByRole("button", { name: "Restore" }).click();
  await page().waitForFunction(() => state.notebook?.cells[0]?.source === "orders = 0");
  assert.strictEqual(calls("save_notebook").length, 1);
  assert.strictEqual(calls("save_notebook")[0].cells[0].source, "orders = 9");
  assert.deepStrictEqual(calls("notebook_version_restore"), [{ name: "Daily sales", version_id: "v1" }]);
  assert.match(await page().locator("#toast").textContent(), /^Restored the version from .*The previous cells are kept as a version\.$/);
});

test("a saved version's compare and restore name it as the version saved", { skip }, async () => {
  versions = [{ id: "v9", kind: "auto", at: daysAgo(2), cells: 2 }];
  compare = { changes: [{ kind: "reordered" }] };
  await openVersions();
  await versionsDialog().getByRole("button", { name: /^Compare with Saved/ }).click();
  const cmp = page().getByRole("dialog", { name: /^Now compared with the version saved / });
  await cmp.getByText("Cells reordered.").waitFor();
  assert.match(await cmp.innerText(), /Cells were reordered/);
  assert.deepStrictEqual(calls("notebook_version_compare").map((p) => [p.version_id, p.cells.length]), [["v9", 2]]);
  await cmp.getByRole("button", { name: "Restore this version" }).click();
  await page().getByRole("dialog", { name: "Restore this version?" }).waitFor();
  await page().getByRole("dialog", { name: "Restore this version?" }).getByRole("button", { name: "Cancel" }).click();
  await versionsDialog().waitFor();
});

test("Compare with last commit sends the cells against head and shows the engine's problem", { skip }, async () => {
  compare = { problem: "This notebook isn't in a Git repository.", changes: [] };
  await openVersions();
  await versionsDialog().getByRole("button", { name: "Compare with last commit" }).click();
  const cmp = page().getByRole("dialog", { name: "Now compared with the last commit" });
  await cmp.getByText("This notebook isn't in a Git repository.").waitFor();
  assert.strictEqual(await cmp.getByRole("button", { name: "Restore this version" }).count(), 0);
  const sent = calls("notebook_version_compare");
  assert.strictEqual(sent.length, 1);
  assert.strictEqual(sent[0].against, "head");
  assert.strictEqual(sent[0].version_id, undefined);
  assert.deepStrictEqual(sent[0].cells.map((c) => c.source), ["orders = 1", "total = orders * 2"]);
});

test("a compare with no cell changes says so, and with changes counts them", { skip }, async () => {
  await openVersions();
  await page().evaluate(() => nbVersionCompare(null));
  await page().getByRole("dialog", { name: "Now compared with the last commit" }).getByText("No changes to cells.").waitFor();
  compare = { changes: [{ kind: "added", position: 3, diff: ["+x = 1"] }, { kind: "added", position: 4, diff: [] }, { kind: "removed", position: 1, diff: ["-y"] }] };
  await page().evaluate(() => nbVersionCompare(null));
  const cmp = page().getByRole("dialog", { name: "Now compared with the last commit" });
  await cmp.getByText("2 cells added, 1 cell removed.").waitFor();
  assert.deepStrictEqual(await cmp.locator(".nbv-diff span.add").allTextContents(), ["+x = 1"]);
  assert.deepStrictEqual(await cmp.locator(".nbv-diff span.del").allTextContents(), ["-y"]);
});

test("Cancel on a new checkpoint goes back to the list without creating one", { skip }, async () => {
  await openVersions();
  await versionsDialog().getByRole("button", { name: "Create checkpoint…" }).click();
  await page().getByRole("dialog", { name: "Create a checkpoint" }).getByRole("button", { name: "Cancel" }).click();
  await versionsDialog().waitFor();
  assert.deepStrictEqual(calls("notebook_checkpoint"), []);
});

test("the Create a checkpoint command saves unsaved edits, then keeps the checkpoint", { skip }, async () => {
  await page().evaluate(() => { state.notebook.cells[1].source = "total = orders * 4"; markDirty(); });
  await command("Create a checkpoint");
  const ask = page().getByRole("dialog", { name: "Create a checkpoint" });
  await ask.getByRole("textbox").fill("Release 1");
  await ask.getByRole("button", { name: "Create" }).click();
  await versionsDialog().waitFor();
  assert.strictEqual(await page().locator("#toast").textContent(), 'Checkpoint "Release 1" kept');
  assert.deepStrictEqual(calls("notebook_checkpoint"), [{ name: "Daily sales", label: "Release 1" }]);
  assert.strictEqual(calls("save_notebook")[0].cells[1].source, "total = orders * 4");
  assert.ok(app.calls.findIndex((c) => c.method === "save_notebook") < app.calls.findIndex((c) => c.method === "notebook_checkpoint"));
});

test("the Version history command opens the list", { skip }, async () => {
  await command("Version history");
  await versionsDialog().waitFor();
  assert.deepStrictEqual(calls("notebook_versions"), [{ name: "Daily sales" }]);
});

test("the commands ask for a notebook when none is open", { skip }, async () => {
  await page().evaluate(() => { state.notebook = null; go("home"); });
  await command("Version history");
  await page().waitForFunction(() => document.getElementById("toast").textContent === "Open a notebook first.");
  await page().evaluate(() => { document.getElementById("toast").textContent = ""; });
  await command("Create a checkpoint");
  await page().waitForFunction(() => document.getElementById("toast").textContent === "Open a notebook first.");
  assert.strictEqual(await page().getByRole("dialog").count(), 0);
  assert.deepStrictEqual(calls("notebook_versions"), []);
  // nbVersions itself does nothing without a notebook.
  await page().evaluate(() => nbVersions());
  assert.deepStrictEqual(calls("notebook_versions"), []);
  assert.deepStrictEqual(app.errors, []);
});
