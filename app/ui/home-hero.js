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
