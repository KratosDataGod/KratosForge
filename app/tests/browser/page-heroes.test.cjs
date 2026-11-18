// The tab headers (home-hero.js pageHero, Preview): Chris's approved artwork on Create, Workspaces,
// Platform, Data mesh, Optimize, Monitor, Academy, Source control, Settings and Profile, with the tab's name and
// its actions as real UI in the navy sky at left. Run it as harness.cjs says; SCREENSHOT_DIR saves a picture of each page at each width.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");
const { heroArt } = require("./hero-art.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const RATIO = 2172 / 724;
// Each page with header art: its view and its h1. Every picture in the page's pool (art/experience/heroes.js)
// is checked against the sky line its manifest entry gives.
const PAGES = [
  { view: "create", name: "create", title: "Create", actions: ["New notebook", "New lakehouse"] },
  { view: "workspaces", name: "workspace", title: "Workspaces" },
  { view: "platform", name: "platform", title: "Platform" },
  { view: "metadata", name: "datamesh", title: "Data mesh" },
  { view: "optimize", name: "optimize", title: "Optimize" },
  { view: "academy", name: "academy", title: "Academy", prep: () => { academyPage.open = null; } },
  { view: "source", name: "source", title: "Source control" },
  { view: "settings", name: "settings", title: "Settings" },
  { view: "profile", name: "profile", title: "Profile", prep: () => { profilePage.tab = "overview"; } },
  // With the Forge view off: its band carries #2226's own Monitor art, and the header steps aside for it.
  { view: "monitor", name: "monitor", title: "Monitor", prep: () => { forgeActivity.on = false; monitorPage.panel = "runs"; } },
];
const WIDTHS = [1920, 1440, 1330, 1251, 1200, 1181, 1101, 1041, 1000, 961, 850, 700];

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["app_settings", "save_app_settings", "profile_get"],
    mocks: { platform_whoami: () => ({ admin: true, can_claim: false }) } });
  await page().waitForFunction(() => state.settings && "show_preview" in state.settings && !state.starting);
});

after(async () => { if (app) await app.close(); });

const open = (view) => page().evaluate((v) => go(v), view);
// Saved in the real engine, since Settings reads them again when it opens.
const mode = (motion, preview) => page().evaluate(([m, pv]) => saveSettings({ motion_level: m, show_preview: pv }), [motion, preview]);

