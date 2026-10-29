// Home > Recent notebooks: each notebook's workspace, lakehouse, where Run all runs it and how
// it last ran; ticking several and running them at once (Preview, "runSeveral"); and items pinned to
// Home (Preview, "itemPins"). Notebooks for this PC run in the background, each as Run all on its
// saved version (notebook_batch_service.py); ones for Fabric are published and started there after
// the same confirm and risk check as the notebook's own Run in Fabric. Pins are kept on this PC
// beside the workspace pins (item_pins in service.py): Fabric has no API for pins.

const homeRun = { picked: new Set(), batch: null, timer: null, details: new Map(), redraw: null };
const HOME_BATCH_POLL_MS = 2500;
const HOME_PARALLEL_KEY = "fd.home.parallel";

// Where Run all runs a notebook with this "Runs on" choice, as nbWhere does for an open notebook.
function nbRunsOnFabric(compute) {
  return compute === "fabric" || (compute !== "local" && !!envWhere(state.meta?.project, state.meta?.env).fabric);
}

function nbRunsOnText(d) {
  if (!d) return "";
  const fabric = nbRunsOnFabric(d.compute);
  return d.compute === "default" ? `${fabric ? "Fabric" : "This PC"} (environment)` : fabric ? "Fabric" : "This PC";
}

// ---------- items pinned to Home ----------

async function itemPinsLoaded() {
  if (!state.itemPins) state.itemPins = await rpc("item_pins").catch(() => []);
  return state.itemPins;
}

function itemPinned(kind, id) {
  return (state.itemPins || []).some((p) => p.kind === kind && p.id === id);
}

async function toggleItemPin(pin) {
  const on = !itemPinned(pin.kind, pin.id);
  const pins = await attempt(() => rpc("set_item_pin", { ...pin, on }), on ? "Could not pin it" : "Could not unpin it");
  if (!pins) return false;
  state.itemPins = pins;
  homeCache.done = false;  // Home's Pinned card changes
  return true;
}

// The pin button for an item row: filled when pinned. pin: {kind, id, name, workspace_id, workspace_name}.
function itemPinButton(pin, after) {
  const on = itemPinned(pin.kind, pin.id);
  const label = on ? `Unpin ${pin.name} from Home` : `Pin ${pin.name} to Home`;
  return h("button", { class: `star pin${on ? " on" : ""}`, title: label, "aria-label": label,
    onclick: async (e) => { e.stopPropagation(); if (await toggleItemPin(pin)) after?.(); } }, icon(on ? "pinFilled" : "pin", 16));
}

function openPinnedItem(p) {
  if (p.kind === "notebook") { openNotebook(p.id); return; }
  openInFabric(fabricItemLink({ workspace_id: p.workspace_id, item_id: p.id, type: p.kind }));
}

// Home's Pinned card: nothing until something is pinned.
function homePinnedCard() {
  if (!PREVIEW_FEATURES.itemPins || !previewShown()) return null;
  const slot = h("div", { class: "home-pins-slot" });
  itemPinsLoaded().then((pins) => {
    if (!pins.length) return;
    const draw = () => {
      const now = state.itemPins || [];
      if (!now.length) { slot.replaceChildren(); return; }
      slot.replaceChildren(h("section", { class: "card pad home-pins" },
        h("div", { class: "row" }, preview("itemPins", h("h2", { class: "grow" }, "Pinned"))),
        h("div", { class: "home-pin-grid" }, now.map((p) => h("div", { class: "home-pin" },
          h("div", { class: "row" }, h("span", { class: "pill-s neu" }, p.kind === "notebook" ? "Notebook" : typeLabel(p.kind)),
            h("span", { class: "grow" }), itemPinButton(p, draw)),
          h("button", { class: "name-link home-pin-name", title: `Open ${p.name}`, onclick: () => openPinnedItem(p) }, p.name),
          h("div", { class: "muted-text small" }, p.kind === "notebook"
            ? [homeRun.details.get(p.id)?.workspace_name, nbRunsOnText(homeRun.details.get(p.id))].filter(Boolean).join(" · ") || "On this PC"
            : p.workspace_name || ""),
          p.kind === "notebook" && PREVIEW_FEATURES.runSeveral && previewShown()
            ? btn("Run", "play", () => homeRunNotebooks([p.id]), "tool small link") : null)))));
    };
    homeRun.redrawPins = draw;
    draw();
  });
  return slot;
}

