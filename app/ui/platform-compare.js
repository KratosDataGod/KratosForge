// Optimize > Cost and savings: Compare platforms for one workload (Preview). What one workload used on
// this PC, in Fabric, in Databricks and in Snowflake, each in its own unit (time, CU, DBU, credits),
// never converted into each other (engine compare_platforms, savings/compare.py). Money shows only
// under a pricing scenario, badged Scenario, and the closing line compares money only between
// platforms priced under the same version: it never says "savings" and never recommends one.
// Refresh reads only what's on this PC. Reading Databricks billing or Snowflake query history runs
// a query on a warehouse that may start or resume, so each asks first (databricks_billing,
// snowflake_query_credits), as the Databricks DBUs section does.
// Mockup and UX review: ux/cost-comparison (README "UX review (2026-11-24)").

const platformCompare = { workload: "", days: 30, pricing: "", reading: "", load: 0 };

const PCMP_SUBTITLE = "What one workload used on each platform, in each platform's own unit. Units aren't converted into each other. This isn't an invoice, and a lower figure isn't a recommendation.";
const PCMP_FOOTNOTE = "Click a figure to see how it was worked out. A run elsewhere counts only when it's matched to this workload, by definition for Fabric, by a job you link for Databricks or by a query tag for Snowflake. A price isn't a charge.";
const PCMP_UNIT = {
  pc: "Wall-clock time measured on this PC. Not converted to CU, DBU or credits.",
  fabric: "Fabric CU seconds. Not converted to DBU or credits.",
  databricks: "Databricks DBU. Not converted to CU or credits.",
  snowflake: "Snowflake credits. Not converted to CU or DBU.",
};
const PCMP_WHY = {
  databricks: "Billed usage, read from Databricks' system.billing.usage.",
  snowflake: "Worked out from the assumptions below, not read from Snowflake's billing.",
};
const PCMP_PER = { pc: "time per run", fabric: "CU per run", databricks: "DBU per run", snowflake: "credits per run" };

function platformCompareSection() {
  const box = h("section", { class: "card pad xcmp", "aria-label": "Compare platforms for one workload" }, pcmpHead(),
    loading("Reading this PC's runs…"));
  pcmpLoad(box);
  return box;
}

function pcmpHead() {
  return h("div", { class: "xcmp-head" },
    h("div", { class: "grow" }, h("h2", {}, "Compare platforms for one workload ", previewTag()),
      h("p", { class: "muted-text small xcmp-sub" }, PCMP_SUBTITLE)),
    btn("Refresh", "sync", (ev) => pcmpLoad(ev.currentTarget.closest(".xcmp")), "tool outline small"));
}

async function pcmpLoad(box) {
  if (!box) return;
  const mine = ++platformCompare.load;
  const params = { workload: platformCompare.workload, days: platformCompare.days, pricing_version: platformCompare.pricing };
  let res;
  try {
    res = await rpc("compare_platforms", params);
  } catch (err) {
    if (mine === platformCompare.load && box.isConnected) box.replaceChildren(pcmpHead(), h("p", { class: "muted-text small" }, friendlyError(err).text));
    return;
  }
  if (mine !== platformCompare.load || !box.isConnected) return;
  box.replaceChildren(...pcmpBody(res));
}

// The Cost tab may have been drawn again while a read ran: reload whichever section is on screen.
function pcmpRedraw(box) {
  pcmpLoad(box?.isConnected ? box : document.querySelector(".xcmp"));
}

