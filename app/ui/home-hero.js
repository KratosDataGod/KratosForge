// Living Experience on Home (LX-06, Preview): the hero band across the top and the progression panel
// beside the page. "Quiet while you work": nothing here moves, and nothing here holds a control the
// work needs. The hero is decoration only, so Professional (motion level 0) drops it. The panel reads
// only what Profile already returns (profile_get); it stores nothing new and estimates nothing.
// Loaded after profile.js and before app.js draws Home.

const HOME_PANEL_HIDDEN = "fd.home.progress.hidden";

function homePanelHidden() {
  try { return localStorage.getItem(HOME_PANEL_HIDDEN) === "1"; } catch { return false; }
}

function setHomePanelHidden(hidden) {
  try { localStorage.setItem(HOME_PANEL_HIDDEN, hidden ? "1" : "0"); } catch { /* kept until the next start */ }
}

// Whether Home shows the Living Experience parts at all: Preview features on, and not Professional.
function homeLivingOn() {
  return previewShown() && chosenMotionLevel() > 0;
}

// Chris's approved title artwork (docs/design/assets.md): Kratos and the wolf at dawn, characters on the
// right and a text-safe navy sky on the left. Static: nothing in it moves. The half-size file serves
// ordinary windows and the full one high-density screens; object-position keeps the characters in view.
function homeHeroArt() {
  return h("img", { class: "home-hero-img", alt: "", "aria-hidden": "true", decoding: "async",
    src: "art/experience/home/home-hero-1086.webp",
    srcset: "art/experience/home/home-hero-1086.webp 1086w, art/experience/home/home-hero.webp 2172w",
    sizes: "(max-width: 720px) 0px, calc(100vw - 72px)" });
}

// The hero: full width under the top bar. The welcome and the primary actions are real UI in the navy
// sky at left (Chris, 2026-11-07); the characters stay at right and nothing is drawn over them.
function homeHero({ welcome, actions = [] } = {}) {
  if (!homeLivingOn()) return null;
  return h("section", { class: "home-hero", "aria-label": "KratosForge" },
    homeHeroArt(),
    h("div", { class: "home-hero-text" },
      h("span", { class: "home-hero-kicker" }, "KratosForge"),
      h("span", { class: "home-hero-title" }, "Build. Test. Optimize."),
      h("h1", { class: "home-hero-welcome" }, welcome || "Welcome to KratosForge"),
      h("div", { class: "row wrap home-hero-actions" }, actions)));
}

// The side column: the rank card, Your journey, Recent achievement and Forge stats. The first three read
// profile_get; Forge stats count this PC's own run logs (the ones Monitor reads). Returns { el, load } so
// Home can draw it at once and fill it when the engine answers. With progression off in Profile, or the
// panel hidden, it shows nothing and the page closes the gap.
function homeProgressPanel() {
  const el = h("aside", { class: "home-aside", "aria-label": "Your progress" });
  const close = () => { el.parentElement?.classList.remove("with-aside"); el.remove(); };
  const load = async () => {
    if (!homeLivingOn() || homePanelHidden()) { close(); return; }
    const p = await rpc("profile_get", {}).catch(() => null);
    if (!el.isConnected) return;
    if (!p || !p.settings?.progression || !p.progression) { close(); return; }
    const week = h("section", { class: "card pad home-stats" });
    el.replaceChildren(...homeProgressCards(p), week);
    homeWeekStats(week);
  };
  return { el, load };
}

// The run logs kept on this PC, as Monitor reads them: metadata runs on this PC, scheduled and Home runs,
// and pipeline runs. Each list says whether it was cut at its limit, so a count is never passed off as whole.
const HOME_RUN_LIMIT = 500;
async function homeLocalRuns() {
  const [meta, scheduled, pipelines] = await Promise.all([
    rpc("meta_runs_all", { limit: HOME_RUN_LIMIT }).catch(() => null),
    rpc("pc_schedule_runs", { limit: HOME_RUN_LIMIT }).catch(() => null),
    rpc("pipeline_runs", { limit: HOME_RUN_LIMIT }).catch(() => null),
  ]);
  const lists = [
    (meta?.runs || []).filter((r) => (r.compute || "local") !== "fabric").map((r) => r.started_at),
    (scheduled || []).map((r) => r.started),
    (pipelines?.runs || []).map((r) => r.started),
  ];
  const cut = (meta?.runs || []).length >= HOME_RUN_LIMIT || (scheduled || []).length >= HOME_RUN_LIMIT
    || (pipelines?.runs || []).length >= HOME_RUN_LIMIT;
  return { at: lists.flat().map((a) => readDate(a)).filter(Boolean), cut, read: !!(meta || scheduled || pipelines) };
}

