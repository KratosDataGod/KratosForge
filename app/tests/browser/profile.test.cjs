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
from fabric_desktop import progress
lab = {"id": "LAB-02", "kind": "lab", "title": "Optimize a slow query", "status": "live", "version": 1, "exercises": [{"id": "store-revenue-2026"}]}
learn = progress.empty()
progress.add(learn, progress.from_lab({"exercise": "store-revenue-2026", "version": 1, "passed": True, "results": [
    {"kind": "result_matches", "passed": True}, {"kind": "faster_than", "passed": True,
     "measured": {"seconds": 0.12, "baseline_seconds": 0.5, "speedup": 4.17}}]}, lab))
progress.add(learn, progress.module_complete(learn, lab))
progress.add(learn, progress.from_fix("SQ002", "Performance", {"result": "match", "level": "complete", "rows": {"after": 4},
    "seconds": {"before": 1.84, "after": 0.21}, "target": "warehouse"}, {"before": "run-a", "after": "run-b"}, "WH_Gold"))
json.dump({"profile": s.profile_get(), "report": s.profile_report(), "earned": earned, "proved": proved,
           "learn": learn["evidence"]}, sys.stdout)
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
  assert.ok(await page.getByText("Level 2", { exact: true }).isVisible());
  assert.ok(await page.getByText("Apprentice", { exact: true }).first().isVisible());  // the rank is the title (PB-125)
  assert.ok(await page.getByText("Rank 1 of 13").isVisible());
  assert.ok(await page.getByText("First Flame", { exact: true }).isVisible());
  assert.ok(await page.getByText("They are not a skill rating.").isVisible());
  assert.strictEqual(await page.locator(".profile-bar").getAttribute("aria-valuenow"), "100");
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "profile-overview.png"), fullPage: true });
});

test("Journey shows the level, milestones and badges; Achievements gives the rules", { skip }, async () => {
  const page = app.page;
  await page.getByRole("tab", { name: "Journey" }).click();
  await page.getByText("Recent milestones").waitFor();
  assert.ok(await page.getByText("Reached level 2", { exact: true }).isVisible());
  assert.ok(await page.getByText("2 of 8 earned").isVisible());  // rules v2: five Learn achievements
  assert.strictEqual(await page.locator(".profile-shield-cell.earned").count(), 2);
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "profile-journey.png"), fullPage: true });
  await page.getByRole("tab", { name: "Achievements" }).click();
  await page.getByText("How to earn it: Five notebooks", { exact: false }).waitFor();
  assert.ok(await page.getByText("Not yet").first().isVisible());
  assert.ok(await page.getByText("How to earn it: Every check of one exercise in a published Learn lesson", { exact: false }).isVisible());
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "profile-achievements.png"), fullPage: true });
});