function pcmpBody(res) {
  const head = pcmpHead();
  if (!res.workload) {
    return [head, h("p", { class: "muted-text small" }, "No workload has run yet. Run a table of your metadata project on this PC or in Fabric, and it shows here.")];
  }
  platformCompare.workload = res.workload;
  const cmp = res.comparison;
  const scen = cmp.scenario;
  if (!scen) platformCompare.pricing = "";
  const reload = (ev) => pcmpLoad(ev.target.closest(".xcmp"));
  const controls = h("div", { class: "xcmp-controls" },
    h("label", { class: "field" }, "Workload", h("select", { "aria-label": "Workload", onchange: (e) => { platformCompare.workload = e.target.value; reload(e); } },
      res.workloads.map((w) => h("option", { value: w.key, selected: w.key === res.workload }, w.name)))),
    h("label", { class: "field" }, "Runs", h("select", { "aria-label": "Runs", onchange: (e) => { platformCompare.days = Number(e.target.value); reload(e); } },
      [[7, "Average per run, last 7 days"], [30, "Average per run, last 30 days"]].map(([d, t]) => h("option", { value: d, selected: d === res.days }, t)))),
    h("label", { class: "field" }, "Pricing scenario", h("select", { "aria-label": "Pricing scenario", onchange: (e) => { platformCompare.pricing = e.target.value; reload(e); } },
      h("option", { value: "", selected: !scen }, "None. Native units only"),
      res.scenarios.map((s) => h("option", { value: s.pricing_version, selected: scen?.pricing_version === s.pricing_version }, s.scenario)))));
  const meta = h("div", { class: "row xcmp-scen" },
    scen ? h("span", { class: "muted-text small grow" }, "Version ", h("span", { class: "mono" }, scen.pricing_version),
      scen.source ? ` · ${scen.source}` : "", " · ", h("span", { class: "pill-s neu" }, pcmpConfidence(scen.confidence)))
      : h("span", { class: "grow" }),
    btn("Prices", null, () => pcmpPricesDialog(res), "tool outline small"));
  const closing = cmp.comparison
    ? h("div", { class: "xcmp-line scenario" }, h("span", { class: "pill-s cus" }, "Scenario"), h("span", {}, cmp.comparison.text))
    : h("div", { class: "xcmp-line" }, h("b", {}, scen ? "Nothing compared" : "No money shown"), h("span", {}, cmp.note));
  return [head, controls, meta,
    h("div", { class: "xcmp-grid" }, cmp.cards.map((c) => pcmpCard(c, res))),
    closing,
    h("p", { class: "muted-text small" }, PCMP_FOOTNOTE)];
}

function pcmpConfidence(c) {
  return { confirmed: "Confirmed", estimated: "Estimated", assumption: "Assumption" }[c] || "Assumption";
}

// "today 08:55", or the date and time when it wasn't today.
function pcmpReadAt(at) {
  const d = readDate(at);
  if (!d) return "";
  return d.toDateString() === new Date().toDateString() ? `today ${timeText(at)}` : dateTimeText(at);
}

function pcmpSource(c) {
  const f = c.figure;
  // Only Databricks and Snowflake figures are read from somewhere; Fabric's come from its runs.
  const at = c.platform === "databricks" || c.platform === "snowflake" ? pcmpReadAt(c.read_at || f.collected_at) : "";
  const source = f.source || (at ? { databricks: "Billed usage, system.billing.usage", snowflake: "QUERY_HISTORY" }[c.platform] : "");
  return source ? `${source}${at ? `, read ${at}` : ""}` : "";
}

function pcmpCard(c, res) {
  const f = c.figure;
  const off = c.status === "not_connected";
  const head = h("div", { class: "ph" }, h("b", {}, c.label), off ? h("span", { class: "pill-s neu" }, "Not connected") : null,
    h("span", { class: "unit" }, c.unit_label));
  const body = [];
  if (off) {
    body.push(h("div", { class: "notconn" }, h("span", {}, f.reason), h("span", {}, c.connect),
      h("span", {}, btn("Open Connections", null, () => { state.settingsTab = "connections"; go("settings"); }, "tool outline small"))));
  } else {
    body.push(h("div", { class: "matched" }, c.matched_by || "", pcmpLinks(c, res)));
    body.push(h("div", { class: "fig" }, h("div", { class: "k" }, "Per run"),
      h("button", { class: "measure-fig", title: "How this was worked out", onclick: (ev) => { ev.stopPropagation(); pcmpDialog(c, res); } },
        h("span", { class: `v${f.value === null ? " unknown" : ""}` }, f.text), " ", measureBadge(f)),
      c.sub ? h("div", { class: "alt" }, c.sub) : null));
    if (f.value === null) body.push(h("div", { class: "why notknown" }, f.reason));
    const rows = [];
    for (const d of c.details || []) rows.push([d.label, [d.text, " ", d.classification === "measured" ? measureBadge(d) : null]]);
    if (c.input) rows.push(["Input", c.input]);
    if (f.value !== null && c.platform !== "pc" && f.method) rows.push(["Method", f.method[0].toUpperCase() + f.method.slice(1)]);
    if (f.value !== null || c.read_at) rows.push(["Source", pcmpSource(c) || "Not read yet"]);
    body.push(h("dl", {}, rows.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])));
    if (c.status !== "not_linked" && (c.platform === "databricks" || c.platform === "snowflake")) body.push(pcmpReader(c, res));
  }
  return h("div", { class: `plat${off ? " off" : ""}`, "data-platform": c.platform }, head, h("div", { class: "pbody" }, body), pcmpMoney(c));
}

