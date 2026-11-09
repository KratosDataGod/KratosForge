// LX-06: the Living Experience hero and progression panel on Home (home-hero.js), driven with what the
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

test("Home shows the hero and the progression panel from profile_get", { skip }, async () => {
  await page().evaluate(() => { state.settings.show_preview.value = true; state.settings.motion_level.value = 2; });
  await home();
  await page().locator(".home-hero").waitFor();
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
  // The welcome and the primary actions are real UI in the hero's text side; none sits on the art.
  assert.ok(await page().locator(".home-hero h1").getByText("Welcome to KratosForge").isVisible());
  assert.ok(await page().locator(".home-hero-actions").getByRole("button", { name: "New notebook" }).isVisible());
  assert.strictEqual(await page().locator(".home-hero button, .home-hero a, .home-hero input").count(),
    await page().locator(".home-hero-text button, .home-hero-text a, .home-hero-text input").count());
  assert.strictEqual(await page().locator(".home-hero-img").getAttribute("alt"), "");
  // Never a god name, never a capacity figure.
  assert.ok(!(await page().locator(".home-aside").innerText()).includes("Data God"));
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "home-living.png") });
  // Chris's art is never cropped or stretched: the drawn picture keeps 3:1 and sits whole inside the hero.
  for (const width of [1440, 1920]) {
    await page().setViewportSize({ width, height: 940 });
    await page().waitForFunction(() => document.querySelector(".home-hero-img")?.complete);
    const box = await page().evaluate(() => {
      const img = document.querySelector(".home-hero-img").getBoundingClientRect();
      const hero = document.querySelector(".home-hero").getBoundingClientRect();
      return { ratio: img.width / img.height, inside: img.left >= hero.left - 0.5 && img.right <= hero.right + 0.5 && img.top >= hero.top - 0.5 && img.bottom <= hero.bottom + 0.5 };
    });
    assert.ok(Math.abs(box.ratio - 2172 / 724) < 0.02, `ratio at ${width}: ${box.ratio}`);
    assert.ok(box.inside, `the picture sits whole inside the hero at ${width}`);
    if (SHOTS && width === 1920) await page().screenshot({ path: path.join(SHOTS, "home-living-1920.png") });
  }
  // At any width the picture shows, the text ends before the art's 27% line, where the cape starts: text may sit on the empty sky only.
  for (const width of [1440, 1330, 1251, 1200, 1181, 1180, 1101, 1100, 1041, 1000, 961, 850]) {
    await page().setViewportSize({ width, height: 940 });
    const clear = await page().evaluate(() => {
      const img = document.querySelector(".home-hero-img");
      if (getComputedStyle(img).display === "none") return { shown: false, ok: true };
      // The ink, not the boxes: block headings and the action row span the whole column.
      const ink = (e) => { const r = document.createRange(); r.selectNodeContents(e); return r.getBoundingClientRect().right; };
      const textRight = Math.max(...[...document.querySelectorAll(".home-hero-kicker, .home-hero-title, .home-hero-welcome")].map(ink),
        ...[...document.querySelectorAll(".home-hero-actions > *")].map((e) => e.getBoundingClientRect().right));
      const art = img.getBoundingClientRect();
      const hero = document.querySelector(".home-hero").getBoundingClientRect();
      const cape = art.left + 0.27 * art.width;
      return { shown: true, ok: cape >= textRight && Math.abs(art.height - hero.height) < 1 && Math.abs(art.width / art.height - 2172 / 724) < 0.02, left: cape, textRight };
    });
    assert.ok(clear.ok, `at ${width} the cape starts at ${clear.left}, the text ends at ${clear.textRight}; the art fills the hero height`);
  }
  await page().setViewportSize({ width: 1440, height: 940 });
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
  await page().locator(".home-hero").waitFor();
  await page().waitForFunction(() => !document.querySelector(".home-aside"));
  assert.strictEqual(await page().locator(".home-body.with-aside").count(), 0);
  profile.settings.progression = true;
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
