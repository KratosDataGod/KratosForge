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
  app = await openApp({ engine: ["app_settings", "app_settings_save"], mocks: { profile_get: () => profile, list_notebooks: () => [] } });
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
  assert.ok(await page().getByText(`${p.next_level_xp - p.xp} XP to level ${p.level + 1}`).isVisible());
  assert.ok(await page().locator(".home-id-rank").getByText(p.title, { exact: true }).isVisible());
  assert.ok(await page().getByText("First Flame", { exact: true }).isVisible());
  assert.ok(await page().getByText("Not measured").isVisible());
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
  // At any width the picture shows, it starts right of the text column, so no text or button sits on the art.
  for (const width of [1440, 1100, 1000, 850]) {
    await page().setViewportSize({ width, height: 940 });
    const clear = await page().evaluate(() => {
      const img = document.querySelector(".home-hero-img");
      if (getComputedStyle(img).display === "none") return { shown: false, ok: true };
      const textRight = Math.max(...[...document.querySelectorAll(".home-hero-text *")].map((e) => e.getBoundingClientRect().right));
      return { shown: true, ok: img.getBoundingClientRect().left >= textRight, left: img.getBoundingClientRect().left, textRight };
    });
    assert.ok(clear.ok, `at ${width} the art starts at ${clear.left}, the text ends at ${clear.textRight}`);
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
