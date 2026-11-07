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

// Chris's approved header art (art/experience/heroes.js, logged in docs/design/assets.md): each page has
// its own pool of 2172 x 724 pictures, characters on the right and a text-safe navy sky on the left, and
// shows only its own. The pick is stable for a day: one picture per page all day, a new one tomorrow, so
// it never changes while you work. A pool of one always shows that one. Static: nothing in it moves.
const heroArtPin = {};  // page -> id, for tests and review screenshots

function heroArtDay() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// FNV-1a: a small, even spread of page + day over the pool.
function heroArtHash(text) {
  let x = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) x = Math.imul(x ^ text.charCodeAt(i), 0x01000193) >>> 0;
  return x;
}

// The picture a page shows today: published and not yet retired, else the pool's first; none without a pool.
function heroArtPick(page, day = heroArtDay()) {
  const pool = (typeof HERO_ART === "undefined" ? [] : HERO_ART).filter((e) => e.page === page);
  if (!pool.length) return null;
  const pinned = pool.find((e) => e.id === heroArtPin[page]);
  if (pinned) return pinned;
  const live = pool.filter((e) => (!e.published || e.published <= day) && (!e.retire || day < e.retire));
  if (!live.length) return pool[0];
  return live[heroArtHash(`${page}:${day}`) % live.length];
}

// The half-size file serves ordinary windows and the full one high-density screens; the CSS keeps the
// whole picture in view. Decoration beside the page's real h1, so it has no name of its own.
function lxHeroArt(art) {
  const base = `art/experience/${art.file}`;
  return h("img", { class: "home-hero-img", alt: art.alt || "", "aria-hidden": "true", decoding: "async", "data-art": art.id,
    src: `${base}-1086.webp`, srcset: `${base}-1086.webp 1086w, ${base}.webp 2172w`,
    sizes: "(max-width: 720px) 0px, calc(100vw - 72px)" });
}

// The hero band under the top bar, shared by Home and every page with header art: the page's words and
// primary actions are real UI in the navy sky at left (Chris, 2026-11-07); the characters stay at right
// and nothing is drawn over them. `heading` is the page's own h1; `line` is an optional sentence under it.
// Null when the page has no art, so the page keeps its plain heading.
function lxHero({ name, label, kicker = "KratosForge", title, heading, line, actions = [] }) {
  const art = heroArtPick(name);
  if (!art) return null;
  return h("section", { class: `home-hero ${name}-hero`, "aria-label": label },
    lxHeroArt(art),
    h("div", { class: "home-hero-text" },
      kicker ? h("span", { class: "home-hero-kicker" }, kicker) : null,
      title ? h("span", { class: "home-hero-title" }, title) : null,
      heading,
      line ? h("p", { class: "home-hero-line" }, line) : null,
      h("div", { class: "row wrap home-hero-actions" }, actions)));
}

// Home's hero: the welcome is the page's h1 under the KratosForge title.
function homeHero({ welcome, actions = [] } = {}) {
  if (!homeLivingOn()) return null;
  return lxHero({ name: "home", label: "KratosForge", title: "Build. Test. Optimize.",
    heading: h("h1", { class: "home-hero-welcome" }, welcome || "Welcome to KratosForge"), actions });
}

// A tab's header (Chris's art, 2026-11-07): Create (Kratos and the wolf over the blueprint), Workspaces
// (the wolf on the rock) and Platform (Kratos and the wolf beside the island network). The tab's name is
// its h1 in the title face, with the page's own actions under it. Off (Preview off, Professional) the
// page keeps its plain heading, so callers fall back when this returns null.
function pageHero(name, title, { actions = [], tag = null } = {}) {
  if (!homeLivingOn()) return null;
  // A long name (Source control) steps down a size, so it ends on the sky like the short ones.
  const cls = title.trim().length > 10 ? "home-hero-title long" : "home-hero-title";
  // No KratosForge kicker: the page's own name is the whole title (UX, #2246).
  return lxHero({ name, label: title.trim(), kicker: null, heading: h("h1", { class: cls }, title, tag), actions });
}

// The side column: the identity card, Your journey, Latest achievement and Forge stats, all from
// profile_get. Returns { el, load } so Home can draw it at once and fill it when the engine answers.
// With progression off in Profile, or the panel hidden, it shows nothing and the page closes the gap.
function homeProgressPanel() {
  const el = h("aside", { class: "home-aside", "aria-label": "Your progress" });
  const close = () => { el.parentElement?.classList.remove("with-aside"); el.remove(); };
  const load = async () => {
    if (!homeLivingOn() || homePanelHidden()) { close(); return; }
    const p = await rpc("profile_get", {}).catch(() => null);
    if (!el.isConnected) return;
    if (!p || !p.settings?.progression || !p.progression) { close(); return; }
    el.replaceChildren(...homeProgressCards(p));
  };
  return { el, load };
}

