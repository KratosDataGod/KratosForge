// Motion & Effects (docs/forge-animations.md): small, optional effects on real state changes.
// Each effect is one aria-hidden element laid over an existing status element and animated with
// CSS transform and opacity only (styles.css, "Forge animations"). Nothing here waits, reads a
// result or changes what the app does. Features name what happened with forgeEvent(name, anchor);
// FORGE_EVENTS says which effect, if any, that is. Other features add an event and a kind here and
// its CSS, rather than animating on their own.
//
// Visual intensity (Settings > Appearance > Motion & effects, engine setting motion_level):
//   0 Off         no motion at all
//   1 Minimal     functional transitions only: page changes, progress easing. No branded effects.
//   2 Refined     subtle branded effects (the default)
//   3 Expressive  the same moments with more character: extra sparks, halftone and offset rings
// Windows' reduced-motion setting means level 0 unless the person unticked "Respect Windows'
// reduced-motion setting". Each kind also belongs to a family the person can switch off on its own;
// achievements are off unless turned on.
const MOTION_LEVELS = ["Off", "Minimal", "Refined", "Expressive"];
const MOTION = {
  spark: { ms: 400, gap: 300, level: 2, family: "sparks" },      // a cell succeeded
  fail: { ms: 450, gap: 300, level: 2, family: "sparks" },       // something failed: an ember goes out
  arrive: { ms: 550, level: 2, family: "wolf" },                 // the wolf reached the end of its track
  pulse: { ms: 350, gap: 1000, level: 2, family: "ambient" },    // work started: one ring off the button
  settle: { ms: 450, level: 2, family: "ambient" },              // a pipeline activity finished
  strike: { ms: 650, gap: 1500, level: 2, family: "sparks", build: anvilParts },  // a deployment succeeded
  wolfrun: { ms: 900, gap: 5000, level: 2, family: "wolf", build: wolfRunParts },  // long work finished
  achieve: { ms: 900, gap: 3000, level: 2, family: "achievements" },  // for the developer profile (#327)
  cool: { ms: 500, gap: 300, level: 2, family: "sparks" },       // a check passed: the badge glows orange and cools
};
// What happened -> the effect it plays. Only finished, real state changes are listed; nothing here
// is told about work before it is confirmed.
const FORGE_EVENTS = {
  "notebook.run.started": "pulse",
  "notebook.cell.succeeded": "spark",
  "notebook.cell.failed": "fail",
  "notebook.run.finished": "arrive",
  "notebook.long.succeeded": "wolfrun",
  "pipeline.started": "pulse",
  "pipeline.activity.succeeded": "settle",
  "pipeline.activity.failed": "fail",
  "pipeline.succeeded": "strike",
  "pipeline.failed": "fail",
  "deploy.succeeded": "strike",
  "deploy.failed": "fail",
  "fabric.run.started": "pulse",
  "fabric.run.succeeded": "arrive",
  "fabric.run.failed": "fail",
  "mesh.run.started": "pulse",
  "mesh.run.succeeded": "strike",
  "mesh.run.failed": "fail",
  "git.op.succeeded": "spark",
  "git.op.failed": "fail",
  "notebook.saved": "spark",
  "sql.succeeded": "spark",
  "sql.failed": "fail",
  "achievement.unlocked": "achieve",
  "validate.passed": "cool",
  "validate.failed": "fail",
  "prove.matched": "cool",
  "prove.failed": "fail",
  "publish.verified": "cool",
  "publish.failed": "fail",
};
// Runs shorter than this never show the progress track, so quick runs don't flash it.
const WOLF_DELAY_MS = 600;
// A single cell that ran at least this long gets the Wolf Run instead of a spark when it succeeds.
const LONG_RUN_SECONDS = 10;
// At most this many effects play at once across the app; more are skipped, never queued.
const MAX_LIVE_FX = 3;
// Under sustained load (long main-thread tasks adding up to LOAD_BUDGET_MS within LOAD_WINDOW_MS)
// effects drop one level for LOAD_COOLDOWN_MS, so decoration gives way to the work.
const LOAD_WINDOW_MS = 5000;
const LOAD_BUDGET_MS = 500;
const LOAD_COOLDOWN_MS = 30000;
// The wolf's head from the Wolf Forge emblem (docs/brand/wolf-forge-emblem-2048.png), traced to
// one 24px path with the eye cut out.
const WOLF_PATH = "M1.70 8.48 3.15 5.33 5.33 0.24 5.70 3.64 7.76 6.79 10.91 9.45 14.55 11.64 15.52 13.58 16.73 15.52 "
  + "20.12 17.45 23.52 18.96 23.81 19.88 22.59 21.09 21.82 22.11 19.15 22.59 18.18 23.32 15.27 22.59 11.39 21.21 "
  + "8.97 21.45 6.18 23.81 1.70 23.81 0.61 20.00 1.94 16.73 0.24 13.82 1.70 11.64 0.61 9.70Z"
  + "M8.73 13.28 12.61 13.28 15.27 15.88 12.12 15.08Z";