// ---------- Recent notebooks ----------

function homeLastRun(d, name) {
  const item = (homeRun.batch?.state === "running" || homeRun.batch?.state === "starting")
    ? homeRun.batch.items.find((i) => i.name === name && (i.status === "running" || i.status === "waiting")) : null;
  if (item) {
    const text = item.status === "waiting" ? "Waiting to start"
      : item.cells ? `Running, cell ${item.cell} of ${item.cells}` : "Running";
    return h("div", { class: "home-run-going" }, h("span", {}, text),
      item.status === "running" ? h("div", { class: "progress" }, h("div", { class: item.cells ? "bar" : "bar indeterminate",
        style: item.cells ? `width:${Math.round((100 * (item.cell - 1)) / item.cells)}%` : null })) : null);
  }
  const r = d?.last_run;
  if (!d) return "";
  if (!r) return h("span", { class: "muted-text" }, "Never run");
  const ok = r.status === "ok" || r.status === "Completed";
  const failed = r.status === "failed" || r.status === "Failed";
  const word = ok ? "Succeeded" : failed ? "Failed" : r.status === "skipped" ? "Skipped" : r.status;
  const at = r.ended || r.started;
  return h("span", { class: ok ? "home-run ok" : failed ? "home-run failed" : "home-run", title: r.message || null },
    `${word}${r.source === "fabric" ? " in Fabric" : ""} ${at ? relTime(at) : ""}${ok && r.seconds ? `, ${took(r.seconds)}` : ""}`.trim());
}

