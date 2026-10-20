// KF-DX-001: the Profile page with one verified notebook (see harness.cjs for how to run it). The
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
json.dump({"profile": s.profile_get(), "report": s.profile_report(), "earned": earned}, sys.stdout)
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
});

test("Overview shows the verified accomplishment, level and achievement", { skip }, async () => {
  const page = app.page;
  await page.evaluate(() => { profilePage.tab = "overview"; go("profile"); });
  await page.getByText("Validated notebook sales_daily_load").waitFor();
  assert.ok(await page.getByText("Level 1 · Apprentice").isVisible());
  assert.ok(await page.getByText("First Flame", { exact: true }).isVisible());
  assert.ok(await page.getByText("They are not a skill rating.").isVisible());
  assert.strictEqual(await page.locator(".profile-bar").getAttribute("aria-valuenow"), "50");
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "profile-overview.png"), fullPage: true });
});

test("the report has the verified line and no game terms", { skip }, async () => {
  const page = app.page;
  await page.getByRole("tab", { name: "Professional report" }).click();
  await page.getByText("Validated notebook sales_daily_load: clean run finished Ready").waitFor();
  const text = await page.locator(".profile-report").innerText();
  assert.match(text, /Verified, E1/);
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
