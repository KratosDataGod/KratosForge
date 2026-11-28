// PB-134: Profile > Skill trees, driven with what the real engine (progress_service.profile_mastery) returns
// for a profile with a few lessons and one Academy exercise (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const BUILD = `
import json, sys, tempfile
from pathlib import Path
from fabric_desktop.service import Service
from fabric_desktop import progress
from fabric_desktop.academy import registry, exercises
class Auth:
    def signed_in_user(self): return "dev@contoso.com"
s = Service(client_id="x", root=Path(tempfile.mkdtemp()) / "app")
s._auth = Auth()
s.profile_save(settings={"record": True}, identity={"display_name": "Chris"})
p = progress.load(s._profile_path())
day = {"v": "2026-10-01T10:00:00+00:00"}
progress._now = lambda: day["v"]
def lesson(mid, ex="ex"):
    m = {"id": mid, "kind": "module", "title": mid, "status": "live", "version": 1, "exercises": [{"id": ex}]}
    progress.add(p, progress.from_lab({"exercise": ex, "version": 1, "passed": True, "results": [{"kind": "result_matches", "passed": True}]}, m))
cert = registry.certification("dp-700")
def academy(eid):
    e = {**exercises.get("dp-700", eid), "status": "live"}
    progress.add(p, progress.from_academy({"passed": True, "results": [{"kind": "result_matches", "passed": True}]}, e, cert))
for mid in ("LM-07", "LM-11", "LM-29", "LM-06"):
    lesson(mid)
day["v"] = "2026-10-12T10:00:00+00:00"
for eid in ("E22",):
    academy(eid)
