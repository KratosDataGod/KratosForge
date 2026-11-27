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
      profile_mastery: (params) => { asked.push(params); return data.mastery; },
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

test("the tree zooms, pans and fits from the toolbar, the keyboard, Ctrl + wheel and a drag", { skip }, async () => {
  const page = app.page;
  await page.evaluate(() => { profilePage.trees.view = null; });
  await openTrees();
  const bar = page.getByRole("toolbar", { name: "Zoom" });
  const pct = bar.locator(".tree-zoom-pct");
  const scale = () => page.evaluate(() => new DOMMatrix(document.querySelector(".tree-world").style.transform).a);
  const shift = () => page.evaluate(() => { const m = new DOMMatrix(document.querySelector(".tree-world").style.transform); return [m.e, m.f]; });
  // Opens at Fit: the whole tree, Fit disabled, no overview box.
  assert.strictEqual(await pct.textContent(), "Fit");
  assert.ok(await bar.getByRole("button", { name: "Fit" }).isDisabled());
  assert.ok(!(await page.locator(".tree-zoom-overview").isVisible()));
  // + steps 25%; the live region says the zoom; the overview box shows while zoomed in.
  await bar.getByRole("button", { name: "Zoom in" }).click();
  assert.strictEqual(await pct.textContent(), "125%");
  await page.waitForFunction(() => document.getElementById("announce").textContent === "Zoom 125%");
  assert.strictEqual(await scale(), 1.25);
  assert.ok(await page.locator(".tree-zoom-overview").isVisible());
  assert.ok(await bar.getByRole("button", { name: "Fit" }).isEnabled());
  for (let i = 0; i < 5; i++) await bar.getByRole("button", { name: "Zoom in" }).click({ force: true });
  assert.strictEqual(await scale(), 2, "200% at most");
  assert.ok(await bar.getByRole("button", { name: "Zoom in" }).isDisabled());
  await bar.getByRole("button", { name: "Fit" }).click();
  assert.strictEqual(await scale(), 1);
  assert.deepStrictEqual(await shift(), [0, 0]);
  // Keys while a talent has focus: + zooms around it (it stays put), - zooms out to 50% at least, 0 fits.
  const node = page.locator(".tree-node").nth(4);
  await node.focus();
  const settled = () => page.waitForFunction(() => !document.querySelector(".tree-world").getAnimations().length);
  await settled();
  const before = await node.boundingBox();
  await page.keyboard.press("+");
  await page.keyboard.press("+");
  assert.strictEqual(await scale(), 1.5);
  await settled();
  const after = await node.boundingBox();
  assert.ok(Math.abs(after.x + after.width / 2 - (before.x + before.width / 2)) < 2, "the focused talent stays put");
  for (let i = 0; i < 6; i++) await page.keyboard.press("-");
  assert.strictEqual(await scale(), 0.5, "50% at least");
  await page.keyboard.press("0");
  assert.strictEqual(await pct.textContent(), "Fit");
  await page.waitForFunction(() => document.getElementById("announce").textContent === "Fit to view");
  // Ctrl + wheel zooms around the pointer; a plain wheel is left to the page.
  let box = await page.locator(".tree-view").boundingBox();
  await page.mouse.move(box.x + box.width / 3, box.y + box.height / 3);
  await page.keyboard.down("Control");
  await page.mouse.wheel(0, -200);
  await page.keyboard.up("Control");
  await page.waitForFunction(() => new DOMMatrix(document.querySelector(".tree-world").style.transform).a > 1);
  const s = await scale();
  await page.mouse.wheel(0, 100);
  assert.strictEqual(await scale(), s, "a plain wheel doesn't zoom");
  // Dragging the background pans (the plain wheel scrolled the page, so the frame has moved).
  await page.locator(".tree-view").scrollIntoViewIfNeeded();
  box = await page.locator(".tree-view").boundingBox();
  const [x0, y0] = await shift();
  await page.mouse.move(box.x + 20, box.y + box.height - 30);
  await page.mouse.down();
  await page.mouse.move(box.x + 80, box.y + box.height - 70, { steps: 4 });
  await page.mouse.up();
  const [x1, y1] = await shift();
  assert.ok(Math.abs(x1 - x0 - 60) < 1 && Math.abs(y1 - y0 + 40) < 1, `panned by ${x1 - x0}, ${y1 - y0}`);
  // With reduced motion the view jumps: no easing runs.
  await page.evaluate(() => { document.documentElement.dataset.motion = "off"; });
  await page.locator(".tree-node").first().focus();
  await page.keyboard.press("+");
  assert.deepStrictEqual(await page.evaluate(() => [document.documentElement.dataset.motion,
    parseFloat(getComputedStyle(document.querySelector(".tree-world")).transitionDuration) < 0.001]), ["off", true]);
  if (SHOTS) await page.locator(".tree-body").screenshot({ path: path.join(SHOTS, "profile-tree-zoomed.png") });
  await page.evaluate(() => { document.documentElement.dataset.motion = "on"; });
  // Another specialization starts at Fit again; the detail panel never zooms.
  await page.getByRole("tab", { name: /Delta Smith/ }).click();
  assert.strictEqual(await page.locator(".tree-zoom-pct").textContent(), "Fit");
  assert.strictEqual(await page.evaluate(() => getComputedStyle(document.querySelector(".tree-detail")).transform), "none");
  if (axeSource) assert.deepStrictEqual(await axeFindings(page, ".tree-body"), []);
});
