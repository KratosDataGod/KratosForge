// Monitor > Runs: the Forge Activity view (LX-05, Preview). A strip of today's counts and, when the
// person keeps the Forge view on, a band with a lane for each of the first FORGE_LANES running runs
// beside a small forge scene. Everything it shows comes from the runs Runs already read and from the
// notebooks running in this app; the table below stays the full record.
//
// Honest progress only: a run with a known step count (Run all on this PC: cells done of cells to run)
// gets a bar and "cell N of M"; a run in Fabric gets a still ring and "Waiting on Fabric"; a run whose
// total isn't known gets elapsed time only. Nothing moves on a timer except the elapsed clock.
// The motion comes from motion.js: the scene's loops follow the ambient and wolf family switches and
// html[data-motion], and a run seen running that then finishes plays one effect through forgeEvent(). Below Heroic
// (Professional, or Windows' reduced motion) the text and the bars stay and everything else is still.
const FORGE_LANES = 3;
// A run that finished this recently is shown as the scene's outcome when nothing else is running.
const FORGE_RECENT_MS = 10 * 60 * 1000;
// on: the person's choice this session (null: on at Heroic and Legendary, motion_level 2 and 3, off below).
// rows: the runs Runs read last. seen: runs shown running this session; only those play an outcome, so
// opening Monitor or restarting the app never replays one. struck: outcomes already played, once each.
const forgeActivity = { on: null, rows: [], seen: new Set(), struck: new Set(), timer: 0 };

function forgeViewShown() {
  return !!PREVIEW_FEATURES.forgeActivity && previewShown();
}

function forgeViewOn() {
  return forgeActivity.on ?? chosenMotionLevel() >= 2;
}

// The view row's switch for the band.
function forgeViewToggle() {
  return h("label", { class: "check forge-toggle" }, h("input", { type: "checkbox", checked: forgeViewOn() ? true : null,
    onchange: (e) => { forgeActivity.on = e.target.checked; render(); } }), "Forge view");
}

const forgeKey = (r) => `${r.kind}|${r.activity}|${r.at}`;
const forgeStart = (iso) => { const d = readDate(iso); return d ? d.getTime() : null; };

// m:ss, or h:mm:ss past an hour.
function forgeElapsed(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const pad = (n) => String(n).padStart(2, "0");
  return s >= 3600 ? `${Math.floor(s / 3600)}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}` : `${Math.floor(s / 60)}:${pad(s % 60)}`;
}

// Run all in this app on this PC (notebook.js keeps nb.forgeRun while it goes and after it ends).
function forgeNotebooks() {
  return [...openNotebooks.values()].filter((nb) => nb.forgeRun).map((nb) => ({ nb, run: nb.forgeRun }));
}

// The lanes and the scene's state, from the rows Runs read and the notebooks running here.
function forgeModel(rows, now = Date.now()) {
  const lanes = [];
  for (const { nb, run } of forgeNotebooks()) {
    if (run.result) continue;
    lanes.push({ key: `nb|${nb.name}|${run.started}`, name: nb.name, kind: "Notebook", where: "This PC", mode: "steps",
      done: run.done, total: run.total, started: run.started });
  }
  for (const r of rows) {
    if (r.status !== "running") continue;
    const fabric = r.where === "fabric";
    lanes.push({ key: forgeKey(r), name: r.activity, kind: r.kind, where: fabric ? "Fabric" : r.ranOn || "This PC",
      mode: fabric ? "fabric" : "unknown", started: forgeStart(r.at) });
  }
  lanes.sort((a, b) => (b.started || 0) - (a.started || 0));
  if (lanes.length) {
    const fabricOnly = lanes.every((l) => l.mode === "fabric");
    return { state: fabricOnly ? "waiting" : "active", lanes: lanes.slice(0, FORGE_LANES), more: lanes.length - FORGE_LANES, running: lanes.length };
  }
  // Nothing running: the most recent run that finished in the last few minutes, if any.
  const ended = [];
  for (const { nb, run } of forgeNotebooks()) {
    if (run.result) ended.push({ key: `nb|${nb.name}|${run.started}`, name: nb.name, kind: "Notebook", where: "This PC", ok: run.result === "ok",
      at: run.ended, pct: run.result === "ok" ? 100 : run.total ? Math.round((run.done / run.total) * 100) : null, text: run.result === "ok" ? `Succeeded, ${run.total} ${run.total === 1 ? "cell" : "cells"}`
        : run.result === "failed" ? `Failed at cell ${run.done + 1} of ${run.total}` : `Stopped after ${run.done} of ${run.total} cells`,
      seconds: (run.ended - run.started) / 1000, stopped: run.result === "stopped" });
  }
  for (const r of rows) {
    if (r.status !== "ok" && r.status !== "failed") continue;
    const start = forgeStart(r.at);
    if (start === null) continue;
    const end = start + (Number(r.seconds) || 0) * 1000;
    ended.push({ key: forgeKey(r), name: r.activity, kind: r.kind, where: r.where === "fabric" ? "Fabric" : r.ranOn || "This PC",
      ok: r.status === "ok", at: end, seconds: r.seconds, pct: r.status === "ok" ? 100 : null,
      text: r.status === "ok" ? `Succeeded${r.rows !== "" && r.rows !== undefined && r.rows !== "—" ? `, ${Number(r.rows).toLocaleString()} rows written` : ""}`
        : `Failed${r.reason ? `: ${r.reason}` : ""}` });
  }
  const last = ended.filter((e) => !e.stopped && now - e.at < FORGE_RECENT_MS).sort((a, b) => b.at - a.at)[0];
  if (last) return { state: last.ok ? "done" : "failed", lanes: [last], more: 0, running: 0 };
  return { state: "idle", lanes: [], more: 0, running: 0 };
}

