// LX-06: Home under the Living Experience (home-hero.js), driven with what the
// real engine's profile_get returns for one validated notebook (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { playwright, openApp } = require("./harness.cjs");

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
  app = await openApp({ engine: ["app_settings", "app_settings_save"], mocks: { profile_get: () => profile, list_notebooks: () => [] } });
  await page().waitForFunction(() => state.settings && "show_preview" in state.settings && !state.starting);
});

after(async () => { if (app) await app.close(); });

const home = async () => page().evaluate(() => { homeCache.done = false; go("home"); });

// RPG art is for ranks, progression and achievements only (Chris, 2026-11-09): Home has no header art, so
// even with Preview on at the Heroic level it is the flat page, with Continue and New notebook in Start something.
// The progression panel gets its own switch in the LX Home work; until then it shows only beside header art.
test("Home has no header art: Preview at Heroic shows the plain Home", { skip }, async () => {
  await page().evaluate(() => { state.settings.show_preview.value = true; state.settings.motion_level.value = 2; });
  await home();
  await page().locator(".home-head h1").waitFor();
  assert.strictEqual(await page().locator(".home-hero").count(), 0);
  assert.ok(await page().locator(".home-start").getByRole("button", { name: "New notebook" }).isVisible());
  assert.strictEqual(await page().locator(".home-start .home-continue").count(), 1);
  assert.deepStrictEqual(app.errors, []);
});

test("Professional and preview off show the plain Home", { skip }, async () => {
  await page().evaluate(() => { state.settings.motion_level.value = 0; });
  await home();
  await page().locator(".home-head").waitFor();
  assert.strictEqual(await page().locator(".home-hero, .home-aside").count(), 0);
  await page().evaluate(() => { state.settings.motion_level.value = 2; state.settings.show_preview.value = false; });
  await home();
  await page().locator(".home-head").waitFor();
  assert.strictEqual(await page().locator(".home-hero, .home-aside").count(), 0);
});
