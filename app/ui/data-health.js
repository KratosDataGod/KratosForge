// Data health (#2358): the tables you watch, each checked from its Delta log on this PC (meta_data_health_list,
// meta/data_health.py): freshness against its promise, rows written per load against the usual, and columns
// added or removed. The usual is a plain median and spread, and its rule shows beside every alert. Alerts are
// in the app only; Acknowledge and Snooze change only this PC's list, never the table.
// Monitor > KratosForge > Operations > Data health shows it.

const dhPage = { env: "dev", open: null };

const DH_FRESH = {
  fresh: "pass", late: "warn", no_promise: "neu", unread: "neu",
};

function dhAge(hours) {
  if (hours == null) return "";
  return hours < 1 ? `${Math.max(1, Math.round(hours * 60))} min ago` : `${Math.round(hours)} h ago`;
}

function dhFreshness(f) {
  if (!f) return h("span", { class: "pill-s neu" }, "Unknown");
  const text = f.state === "late" ? `${Math.round(f.age_hours)} h, promise ${f.within_hours} h`
    : f.state === "unread" ? "Can't tell" : dhAge(f.age_hours);
  return h("span", { class: `pill-s ${DH_FRESH[f.state] || "neu"}`, title: f.why || null }, text);
}

function dhTimes(v) {
  const m = v.usual?.median;
  return m ? `${(v.latest.rows / m).toFixed(1)}× usual` : v.state === "high" ? "Above usual" : "Below usual";
}

function dhVolume(v) {
  if (!v) return h("span", { class: "pill-s neu" }, "Unknown");
  if (v.state === "high" || v.state === "low") return h("span", { class: "pill-s fail" }, dhTimes(v));
  if (v.state === "usual") return h("span", { class: "pill-s pass" }, "Usual");
  if (v.state === "not_enough_history") return h("span", { class: "pill-s neu" }, `Not enough history (${v.usual.have} of ${v.usual.need} loads)`);
  if (v.state === "no_loads") return h("span", { class: "pill-s neu" }, "No loads yet");
  return h("span", { class: "pill-s neu", title: v.why || null }, "Rows not recorded");
}

function dhSchemaText(s) {
  const parts = [];
  if (s.added?.length) parts.push(`${s.added.length} column${s.added.length === 1 ? "" : "s"} added`);
  if (s.removed?.length) parts.push(`${s.removed.length} removed`);
  if (s.retyped?.length) parts.push(`${s.retyped.length} retyped`);
  return parts.join(", ");
}

function dhSchema(s) {
  if (!s || s.state === "unread") return h("span", { class: "pill-s neu", title: s?.why || null }, "Can't tell");
  if (s.state === "changed") return h("span", { class: "pill-s warn" }, dhSchemaText(s));
  if (s.state === "no_earlier_version") return h("span", { class: "pill-s neu" }, "First version");
  return h("span", { class: "pill-s pass" }, "Same");
}

// The rule beside an alert: what "usual" is, in numbers, and how it was worked out.
function dhRuleLine(v) {
  const u = v?.usual;
  if (!u?.enough) return null;
  return `Usual: median ${Math.round(u.median).toLocaleString()} rows a load, spread ±${Math.round(u.spread).toLocaleString()}, over the last ${u.have} loads.`;
}

function dhOpenAlerts(tables) {
  const out = [];
  for (const t of tables) for (const a of t.alerts || []) if (!a.quiet) out.push([t, a]);
  return out;
}

function dhAlertText(t, a) {
  if (a.kind === "volume") {
    const v = t.volume;
    return `${t.entity}: version ${a.version} wrote ${v.latest.rows.toLocaleString()} rows, ${dhTimes(v)}`;
  }
  if (a.kind === "freshness") return `${t.entity} last changed ${Math.round(t.freshness.age_hours)} h ago; its promise is ${t.freshness.within_hours} h`;
  return `${t.entity}: ${dhSchemaText(t.schema)} in version ${a.version}`;
}

// A watched table with no result to show, and the words for why (the row then carries its reason).
const DH_NOT_READ = { error: "Can't read", no_table: "No table yet", not_in_project: "Not in the project" };

