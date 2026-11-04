// KF-DX-009/011: Profile > Impact and Profile > Goals with a profile that has every kind of evidence
// (see harness.cjs for how to run it). The data comes from the real engine code (progress_service.py)
// run once in a temp folder, so the page is drawn from exactly what the engine returns.
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
from fabric_desktop import progress, roadmap
class Auth:
    def signed_in_user(self): return "dev@contoso.com"
s = Service(client_id="x", root=Path(tempfile.mkdtemp()) / "app")
s._auth = Auth()
s.profile_save(settings={"record": True}, identity={"display_name": "Chris", "role": "Data engineer"})
for i, name in enumerate(("sales_daily_load", "customers_merge")):
    s._progress_observe({"id": f"validate-2026102{i}-104212-a1b2c3", "name": name, "verdict": "ready", "run": {"status": "ok"},
        "finished_at": f"2026-10-2{i}T10:42:12+00:00", "code": {"revision": str(i) * 64}, "tables": {"reads": ["a"], "writes": ["c"]}})
s._progress_observe_proof({"id": "20261022-120000-abcdef", "name": "sales_daily_load", "state": "done",
    "finished_at": "2026-10-22T12:00:00+00:00", "receipt_id": "9" * 64, "code": {"revision": "0" * 64},
    "tables": [{}, {}], "verdict": {"verdict": "same", "level": "exact"},
    "scorecard": {"time": {"pc": {"seconds": 12.5, "kind": "measured"}, "fabric": {"total_seconds": 95, "kind": "measured"}}}})
path = s._profile_path()
p = progress.load(path)
lab = {"id": "LAB-02", "kind": "lab", "title": "Optimize a slow query", "status": "live", "version": 1, "exercises": [{"id": "store-revenue-2026"}]}
progress.add(p, progress.from_lab({"exercise": "store-revenue-2026", "version": 1, "passed": True, "results": [
    {"kind": "result_matches", "passed": True}, {"kind": "faster_than", "passed": True,
     "measured": {"seconds": 0.12, "baseline_seconds": 0.5, "speedup": 4.17}}]}, lab))
progress.add(p, progress.module_complete(p, lab))
progress.add(p, progress.from_fix("SQ002", "Performance", {"result": "match", "level": "complete", "rows": {"after": 4},
    "seconds": {"before": 1.84, "after": 0.21}, "target": "warehouse"}, {"before": "run-a", "after": "run-b"}, "WH_Gold"))
roadmap.add_goal(p, milestone="sql-tuning")
roadmap.add_goal(p, text="Present the Gold layer design to the data team")
progress.save(path, p)
json.dump({"profile": s.profile_get(), "report": s.profile_report()}, sys.stdout)
`;

let app;
let data;

before(async () => {
  if (skip) return;
  const python = process.env.FABRIC_DESKTOP_PYTHON || (process.platform === "win32" ? "python" : "python3");
  const out = spawnSync(python, ["-c", BUILD], { cwd: path.join(__dirname, "..", "..", "..", "sidecar"), encoding: "utf8" });
  assert.strictEqual(out.status, 0, out.stderr);
  data = JSON.parse(out.stdout);
  app = await openApp({ mocks: { profile_get: () => data.profile, profile_report: () => data.report } });
});

after(async () => { if (app) await app.close(); });

test("Impact shows each measured figure as measured, the verified outcomes, and capacity as not measured", { skip }, async () => {
  const page = app.page;
  await page.evaluate(() => { profilePage.tab = "impact"; go("profile"); });
  await page.getByText("Verified outcomes").waitFor();
  const text = await page.locator(".page.profile").innerText();
  assert.match(text, /3 measured · 2 faster after the change/);
  assert.match(text, /Fixed SQ002 on WH_Gold\n?.*Measured/);
  assert.match(text, /1\.84 s before · 0\.21 s after · 88\.6% less time/);
  assert.match(text, /0\.5 s before · 0\.12 s after · 4\.17× faster/);
  assert.match(text, /This PC 12\.5 s · Fabric 95 s, end to end in the same proof/);
  assert.match(text, /Not measured/);
  assert.match(text, /claims no capacity or cost saving/);
  assert.doesNotMatch(text, /\$|saved \d|XP/);
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "profile-impact.png"), fullPage: true });
});

test("Goals show my goals with their progress, a suggestion next to my work and all seven tracks", { skip }, async () => {
  const page = app.page;
  await page.getByRole("tab", { name: "Goals" }).click();
  await page.getByText("Development roadmap", { exact: false }).waitFor();
  const mine = page.locator(".card", { hasText: "My goals" });
  assert.match(await mine.innerText(), /Query tuning[\s\S]*In progress[\s\S]*Finish LM-18 and LAB-02[\s\S]*1 of 2/);
  assert.match(await mine.innerText(), /Present the Gold layer design to the data team[\s\S]*Entered by you/);
  assert.strictEqual(await page.locator(".card h2", { hasText: /^(Data Engineering|SQL & Warehouse|Semantic Modeling|Data Integration|Data Governance|Performance Optimization|Platform Engineering)/ }).count(), 7);
  const perf = page.locator(".card", { hasText: "Performance Optimization" }).last();
  assert.match(await perf.innerText(), /A measured speed-up[\s\S]*Met/);
  assert.ok(await page.getByRole("button", { name: "LAB-02 Optimize a slow query" }).first().isVisible());
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "profile-goals.png"), fullPage: true });
});

test("the report preview lists development goals with where each came from", { skip }, async () => {
  const page = app.page;
  await page.getByRole("tab", { name: "Professional report" }).click();
  await page.locator(".profile-report", { hasText: "Development goals" }).waitFor();
  const text = await page.locator(".profile-report").innerText();
  assert.match(text, /SQL & Warehouse: Query tuning\. In progress: Finish LM-18 and LAB-02 \(1 of 2\)\. \(Goal set by you\)/);
  assert.match(text, /Present the Gold layer design to the data team: in progress\. \(Entered by you\)/);
  assert.doesNotMatch(text, /XP|Apprentice|achievement/);
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "profile-report-goals.png"), fullPage: true });
  assert.deepStrictEqual(app.errors, []);
});