const FORGE_TITLES = {
  idle: ["THE FORGE IS", "QUIET"], active: ["THE FORGE IS", "ACTIVE"], waiting: ["THE FORGE IS", "WAITING ON FABRIC"],
  done: ["FORGED", "CLEAN"], failed: ["THE FORGE", "COOLED"],
};

function forgeSubtitle(m) {
  const one = m.lanes[0];
  if (m.state === "idle") return "Nothing is running. Start a notebook or pipeline and it shows here.";
  if (m.state === "done") return `${one.name} finished. One strike, then the forge rests.`;
  if (m.state === "failed") return `${one.name} failed. Open it in the table below to see why.`;
  if (m.more > 0) return `${m.running} runs. The ${FORGE_LANES} most recent have lanes.`;
  const by = new Map();
  for (const l of m.lanes) { const k = l.mode === "fabric" ? "waiting on Fabric" : `on ${l.where === "This PC" ? "this PC" : l.where}`; by.set(k, (by.get(k) || 0) + 1); }
  const parts = [...by].map(([k, n]) => `${n} ${k}`);
  return `${m.running === 1 ? "One run" : `${m.running} runs`}: ${parts.join(", ")}.`;
}

// One lane: name and where, what it is doing in words, a bar only for a known step count, the time.
function forgeLane(l, state) {
  const status = h("span", { class: "forge-status", role: "status" });
  let bar = null;
  if (state === "done" || state === "failed") {
    status.append(l.text);
    // A bar only for what is known: a success is whole, a failed Run all stopped at its cell; a failed run
    // from the run log has no count, so no bar.
    bar = l.pct === null ? null : h("span", { class: "forge-bar" }, h("span", { class: "forge-fill", style: `width: ${l.pct}%` }));
  } else if (l.mode === "steps") {
    status.append(h("span", { class: "forge-wolf", "aria-hidden": "true" }, wolfIcon()), h("span", { class: "forge-text" }));
    bar = h("span", { class: "forge-bar", role: "progressbar", "aria-label": `${l.name} cells done`, "aria-valuemin": "0" },
      h("span", { class: "forge-fill sheen" }));
  } else if (l.mode === "fabric") {
    status.append(h("span", { class: "forge-ring", "aria-hidden": "true" }), "Waiting on Fabric");
  } else {
    status.append(h("span", { class: "forge-dot", "aria-hidden": "true" }), "Running, total not known yet");
  }
  const lane = h("li", { class: `forge-lane ${l.mode || ""}`, "data-lane": l.key },
    h("div", { class: "forge-who" }, h("b", {}, l.name), h("span", { class: "muted-text small" }, `${l.kind} · ${l.where}`)),
    h("div", { class: "forge-what" }, status, bar),
    h("div", { class: "forge-time" }, h("b", { "data-start": l.started ?? null }, l.started ? forgeElapsed(Date.now() - l.started) : took(l.seconds)),
      h("span", { class: "muted-text small" }, state === "done" || state === "failed" ? "took" : "elapsed")));
  forgePaintSteps(lane, l);
  return lane;
}

function forgePaintSteps(lane, l) {
  if (l.mode !== "steps") return;
  const text = lane.querySelector(".forge-text");
  if (text) text.textContent = `Running, cell ${Math.min(l.done + 1, l.total)} of ${l.total}`;
  const bar = lane.querySelector(".forge-bar");
  bar?.setAttribute("aria-valuemax", String(l.total));
  bar?.setAttribute("aria-valuenow", String(l.done));
  const fill = lane.querySelector(".forge-fill");
  if (fill) fill.style.width = `${l.total ? Math.round((l.done / l.total) * 100) : 0}%`;
}

// The scene: an anvil with the wolf beside it, drawn from the app's own marks until the painted art
// exists (docs/design/assets.md). Loops only while something runs; an outcome plays once.
function forgeScene() {
  return h("div", { class: "forge-scene", "aria-hidden": "true" },
    h("span", { class: "forge-glow" }), h("span", { class: "forge-anvil" }, svgIcon(ANVIL_PATH, 72)),
    h("span", { class: "forge-sparks" }, h("i"), h("i"), h("i")),
    h("span", { class: "forge-scene-wolf" }, svgIcon(WOLF_PATH, 40)));
}

