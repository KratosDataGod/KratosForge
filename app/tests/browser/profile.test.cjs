// KF-DX-001/002: the Profile page with one validated and proven notebook (see harness.cjs for how to run it). The
// profile data comes from the real engine code (progress_service.py) run once in a temp folder,
// so the page is driven with exactly what the engine returns.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const BUILD = `
import json, sys, tempfile
from pathlib import Path
from fabric_desktop.service import Service
class Auth:
    def signed_in_user(self): return "dev@contoso.com"
s = Service(client_id="x", root=Path(tempfile.mkdtemp()) / "app")
s._auth = Auth()
s.profile_save(settings={"record": True}, identity={"display_name": "Chris", "role": "Data engineer", "technologies": "Python, SQL, Delta Lake"})
earned = s._progress_observe({"id": "validate-20261020-104212-a1b2c3", "name": "sales_daily_load", "verdict": "ready",
    "run": {"status": "ok"}, "finished_at": "2026-10-20T10:42:12+00:00",
    "code": {"revision": "3f9c1e0d2b7a" + "0" * 52}, "tables": {"reads": ["a", "b"], "writes": ["c"]}})
proved = s._progress_observe_proof({"id": "20261020-120000-abcdef", "name": "sales_daily_load", "state": "done",
    "finished_at": "2026-10-20T12:00:00+00:00", "receipt_id": "9" * 64, "code": {"revision": "3f9c1e0d2b7a" + "0" * 52},
    "tables": [{}, {}], "verdict": {"verdict": "same", "level": "exact"},
    "scorecard": {"time": {"pc": {"seconds": 12.5, "kind": "measured"}, "fabric": {"total_seconds": 95, "kind": "measured"}}}})
json.dump({"profile": s.profile_get(), "report": s.profile_report(), "earned": earned, "proved": proved}, sys.stdout)
`;

let app;
let data;

before(async () => {
  if (skip) return;
  const python = process.env.FABRIC_DESKTOP_PYTHON || (process.platform === "win32" ? "python" : "python3");
  const out = spawnSync(python, ["-c", BUILD], { cwd: path.join(__dirname, "..", "..", "..", "sidecar"), encoding: "utf8" });
  assert.strictEqual(out.status, 0, out.stderr);
  data = JSON.parse(out.stdout);
  app = await openApp({
    mocks: {
      profile_get: () => data.profile,
      profile_report: () => data.report,
      profile_save: ({ settings }) => Object.assign(data.profile.settings, settings || {}) && data.profile,
    },
  });
});

after(async () => { if (app) await app.close(); });

test("the engine awards First Flame for the first verified notebook", { skip }, () => {
  assert.deepStrictEqual(data.earned, [{ id: "first-flame", name: "First Flame", xp: 50, first: true }]);
  assert.deepStrictEqual(data.proved, [{ id: "proof-forged", name: "Proof Forged", xp: 150, first: false }]);
});

test("Overview shows the verified accomplishment, level and achievement", { skip }, async () => {
  const page = app.page;
  await page.evaluate(() => { profilePage.tab = "overview"; go("profile"); });
  await page.getByText("Validated notebook sales_daily_load").waitFor();
  assert.ok(await page.getByText("Proved notebook sales_daily_load").isVisible());
  assert.ok(await page.getByText("Measured end to end: this PC 12.5 s, Fabric 95 s.", { exact: false }).isVisible());
  assert.ok(await page.getByText("Level 2 · Apprentice").isVisible());
  assert.ok(await page.getByText("First Flame", { exact: true }).isVisible());
  assert.ok(await page.getByText("They are not a skill rating.").isVisible());
  assert.strictEqual(await page.locator(".profile-bar").getAttribute("aria-valuenow"), "100");
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "profile-overview.png"), fullPage: true });
});

test("Journey shows the level, milestones and badges; Achievements gives the rules", { skip }, async () => {
  const page = app.page;
  await page.getByRole("tab", { name: "Journey" }).click();
  await page.getByText("Recent milestones").waitFor();
  assert.ok(await page.getByText("Reached level 2, Apprentice").isVisible());
  assert.ok(await page.getByText("2 of 3 earned").isVisible());
  assert.strictEqual(await page.locator(".profile-shield-cell.earned").count(), 2);
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "profile-journey.png"), fullPage: true });
  await page.getByRole("tab", { name: "Achievements" }).click();
  await page.getByText("How to earn it: Five notebooks", { exact: false }).waitFor();
  assert.ok(await page.getByText("Not yet").isVisible());
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "profile-achievements.png"), fullPage: true });
});

test("the report has the verified line and no game terms", { skip }, async () => {
  const page = app.page;
  await page.getByRole("tab", { name: "Professional report" }).click();
  await page.getByText("Validated notebook sales_daily_load: clean run finished Ready").waitFor();
  const text = await page.locator(".profile-report").innerText();
  assert.match(text, /Verified, E1/);
  assert.match(text, /Proved notebook sales_daily_load gives the same results on a workstation and in Microsoft Fabric/);
  assert.match(text, /Run receipt 9{64} \(SHA-256\)/);
  assert.doesNotMatch(text, /XP|First Flame|Apprentice/);
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "profile-report.png"), fullPage: true });
});

test("Settings show the switches and the danger delete", { skip }, async () => {
  const page = app.page;
  await page.getByRole("tab", { name: "Settings" }).click();
  await page.getByText("Record my accomplishments").first().waitFor();
  assert.ok(await page.getByRole("button", { name: "Delete my profile data" }).isVisible());
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "profile-settings.png"), fullPage: true });
  assert.deepStrictEqual(app.errors, []);
});