// The list for these notebooks (already filtered by the search box). Draws at once with what
// list_notebooks gave, then again with each one's details. Returns { el, done }.
function homeRecentList(shown) {
  const box = h("div", { class: "home-recent" });
  const several = PREVIEW_FEATURES.runSeveral && previewShown();
  const pins = PREVIEW_FEATURES.itemPins && previewShown();
  const anyPlace = shown.some((n) => n.place);
  const names = shown.map((n) => n.name);
  for (const n of [...homeRun.picked]) if (!names.includes(n)) homeRun.picked.delete(n);

  const draw = () => {
    const going = homeRun.batch && (homeRun.batch.state === "running" || homeRun.batch.state === "starting");
    const picked = names.filter((n) => homeRun.picked.has(n));
    const all = h("input", { type: "checkbox", "aria-label": "Select every notebook shown", checked: picked.length === names.length && names.length > 0,
      onchange: (e) => { names.forEach((n) => (e.target.checked ? homeRun.picked.add(n) : homeRun.picked.delete(n))); draw(); } });
    const tick = (name) => h("input", { type: "checkbox", "aria-label": `Select ${name}`, checked: homeRun.picked.has(name),
      onclick: (e) => e.stopPropagation(),
      onchange: (e) => { if (e.target.checked) homeRun.picked.add(name); else homeRun.picked.delete(name); draw(); } });
    const bar = several ? h("div", { class: "row wrap home-run-bar" },
      going ? [h("span", { class: "grow" }, homeBatchText(homeRun.batch)),
        homeRun.batch.stopping ? h("span", { class: "muted-text small" }, "The rest won't start")
          : btn("Don't start the rest", null, () => homeBatchStop(), "tool outline small")]
        : [h("span", { class: "grow muted-text small" }, picked.length ? `${picked.length} selected` : "Tick notebooks to run several at once."),
          picked.length ? btn("Clear", null, () => { homeRun.picked.clear(); draw(); }, "tool link small") : null,
          preview("runSeveral", btn(`Run ${picked.length || ""} notebook${picked.length === 1 ? "" : "s"}`.replace("  ", " "), "play",
            () => homeRunNotebooks(picked), "tool primary small", { disabled: !picked.length }))]) : null;
    const table = h("table", { class: "list home-notebooks" },
      h("thead", {}, h("tr", {},
        several ? h("th", { class: "tick" }, all) : null,
        h("th", {}, "Name"), h("th", {}, "Workspace"), h("th", {}, "Lakehouse"), h("th", {}, "Runs on"),
        h("th", { title: "Runs from schedules, Run from Home and Run in Fabric. Cells run in the open notebook aren't counted." }, "Last run"),
        h("th", {}, "Modified"), pins ? h("th", { class: "tick" }, h("span", { class: "sr-only" }, "Pin")) : null)),
      h("tbody", {}, shown.map((n) => {
        const d = homeRun.details.get(n.name);
        const ws = d?.workspace_name || "";
        return h("tr", { class: `click${homeRun.picked.has(n.name) ? " selected" : ""}`, onclick: () => openNotebook(n.name) },
          several ? h("td", { class: "tick" }, tick(n.name)) : null,
          h("td", {}, h("span", { class: "item" }, icon("notebook", 18), h("span", {}, n.name,
            anyPlace ? h("div", { class: "muted-text small" }, n.place ? n.place.label : "This PC") : null))),
          h("td", {}, !d ? "" : d.workspace_id
            ? h("button", { class: "name-link", title: `Open ${ws || "the workspace"} in Fabric`, onclick: (e) => {
              e.stopPropagation(); openInFabric(fabricItemLink({ workspace_id: d.workspace_id }));
            } }, ws || "Workspace")
            : h("span", { class: "muted-text", title: "Pick a workspace or attach a lakehouse to publish it" }, "Not published")),
          h("td", {}, d ? d.lakehouse || h("span", { class: "muted-text" }, "None") : ""),
          h("td", {}, d && !d.error ? h("span", { class: "chip-s" }, nbRunsOnText(d)) : ""),
          h("td", {}, homeLastRun(d, n.name)),
          h("td", {}, when(new Date(n.modified * 1000).toISOString())),
          pins ? h("td", { class: "tick" }, itemPinButton({ kind: "notebook", id: n.name, name: n.name,
            workspace_id: d?.workspace_id || "", workspace_name: ws }, () => { draw(); homeRun.redrawPins?.(); })) : null);
      })));
    box.replaceChildren(...[bar, table].filter(Boolean));
  };
  homeRun.redraw = () => { if (box.isConnected) draw(); };
  draw();
  const done = Promise.all([
    rpc("notebook_details", { names }).catch(() => []),
    pins ? itemPinsLoaded() : null,
  ]).then(([details]) => {
    for (const d of details || []) homeRun.details.set(d.name, d);
    draw();
    homeRun.redrawPins?.();
  });
  return { el: box, done };
}

function homeBatchText(b) {
  const count = (s) => b.items.filter((i) => i.status === s).length;
  const ended = count("ok") + count("failed") + count("skipped");
  return [`Running ${b.items.length}: ${ended} done`, count("running") ? `${count("running")} running` : "",
    count("waiting") ? `${count("waiting")} waiting` : ""].filter(Boolean).join(", ");
}

// ---------- Run several ----------

