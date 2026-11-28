// app/ui/home-hero.js beyond the Home and page-hero tests: the art pick (no pool, a pin, nothing live yet),
// the hero's optional line and default welcome, the progress cards for a journey with checks met and not,
// none, every rank held and a rank with no title, View skill tree, the hide and show links, a store that
// refuses the write, and the panel's load from profile_get. profile_get is the real engine's; the panel test
// swaps in variants of that same reply (progression off, nothing) on the way back.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

// A profile_get reply cut to what the cards read, given to homeProgressCards directly.
const PROFILE = {
  progression: { level: 3, level_xp: 200, next_level_xp: 400, xp: 250, title: "", achievements: [
    { name: "First Flame", description: "Validated a notebook.", badge: "flame", at: "2026-11-01T10:00:00Z", xp: 50 },
    { name: "Proven", description: "Proved a notebook.", badge: "shield", at: "2026-11-20T10:00:00Z", xp: 75 }] },
  ranks: { rank: "Apprentice", next: { name: "Journeyman", checks: [
    { text: "Validate notebooks", met: true, have: 4, need: 3 },
    { text: "Prove a notebook", met: false, have: 0, need: 1 }] } },
  identity: { role: "Data engineer", technologies: "Spark" },
  avatar: null,
  impact: { outcomes: { notebooks_validated: 4, notebooks_proven: 0, fixes_checked: 1 }, faster: 0, capacity: { why: "Fabric reports no capacity use per run." } },
  settings: { progression: true },
};

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["app_settings", "save_app_settings", "profile_get"] });
  await page().waitForFunction(() => state.settings && "show_preview" in state.settings && !state.starting);
  await page().evaluate(() => {
    const real = window.__TAURI__.core.invoke;
    window.__TAURI__.core.invoke = async (cmd, args) => {
      const out = await real(cmd, args);
      return cmd === "rpc" && args.method === "profile_get" && window.__profileEdit ? window.__profileEdit(out) : out;
    };
    return saveSettings({ motion_level: 2, show_preview: true });
  });
});
after(async () => { await app?.close(); });

test("the art pick: none without a pool, a pinned picture, and the pool's first before anything is published", { skip }, async () => {
  const got = await page().evaluate(() => {
    const home = HERO_ART.filter((e) => e.page === "home");
    const before = heroArtPick("home", "2000-01-01")?.id;
    heroArtPin.home = home.at(-1).id;
    const pinned = heroArtPick("home", "2000-01-01")?.id;
    delete heroArtPin.home;
    return { none: heroArtPick("no-such-page"), before, first: home[0].id, pinned, last: home.at(-1).id };
  });
  assert.strictEqual(got.none, null);
  assert.strictEqual(got.before, got.first);
  assert.strictEqual(got.pinned, got.last);
  assert.deepStrictEqual(app.errors, []);
});

test("a hero carries its line under the heading, and Home's hero welcomes by default", { skip }, async () => {
  const got = await page().evaluate(() => {
    const hero = lxHero({ name: "home", label: "Test", title: "T", heading: h("h1", {}, "H"), line: "A line under it." });
    const home = homeHero();
    return { line: hero.querySelector(".home-hero-line")?.textContent, kicker: hero.querySelector(".home-hero-kicker")?.textContent,
      welcome: home?.querySelector("h1")?.textContent };
  });
  assert.strictEqual(got.line, "A line under it.");
  assert.strictEqual(got.kicker, "KratosForge");
  assert.strictEqual(got.welcome, "Welcome to KratosForge");
  assert.deepStrictEqual(app.errors, []);
});

const cards = (p) => page().evaluate((p) => {
  document.getElementById("kf-cards")?.remove();
  const box = h("div", { id: "kf-cards", style: "position:fixed;top:0;left:0;z-index:9999;background:#fff" }, ...homeProgressCards(p));
  document.body.append(box);
  const t = (sel) => box.querySelector(sel)?.textContent;
  return { rank: t(".home-id-rank"), role: t(".home-id-role"), level: t(".home-id-level"), foot: t(".home-id-foot"),
    journey: [...box.querySelectorAll(".home-journey li")].map((li) => [li.className, li.textContent]),
    empty: t(".home-journey p"), latest: t(".home-latest b"), stats: t(".home-stats") };
}, p);