// Runs in the last 7 days and the 7 before, from homeLocalRuns. The change shows only when both weeks are
// whole and the week before had runs; otherwise there is nothing honest to compare.
function homeWeekCounts(runs, now = Date.now()) {
  const day = 86400000;
  const inWeek = (back) => runs.at.filter((d) => d.getTime() > now - (back + 7) * day && d.getTime() <= now - back * day).length;
  const thisWeek = inWeek(0);
  const lastWeek = inWeek(7);
  const change = !runs.cut && lastWeek > 0 ? Math.round(((thisWeek - lastWeek) / lastWeek) * 100) : null;
  return { thisWeek, lastWeek, change, cut: runs.cut };
}

async function homeWeekStats(card) {
  const runs = await homeLocalRuns();
  if (!card.isConnected) return;
  const c = homeWeekCounts(runs);
  const delta = (pct) => (pct === null ? h("span", { class: "home-stat-delta none" }, "")
    : h("span", { class: `home-stat-delta ${pct >= 0 ? "up" : "down"}`, title: `Last 7 days against the 7 before: ${c.thisWeek} against ${c.lastWeek}` },
      h("span", { "aria-hidden": "true" }, pct >= 0 ? "↑" : "↓"), ` ${pct >= 0 ? "+" : "−"}${Math.abs(pct)}%`));
  const row = (label, value, change, why) => h("div", { class: "home-stat", title: why || null },
    h("span", {}, label), h("b", {}, value), change);
  const notYet = (label, why) => h("div", { class: "home-stat muted-text", title: why },
    h("span", {}, label), h("span", { class: "home-stat-none" }, "Not measured yet"));
  card.replaceChildren(
    h("h2", { class: "home-stats-head" }, homeBarsIcon(), "Forge stats ", h("span", { class: "muted-text small" }, "(last 7 days)")),
    runs.read
      ? row("Runs on this PC", c.cut ? `${c.thisWeek}+` : String(c.thisWeek), delta(c.change),
        "Metadata runs, scheduled and Home runs, and pipeline runs on this PC, from their run logs.")
      : notYet("Runs on this PC", "The run logs on this PC couldn't be read."),
    notYet("Fabric CU avoided (est.)", "Comes from Optimize's savings ledger once it records an estimate. Nothing is guessed here."),
    notYet("Workloads optimized", "Comes from Optimize once a recommendation is carried out and measured."));
}

function homeBarsIcon() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 16 16"); svg.setAttribute("width", "16"); svg.setAttribute("height", "16");
  svg.setAttribute("aria-hidden", "true"); svg.setAttribute("class", "home-stats-icon");
  for (const [x, y] of [[1, 9], [6, 5], [11, 1]]) {
    const r = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    r.setAttribute("x", x); r.setAttribute("y", y); r.setAttribute("width", "4"); r.setAttribute("height", String(15 - y)); r.setAttribute("rx", "1");
    svg.append(r);
  }
  return svg;
}

// Stand-in for the journey's mountain until Chris supplies the original art: a few flat shapes, no detail.
function homeMountain() {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 120 90"); svg.setAttribute("class", "home-journey-art"); svg.setAttribute("aria-hidden", "true");
  const shape = (tag, attrs) => { const e = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); svg.append(e); };
  shape("path", { d: "M0 90 L38 40 L58 62 L82 22 L120 90 Z", class: "far" });
  shape("path", { d: "M70 90 L82 22 L94 44 L102 36 L120 66 L120 90 Z", class: "near" });
  shape("path", { d: "M77 32 L82 22 L88 33 L84 30 L80 34 Z", class: "snow" });
  shape("path", { d: "M82 22 L82 4", class: "pole" });
  shape("path", { d: "M82 4 L98 8 L82 13 Z", class: "flag" });
  return svg;
}