// The confirm: which notebooks run where, the risk check for each one in Fabric, then start.
async function homeRunNotebooks(names) {
  if (!names.length) return;
  const missing = names.filter((n) => !homeRun.details.has(n));
  if (missing.length) {
    const got = await attempt(() => rpc("notebook_details", { names: missing }), "Could not read the notebooks");
    if (!got) return;
    for (const d of got) homeRun.details.set(d.name, d);
  }
  const details = names.map((n) => homeRun.details.get(n)).filter((d) => d && !d.error);
  const local = details.filter((d) => !nbRunsOnFabric(d.compute));
  const fabricAll = details.filter((d) => nbRunsOnFabric(d.compute));
  const cant = fabricAll.filter((d) => !d.workspace_id);
  let fabric = fabricAll.filter((d) => d.workspace_id);
  if (fabric.length && !(await ensureSignedIn())) fabric = [];
  const risks = new Map(await Promise.all(fabric.map(async (d) => [d.name,
    await rpc("risk_check", { id: `notebook:${d.name}`, action: "run_in_fabric", env: null }).catch(riskCheckFailed)])));
  const blocked = fabric.filter((d) => risks.get(d.name)?.result === "blocked");
  fabric = fabric.filter((d) => risks.get(d.name)?.result !== "blocked");
  const boxes = new Map(fabric.map((d) => [d.name, riskAckBox(risks.get(d.name))]));
  const total = local.length + fabric.length;
  let saved = 2;
  try { saved = Number(localStorage.getItem(HOME_PARALLEL_KEY)) || 2; } catch { /* storage blocked: the default */ }
  const parallel = h("input", { type: "number", min: "1", max: "4", value: String(Math.min(4, Math.max(1, saved))), class: "home-parallel" });
  const row = (d, where) => h("div", { class: "home-run-row" }, h("b", { class: "grow" }, d.name), h("span", { class: "muted-text small" }, where));
  const dirty = state.notebook?.dirty && local.concat(fabric).some((d) => d.name === state.notebook.name) ? state.notebook.name : null;
  const body = h("div", { class: "wl-confirm home-run-confirm" },
    h("p", {}, "Each notebook runs its saved version from top to bottom, like Run all, in the background. Frozen cells are skipped."),
    dirty ? h("p", { class: "warn-text small" }, `${dirty} has unsaved changes. The saved version runs.`) : null,
    local.length ? [h("h3", {}, `On this PC (${local.length})`),
      h("div", { class: "home-run-list" }, local.map((d) => row(d, [d.workspace_name, d.lakehouse].filter(Boolean).join(" / ") || "No lakehouse"))),
      h("label", { class: "row home-run-parallel" }, "At most", parallel, "at a time on this PC"),
      h("p", { class: "muted-text small" }, "These start no Fabric job. Reading or writing OneLake tables still uses capacity.")] : null,
    fabric.length ? [h("h3", {}, `On Fabric (${fabric.length})`),
      h("p", { class: "small" }, "Each is published to its Fabric item (replacing what is there), then run on its workspace's capacity as you."),
      fabric.map((d) => h("div", { class: "home-run-fabric" }, row(d, d.workspace_name),
        riskPanel(risks.get(d.name), { box: boxes.get(d.name) })))] : null,
    blocked.length ? h("p", { class: "warn-text small" }, `Not run, the risk check found a restriction: ${blocked.map((d) => d.name).join(", ")}. `,
      "Open each to see why.") : null,
    cant.length ? h("p", { class: "warn-text small" }, `Not run, they run on Fabric but have no workspace yet: ${cant.map((d) => d.name).join(", ")}. `,
      "Attach a lakehouse or pick a workspace in each first.") : null);
  const start = async (withFabric) => {
    const goFabric = withFabric ? fabric : [];
    for (const d of goFabric) {
      if (!(await riskProceed({ id: `notebook:${d.name}`, action: "run_in_fabric" }, risks.get(d.name), boxes.get(d.name)))) return;
    }
    const n = Math.min(4, Math.max(1, Number(parallel.value) || 1));
    try { localStorage.setItem(HOME_PARALLEL_KEY, String(n)); } catch { /* storage blocked: not remembered */ }
    closeDialog();
    homeRun.picked.clear();
    if (local.length) {
      const batch = await attempt(() => rpc("notebook_batch_start", { names: local.map((d) => d.name), parallel: n }),
        "Could not start the notebooks on this PC");
      if (batch) homeFollowBatch(batch);
    }
    for (const d of goFabric) await homeStartInFabric(d);
    homeRun.redraw?.();
  };
  const actions = !total ? [btn("Close", null, () => closeDialog(), "tool primary")] : [
    btn("Cancel", null, () => closeDialog(), "tool outline"),
    local.length && fabric.length ? btn(`Run only the ${local.length} on this PC`, null, () => start(false), "tool outline") : null,
    btn(total === 1 ? `Run ${(local[0] || fabric[0]).name}` : `Run ${total} notebooks`, "play", () => start(true), "tool primary"),
  ].filter(Boolean);
  showDialog(total === 1 ? `Run ${(local[0] || fabric[0]).name}?` : total ? `Run ${total} notebooks?` : "Nothing can run", body, actions);
}