// An anvil on its block, drawn for this (24px box): horn left, face, waist and foot.
const ANVIL_PATH = "M1 7h15.5c2.5 0 5 .8 6.5 2.5h-6.2c-.6 1.6-2 2.7-3.6 3.1V15h2.3l1.5 3H5l1.5-3h2.3v-2.4C5.5 12 3 10.2 1 7Z"
  + "M4 19.5h14V22H4Z";
const motionLast = {};
// What the effects did, for Settings > Motion & effects > Preview and for tests.
const motionStats = { played: {}, skipped: {}, events: {}, live: 0, loadMs: 0, drop: 0 };
let loadTasks = [];
let dropUntil = 0;

function reducedMotion() {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function motionSetting(key, fallback) {
  const v = state.settings?.[key]?.value;
  return v === undefined ? fallback : v;
}

// The level the person chose, before reduced motion and load. Older settings had one switch,
// forge_animations; off there still means Off.
function chosenMotionLevel() {
  const level = motionSetting("motion_level", motionSetting("forge_animations", true) === false ? 0 : 2);
  return Math.max(0, Math.min(3, Number(level) || 0));
}

// Whether Windows' reduced-motion setting is in force for this app.
function systemReducesMotion() {
  return reducedMotion() && motionSetting("motion_follow_system", true) !== false;
}

// The level effects play at now: the chosen level, Off while Windows asks for reduced motion (unless
// the person opted out of that), and one lower while the app is under sustained load.
function motionLevel() {
  if (systemReducesMotion()) return 0;
  const level = chosenMotionLevel();
  if (level >= 2 && Date.now() < dropUntil) return level - 1;
  return level;
}

// Decorative (branded) motion is wanted: level 2 or 3, and the window can be seen.
function motionOn(family) {
  if (motionLevel() < 2 || document.hidden) return false;
  return !family || motionSetting(`motion_${family}`, family !== "achievements") === true;
}

// The setting, Windows' preference and the family switches, as attributes on <html>, so CSS also
// hides any effect already playing when one changes. Called whenever settings load or change.
function applyMotion() {
  const root = document.documentElement;
  const level = motionLevel();
  root.dataset.motion = level >= 2 ? "on" : "off";
  root.dataset.motionLevel = String(level);
  // The person asked for motion although Windows reduces it: the stylesheet's reduced-motion rules step aside.
  if (reducedMotion() && !systemReducesMotion() && level > 0) root.dataset.motionOverride = "on";
  else delete root.dataset.motionOverride;
  for (const family of ["wolf", "sparks", "achievements", "ambient"]) {
    root.dataset[`fx${family[0].toUpperCase()}${family.slice(1)}`] = motionOn(family) ? "on" : "off";
  }
}

function skipFx(reason) {
  motionStats.skipped[reason] = (motionStats.skipped[reason] || 0) + 1;
  return null;
}

// Plays one effect over anchor. At most one effect per anchor at a time, each kind at most once per
// its gap and MAX_LIVE_FX app-wide, so repeated events don't stack up. Returns the element, or null
// when nothing plays (and motionStats says why).
function fx(anchor, kind) {
  const m = MOTION[kind];
  if (!anchor || !m) return null;
  if (motionLevel() < m.level || document.hidden) return skipFx("level");
  if (!motionOn(m.family)) return skipFx("family");
  if (!anchor.isConnected) return skipFx("gone");
  if (anchor.querySelector(":scope > .fx")) return skipFx("busy");
  if (motionStats.live >= MAX_LIVE_FX) return skipFx("budget");
  const rect = anchor.getBoundingClientRect?.();
  if (rect && (rect.bottom < 0 || rect.top > innerHeight || (!rect.width && !rect.height))) return skipFx("offscreen");
  const now = Date.now();
  if (m.gap && kind in motionLast && now - motionLast[kind] < m.gap) return skipFx("gap");
  motionLast[kind] = now;
  if (getComputedStyle(anchor).position === "static") anchor.classList.add("fx-anchor");
  const el = h("span", { class: `fx fx-${kind}`, "aria-hidden": "true" });
  m.build?.(el);
  anchor.append(el);
  motionStats.live += 1;
  motionStats.played[kind] = (motionStats.played[kind] || 0) + 1;
  setTimeout(() => { el.remove(); motionStats.live -= 1; }, m.ms + 100);
  return el;
}

// Something happened that people may like to see: plays its effect, if it has one and motion allows.
function forgeEvent(name, anchor) {
  motionStats.events[name] = (motionStats.events[name] || 0) + 1;
  const kind = FORGE_EVENTS[name];
  return kind ? fx(anchor, kind) : null;
}

function motionDiagnostics() {
  return { level: motionLevel(), chosen: chosenMotionLevel(), reduced: reducedMotion(), ...JSON.parse(JSON.stringify(motionStats)) };
}

function svgIcon(d, size = 16) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", d);
  path.setAttribute("fill", "currentColor");
  path.setAttribute("fill-rule", "evenodd");
  svg.append(path);
  return svg;
}