lesson("LM-12"); lesson("LM-25")
progress.save(s._profile_path(), p)
json.dump({"profile": s.profile_get(), "mastery": s.profile_mastery()}, sys.stdout)
`;

let app;
let data;
let failMastery = false;
const asked = [];

before(async () => {
  if (skip) return;
  const python = process.env.FABRIC_DESKTOP_PYTHON || (process.platform === "win32" ? "python" : "python3");
  const out = spawnSync(python, ["-c", BUILD], { cwd: path.join(__dirname, "..", "..", "..", "sidecar"), encoding: "utf8" });
  assert.strictEqual(out.status, 0, out.stderr);
  data = JSON.parse(out.stdout);
  app = await openApp({
    mocks: {
      profile_get: () => data.profile,
      profile_mastery: (params) => {
        asked.push(params);
        if (failMastery) throw new Error("The engine stopped");
        return data.mastery;
      },
    },
  });
});

after(async () => { if (app) await app.close(); });

async function openTrees() {
  await app.page.evaluate(() => { profilePage.tab = "trees"; go("profile"); });
  await app.page.locator(".tree-canvas").waitFor();
}

test("Skill trees shows the class, its specializations and a tree of buttons from the engine", { skip }, async () => {
  const page = app.page;
  await openTrees();
  assert.deepStrictEqual(asked[0], {});
  assert.ok(await page.getByRole("tab", { name: "Skill trees" }).isVisible());
  assert.strictEqual(await page.locator(".tree-title").textContent(), "Lakehouse Warden");
  assert.strictEqual(await page.getByRole("tablist", { name: "Specializations" }).getByRole("tab").count(), 3);
  assert.strictEqual(await page.locator(".tree-canvas .tree-node").count(), 10);
  assert.match(await page.locator(".profile-col").last().textContent(), /They aren't professional certifications/);
  if (axeSource) assert.deepStrictEqual(await axeFindings(page, ".profile"), []);
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "profile-skill-trees.png"), fullPage: true });
});

test("a mastered talent lights the path to the next one, and picking a talent shows its evidence", { skip }, async () => {
  const page = app.page;
  await openTrees();
  await page.getByRole("tab", { name: /Delta Smith/ }).click();
  const merge = page.getByRole("button", { name: "MERGE upserts, Adept, mastered" });
  await merge.click();
  assert.strictEqual(await merge.getAttribute("aria-pressed"), "true");
  const detail = page.getByRole("complementary", { name: "Selected talent" });
  assert.match(await detail.textContent(), /MERGE upserts[\s\S]*To reach Expert[\s\S]*Evidence/);
  // Lines leave a mastered talent only toward one that has opened.
  // Every lit line leaves a mastered talent toward an open one, and a line out of an unmastered talent stays dark.
  const lines = await page.evaluate(() => [...document.querySelectorAll(".tree-line[data-from]")].map((l) => ({
    lit: l.classList.contains("lit"),
    from: document.querySelector(`.tree-node[data-talent="${l.dataset.from}"]`).classList[1],
    to: document.querySelector(`.tree-node[data-talent="${l.dataset.to}"]`).classList[1] })));
  assert.ok(lines.some((l) => l.lit));
  for (const l of lines) assert.strictEqual(l.lit, l.from === "mastered" && l.to !== "locked", JSON.stringify(l));
  assert.ok(lines.some((l) => !l.lit && l.from !== "mastered" && l.from !== "locked" && l.to !== "locked"), "a path out of an unmastered, open talent");
  // Arrow keys walk the tree: down from MERGE upserts is the talent it opens, up goes back to its root.
  await merge.focus();
  await page.keyboard.press("ArrowDown");
  assert.match(await page.evaluate(() => document.activeElement.getAttribute("aria-label")), /^Incremental loads with a watermark, /);
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("ArrowUp");
  assert.match(await page.evaluate(() => document.activeElement.getAttribute("aria-label")), /^Files, Parquet and the Delta log, /);
  // A locked talent says so, and the panel names what opens it.
  const locked = page.getByRole("button", { name: "Reading a query plan, locked" });
  assert.match(await locked.textContent(), /Locked/);
  await locked.click();
  assert.match(await detail.textContent(), /Builds on Partitioning and file pruning\.[\s\S]*Opens when Partitioning and file pruning reaches Apprentice\./);
});

test("another class opens from the picker", { skip }, async () => {
  const page = app.page;
  await openTrees();
  await page.getByLabel("Developer class").selectOption("pipeline-forgemaster");
  await page.waitForFunction(() => document.querySelector(".tree-title")?.textContent);
  assert.deepStrictEqual(asked.at(-1), { class_id: "pipeline-forgemaster" });
});

test("class achievements sit above the tree: earned with its day, progress as talents met, or why not yet", { skip }, async () => {
  const page = app.page;
  const real = data.mastery.class.achievements;
  assert.strictEqual(real.length, 5);
  // The engine's own answer for this profile, then one earned and one no one can earn yet.
  data.mastery.class.achievements = real.map((a, i) => i === 0 ? { ...a, earned: true, earned_on: "2026-11-12", met: a.of }
    : i === 1 ? { ...a, available: true, unavailable_reason: "" }   // one being worked toward, whatever content is published today
    : i === 4 ? { ...a, earned: false, available: false, unavailable_reason: "No lesson, exercise or check verifies this skill yet." } : a);
  await page.evaluate(() => { profilePage.trees.data = null; });
  await openTrees();
  const row = page.getByRole("region", { name: "Class achievements" });
  assert.match(await row.locator(".tree-achs-head").textContent(), /Class achievements\s*1 of 5 earned/);
  const cards = row.locator(".tree-ach");
  assert.strictEqual(await cards.count(), 5);
  assert.match(await cards.nth(0).getAttribute("class"), /earned/);
  assert.strictEqual(await cards.nth(0).locator(".tree-ach-status").textContent(), "Earned Nov 12");
  assert.strictEqual(await cards.nth(0).locator("h4").textContent(), real[0].name);
  assert.strictEqual(await cards.nth(0).locator("p").first().textContent(), real[0].criteria);
  const mid = real[1];
  const bar = cards.nth(1).getByRole("progressbar", { name: `${mid.name} progress` });
  assert.strictEqual(await bar.getAttribute("aria-valuenow"), String(mid.met));
  assert.strictEqual(await bar.getAttribute("aria-valuemax"), String(mid.of));
  assert.strictEqual(await cards.nth(1).locator(".tree-ach-status").textContent(), `${mid.met} of ${mid.of}`);
  assert.match(await cards.nth(4).getAttribute("class"), /unavailable/);
  assert.strictEqual(await cards.nth(4).locator(".tree-ach-status").textContent(),
    "Not yet available. No lesson, exercise or check verifies this skill yet.");
  assert.strictEqual(await cards.nth(4).getByRole("progressbar").count(), 0);
  // An earned achievement the dated evidence can't place says only "Earned".
  data.mastery.class.achievements[0] = { ...data.mastery.class.achievements[0], earned_on: null };
  await page.evaluate(() => { profilePage.trees.data = null; });
  await openTrees();
  assert.strictEqual(await page.locator(".tree-ach").nth(0).locator(".tree-ach-status").textContent(), "Earned");
  if (axeSource) {
    const found = await axeFindings(page, ".tree-achs");
    assert.deepStrictEqual(found, []);
  }
  if (SHOTS) await row.screenshot({ path: path.join(SHOTS, "profile-class-achievements.png") });
  data.mastery.class.achievements = real;
});

test("class achievements: 3 per line in a narrow window, no glow with reduced motion or Professional, none shown when a class has none", { skip }, async () => {
  const page = app.page;
  const real = data.mastery.class.achievements;
  data.mastery.class.achievements = real.map((a, i) => (i === 0 ? { ...a, earned: true, earned_on: "2026-11-12", met: a.of } : a));
  await page.evaluate(() => { profilePage.trees.data = null; });
  await openTrees();
  const columns = () => page.evaluate(() => getComputedStyle(document.querySelector(".tree-ach-row")).gridTemplateColumns.split(" ").length);
  const glow = () => page.evaluate(() => getComputedStyle(document.querySelector(".tree-ach.earned .tree-ach-badge")).filter);
  const noGlow = () => page.waitForFunction(() => document.documentElement.dataset.motion === "off"
    && getComputedStyle(document.querySelector(".tree-ach.earned .tree-ach-badge")).filter === "none", null, { timeout: 5000 });
  const size = page.viewportSize();
  assert.strictEqual(await columns(), 5);
  await page.setViewportSize({ width: 860, height: 900 });   // below 900 px (mockup state 3)
  assert.strictEqual(await columns(), 3);
  if (SHOTS) await page.locator(".tree-achs").screenshot({ path: path.join(SHOTS, "profile-class-achievements-narrow.png") });
  await page.setViewportSize(size);
  // The ember glow is branded motion: on at Heroic, off with Windows' reduced motion and in Professional (mockup state 4).
  const motion = (level) => page.evaluate((lv) => {
    state.settings = { ...(state.settings || {}), motion_level: { value: lv }, motion_follow_system: { value: true } };
    applyMotion();
  }, level);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await motion(2);
  assert.match(await glow(), /drop-shadow/);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await motion(2);
  await noGlow();
  assert.match(await page.locator(".tree-ach.earned").getAttribute("class"), /earned/);   // still earned: the ring stays
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await motion(0);
  await noGlow();
  // A class with no achievements shows no row at all.
  data.mastery.class.achievements = [];
  await page.evaluate(() => { profilePage.trees.data = null; });
  await openTrees();
  assert.strictEqual(await page.getByRole("region", { name: "Class achievements" }).count(), 0);
  data.mastery.class.achievements = real;
});

test("when the engine can't answer, Skill trees says so and Try again opens it", { skip }, async () => {
  const page = app.page;
  failMastery = true;
  await page.evaluate(() => { profilePage.trees.data = null; profilePage.trees.error = false; profilePage.tab = "trees"; go("profile"); });
  const msg = page.getByText("Couldn't open your skill trees.");
  await msg.waitFor();
  assert.strictEqual(await page.locator(".tree-achs, .tree-canvas").count(), 0);
  failMastery = false;
  await page.getByRole("button", { name: "Try again" }).click();
  await page.locator(".tree-canvas").waitFor();
  assert.ok(await page.getByRole("region", { name: "Class achievements" }).isVisible());
});
