// Stage part of a file, against the real engine: each block of changed lines has its own Stage
// button, Discard asks first and keeps the rest of the file, and a staged block can be unstaged.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const BASE = Array.from({ length: 30 }, (_, i) => `line ${i + 1}\n`).join("");

let app, root;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1280, height: 900 },
    engine: ["git_create", "git_status", "git_diff", "git_apply_hunk", "git_stage_all", "git_commit", "git_set_identity"] });
  root = path.join(app.engineHome, "sales-notebooks");
  root = await page().evaluate((p) => rpc("git_create", { path: p, kind: "notebooks" }).then((st) => st.root || p), root);
  fs.writeFileSync(path.join(root, "load.py"), BASE);
  await page().evaluate(async (r) => {
    await rpc("git_set_identity", { repo: r, name: "Test Person", email: "test@example.com" });
    await rpc("git_stage_all", { repo: r });
    await rpc("git_commit", { repo: r, message: "First commit" });
  }, root);
  fs.writeFileSync(path.join(root, "load.py"), BASE.replace("line 3\n", "line 3 fixed\n").replace("line 27\n", "line 27 experiment\n"));
});

after(async () => { if (app) await app.close(); });

// The diff panel on its own, for one file in one list, refreshed after each change as the page does.
async function show(group) {
  await page().evaluate(async ([r, group]) => {
    gitCurrent = () => r;
    gitAfterChange = async (st) => { window.__st = st; gitPage.diff = null; await gitLoadDiff(); };
    gitPage.selected = { path: "load.py", group };
    gitPage.diff = null;
    document.getElementById("git-diff")?.remove();
    document.body.prepend(gitDiffPanel());
    await gitLoadDiff();
  }, [root, group]);
  await page().locator("#git-diff tr.hunk").first().waitFor();
}

const staged = () => page().evaluate((r) => rpc("git_diff", { repo: r, path: "load.py", staged: true }), root);

test("Stage one block: the other stays unstaged and the file on disk is untouched", { skip }, async () => {
  await show("unstaged");
  assert.strictEqual(await page().locator("#git-diff tr.hunk").count(), 2);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "git-hunks.png") });
  await page().locator("#git-diff tr.hunk").first().getByRole("button", { name: "Stage block" }).click();
  await page().waitForFunction(() => document.querySelectorAll("#git-diff tr.hunk").length === 1);
  const d = await staged();
  assert.deepStrictEqual(d.hunks.flatMap((h) => h.lines.filter((l) => l.kind === "+").map((l) => l.text)), ["line 3 fixed"]);
  assert.match(fs.readFileSync(path.join(root, "load.py"), "utf8"), /line 27 experiment/);
  assert.deepStrictEqual(app.errors, []);
});

test("Discard asks first, then throws away only that block", { skip }, async () => {
  await show("unstaged");
  await page().locator("#git-diff tr.hunk").first().getByRole("button", { name: "Discard block…" }).click();
  const dialog = page().getByRole("dialog");
  await dialog.getByText("Discard this block?").waitFor();
  assert.match(await dialog.textContent(), /2 changed lines in load\.py/);
  await dialog.getByRole("button", { name: "Discard block" }).click();
  await page().waitForFunction(() => !document.querySelector("#git-diff tr.hunk") && gitPage.diff);
  const text = fs.readFileSync(path.join(root, "load.py"), "utf8");
  assert.ok(!text.includes("experiment") && text.includes("line 3 fixed"), text);
});

test("A staged block can be unstaged", { skip }, async () => {
  await show("staged");
  await page().locator("#git-diff tr.hunk").first().getByRole("button", { name: "Unstage block" }).click();
  await page().waitForFunction(() => !document.querySelector("#git-diff tr.hunk") && gitPage.diff);
  assert.deepStrictEqual((await staged()).hunks, []);
  assert.deepStrictEqual(app.errors, []);
});