test("Ranks shows your rank, the Forge progression, the next rank's requirements and rewards from real data (PB-125)", { skip }, async () => {
  const page = app.page;
  await page.getByRole("tab", { name: "Ranks" }).click();
  await page.getByRole("heading", { name: "Your rank" }).waitFor();
  const r = data.profile.ranks;
  const pr = data.profile.progression;
  for (const h of ["Ranks", "Next rank", "Rank rewards", `Rank requirements: ${r.next.name}`]) assert.ok(await page.getByRole("heading", { name: h, exact: true }).isVisible(), h);
  assert.ok(await page.getByText(`${pr.xp} XP / ${pr.next_level_xp} XP`).isVisible(), "xp");
  // The Forge tier's five ranks plus the top rank, yours marked; the top is further on, so the gap says how many ranks lie between.
  assert.strictEqual(await page.locator(".rk-shield").count(), 6);
  assert.strictEqual(await page.locator('.rk-shield[aria-current="step"]').count(), 1);
  assert.match(await page.locator(".rk-shield.top").innerText(), /Legend of the Forge/);
  assert.ok(await page.getByText("+7 ranks").isVisible(), "gap");
  assert.ok(await page.locator(".rk-reqs").getByText(r.next.checks[0].text).isVisible(), "req");
  assert.strictEqual(await page.locator(".rk-reqs .rk-req").count(), r.next.checks.length);
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "profile-ranks.png"), fullPage: true });
  await page.getByRole("button", { name: "View full rank path" }).click();
  await page.getByRole("heading", { name: "The Pack" }).waitFor();
  for (const tier of ["The Forge", "The Pack", "The Myth"]) assert.ok(await page.getByRole("heading", { name: tier, exact: true }).isVisible());
  assert.strictEqual(await page.locator(".profile-rank").count(), 13);
  assert.strictEqual(await page.getByRole("button", { name: "Copy picture prompt" }).count(), 13);
  assert.doesNotMatch(await page.locator(".profile").innerText(), /\bgod\b|deity|divine/i);
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "profile-ranks-full.png"), fullPage: true });
  await page.getByRole("button", { name: "Hide full rank path" }).click();
  // Chris's approved art: plain while a rank is ahead, glowing once it is yours. Labels stay real text.
  assert.deepStrictEqual(await page.locator(".rk-shield.has-art img").evaluateAll((l) => l.map((i) => i.getAttribute("src"))),
    ["forge-initiate", "iron-forger", "steel-forger", "master-forger", "legend-of-the-forge"].map((id) => `art/ranks/${id}-inactive.webp`));
  const art = page.locator(".rk-shield.has-art").filter({ hasText: "Forge Initiate" });
  assert.strictEqual(await art.locator("img").getAttribute("src"), "art/ranks/forge-initiate-inactive.webp");
  assert.match(await art.innerText(), /Forge Initiate\s+Rank 2/);
  const saved = structuredClone(r);
  Object.assign(r, { number: 2, rank: r.ladder[1].name, next: r.ladder[2] });
  r.ladder[1].held = true;
  await page.evaluate(() => render());
  await page.locator('.rk-shield.has-art[aria-current="step"]').waitFor();
  assert.strictEqual(await art.locator("img").getAttribute("src"), "art/ranks/forge-initiate-active.webp");
  assert.strictEqual(await art.locator("img").evaluate((i) => i.naturalWidth > 0), true);
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "profile-ranks-forge-initiate.png"), fullPage: true });
  // Every rank held: the Myth tier shows, the top rank glows, and nothing is left to earn.
  Object.assign(r, { number: r.of, rank: r.ladder[r.of - 1].name, next: null });
  for (const x of r.ladder) x.held = true;
  await page.evaluate(() => render());
  await page.getByText("You hold every rank there is.").waitFor();
  assert.strictEqual(await page.locator('.rk-shield[aria-current="step"] img').getAttribute("src"), "art/ranks/legend-of-the-forge-active.webp");
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "profile-ranks-top.png"), fullPage: true });
  data.profile.ranks = saved;
});

test("a profile picture shows on the page and the account button, and the letter comes back without one (PB-125)", { skip }, async () => {
  const page = app.page;
  const png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";
  data.profile.avatar = png;
  await page.evaluate((url) => { state.user = "dev@contoso.com"; state.avatar = url; renderChrome(); profilePage.tab = "overview"; go("profile"); }, png);
  await page.locator("img.profile-avatar").waitFor();
  assert.strictEqual(await page.locator("#account img").count(), 1);
  data.profile.avatar = "javascript:alert(1)";
  await page.evaluate(() => { state.avatar = "javascript:alert(1)"; render(); renderChrome(); });
  await page.locator("div.profile-avatar").waitFor();
  assert.strictEqual(await page.locator("#account img").count(), 0);
  data.profile.avatar = null;
  await page.evaluate(() => { state.avatar = null; state.user = null; renderChrome(); });
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

test("a Learn exercise and a finished lab read as what was done, with the measured speed-up (LX-07)", { skip }, async () => {
  const text = await app.page.evaluate((rows) => rows.map((r) => profileRow({ ...r, xp: 0, excluded: false }).textContent), data.learn);
  assert.match(text[0], /Passed exercise 1 in LAB-02 · Optimize a slow query/);
  assert.match(text[0], /Every check passed on this PC: Same result, Faster\./);
  assert.match(text[0], /Measured 4\.17× faster than the starting query on this PC \(0\.12 s against 0\.5 s\)/);
  assert.match(text[0], /Learn LAB-02\/store-revenue-2026 · lab version 1/);
  assert.match(text[1], /Finished lab LAB-02 · Optimize a slow query/);
  assert.match(text[1], /Its exercise passed on this PC\./);
});

test("a fix checked with Compare reads as what was checked and measured, one run each (GD-06)", { skip }, async () => {
  const text = await app.page.evaluate((r) => profileRow({ ...r, xp: 0, excluded: false }).textContent, data.learn[2]);
  assert.match(text, /Fixed SQ002 on WH_Gold, and checked it/);
  assert.match(text, /Compare ran the query as written and as fixed: the same 4 rows, every row compared\. Measured, one run each: 1\.84 s as written, 0\.21 s fixed\./);
  assert.match(text, /Compare · runs run-a and run-b/);
  assert.match(text, /Skills: Engineering Quality, Performance & Optimization/);
  assert.doesNotMatch(text, /master|XP/i);
});