function dataHealthPanel() {
  const box = h("div", { class: "inc-panel dh-panel" }, h("p", { class: "muted-text monitor-empty" }, "Reading the Delta logs..."));
  loadDataHealth(box);
  return h("div", { class: "mon-how" }, withHowItWorks(box, howItWorks("monitor.datahealth", {
    reads: "The Delta log of each table you watch, in the environment you pick: the last 200 commits and the table's columns. On this PC for Dev; in OneLake for Test and Prod, with your sign-in. No Fabric job runs.",
    changes: "Nothing in your tables changes. Watch, Acknowledge and Snooze change only this PC's list.",
    results: "The list of watched tables and the alerts you acknowledged or snoozed, in this app's folder on this PC.",
    more: [["Usual", "The usual is the median rows written by the last 20 loads, and the spread is their median distance from it. A load is unusual when it's more than 3 spreads from the median. When loads barely vary, it's unusual when it's more than 10% above or below the median. With fewer than 10 loads that recorded their rows there's no usual yet, so there's no alert."],
      ["Can't tell", "The log didn't hold that number, so none is shown. A number is never estimated."]],
    anchor: "data-health-preview",
  })));
}

async function loadDataHealth(box) {
  let out;
  try {
    out = await rpc("meta_data_health_list", { env: dhPage.env });
  } catch (err) {
    box.replaceChildren(h("p", { class: "reason monitor-empty" }, `Couldn't read Data health: ${friendlyError(err).text}`));
    return;
  }
  if (!box.isConnected) return;
  const open = dhPage.open && out.tables.find((t) => t.entity === dhPage.open);
  box.replaceChildren(...(open ? dhDetail(box, out, open) : dhList(box, out)));
}

function dhEnvSeg(box) {
  return h("div", { class: "seg", role: "group", "aria-label": "Environment" }, Object.entries(ENV_LABEL).map(([env, label]) =>
    h("button", { class: dhPage.env === env ? "on" : null, "aria-pressed": String(dhPage.env === env),
      onclick: () => { dhPage.env = env; dhPage.open = null; loadDataHealth(box); } }, label)));
}

function dhList(box, out) {
  const tables = out.tables.filter((t) => matches(t.entity));
  const alerts = dhOpenAlerts(out.tables);
  const watchable = out.entities.filter((n) => !out.tables.some((t) => t.entity === n));
  const filters = h("div", { class: "monitor-filters" }, dhEnvSeg(box), h("span", { class: "grow" }),
    out.no_project ? null : btn("Watch a table...", "add", () => dhWatchDialog(box, watchable), "tool outline"),
    btn("Refresh", "sync", () => loadDataHealth(box), "tool outline"));
  if (out.no_project) return [filters, h("p", { class: "muted-text monitor-empty" }, "Data health watches the tables of a metadata project. Open or create one in Data mesh first.")];
  const alertBox = alerts.length ? h("section", { class: "dh-alerts", "aria-label": "Open alerts" },
    h("h3", {}, `Open alerts (${alerts.length})`),
    alerts.map(([t, a]) => h("div", { class: "dh-alert" },
      h("span", { class: `pill-s ${a.kind === "volume" ? "fail" : "warn"}` }, { volume: "Volume", freshness: "Freshness", schema: "Schema" }[a.kind]),
      h("span", { class: "grow" }, dhAlertText(t, a),
        a.kind === "volume" ? h("div", { class: "muted-text small" }, dhRuleLine(t.volume)) : null),
      btn("Open", null, () => { dhPage.open = t.entity; loadDataHealth(box); }, "tool link small")))) : null;
  const list = tables.length ? h("div", { class: "monitor-scroll" }, h("table", { class: "list inc-list" },
    h("thead", {}, h("tr", {}, ["Table", "Freshness", "Volume", "Schema"].map((c) => h("th", {}, c)))),
    h("tbody", {}, tables.map((t) => h("tr", { class: "dh-row", tabindex: "0",
      onclick: () => { dhPage.open = t.entity; loadDataHealth(box); },
      onkeydown: (e) => { if (e.key === "Enter") { dhPage.open = t.entity; loadDataHealth(box); } } },
      h("td", {}, h("b", {}, t.entity)),
      DH_NOT_READ[t.state]
        ? h("td", { colspan: "3" }, h("span", { class: "pill-s neu" }, DH_NOT_READ[t.state]), " ", h("span", { class: "muted-text small" }, t.why))
        : [h("td", {}, dhFreshness(t.freshness)), h("td", {}, dhVolume(t.volume)), h("td", {}, dhSchema(t.schema))])))))
    : h("p", { class: "muted-text monitor-empty" }, state.query && out.tables.length ? "Nothing matches your search."
      : `No tables watched in ${envName(dhPage.env)}. Watch a table to check its freshness, volume and schema.`);
  const reset = out.reset ? h("p", { class: "reason dh-reset", role: "status" },
    `This PC's Data health list couldn't be read, so it started again empty on ${dateText(out.reset.at)}. `
    + `The old file was kept as ${out.reset.copy} in the app's folder. Watch your tables again to bring them back.`) : null;
  return [filters, reset || "", alertBox || "", list,
    h("p", { class: "muted-text small inc-foot" }, "Read from each table's Delta log; no Fabric job. Alerts only; nothing in the table changes.")];
}

