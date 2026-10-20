// Forge animations (docs/forge-animations.md): small, optional effects on real state changes.
// Each effect is one aria-hidden element laid over an existing status element and animated with
// CSS transform and opacity only (styles.css, "Forge animations"). Nothing here waits, reads a
// result or changes what the app does; with the setting off, Windows asking for reduced motion or
// the window hidden, no effect element is made at all. Other features add a kind to MOTION and
// its CSS rather than animating on their own.
const MOTION = {
  spark: { ms: 400, gap: 300 },  // a cell succeeded
  fail: { ms: 450, gap: 300 },   // a cell failed
  arrive: { ms: 550 },           // the wolf reached the end of Run all
};
// Runs shorter than this never show the progress track, so quick runs don't flash it.
const WOLF_DELAY_MS = 600;
// The wolf's head from the Wolf Forge emblem (docs/brand/wolf-forge-emblem-2048.png), traced to
// one 24px path with the eye cut out.
const WOLF_PATH = "M1.70 8.48 3.15 5.33 5.33 0.24 5.70 3.64 7.76 6.79 10.91 9.45 14.55 11.64 15.52 13.58 16.73 15.52 "
  + "20.12 17.45 23.52 18.96 23.81 19.88 22.59 21.09 21.82 22.11 19.15 22.59 18.18 23.32 15.27 22.59 11.39 21.21 "
  + "8.97 21.45 6.18 23.81 1.70 23.81 0.61 20.00 1.94 16.73 0.24 13.82 1.70 11.64 0.61 9.70Z"
  + "M8.73 13.28 12.61 13.28 15.27 15.88 12.12 15.08Z";
const motionLast = {};

function reducedMotion() {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// Decorative motion is wanted: the setting is on (it is unless someone turned it off), Windows
// isn't asking for less motion, and the window can be seen.
function motionOn() {
  if (state.settings?.forge_animations?.value === false) return false;
  if (reducedMotion()) return false;
  return !document.hidden;
}

// The setting and Windows' preference, as data-motion on <html>, so CSS also hides any effect that
// is already playing when either changes. Called whenever settings load or change.
function applyMotion() {
  document.documentElement.dataset.motion = state.settings?.forge_animations?.value === false || reducedMotion() ? "off" : "on";
}

// Plays one effect over anchor. At most one effect per anchor at a time, and each kind at most
// once per its gap, so repeated events don't stack up. Returns the element, or null when nothing plays.
function fx(anchor, kind) {
  const m = MOTION[kind];
  if (!anchor || !m || !motionOn() || anchor.querySelector(":scope > .fx")) return null;
  const now = Date.now();
  if (m.gap && kind in motionLast && now - motionLast[kind] < m.gap) return null;
  motionLast[kind] = now;
  anchor.classList.add("fx-anchor");
  const el = h("span", { class: `fx fx-${kind}`, "aria-hidden": "true" });
  anchor.append(el);
  setTimeout(() => el.remove(), m.ms + 100);
  return el;
}

function wolfIcon() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "16");
  svg.setAttribute("height", "16");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", WOLF_PATH);
  path.setAttribute("fill", "currentColor");
  path.setAttribute("fill-rule", "evenodd");
  svg.append(path);
  return svg;
}

// A progress track for work counted in steps (Run all: cells done of cells to run). where() gives
// the element to show it in; it appears only if the work is still going after WOLF_DELAY_MS. The
// bar and its text are real progress and show either way; the wolf on it only when motionOn().
// The wolf's position is always the done fraction, never a guess.
function wolfTrack(where, label, unit) {
  let el = null;
  let done = 0;
  let total = 0;
  let over = false;
  const show = () => {
    if (over || !where()) return;
    const fill = h("span", { class: "wolf-fill" });
    const wolf = motionOn() ? h("span", { class: "wolf", "aria-hidden": "true" }, wolfIcon()) : null;
    const bar = h("span", { class: "wolf-bar", role: "progressbar", "aria-label": label, "aria-valuemin": "0" }, fill, wolf);
    el = h("span", { class: "wolf-track" }, h("span", { class: "wolf-text" }), bar);
    paint();
  };
  const paint = () => {
    if (!el) return;
    if (!el.isConnected) where()?.append(el);  // the notebook was drawn again meanwhile
    const pct = total ? Math.round((done / total) * 100) : 0;
    el.querySelector(".wolf-text").textContent = `${unit} ${Math.min(done + 1, total)} of ${total}`;
    const bar = el.querySelector(".wolf-bar");
    bar.setAttribute("aria-valuemax", String(total));
    bar.setAttribute("aria-valuenow", String(done));
    el.querySelector(".wolf-fill").style.width = `${pct}%`;
    const wolf = el.querySelector(".wolf");
    if (wolf) wolf.style.left = `${pct}%`;
  };
  const timer = setTimeout(show, WOLF_DELAY_MS);
  const stop = () => { over = true; clearTimeout(timer); el?.remove(); el = null; };
  return {
    set(d, t) { done = d; total = t; paint(); },
    // All steps done: the wolf glows once at the end and the track goes.
    arrive() {
      const wolf = el?.querySelector(".wolf");
      over = true;
      clearTimeout(timer);
      if (!wolf || !fx(wolf, "arrive")) { stop(); return; }
      wolf.classList.add("arriving");
      const gone = el;
      el = null;
      setTimeout(() => gone.remove(), MOTION.arrive.ms + 100);
    },
    // Failed, cancelled, stopped or left: the track goes at once.
    stop,
    get shown() { return !!el; },
  };
}

// Says text to screen readers through the page's polite status region, whatever the setting.
function announce(text) {
  const live = document.getElementById("announce");
  if (!live) return;
  live.textContent = "";
  setTimeout(() => { live.textContent = text; }, 50);  // a fresh change, so the same words are read again
}

if (typeof matchMedia === "function") matchMedia("(prefers-reduced-motion: reduce)").addEventListener?.("change", applyMotion);