// The rank's shield: Chris's approved rank art where Profile > Ranks has it (RANK_ART), otherwise the
// person's own picture, otherwise the KratosForge emblem.
function homeRankShield(p) {
  const r = p.ranks || {};
  const id = r.ladder?.[(r.number || 0) - 1]?.id;
  const art = typeof RANK_ART === "object" && id && RANK_ART[id] ? RANK_ART[id](true) : null;
  if (art) { art.classList.add("home-id-art"); return h("span", { class: "home-id-portrait art" }, art); }
  return h("span", { class: "home-id-portrait" }, profilePicture(p.avatar, "home-id-img")
    || h("img", { class: "home-id-img", src: "wolf-forge-emblem.png", alt: "" }));
}

function homeProgressCards(p) {
  const pr = p.progression;
  const r = p.ranks || {};
  const span = Math.max(1, pr.next_level_xp - pr.level_xp);
  const into = Math.max(0, Math.min(span, pr.xp - pr.level_xp));
  const num = (n) => Number(n || 0).toLocaleString("en-US");
  const openProfile = (tab) => () => { profilePage.tab = tab; go("profile"); };
  const hideNow = () => { setHomePanelHidden(true); homeCache.done = false; render(); };
  const hide = btn("", "dismiss", hideNow, "tool link small home-id-hide",
    { "aria-label": "Hide your progress on Home", title: "Hide your progress on Home. Show it again from the link at the top of Home." });
  const ranksBtn = btn("", "chevronRight", openProfile("ranks"), "tool link home-id-open",
    { "aria-label": "Open your ranks in Profile", title: "Open your ranks in Profile" });

  const identity = h("section", { class: "home-id", "aria-label": "Your rank" },
    homeRankShield(p),
    h("div", { class: "home-id-body" },
      h("div", { class: "home-id-head" },
        h("span", { class: "home-id-rank" }, pr.title || r.rank || ""), ranksBtn, hide),
      h("div", { class: "home-id-level" },
        h("span", { class: "home-id-lv" }, `Level ${pr.level}`),
        h("span", { title: `${num(pr.next_level_xp - pr.xp)} XP to level ${pr.level + 1}` }, `${num(pr.xp)} / ${num(pr.next_level_xp)} XP`)),
      h("div", { class: "home-id-bar", role: "progressbar", "aria-label": `XP toward level ${pr.level + 1}`,
        "aria-valuemin": "0", "aria-valuemax": String(span), "aria-valuenow": String(into) },
        h("span", { style: `width:${Math.round((100 * into) / span)}%` })),
      h("div", { class: "home-id-foot" }, r.next ? `Next milestone: ${r.next.name}` : "Every rank held")));

  // Your journey: the next rank's checks, done first, the first one still open marked as the one to do now.
  const checks = r.next?.checks || [];
  const now = checks.findIndex((c) => !c.met);
  const journey = h("section", { class: "card pad home-journey" }, homeMountain(), h("h2", {}, "Your journey"),
    checks.length ? h("ul", {}, checks.map((c, i) => h("li", { class: c.met ? "met" : i === now ? "now" : "" },
      h("span", { class: "home-journey-mark", "aria-hidden": "true" }),
      h("span", {}, c.text, c.need > 1 ? h("span", { class: "muted-text small" }, ` ${Math.min(c.have, c.need)} of ${c.need}`) : null),
      h("span", { class: "sr-only" }, c.met ? " (done)" : i === now ? " (next)" : " (not yet)"))))
      : h("p", { class: "muted-text small" }, "Validate a notebook to start your journey."));

  // Recent achievement: the newest earned, with the evidence it rests on.
  const latest = [...(pr.achievements || [])].sort((a, b) => String(b.at || "").localeCompare(String(a.at || "")))[0];
  const achievement = h("section", { class: "card pad home-latest" },
    h("h2", {}, "Recent achievement"),
    latest ? h("div", { class: "home-latest-row" },
      h("div", { class: "profile-shield" }, profileGlyph(latest.badge, true)),
      h("div", { class: "home-latest-text" },
        h("b", { class: "home-latest-name" }, latest.name),
        h("span", { class: "small" }, latest.description),
        h("span", { class: "home-latest-xp" }, `+${num(latest.xp)} XP`),
        h("span", { class: "muted-text small" }, `Earned ${dateText(latest.at)} from verified work`)))
      : h("p", { class: "small" }, "None yet. Your first validated notebook earns First Flame."));

  return [identity, journey, achievement];
}

// When the panel is hidden, one quiet link at the top of Home brings it back.
function homeShowPanelLink() {
  if (!homeLivingOn() || !homePanelHidden()) return null;
  return btn("Show your progress", null, () => { setHomePanelHidden(false); homeCache.done = false; render(); }, "tool link small");
}
