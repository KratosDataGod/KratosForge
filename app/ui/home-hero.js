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

// Stand-in art: an original drawn forge scene with the KratosForge emblem. The painted Kratos and wolf
// hero (art-brief.md, 2400x800) replaces the scene once it is logged in docs/design/assets.md.
function homeHeroArt() {
  const svg = (tag, attrs, ...kids) => {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    el.append(...kids);
    return el;
  };
  const scene = svg("svg", { class: "home-hero-scene", viewBox: "0 0 640 210", preserveAspectRatio: "xMidYMax slice", "aria-hidden": "true" },
    svg("path", { d: "M0 210 V120 L70 70 L120 104 L200 40 L270 96 L330 62 L420 118 L500 58 L560 92 L640 70 V210 Z", fill: "#c9d6ea" }),
    svg("path", { d: "M200 40 L216 62 L204 58 L192 74 Z M500 58 L512 76 L502 72 Z", fill: "#ffffff" }),
    svg("path", { d: "M0 210 V156 L90 122 L160 150 L250 118 L340 154 L430 126 L520 150 L640 124 V210 Z", fill: "#244a70" }),
    svg("path", { d: "M0 210 V182 L120 166 L260 186 L400 168 L520 184 L640 170 V210 Z", fill: "#122a4f" }),
    // The anvil the emblem stands on, with a glow from the forge.
    svg("ellipse", { cx: "470", cy: "178", rx: "120", ry: "22", fill: "#f8991c", opacity: ".35" }),
    svg("path", { d: "M410 150 H530 L556 160 H520 V170 L540 190 H400 L420 170 V160 H410 Z", fill: "#0b1420" }),
    // Embers, drawn still.
    ...[[380, 120, 3], [404, 96, 2], [540, 110, 3], [560, 84, 2], [440, 70, 2], [512, 60, 3]].map(([cx, cy, r]) =>
      svg("circle", { cx: String(cx), cy: String(cy), r: String(r), fill: "#ffb347" })));
  return h("div", { class: "home-hero-art", "aria-hidden": "true" }, scene,
    h("img", { class: "home-hero-emblem", src: "wolf-forge-emblem.png", width: 112, height: 112, alt: "" }));
}

// The hero: 180 to 220 px, text on a light text-safe field at left, art at right. No controls.
function homeHero() {
  if (!homeLivingOn()) return null;
  return h("section", { class: "home-hero", "aria-label": "KratosForge" },
    homeHeroArt(),
    h("div", { class: "home-hero-text" },
      h("span", { class: "home-hero-kicker" }, "KratosForge"),
      h("span", { class: "home-hero-title" }, "Build. Test. Optimize."),
      h("p", {}, "A local-first data development environment for Microsoft Fabric and beyond.")));
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
