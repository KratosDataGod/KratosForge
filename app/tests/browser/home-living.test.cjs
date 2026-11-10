// LX-06: the progress panel on Home (home-hero.js), beside a plain Home header, driven with what the
// real engine's profile_get returns for one validated notebook (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const BUILD = `
import json, tempfile
from pathlib import Path
from fabric_desktop.service import Service
class Auth:
    def signed_in_user(self): return "dev@contoso.com"
s = Service(client_id="x", root=Path(tempfile.mkdtemp()) / "app")
s._auth = Auth()
s.profile_save(settings={"record": True}, identity={"display_name": "Chris", "role": "Data engineer", "technologies": "Fabric, Delta Lake"})
s._progress_observe({"id": "validate-20261020-104212-a1b2c3", "name": "sales_daily_load", "verdict": "ready",
    "run": {"status": "ok"}, "finished_at": "2026-10-20T10:42:12+00:00",
    "code": {"revision": "3f9c1e0d2b7a" + "0" * 52}, "tables": {"reads": ["a", "b"], "writes": ["c"]}})
json.dump(s.profile_get(), __import__("sys").stdout)
`;

let app;
let profile;
const page = () => app.page;

before(async () => {
  if (skip) return;
  const python = process.env.FABRIC_DESKTOP_PYTHON || (process.platform === "win32" ? "python" : "python3");
  const out = spawnSync(python, ["-c", BUILD], { cwd: path.join(__dirname, "..", "..", "..", "sidecar"), encoding: "utf8" });
  assert.strictEqual(out.status, 0, out.stderr);
  profile = JSON.parse(out.stdout);
  // This PC's run logs: four local runs in the last 7 days (one metadata run went to Fabric and doesn't
  // count), two in the 7 before, so Your activity reads 4 and +100%.
  const ago = (days) => new Date(Date.now() - days * 86400000).toISOString();
  app = await openApp({ engine: ["app_settings", "app_settings_save"], mocks: {
    profile_get: () => profile, list_notebooks: () => [],
    meta_runs_all: () => ({ runs: [{ started_at: ago(1) }, { started_at: ago(2), compute: "local" }, { started_at: ago(3), compute: "fabric" }, { started_at: ago(9) }], unavailable: {} }),
    pc_schedule_runs: () => [{ started: ago(0.5) }, { started: ago(10) }],
    pipeline_runs: () => ({ runs: [{ started: ago(4) }] }),
  } });
  await page().waitForFunction(() => state.settings && "show_preview" in state.settings && !state.starting);
});

after(async () => { if (app) await app.close(); });

const home = async () => page().evaluate(() => { homeCache.done = false; go("home"); });

test("Home keeps its plain header and shows the progress panel from profile_get", { skip }, async () => {
  await page().evaluate(() => { state.settings.show_preview.value = true; state.settings.motion_level.value = 2; });
  await home();
  await page().locator(".home-id").waitFor();
  const p = profile.progression;
  assert.ok(await page().getByText(`Level ${p.level}`, { exact: true }).isVisible());
  assert.ok(await page().locator(".home-id-level").getByText(`${p.xp.toLocaleString("en-US")} / ${p.next_level_xp.toLocaleString("en-US")} XP`).isVisible());
  assert.ok(await page().locator(".home-id-rank").getByText(p.title, { exact: true }).isVisible());
  assert.ok(await page().getByText("First Flame", { exact: true }).isVisible());
  // Your activity: runs counted from this PC's run logs, week against week; CU and optimized never guessed.
  assert.ok(await page().getByRole("heading", { name: /^Your activity/ }).isVisible());
  const runs = page().locator(".home-stat").filter({ hasText: "Runs on this PC" });
  await runs.locator("b").getByText("4", { exact: true }).waitFor();
  assert.match(await runs.innerText(), /\+100%/);
  for (const label of ["Fabric CU avoided (est.)", "Workloads optimized"]) {
    assert.match(await page().locator(".home-stat").filter({ hasText: label }).innerText(), /Not measured yet/);
  }
  // RPG art is for ranks, progression and achievements only (Chris, 2026-11-10): Home's header is plain text,
  // its actions stay in Start something, and no banner art is drawn.
  assert.ok(await page().locator(".home-head h1").getByText("Welcome to KratosForge").isVisible());
  assert.ok(await page().locator(".home-start").getByRole("button", { name: "New notebook" }).isVisible());
  assert.strictEqual(await page().locator(".home-hero, .page.home img[src*='experience/home']").count(), 0);
  // Never a god name, never a capacity figure.
  assert.ok(!(await page().locator(".home-aside").innerText()).includes("Data God"));
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "home-living.png") });
});

test("hiding the panel closes the gap, and the link brings it back", { skip }, async () => {
  await page().getByRole("button", { name: "Hide your progress on Home" }).click();
  await page().getByRole("button", { name: "Show your progress" }).waitFor();
  assert.strictEqual(await page().locator(".home-aside").count(), 0);
  await page().getByRole("button", { name: "Show your progress" }).click();
  await page().locator(".home-id").waitFor();
});

test("with progression off in Profile the panel is hidden", { skip }, async () => {
  profile.settings.progression = false;
  await home();
  await page().locator(".home-head").waitFor();
  await page().waitForFunction(() => !document.querySelector(".home-aside"));
  assert.strictEqual(await page().locator(".home-body.with-aside").count(), 0);
  profile.settings.progression = true;
});

test("Professional keeps the panel, and preview off shows the plain Home", { skip }, async () => {
  // The panel is progression, not decoration: it stays at every motion level.
  await page().evaluate(() => { state.settings.motion_level.value = 0; });
  await home();
  await page().locator(".home-id").waitFor();
  await page().evaluate(() => { state.settings.motion_level.value = 2; state.settings.show_preview.value = false; });
  await home();
  await page().locator(".home-head").waitFor();
  assert.strictEqual(await page().locator(".home-aside").count(), 0);
});