test("the cards: a rank with no title, checks met and not, the newest achievement and the measured stats", { skip }, async () => {
  const got = await cards(PROFILE);
  assert.strictEqual(got.rank, "Apprentice");
  assert.strictEqual(got.role, "Data engineer · Spark");
  assert.strictEqual(got.level, "Level 3150 XP to level 4");
  assert.match(got.foot, /^Next milestone: Journeyman/);
  assert.deepStrictEqual(got.journey, [["met", "✓Validate notebooks 3 of 3 (done)"], ["", "○Prove a notebook (not yet)"]]);
  assert.strictEqual(got.latest, "Proven");
  assert.match(got.stats, /Notebooks validated4/);
  assert.deepStrictEqual(app.errors, []);
});

test("with every rank held and no checks, the journey says how to start and the rank is the title", { skip }, async () => {
  const got = await cards({ ...PROFILE, progression: { ...PROFILE.progression, title: "Forge Master" }, ranks: { rank: "Master" }, identity: {} });
  assert.strictEqual(got.rank, "Forge Master");
  assert.strictEqual(got.role, undefined);
  assert.match(got.foot, /^Every rank held/);
  assert.deepStrictEqual(got.journey, []);
  assert.strictEqual(got.empty, "Validate a notebook to start your journey.");
  const none = await cards({ ...PROFILE, ranks: undefined, progression: { ...PROFILE.progression, title: "" } });
  assert.strictEqual(none.rank, "");
  assert.deepStrictEqual(app.errors, []);
});

test("View skill tree opens Profile on its ranks tab", { skip }, async () => {
  await cards(PROFILE);
  await page().locator("#kf-cards").getByText("View skill tree").click();
  await page().waitForFunction(() => state.view === "profile");
  assert.strictEqual(await page().evaluate(() => profilePage.tab), "ranks");
  await page().evaluate(() => { document.getElementById("kf-cards")?.remove(); profilePage.tab = "overview"; go("home"); });
  assert.deepStrictEqual(app.errors, []);
});

test("hide stores the choice, the show link brings it back, and a store that refuses the write is not an error", { skip }, async () => {
  await cards(PROFILE);
  await page().locator("#kf-cards .home-id-hide").click();
  const got = await page().evaluate(() => {
    const hidden = homePanelHidden();
    const link = homeShowPanelLink();
    const label = link?.textContent;
    link.click();
    const shown = !homePanelHidden() && homeShowPanelLink() === null;
    const set = Storage.prototype.setItem;
    Storage.prototype.setItem = () => { throw new Error("full"); };
    try { setHomePanelHidden(true); } finally { Storage.prototype.setItem = set; }
    return { hidden, label, shown, after: homePanelHidden() };
  });
  assert.deepStrictEqual(got, { hidden: true, label: "Show your progress", shown: true, after: false });
  await page().evaluate(() => document.getElementById("kf-cards")?.remove());
  assert.deepStrictEqual(app.errors, []);
});

test("the panel fills from profile_get, and closes with progression off or nothing returned", { skip }, async () => {
  const run = (edit) => page().evaluate(async (edit) => {
    window.__profileEdit = { full: (p) => ({ ...p, settings: { ...p.settings, progression: true } }),
      off: (p) => ({ ...p, settings: { ...p.settings, progression: false } }), none: () => null }[edit];
    const wrap = h("div", { class: "with-aside", id: "kf-panel" });
    document.body.append(wrap);
    const panel = homeProgressPanel();
    wrap.append(panel.el);
    await panel.load();
    const out = { connected: panel.el.isConnected, sections: panel.el.querySelectorAll("section").length, aside: wrap.classList.contains("with-aside") };
    wrap.remove();
    return out;
  }, edit);
  assert.deepStrictEqual(await run("full"), { connected: true, sections: 4, aside: true });
  assert.deepStrictEqual(await run("off"), { connected: false, sections: 0, aside: false });
  assert.deepStrictEqual(await run("none"), { connected: false, sections: 0, aside: false });
  await page().evaluate(() => { window.__profileEdit = null; });
  // Taken off the page before the engine answers: it draws nothing.
  const late = await page().evaluate(async () => {
    const panel = homeProgressPanel();
    document.body.append(panel.el);
    const p = panel.load();
    panel.el.remove();
    await p;
    return panel.el.childElementCount;
  });
  assert.strictEqual(late, 0);
  assert.deepStrictEqual(app.errors, []);
});
