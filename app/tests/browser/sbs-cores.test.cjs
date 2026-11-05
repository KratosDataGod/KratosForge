// Side by side's "Cores on this PC": the card offers all cores, Match Fabric (the vCores of the
// notebook's Fabric session) or a number, keeps the choice with the notebook, and the result
// card says how many cores each side had.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const ENGINE = ["app_settings", "save_app_settings"];

let app;
const saved = [];
let lastRun = [];
const page = () => app.page;
const cores = (choice) => {
  const n = choice === "all" ? 24 : choice === "match" ? 2 : choice;
  return { choice, pc_cores: 24, engine: "python", fabric_vcores: 2, cores: n };
};

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ENGINE, mocks: {
    save_notebook: () => ({ saved: true }),
    side_by_side_list: () => lastRun,
    side_by_side_cores: () => cores("match"),
    side_by_side_cores_set: (p) => { saved.push(p.choice); return cores(p.choice); },
  } });
  await page().evaluate(async () => {
    await loadSettings();
    localStorage.setItem("fd.sbs.TPC-H data", "1");
    loadIntoState({ name: "TPC-H data", cells: [{ type: "code", cell_id: "c1", source: "x = 1", meta: {} }] });
    go("notebook");
  });
});

after(async () => { if (app) await app.close(); });

test("the card offers the cores, with Match Fabric chosen", { skip }, async () => {
  const group = page().getByRole("group", { name: "Cores on this PC" });
  await group.waitFor();
  assert.strictEqual(await group.getByRole("button", { name: "Match Fabric (2 vCores)" }).getAttribute("aria-pressed"), "true");
  await page().getByText("Uses 2 cores, the vCores of this notebook's Fabric session", { exact: false }).waitFor();
  if (SHOTS) await page().locator(".sbs").screenshot({ path: path.join(SHOTS, "sbs-cores-card.png") });
});

test("Pick a number keeps a count with the notebook", { skip }, async () => {
  await page().getByRole("button", { name: "Pick a number" }).click();
  const n = page().getByLabel("Number of cores");
  await n.waitFor();
  assert.deepStrictEqual(saved, [2]);
  await n.fill("8");
  await n.dispatchEvent("change");
  await page().getByText("Uses 8 of 24 cores", { exact: false }).waitFor();
  assert.deepStrictEqual(saved, [2, 8]);
  await page().getByRole("button", { name: "All 24 cores" }).click();
  await page().getByText("so the comparison is about hardware as much as engines", { exact: false }).waitFor();
  assert.deepStrictEqual(saved, [2, 8, "all"]);
});

test("the result says how many cores each side had", { skip }, async () => {
  lastRun = [{ id: "r1", name: "TPC-H data", state: "done", started_at: "2026-10-27T20:00:00Z", workspace_name: "Bench",
    machine: { cores: 24, cores_used: 2, memory_gb: 64 },
    local: { status: "ok", seconds: 130, processing_seconds: 40, upload_wait_seconds: 90 },
    fabric: { status: "ok", engine: "python", vcores: 2, total_seconds: 300, start_seconds: 14, run_seconds: 286 },
    verdict: { same: true, text: "Same results in both places" }, tables: [],
    speed: { cores: "Same cores on both sides: 2 on this PC, 2 vCores in Fabric", end_to_end: "This PC was 2.3x faster end to end" } }];
  await page().evaluate(() => { state.notebook.sbs = undefined; renderNotebook(); });
  await page().getByText("This PC · 2 of 24 cores · 64 GB").waitFor();
  await page().getByText("Python notebook · 2 vCores", { exact: false }).waitFor();
  await page().getByText("Same cores on both sides: 2 on this PC, 2 vCores in Fabric").waitFor();
  if (SHOTS) await page().locator(".sbs").screenshot({ path: path.join(SHOTS, "sbs-cores-result.png") });
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