function pcmpMoney(c) {
  const m = c.money;
  if (!m) return null;
  if (m.status === "not_priced") return h("div", { class: "money" }, h("div", { class: "k" }, "Money"), h("div", {}, m.text));
  const k = h("div", { class: "k" }, "Money at this scenario");
  if (m.status === "priced") {
    return h("div", { class: "money" }, k, h("div", {}, h("span", { class: "v" }, m.text), " ", measureBadge(m)), h("div", { class: "muted-text" }, m.price_line));
  }
  if (m.status === "no_figure") return h("div", { class: "money" }, k, h("div", {}, m.text));
  return h("div", { class: "money" }, k, h("div", {}, h("span", { class: "v" }, "Not known"), " ", measureBadge(m)), h("div", { class: "muted-text" }, m.reason));
}

// Linked jobs and tags, with Unlink; or the box to link one when there's none.
function pcmpLinks(c, res) {
  if (c.platform !== "databricks" && c.platform !== "snowflake") return null;
  const dbx = c.platform === "databricks";
  const linked = dbx ? res.links.databricks.map((j) => j.job_id) : res.links.snowflake;
  const unlink = linked.map((ref) => btn(`Unlink ${dbx ? `job ${ref}` : ref}`, null, async (ev) => {
    const box = ev.currentTarget.closest(".xcmp");
    if (await attempt(() => rpc("compare_unlink", { workload: res.workload, platform: c.platform, ref }), "Not unlinked")) pcmpLoad(box);
  }, "tool outline small"));
  if (c.status !== "not_linked") return h("div", { class: "xcmp-links" }, unlink);
  const ref = h("input", { "aria-label": dbx ? "Databricks job id" : "Snowflake query tag", placeholder: dbx ? "Job id, such as 8841" : "Query tag, such as kf:daily-sales", spellcheck: "false" });
  const name = dbx ? h("input", { "aria-label": "Job name", placeholder: "Job name (optional)", spellcheck: "false" }) : null;
  return h("div", { class: "xcmp-link" }, ref, name,
    btn(dbx ? "Link job" : "Link tag", null, async (ev) => {
      const box = ev.currentTarget.closest(".xcmp");
      const params = { workload: res.workload, platform: c.platform, ref: ref.value.trim(), name: name ? name.value.trim() : "" };
      if (await attempt(() => rpc("compare_link", params), "Not linked")) pcmpLoad(box);
    }, "tool outline small"));
}

// Read Databricks billing… or Read Snowflake query history…: asks first, naming the warehouse.
function pcmpReader(c, res) {
  const dbx = c.platform === "databricks";
  const label = dbx ? "Read Databricks billing…" : "Read Snowflake query history…";
  const busy = platformCompare.reading === c.platform;
  const note = dbx
    ? "Reading runs one query on a SQL warehouse you pick, which may bill a few DBUs."
    : "Reading runs one query on the warehouse, which resumes it if it's suspended and uses credits while it runs.";
  const last = c.read_at ? `Last read ${pcmpReadAt(c.read_at)}. ` : "";
  return h("div", { class: "xcmp-read" },
    btn(busy ? "Reading…" : label, null, (ev) => pcmpRemoteRead(ev.currentTarget.closest(".xcmp"), c.platform, res), "tool outline small",
      { disabled: busy || null }),
    h("p", { class: "muted-text small" }, last, note));
}

async function pcmpRemoteRead(box, platform, res) {
  const dbx = platform === "databricks";
  const conn = (res.connections[platform] || []).find((x) => x.key === (dbx ? res.databricks_connection : res.snowflake_connection)) || {};
  const method = dbx ? "databricks_billing" : "snowflake_query_credits";
  const params = dbx
    ? { connection: conn.key, warehouse_id: conn.warehouse_id || "", days: res.days, job_ids: res.links.databricks.map((j) => j.job_id) }
    : { connection: conn.key, warehouse: conn.warehouse || "", days: res.days, tags: res.links.snowflake };
  const what = dbx ? "Billing" : "Query history";
  const ask = await attempt(() => rpc(method, params), "Couldn't check the warehouse");
  if (!ask) return;
  if (ask.refused) { toast(`${what} wasn't read: ${ask.error}`, true); return; }
  if (ask.needs_confirm) {
    const w = ask.warehouse || {};
    const name = w.name || w.id || params.warehouse_id || params.warehouse;
    const text = dbx
      ? `This runs one query on the SQL warehouse ${name}. If it is stopped it starts, and it bills DBUs while it runs.`
      : `This runs one query on the Snowflake warehouse ${name}. If it is suspended it resumes, and it uses credits while it runs.`;
    const ok = await confirmDialog(dbx ? `Read billing on ${name}?` : `Read query history on ${name}?`, text, "Run the query", { danger: true });
    if (!ok) return;
    platformCompare.reading = platform;
    pcmpRedraw(box);
    const out = await attempt(() => rpc(method, { ...params, confirmed: true }), dbx ? "Couldn't read billing" : "Couldn't read query history");
    platformCompare.reading = "";
    if (out && !out.ok) toast(`${what} wasn't read: ${out.error}`, true);
    else if (out?.runs_error) toast(out.runs_error, true);
  } else if (!ask.ok) {
    toast(`${what} wasn't read: ${ask.error}`, true);
  }
  pcmpRedraw(box);
}