function forgeSection(m) {
  const [t1, t2] = FORGE_TITLES[m.state];
  const scene = forgeScene();
  const el = h("section", { class: `forge-band ${m.state}`, "aria-label": "Forge activity", "data-state": m.state },
    scene,
    h("div", { class: "forge-side" },
      h("p", { class: "forge-title" }, `${t1} `, h("span", {}, t2)),
      h("p", { class: "forge-sub" }, forgeSubtitle(m)),
      m.lanes.length ? h("ol", { class: "forge-lanes" }, m.lanes.map((l) => forgeLane(l, m.state))) : null,
      m.more > 0 ? h("a", { href: "#", class: "forge-more", onclick: (e) => {
        e.preventDefault();
        document.querySelector(".page.monitor .monitor-scroll")?.scrollIntoView({ block: "start" });
      } }, `${m.more} more running, see the table below`) : null));
  // An outcome plays once, and only for a run seen running this session: the anvil strikes for a
  // success, an ember goes out for a failure. The anvil is looked up when the frame comes, so a redraw
  // in between doesn't lose it.
  for (const l of m.state === "active" || m.state === "waiting" ? m.lanes : []) forgeActivity.seen.add(l.key);
  const key = (m.state === "done" || m.state === "failed") && m.lanes[0].key;
  if (key && forgeActivity.seen.has(key) && !forgeActivity.struck.has(key)) {
    requestAnimationFrame(() => {
      const anvil = document.querySelector(".forge-band .forge-anvil");
      if (!anvil || forgeActivity.struck.has(key)) return;
      forgeActivity.struck.add(key);
      forgeEvent(m.state === "done" ? "forge.run.succeeded" : "forge.run.failed", anvil);
    });
  }
  return el;
}

// Today's counts and this PC's share, from the run log Runs read (every filter aside).
function forgeCounts(rows, now = new Date()) {
  const today = now.toDateString();
  const day = now.getTime() - 86400000;
  const on = (r, fn) => { const d = readDate(r.at); return d && fn(d); };
  // Succeeded and Failed today count by when the run ended, so one that ran past midnight counts today.
  const endedToday = (r) => on(r, (d) => new Date(d.getTime() + (Number(r.seconds) || 0) * 1000).toDateString() === today);
  const notebooks = forgeNotebooks().filter(({ run }) => !run.result).length;
  const recent = rows.filter((r) => on(r, (d) => d.getTime() >= day));
  return {
    running: rows.filter((r) => r.status === "running").length + notebooks,
    ok: rows.filter((r) => r.status === "ok" && endedToday(r)).length,
    failed: rows.filter((r) => r.status === "failed" && endedToday(r)).length,
    local: recent.filter((r) => r.where !== "fabric").length, of: recent.length,
  };
}

function forgeStrip(rows) {
  const c = forgeCounts(rows);
  const stat = (label, value, cls) => h("div", { class: `forge-stat${cls ? ` ${cls}` : ""}` }, h("span", { class: "muted-text small" }, label), h("b", {}, value));
  return h("div", { class: "forge-strip", role: "group", "aria-label": "Today's runs" },
    stat("Running now", String(c.running)), stat("Succeeded today", String(c.ok), "ok"), stat("Failed today", String(c.failed), c.failed ? "bad" : ""),
    stat("Ran on this PC (24 h)", c.of ? `${c.local} of ${c.of}` : "None"));
}

// Draws the strip and the band into their slots on Runs, and keeps the clock and the notebook lanes
// current while something runs and they're on screen; nothing ticks while the forge is at rest.
// rows: every row Runs read, before its filters.
function forgeActivityFill(stripSlot, bandSlot, rows) {
  forgeActivity.rows = rows;
  stripSlot?.replaceChildren(forgeStrip(rows));
  if (!bandSlot) return;
  let model = forgeModel(rows);
  let sig = forgeSignature(model);
  bandSlot.replaceChildren(forgeSection(model));
  forgeStopTick();
  if (!forgeLive(model)) return;
  forgeActivity.timer = setInterval(() => {
    if (!bandSlot.isConnected) { forgeStopTick(); return; }
    model = forgeModel(forgeActivity.rows);
    const next = forgeSignature(model);
    if (next !== sig) {  // a run started or finished here: draw the band and the counts again
      sig = next;
      bandSlot.replaceChildren(forgeSection(model));
      stripSlot?.replaceChildren(forgeStrip(forgeActivity.rows));
      if (!forgeLive(model)) forgeStopTick();
      return;
    }
    for (const l of model.lanes) {
      const lane = bandSlot.querySelector(`[data-lane="${CSS.escape(l.key)}"]`);
      if (!lane) continue;
      forgePaintSteps(lane, l);
      const clock = lane.querySelector("[data-start]");
      if (clock && l.started && !["done", "failed"].includes(model.state)) clock.textContent = forgeElapsed(Date.now() - l.started);
    }
  }, 1000);
}

const forgeLive = (m) => m.state === "active" || m.state === "waiting";

function forgeStopTick() {
  clearInterval(forgeActivity.timer);
  forgeActivity.timer = 0;
}

function forgeSignature(m) {
  return `${m.state}|${m.lanes.map((l) => l.key).join(",")}|${m.more}`;
}