function dhWatchDialog(box, names) {
  if (!names.length) { toast("Every table in the project is already watched here."); return; }
  const select = h("select", { "aria-label": "Table" }, names.map((n) => h("option", { value: n }, n)));
  showDialog("Watch a table", h("div", { class: "dialog-body" },
    h("label", { class: "field" }, "Table", select),
    h("p", { class: "muted-text small" }, `Its Delta log in ${envName(dhPage.env)} is read each time you open Data health. Nothing in the table changes.`)), [
    btn("Cancel", null, closeDialog, "tool"),
    btn("Watch", null, async () => {
      try {
        await rpc("meta_data_health_watch", { name: select.value, env: dhPage.env, watch: true });
      } catch (err) { toast(friendlyError(err).text, true); return; }
      closeDialog();
      loadDataHealth(box);
    }, "tool primary")]);
}

// Rows written per load as bars, with the usual band behind them and the latest load marked.
function dhChart(v) {
  const loads = (v.loads || []).filter((l) => l.rows != null);
  if (!loads.length) return h("p", { class: "muted-text" }, "No load recorded how many rows it wrote.");
  const max = Math.max(...loads.map((l) => l.rows), v.usual?.enough ? v.usual.median + v.usual.band : 0) || 1;
  const W = 600, H = 120, w = W / loads.length;
  const y = (n) => H - (n / max) * (H - 10);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H + 18}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `Rows written by the last ${loads.length} loads${v.usual?.enough ? ", with the usual band" : ""}`);
  const el = (tag, attrs, text) => {
    const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attrs).forEach(([k, val]) => n.setAttribute(k, val));
    if (text) n.textContent = text;
    svg.appendChild(n);
  };
  if (v.usual?.enough) {
    const top = y(v.usual.median + v.usual.band), bottom = y(Math.max(0, v.usual.median - v.usual.band));
    el("rect", { class: "dh-band", x: 0, y: top, width: W, height: Math.max(1, bottom - top) });
  }
  const last = v.latest?.version;
  loads.forEach((l, i) => el("rect", { class: l.version === last && ["high", "low"].includes(v.state) ? "dh-bar hi" : "dh-bar",
    x: i * w + 2, y: y(l.rows), width: Math.max(1, w - 4), height: Math.max(1, H - y(l.rows)) }));
  el("text", { x: 0, y: H + 14 }, `version ${loads[0].version}`);
  el("text", { x: W, y: H + 14, "text-anchor": "end" }, `version ${loads[loads.length - 1].version}`);
  return svg;
}