async function homeStartInFabric(d) {
  const run = await attempt(() => rpc("notebook_batch_fabric", { name: d.name }), `Could not start ${d.name} in Fabric`);
  if (!run) return;
  logActivity({ id: `fabric:${run.job_id}`, status: "running", title: `${d.name} · Notebook run`,
    text: `In Fabric on ${run.workspace_name || d.workspace_name}`, url: run.url, monitor: true });
  trackFabricJobs();
}

// Follows the batch until every notebook ended: the list, the bell and, at the end, a notice.
function homeFollowBatch(batch) {
  homeRun.batch = batch;
  const title = `Run ${batch.items.length} notebook${batch.items.length === 1 ? "" : "s"}`;
  logActivity({ id: `batch:${batch.id}`, status: "running", title, text: "On this PC" });
  clearTimeout(homeRun.timer);
  const poll = async () => {
    const b = await rpc("notebook_batch", { id: batch.id }).catch(() => null);
    if (b) homeRun.batch = b;
    const now = homeRun.batch;
    if (now.state === "done" || now.state === "lost") {
      const failed = now.items.filter((i) => i.status === "failed");
      const ok = now.items.filter((i) => i.status === "ok").length;
      const skipped = now.items.filter((i) => i.status === "skipped").length;
      const text = [`${ok} succeeded`, failed.length ? `${failed.length} failed (${failed.map((i) => i.name).join(", ")})` : "",
        skipped ? `${skipped} not started` : ""].filter(Boolean).join(", ");
      logActivity({ id: `batch:${batch.id}`, status: failed.length ? "failed" : "ok", title, text, notice: true });
      toast(`${title}: ${text}.`, failed.length > 0);
      for (const i of now.items) homeRun.details.delete(i.name);  // their Last run changed
      homeCache.done = false;
      if (state.view === "home") render();
      return;
    }
    logActivity({ id: `batch:${batch.id}`, status: "running", title, text: homeBatchText(now) });
    homeRun.redraw?.();
    homeRun.timer = setTimeout(poll, HOME_BATCH_POLL_MS);
  };
  homeRun.timer = setTimeout(poll, HOME_BATCH_POLL_MS);
}

async function homeBatchStop() {
  const b = homeRun.batch;
  if (!b) return;
  const out = await attempt(() => rpc("notebook_batch_stop", { id: b.id }), "Could not stop the rest");
  if (out) { homeRun.batch = out; homeRun.redraw?.(); }
}

// The open notebook, from the command bar: pinned to Home, or unpinned when it already is.
defineAction({ id: "notebook.pin", label: "Pin to Home or unpin", icon: "pin", keywords: "notebook pin home favourite favorite unpin",
  contexts: ["notebook"], risk: "low", where: "local", preview: "itemPins", available: (ctx) => (ctx.nb ? true : "Open a notebook first."),
  run: async (ctx) => {
    await itemPinsLoaded();
    const on = !itemPinned("notebook", ctx.nb.name);
    if (await toggleItemPin({ kind: "notebook", id: ctx.nb.name, name: ctx.nb.name })) {
      toast(on ? `${ctx.nb.name} is pinned to Home.` : `${ctx.nb.name} is no longer pinned to Home.`);
    }
  } });