for (const p of PAGES) for (const art of heroArt().filter((e) => e.page === p.name)) {
  test(`${p.title} shows ${art.id} whole, with its name on the sky`, { skip }, async () => {
    await page().setViewportSize({ width: 1440, height: 900 });
    await mode(2, true);
    await page().evaluate(([pg, id]) => { heroArtPin[pg] = id; }, [p.name, art.id]);
    if (p.prep) await page().evaluate(p.prep);
    await open(p.view);
    const hero = `.${p.name}-hero`;
    await page().locator(hero).waitFor();
    assert.ok(await page().locator(`${hero} h1`).getByText(p.title).isVisible());
    assert.strictEqual(await page().locator("#view h1").count(), 1);
    assert.strictEqual(await page().locator(`${hero} .home-hero-kicker`).count(), 0, "no KratosForge kicker on a page's header");
    for (const name of p.actions || []) {
      assert.ok(await page().locator(`${hero} .home-hero-actions`).getByRole("button", { name }).isVisible(), name);
    }
    // Every control in the band is in its text column; none sits on the picture.
    assert.strictEqual(await page().locator(`${hero} button, ${hero} a, ${hero} input`).count(),
      await page().locator(`${hero} .home-hero-text button, ${hero} .home-hero-text a, ${hero} .home-hero-text input`).count());
    const img = page().locator(`${hero} .home-hero-img`);
    assert.strictEqual(await img.getAttribute("alt"), "");
    assert.strictEqual(await img.getAttribute("src"), `art/experience/${art.file}-1086.webp`);

    // At every width the picture shows: 3:1, whole inside the band at its full height, and the text
    // ends on the empty sky, so the characters are never covered or cut. Below 960 px the art hides.
    for (const width of WIDTHS) {
      await page().setViewportSize({ width, height: 900 });
      await page().waitForFunction((s) => document.querySelector(`${s} .home-hero-img`)?.complete, hero);
      const r = await page().evaluate(([s, sky]) => {
        const img = document.querySelector(`${s} .home-hero-img`);
        const band = document.querySelector(s).getBoundingClientRect();
        if (getComputedStyle(img).display === "none") return { shown: false };
        const ink = (e) => { const g = document.createRange(); g.selectNodeContents(e); return g.getBoundingClientRect().right; };
        const textRight = Math.max(...[...document.querySelectorAll(`${s} .home-hero-title`)].map(ink),
          ...[...document.querySelectorAll(`${s} .home-hero-actions button, ${s} .home-hero-actions a`)].map((e) => e.getBoundingClientRect().right));
        const a = img.getBoundingClientRect();
        return { shown: true, ratio: a.width / a.height, fills: Math.abs(a.height - band.height) < 1,
          inside: a.left >= band.left - 0.5 && a.right <= band.right + 0.5, sky: a.left + sky * a.width, textRight };
      }, [hero, art.sky]);
      if (r.shown) {
        assert.ok(Math.abs(r.ratio - RATIO) < 0.02, `${p.title} ratio at ${width}: ${r.ratio}`);
        assert.ok(r.fills && r.inside, `${p.title}: the whole picture fills the band's height at ${width}`);
        assert.ok(r.sky >= r.textRight, `${p.title} at ${width}: the sky ends at ${r.sky}, the text at ${r.textRight}`);
      } else {
        assert.ok(width <= 960, `${p.title}: the picture shows at ${width}`);
        // Without the art the band holds its title and buttons, no empty navy block below them.
        const gap = await page().evaluate((s) => {
          const band = document.querySelector(s).getBoundingClientRect();
          const text = document.querySelector(`${s} .home-hero-text`);
          const last = [...text.children].filter((c) => c.getBoundingClientRect().height > 0).pop().getBoundingClientRect();
          return band.bottom - last.bottom;
        }, hero);
        assert.ok(gap <= 32, `${p.title} at ${width}: ${gap}px of empty band under the title and buttons`);
      }
      if (SHOTS) await page().screenshot({ path: path.join(SHOTS, `${art.id}-${width}.png`) });
    }
    await page().setViewportSize({ width: 1440, height: 900 });
  });
}

test("Monitor shows one picture: no header above the Forge Activity band", { skip }, async () => {
  await mode(2, true);
  await page().evaluate(() => { forgeActivity.on = true; monitorPage.panel = "runs"; });
  await open("monitor");
  await page().locator("#view h1").getByText("Monitor", { exact: true }).waitFor();
  assert.strictEqual(await page().locator(".monitor-hero").count(), 0);
  if (await page().evaluate(() => forgeViewShown() && forgeViewOn())) assert.strictEqual(await page().locator(".forge-slot").count(), 1);
  await page().evaluate(() => { forgeActivity.on = null; });
});

test("Profile > Ranks shows no page header: the tab has its own banner", { skip }, async () => {
  await mode(2, true);
  await page().evaluate(() => { profilePage.tab = "ranks"; go("profile"); });
  await page().locator(".page.profile .subtabs, .page.profile [role=tablist]").first().waitFor();
  assert.strictEqual(await page().locator(".profile-hero").count(), 0);
  await page().evaluate(() => { profilePage.tab = "overview"; });
});

test("Professional and preview off keep each page's plain heading", { skip }, async () => {
  for (const [motion, preview] of [[0, true], [2, false]]) {
    await mode(motion, preview);
    for (const p of PAGES.filter((x) => preview || !["platform", "optimize", "academy"].includes(x.view))) {
      await open(p.view);
      await page().locator("#view h1").first().waitFor();
      assert.strictEqual(await page().locator(".home-hero").count(), 0, `${p.title} motion ${motion} preview ${preview}`);
      // Plain Profile's h1 is your name; every other page's is its own.
      if (p.view !== "profile") assert.ok((await page().locator("#view h1").first().innerText()).startsWith(p.title));
    }
  }
  await mode(2, true);
  assert.deepStrictEqual(app.errors, []);
});
