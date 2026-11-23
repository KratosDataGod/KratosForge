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
