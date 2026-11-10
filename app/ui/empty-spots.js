// Living Experience empty states (LX-07, Preview): one original spot illustration, one sentence and
// one primary action for Data items and Pipelines. Each spot is a small inline SVG
// drawn here (under 4 KB, no image files), coloured with currentColor and the accent token, so it
// follows high contrast. Other skins (Fabric, Databricks, Snowflake) keep the same text and action
// with their own neutral icon, so a theme is never mixed. Nothing here moves, and the title is set in the
// app's own type (Chris's art rule, 2026-11-10: no fantasy styling on working pages).

const EMPTY_SPOTS = {
  // Uncharted land: hills, a far peak and a flag not yet planted.
  land: {
    alt: "Uncharted hills with a flag waiting to be planted",
    paths: [
      ["path", { d: "M4 92 L36 52 L54 70 L74 38 L116 92 Z", fill: "var(--accent-soft)", stroke: "currentColor", "stroke-width": "2.5", "stroke-linejoin": "round" }],
      ["path", { d: "M74 38 L81 49 L75 47 L69 55 Z", fill: "currentColor", opacity: ".35" }],
      ["path", { d: "M26 92 Q50 80 70 92", fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-dasharray": "4 4" }],
      ["path", { d: "M94 92 V62", stroke: "currentColor", "stroke-width": "2.5" }],
      ["path", { d: "M94 62 L108 67 L94 72 Z", fill: "var(--brand-orange)" }],
    ],
  },
  // Tools laid out: a hammer, tongs and a plan on the bench.
  tools: {
    alt: "A hammer, tongs and a plan laid out on a bench",
    paths: [
      ["path", { d: "M8 92 H112", stroke: "currentColor", "stroke-width": "3", "stroke-linecap": "round" }],
      ["rect", { x: "58", y: "44", width: "50", height: "38", rx: "2", fill: "var(--accent-soft)", stroke: "currentColor", "stroke-width": "2" }],
      ["path", { d: "M64 54 H82 V64 H100 M64 74 H92", fill: "none", stroke: "var(--accent)", "stroke-width": "2.5" }],
      ["circle", { cx: "100", cy: "64", r: "3", fill: "var(--brand-orange)" }],
      ["path", { d: "M22 86 L40 46", stroke: "currentColor", "stroke-width": "4", "stroke-linecap": "round" }],
      ["path", { d: "M30 40 L52 50 L48 58 L26 48 Z", fill: "var(--accent)" }],
      ["path", { d: "M10 60 L34 84 M16 56 L38 80", stroke: "currentColor", "stroke-width": "2.5", "stroke-linecap": "round" }],
    ],
  },
};

function emptySpotSvg(name) {
  const spot = EMPTY_SPOTS[name];
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  for (const [k, v] of Object.entries({ class: "empty-spot", viewBox: "0 0 120 100", width: "120", height: "100", role: "img", "aria-label": spot.alt })) svg.setAttribute(k, v);
  for (const [tag, attrs] of spot.paths) {
    const el = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    svg.append(el);
  }
  return svg;
}

// The KratosForge skin with Preview on draws the spot; every other case draws the plain empty state
// with `fallbackIcon`, the same sentence and action.
// `plainTitle` is the fallback's own title, when the spot's title only makes sense beside its drawing.
function livingEmpty({ spot, fallbackIcon, title, plainTitle, text, action }) {
  const skin = state.settings?.ui_skin?.value || "kratosforge";
  if (!previewShown() || skin !== "kratosforge" || !EMPTY_SPOTS[spot]) return emptyState({ icon: fallbackIcon, title: plainTitle || title, text, action });
  return h("div", { class: "empty living-empty" }, emptySpotSvg(spot),
    h("span", { class: "title" }, title), h("span", { class: "text" }, text), action);
}