// How this was worked out: the measure dialog, with the unit, runs, how runs were matched and the money.
function pcmpDialog(c, res) {
  const f = c.figure;
  const cmp = res.comparison;
  const m = c.money;
  const money = m && m.status === "priced" ? [h("p", { class: "text" }, h("b", {}, "Money at this scenario")),
    h("p", { class: "text" }, m.text, " ", measureBadge(m), " ",
      `${m.price_line}, from ${cmp.scenario.scenario} (version ${m.pricing_version})${cmp.scenario.source ? `, ${cmp.scenario.source}` : ""}.`)]
    : m && m.status !== "not_priced" ? [h("p", { class: "text" }, h("b", {}, "Money at this scenario")), h("p", { class: "text" }, m.reason || m.text)] : null;
  measureDialog(`${c.label}: ${PCMP_PER[c.platform]}`, f, "", {
    value: f.text, unit: PCMP_UNIT[c.platform], why: f.classification !== "unknown" ? PCMP_WHY[c.platform] : "",
    source: pcmpSource(c), runs: f.runs_total ? `${f.runs} of ${f.runs_total}` : "", matched: c.matched_by, money,
    note: c.note || (c.platform === "fabric" ? null : "A price isn't a charge."),
  });
}

// Prices: a pricing scenario with a rate per CU hour, per DBU by SKU and per Snowflake credit, as one version.
function pcmpPricesDialog(res) {
  const cur = res.comparison?.scenario || res.scenarios.at(-1) || {};
  const field = (label, attrs) => { const i = h("input", { "aria-label": label, spellcheck: "false", ...attrs }); return [i, h("label", { class: "field" }, label, i)]; };
  const [name, nameF] = field("Name", { value: cur.scenario && cur.scenario !== "Default price" ? cur.scenario : "", placeholder: "List prices, Nov 2026" });
  const [currency, curF] = field("Currency", { value: cur.currency || "USD", maxlength: 3, size: 4 });
  const [cu, cuF] = field("Rate per CU hour", { value: cur.rate_per_cu_hour ?? "", inputmode: "decimal" });
  const [dbu, dbuF] = field("Rate per DBU, by SKU", { value: Object.entries(cur.rate_per_dbu || {}).map(([k, v]) => `${k}=${v}`).join(", "), placeholder: "JOBS_COMPUTE=0.15" });
  const [credit, creditF] = field("Rate per Snowflake credit", { value: cur.rate_per_credit ?? "", inputmode: "decimal" });
  const [source, sourceF] = field("Source", { value: cur.source || "", placeholder: "Each platform's public price page" });
  const save = btn("Save", "save", async () => {
    const rates = {};
    for (const part of dbu.value.split(",").map((x) => x.trim()).filter(Boolean)) {
      const [sku, rate] = part.split("=").map((x) => x.trim());
      rates[sku] = Number(rate);
    }
    const params = { scenario: name.value.trim(), currency: currency.value.trim().toUpperCase(), rate_per_cu_hour: Number(cu.value),
      rate_per_dbu: rates, rate_per_credit: credit.value.trim() ? Number(credit.value) : null, source: source.value.trim() };
    const out = await attempt(() => rpc("compare_pricing_save", params), "Prices not saved");
    if (!out) return;
    closeDialog();
    platformCompare.pricing = out.pricing_version;
    pcmpRedraw(null);
  }, "tool primary");
  showDialog("Prices for the comparison", h("div", { class: "xcmp-prices" },
    h("p", { class: "muted-text small" }, "One scenario holds a rate for each platform's own unit, so every figure in one comparison is priced under one version. Saving the same name again replaces it. A price isn't a charge."),
    nameF, curF, cuF, dbuF, creditF, sourceF),
  [btn("Cancel", null, closeDialog, "tool outline"), save]);
}