function wolfIcon() {
  return svgIcon(WOLF_PATH);
}

// Anvil Strike: the anvil drops a few pixels onto its mark, two sparks fly, and at Expressive a
// halftone ring and an offset outline. No shake, no flash.
function anvilParts(el) {
  el.append(h("span", { class: "fx-anvil" }, svgIcon(ANVIL_PATH, 14)), h("i"), h("i"), h("b"));
}

// Wolf Run: the wolf crosses its small lane in the status bar, leaving a short ember trail.
function wolfRunParts(el) {
  el.append(h("span", { class: "fx-runner" }, wolfIcon()), h("i"), h("i"), h("i"));
}

// A progress track for work counted in steps (Run all: cells done of cells to run). where() gives
// the element to show it in; it appears only if the work is still going after WOLF_DELAY_MS. The
// bar and its text are real progress and show at every level; the wolf on it only when motionOn("wolf").
// The wolf's position is always the done fraction, never a guess. engine, when given, says where
// the work runs ("on this PC"), in words, not only colour.
function wolfTrack(where, label, unit, engine = "") {
  let el = null;
  let done = 0;
  let total = 0;
  let over = false;
  const show = () => {
    if (over || !where()) return;
    const fill = h("span", { class: "wolf-fill" });
    // A few embers rise behind the wolf while it runs (CSS only, three dots).
    const wolf = motionOn("wolf") ? h("span", { class: "wolf", "aria-hidden": "true" },
      h("span", { class: "wolf-embers" }, h("i"), h("i"), h("i")), wolfIcon()) : null;
    const bar = h("span", { class: "wolf-bar", role: "progressbar", "aria-label": label, "aria-valuemin": "0" }, fill, wolf);
    el = h("span", { class: "wolf-track" }, h("span", { class: "wolf-text" }), bar);
    paint();
  };
  const paint = () => {
    if (!el) return;
    if (!el.isConnected) where()?.append(el);  // the notebook was drawn again meanwhile
    const pct = total ? Math.round((done / total) * 100) : 0;
    el.querySelector(".wolf-text").textContent = `${unit} ${Math.min(done + 1, total)} of ${total}${engine ? ` ${engine}` : ""}`;
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

// A run elsewhere (in Fabric) that reports no measurable progress: an indeterminate bar with no fill,
// percentage or time estimate, the wolf trotting in place in Kratos navy (wolf family, level 2+), and
// the words saying where it runs. Without the wolf the plain indeterminate bar shows instead.
function remoteProgress(label, where = "Running in Fabric") {
  const wolf = motionOn("wolf") ? h("span", { class: "wolf wolf-idle", "aria-hidden": "true" }, wolfIcon()) : null;
  return h("div", { class: "remote-progress" },
    h("div", { class: "progress remote", role: "progressbar", "aria-label": label, "aria-valuetext": where },
      h("div", { class: "bar indeterminate" }), wolf),
    h("span", { class: "remote-where" }, where));
}

// Energy Flow: a pipeline run's top-level activities as a row of nodes joined by links, drawn from
// pipeline_run_status (orchestration/runner.py). Each node says its state in words; the link into a
// running activity carries a moving dash (ambient family, level 2+), and an activity that finishes
// settles or fails once. Activities inside ForEach and Until are counted on their parent, never drawn
// one by one. Returns { update(status), el }.
const FLOW_STATE = { pending: "Queued", running: "In progress", succeeded: "Succeeded", failed: "Failed",
  skipped: "Skipped", cancelled: "Cancelled", interrupted: "Interrupted" };
function pipelineFlow(container) {
  const seen = {};
  const where = h("div", { class: "flow-where" });
  const row = h("ol", { class: "flow" });
  const el = h("div", { class: "flow-wrap" }, where, row);
  container.append(el);
  const update = (status) => {
    // Where it runs, in words: a local run is never shown as running in Fabric.
    where.textContent = status.where === "this_pc"
      ? `Local run on this PC${status.verified_against_fabric ? "" : " (not run in Fabric)"}` : "Run in Fabric";
    const acts = (status.activities || []).filter((a) => !a.parent);
    const inner = (status.activities || []).filter((a) => a.parent);
    acts.forEach((a, i) => {
      let node = row.querySelector(`[data-flow="${CSS.escape(a.id)}"]`);
      if (!node) {
        node = h("li", { class: "flow-node", "data-flow": a.id },
          i ? h("span", { class: "flow-link", "aria-hidden": "true" }) : null,
          h("span", { class: "flow-box" }, h("b", {}, a.name), h("span", { class: "flow-state" })));
        row.append(node);
      }
      const kids = inner.filter((c) => c.parent === a.id);
      const done = kids.filter((c) => !["pending", "running"].includes(c.state)).length;
      node.dataset.state = a.state;
      node.querySelector(".flow-state").textContent = FLOW_STATE[a.state] || a.state;
      if (kids.length) node.querySelector(".flow-state").textContent += ` · ${done} of ${kids.length}`;
      const before = seen[a.id];
      seen[a.id] = a.state;
      if (before && before !== a.state) {
        const box = node.querySelector(".flow-box");
        if (a.state === "succeeded") forgeEvent("pipeline.activity.succeeded", box);
        if (a.state === "failed") forgeEvent("pipeline.activity.failed", box);
      }
    });
  };
  return { el, update };
}

// Page changes slide a few pixels in the direction you moved down or up the rail, 160 ms, transform
// and opacity only, never blocking input. Only when the page itself changes, not on every redraw.
let motionLastView = null;
function viewTransition(view) {
  const from = motionLastView;
  motionLastView = state.view;
  if (!from || from === state.view || motionLevel() < 1 || !view.animate) return;
  // The rail's order, top to bottom; a page off the rail (an open notebook) counts as below it.
  const order = [...railFeatures(), ...railFeatures(true)].map((f) => [...Object.keys(f.views || {}), ...(f.rail.also || [])]);
  const at = (v) => { const i = order.findIndex((views) => views.includes(v)); return i < 0 ? order.length : i; };
  const dir = at(state.view) >= at(from) ? 1 : -1;
  view.getAnimations?.().forEach((a) => a.cancel());
  const depth = motionLevel() >= 3 ? " scale(0.996)" : "";
  view.animate([{ opacity: 0.85, transform: `translateY(${6 * dir}px)${depth}` }, { opacity: 1, transform: "none" }],
    { duration: 160, easing: "cubic-bezier(.2,.7,.3,1)" });
}

// Says text to screen readers through the page's polite status region, whatever the setting.
function announce(text) {
  const live = document.getElementById("announce");
  if (!live) return;
  live.textContent = "";
  setTimeout(() => { live.textContent = text; }, 50);  // a fresh change, so the same words are read again
}

// Long main-thread tasks (WebView2 and Chromium report them) add up; past the budget, effects drop a
// level for a while. Decorative motion never competes with the work. Kept as motionLoadWatch so a
// test can stop it and check one effect without the machine's load deciding the outcome.
const motionLoadWatch = typeof PerformanceObserver === "function" && PerformanceObserver.supportedEntryTypes?.includes("longtask")
  ? new PerformanceObserver((list) => {
    const now = performance.now();
    loadTasks = loadTasks.concat(list.getEntries().map((e) => [e.startTime, e.duration])).filter(([t]) => now - t < LOAD_WINDOW_MS);
    motionStats.loadMs = Math.round(loadTasks.reduce((s, [, d]) => s + d, 0));
    if (motionStats.loadMs > LOAD_BUDGET_MS && Date.now() >= dropUntil) {
      dropUntil = Date.now() + LOAD_COOLDOWN_MS;
      motionStats.drop += 1;
      applyMotion();
      setTimeout(applyMotion, LOAD_COOLDOWN_MS + 50);
    }
  }) : null;
motionLoadWatch?.observe({ type: "longtask", buffered: false });
if (typeof matchMedia === "function") matchMedia("(prefers-reduced-motion: reduce)").addEventListener?.("change", applyMotion);
// Ambient loops (the embers behind the wolf, the running cell's glow) pause while the app isn't in front.
const motionPause = () => { document.documentElement.dataset.motionPaused = document.hidden || !document.hasFocus() ? "on" : "off"; };
addEventListener("blur", motionPause);
addEventListener("focus", motionPause);
document.addEventListener("visibilitychange", motionPause);

// Panel and dialog transitions (KF-MO-003). Dialogs, the activity tray and side panes slide and fade in
// at Minimal and above (styles.css, .mo-in); nothing at Off or while Windows reduces motion. The way in
// never holds focus back: the element is in the page, focusable, from the first frame. On the way out
// the real dialog or tray goes at once (focus returns, screen readers lose it) and a blank card where
// it was fades, so nothing closed can be clicked or read while it fades. Escape closes with no fade.
const PANE_OUT_MS = 160;
const SIDE_PANES = ["explorer", "varpane", "provepane", "validatepane", "runspane", "commentspane"];
const PANES = ["overlay", "tray", ...SIDE_PANES];
let paneEscAt = -Infinity;
let paneView;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") paneEscAt = Date.now(); }, true);

const paneClass = (el) => PANES.find((c) => el.classList.contains(c));

function panesIn(nodes) {
  const out = [];
  for (const n of nodes) if (n.nodeType === 1) out.push(n, ...n.querySelectorAll(PANES.map((c) => `.${c}`).join(", ")));
  return out.filter(paneClass);
}

// Marks newly opened dialogs, the tray and side panes so CSS plays their way in. A redraw that swaps a
// pane for a fresh copy of itself isn't an opening, and neither is a page opening with its panes.
function motionPanes(records) {
  if (typeof state !== "object") return;  // the page is still loading app.js
  const samePage = state.view === paneView;
  paneView = state.view;
  if (motionLevel() < 1 || document.hidden) return;
  const added = panesIn(records.flatMap((r) => [...r.addedNodes]));
  if (!added.length) return;
  const redrawn = new Map(panesIn(records.flatMap((r) => [...r.removedNodes])).map((el) => [paneClass(el), el]));
  const now = performance.now();
  for (const el of added) {
    const kind = paneClass(el);
    if (!el.isConnected) continue;
    if (redrawn.has(kind)) {
      // Redrawn while still on its way in (the tray redraws as its notices arrive): the copy carries on
      // from where the first one was, rather than jumping to the end.
      const old = redrawn.get(kind);
      if (old.moEntering) paneIn(el, now - old.moInAt);
      continue;
    }
    if (kind === "overlay" && (el.parentElement?.id !== "dialog-root" || el.classList.contains("splash"))) continue;
    if (SIDE_PANES.includes(kind) && !samePage) continue;
    paneIn(el, 0);
  }
}

// Entering lasts until the element's own way-in animation ends, so the CSS duration is the only clock.
function paneIn(el, since) {
  el.moInAt = performance.now() - since;
  el.moEntering = true;
  const done = (e) => { if (e.target === el) el.moEntering = false; };
  el.addEventListener("animationend", done);
  el.addEventListener("animationcancel", done);
  el.classList.add("mo-in");
  // The first copy's animationend may not have been dispatched yet when its time was already up.
  if (since && since >= parseFloat(getComputedStyle(el).animationDuration) * 1000) {
    el.classList.remove("mo-in");
    el.moEntering = false;
    return;
  }
  if (since) for (const x of [el, el.querySelector(":scope > .dialog")]) if (x) x.style.animationDelay = `-${Math.round(since)}ms`;
}
if (typeof MutationObserver === "function" && document.body) new MutationObserver(motionPanes).observe(document.body, { childList: true, subtree: true });

// Call just before taking an open dialog (its .overlay) or the tray away: a blank card where it was
// fades out. Returns the card, or null when nothing fades (Off, reduced motion, Escape, hidden window).
function motionLeave(el) {
  if (!el?.isConnected || motionLevel() < 1 || document.hidden || Date.now() - paneEscAt < 250) return null;
  const box = (from, cls) => {
    const r = from.getBoundingClientRect();
    const d = h("div", { class: cls });
    Object.assign(d.style, { left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px` });
    return d;
  };
  const overlay = el.classList.contains("overlay");
  const card = overlay ? el.querySelector(".dialog") : el;
  const ghost = h("div", { class: "mo-ghost", "aria-hidden": "true" },
    overlay ? box(el, "mo-ghost-veil") : null,
    card ? box(card, `mo-ghost-card${overlay ? "" : " side"}`) : null);
  document.body.append(ghost);
  setTimeout(() => ghost.remove(), PANE_OUT_MS + 60);
  return ghost;
}