function homeProgressCards(p) {
  const pr = p.progression;
  const r = p.ranks || {};
  const who = p.identity || {};
  const span = Math.max(1, pr.next_level_xp - pr.level_xp);
  const into = Math.max(0, Math.min(span, pr.xp - pr.level_xp));
  const toNext = Math.max(0, pr.next_level_xp - pr.xp);
  const openProfile = (tab) => () => { profilePage.tab = tab; go("profile"); };
  const hideNow = () => { setHomePanelHidden(true); homeCache.done = false; render(); };
  const hide = btn("", "dismiss", hideNow, "tool link small home-id-hide",
    { "aria-label": "Hide your progress on Home", title: "Hide your progress on Home. Show it again from the link at the top of Home." });

  const identity = h("section", { class: "home-id", "aria-label": "Your developer profile" },
    h("div", { class: "home-id-head" },
      h("span", { class: "home-id-portrait" }, profilePicture(p.avatar, "home-id-img")
        || h("img", { class: "home-id-img", src: "wolf-forge-emblem.png", alt: "" })),
      h("div", { class: "home-id-names" },
        h("span", { class: "home-id-rank" }, pr.title || r.rank || ""),
        who.role ? h("span", { class: "home-id-role" }, [who.role, who.technologies].filter(Boolean).join(" · ")) : null),
      hide),
    h("div", { class: "home-id-level" },
      h("span", { class: "home-id-pill" }, `Level ${pr.level}`),
      h("span", {}, `${toNext} XP to level ${pr.level + 1}`)),
    h("div", { class: "home-id-bar", role: "progressbar", "aria-label": `XP toward level ${pr.level + 1}`,
      "aria-valuemin": "0", "aria-valuemax": String(span), "aria-valuenow": String(into) },
      h("span", { style: `width:${Math.round((100 * into) / span)}%` })),
    h("div", { class: "home-id-foot" },
      h("span", {}, r.next ? ["Next milestone: ", h("b", {}, r.next.name)] : "Every rank held"),
      h("a", { href: "#", onclick: (e) => { e.preventDefault(); openProfile("ranks")(); } }, "View skill tree")));

  // Your journey: the next rank's checks, met first. With nothing to show, one short sentence.
  const checks = r.next?.checks || [];
  const journey = h("section", { class: "card pad home-journey" }, h("h2", {}, "Your journey"),
    checks.length ? h("ul", {}, checks.map((c) => h("li", { class: c.met ? "met" : "" },
      h("span", { class: "home-journey-mark", "aria-hidden": "true" }, c.met ? "✓" : "○"),
      h("span", {}, c.text, c.need > 1 ? h("span", { class: "muted-text small" }, ` ${Math.min(c.have, c.need)} of ${c.need}`) : null),
      h("span", { class: "sr-only" }, c.met ? " (done)" : " (not yet)"))))
      : h("p", { class: "muted-text small" }, "Validate a notebook to start your journey."));

  // Latest achievement: the newest earned, with the evidence it rests on.
  const latest = [...(pr.achievements || [])].sort((a, b) => String(b.at || "").localeCompare(String(a.at || "")))[0];
  const achievement = h("section", { class: "card pad home-latest" },
    latest ? [
      h("div", { class: "profile-shield" }, profileGlyph(latest.badge, true)),
      h("div", { class: "home-latest-text" },
        h("span", { class: "muted-text small" }, "Latest achievement"),
        h("b", {}, latest.name),
        h("span", { class: "small" }, latest.description),
        h("span", { class: "muted-text small" }, `Earned ${dateText(latest.at)} from verified work`),
        h("span", { class: "home-latest-xp" }, `+${latest.xp} XP`))]
      : [h("div", { class: "home-latest-text" }, h("span", { class: "muted-text small" }, "Latest achievement"),
        h("span", { class: "small" }, "None yet. Your first validated notebook earns First Flame."))]);

  // Forge stats: measured outcomes only. Capacity is never estimated.
  const o = p.impact?.outcomes || {};
  const stat = (label, value) => h("div", { class: "home-stat" }, h("span", {}, label), h("b", {}, String(value)));
  const anyStat = (o.notebooks_validated || 0) + (o.notebooks_proven || 0) + (o.fixes_checked || 0) + (p.impact?.faster || 0) > 0;
  const stats = h("section", { class: "card pad home-stats" },
    h("h2", {}, "Forge stats ", h("span", { class: "muted-text small" }, "measured")),
    anyStat ? [
      stat("Notebooks validated", o.notebooks_validated || 0),
      stat("Notebooks proven", o.notebooks_proven || 0),
      stat("Fixes checked", o.fixes_checked || 0),
      stat("Measured faster", p.impact?.faster || 0)]
      : h("p", { class: "muted-text small" }, "Nothing measured yet. Validate or prove a notebook and its results show here."),
    h("div", { class: "home-stat muted-text", title: p.impact?.capacity?.why || "" },
      h("span", {}, "Fabric capacity saved"), h("span", {}, "Not measured")));

  return [identity, journey, achievement, stats];
}

// When the panel is hidden, one quiet link at the top of Home brings it back.
function homeShowPanelLink() {
  if (!homeLivingOn() || !homePanelHidden()) return null;
  return btn("Show your progress", null, () => { setHomePanelHidden(false); homeCache.done = false; render(); }, "tool link small");
}