function dhDetail(box, out, t) {
  const back = btn("Back to Data health", null, () => { dhPage.open = null; loadDataHealth(box); }, "tool link");
  const head = h("div", { class: "monitor-filters" }, back, h("b", { class: "grow" }, `${t.entity} · ${envName(t.env)}`),
    btn("Stop watching", null, async () => {
      try { await rpc("meta_data_health_watch", { name: t.entity, env: t.env, watch: false }); } catch (err) { toast(friendlyError(err).text, true); return; }
      dhPage.open = null;
      loadDataHealth(box);
    }, "tool outline"),
    btn("Check again", "sync", async () => {
      try { await rpc("meta_data_health", { name: t.entity, env: t.env }); } catch (err) { toast(friendlyError(err).text, true); return; }
      loadDataHealth(box);
    }, "tool outline"));
  if (DH_NOT_READ[t.state]) return [head, h("p", { class: "reason monitor-empty" }, t.why)];
  const quiet = async (a, days) => {
    try {
      await rpc("meta_data_health_quiet", { name: t.entity, env: t.env, kind: a.kind, version: a.version, snooze_days: days });
    } catch (err) { toast(friendlyError(err).text, true); return; }
    loadDataHealth(box);
  };
  const runOf = (version) => (t.volume.loads || []).find((l) => l.version === version)?.run_id;
  const notes = (t.alerts || []).map((a) => {
    const run = runOf(a.version);
    return h("div", { class: `dh-note${a.quiet ? " quiet" : ""}`, role: a.quiet ? "status" : "alert" },
      h("span", {}, h("b", {}, dhAlertText(t, a)), a.kind === "volume" ? [" ", dhRuleLine(t.volume)] : null,
        a.quiet ? h("span", { class: "muted-text" }, a.quiet.until ? ` Snoozed until ${dateText(a.quiet.until)}.` : " Acknowledged.") : null),
      a.quiet ? null : h("div", { class: "row tight" },
        run ? btn("Open the run that wrote it", null, () => showRun(run, t.env), "tool outline small") : null,
        btn("Acknowledge", null, () => quiet(a, 0), "tool outline small"),
        btn("Snooze 1 day", null, () => quiet(a, 1), "tool outline small"),
        btn("Open an incident", null, () => reportIncident({ env: t.env, title: dhAlertText(t, a),
          text: [a.kind === "volume" ? dhRuleLine(t.volume) : "", "Found by Data health from the table's Delta log."].filter(Boolean).join(" "),
          lead: "Track this alert as an incident in the environment's shared list, so your team sees who is on it." },
        () => loadDataHealth(box)), "tool outline small")));
  });
  const f = t.freshness, s = t.schema, v = t.volume;
  const tile = (big, small) => h("div", { class: "dh-tile" }, h("b", {}, big), h("span", { class: "muted-text small" }, small));
  return [head, ...notes,
    h("section", { class: "dh-chart", "aria-label": "Rows written per load" }, h("h3", {}, "Rows written per load"), dhChart(v),
      v.unread ? h("p", { class: "muted-text small" }, `${v.unread} load${v.unread === 1 ? "" : "s"} didn't record ${v.unread === 1 ? "its" : "their"} rows, so ${v.unread === 1 ? "it is" : "they are"} left out.`) : null),
    h("div", { class: "dh-tiles" },
      tile(f.state === "unread" ? "Can't tell" : dhAge(f.age_hours), f.within_hours ? `Freshness · promise ${f.within_hours} h` : "Freshness · no promise"),
      tile(v.usual?.enough ? `${Math.round(v.usual.median).toLocaleString()} rows` : `${v.usual?.have ?? 0} of ${v.usual?.need ?? 10} loads`,
        v.usual?.enough ? `Usual load · spread ±${Math.round(v.usual.spread).toLocaleString()}` : "Not enough history for a usual"),
      tile(s.state === "changed" ? dhSchemaText(s) : s.state === "same" ? "Same" : s.state === "unread" ? "Can't tell" : "First version",
        s.since_version != null ? `Schema · since version ${s.since_version}` : "Schema")),
    s.state === "changed" ? h("p", { class: "muted-text small" }, [
      s.added.length ? `Added: ${s.added.join(", ")}.` : "", s.removed.length ? ` Removed: ${s.removed.join(", ")}.` : "",
      s.retyped.length ? ` Retyped: ${s.retyped.map((r) => r.name).join(", ")}.` : ""].join("")) : null,
    h("p", { class: "muted-text small inc-foot" }, `${out.rule.text} Read from the table's Delta log; no Fabric job. Alerts only; nothing in the table changes.`)];
}

registerFeature({
  id: "dataHealth", preview: true,
  commands: [
    { label: "Data health", icon: "gauge", keywords: "monitor data health freshness volume anomaly schema drift delta log observability",
      run: () => { monitorPage.panel = "data_health"; go("monitor"); } },
  ],
});
