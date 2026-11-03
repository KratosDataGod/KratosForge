// Optimize > Cost: Databricks DBUs billed (Preview). Billing is a system table, so reading it runs
// one query on a SQL warehouse you pick, which may start and bill a few DBUs: Read billing asks first,
// naming the warehouse and its state (engine databricks_billing). DBUs stay DBUs: never added to
// Fabric CU or Snowflake credits, never turned into money. The last read stays on this PC. A profile
// whose execution policy is "never" refuses; one that allows Databricks SQL runs without the confirm.
// Mockup: https://claude.ai/artifact/XgpknxChxChHfHTKHKHugD (Cost)

const dbxCost = { connection: "", days: 7, reading: false };
const DBX_KIND = { job: "Job", pipeline: "Lakeflow pipeline", warehouse: "SQL warehouse", cluster: "Cluster", other: "Other" };

function databricksCostSection() {
  const box = h("section", { class: "card pad dbx-cost", "aria-label": "Databricks DBUs" },
    h("h2", {}, "Databricks DBUs ", previewTag()), loading("Reading Databricks connections…"));
  dbxCostLoad(box);
  return box;
}

async function dbxCostLoad(box) {
  const found = await rpc("engine_connections").catch(() => ({ connections: [] }));
  const conns = (found.connections || []).filter((c) => c.host);
  const head = h("h2", {}, "Databricks DBUs ", previewTag());
  if (!conns.length) {
    box.replaceChildren(head, h("p", { class: "muted-text small" }, "No Databricks connection on this PC. Add one on Settings > Connections to read its billing."));
    return;
  }
  if (!conns.some((c) => c.key === dbxCost.connection)) dbxCost.connection = conns[0].key;
  const conn = conns.find((c) => c.key === dbxCost.connection);
  const last = await rpc("databricks_billing_last", { connection: dbxCost.connection }).catch(() => ({}));
  if (!box.isConnected) return;
  const warehouse = h("input", { "aria-label": "SQL warehouse id", value: conn.warehouse_id || "", placeholder: "warehouse id", spellcheck: "false" });
  box.replaceChildren(head,
    h("p", { class: "muted-text small" }, "DBUs from system.billing.usage. They are never added to Fabric CU or Snowflake credits."),
    h("div", { class: "row wrap" },
      h("label", { class: "field" }, "Connection", h("select", { "aria-label": "Databricks connection", onchange: (e) => { dbxCost.connection = e.target.value; dbxCostLoad(box); } },
        conns.map((c) => h("option", { value: c.key, selected: c.key === dbxCost.connection }, c.label)))),
      h("label", { class: "field" }, "SQL warehouse", warehouse),
      h("label", { class: "field" }, "Last", h("select", { "aria-label": "Billing days", onchange: (e) => { dbxCost.days = Number(e.target.value); } },
        [[1, "24 hours"], [7, "7 days"], [30, "30 days"]].map(([d, t]) => h("option", { value: d, selected: d === dbxCost.days }, t)))),
      btn(dbxCost.reading ? "Reading…" : "Read Databricks billing…", null, () => dbxCostRead(box, warehouse.value.trim()), "tool outline",
        { disabled: dbxCost.reading || null })),
    last?.rows ? dbxCostTable(last) : h("p", { class: "muted-text small" }, "Not read yet on this PC."));
}

async function dbxCostRead(box, warehouseId) {
  const params = { connection: dbxCost.connection, warehouse_id: warehouseId, days: dbxCost.days };
  const ask = await attempt(() => rpc("databricks_billing", params), "Couldn't check the warehouse");
  if (!ask) return;
  // The profile's execution policy answers first: "never" refuses, and an allowed engine runs without asking.
  if (ask.refused) { toast(`Billing wasn't read: ${ask.error}`, true); return; }
  if (!ask.needs_confirm) {
    if (!ask.ok) toast(`Billing wasn't read: ${ask.error}`, true);
    if (box.isConnected) dbxCostLoad(box);
    return;
  }
  const w = ask.warehouse || {};
  const state = w.state ? ` It is ${w.state.toLowerCase()} now${w.auto_stop_mins ? ` and stops after ${w.auto_stop_mins} minutes idle` : ", with no auto-stop"}.` : "";
  const ok = await confirmDialog(`Read billing on ${w.name || w.id || warehouseId}?`,
    `This runs one query on the SQL warehouse ${w.name || w.id || warehouseId}. If it is stopped it starts, and it bills DBUs while it runs.${state}${ask.error ? ` (Its state couldn't be read: ${ask.error})` : ""}`,
    "Run the query", { danger: true });
  if (!ok) return;
  dbxCost.reading = true;
  dbxCostLoad(box);
  const out = await attempt(() => rpc("databricks_billing", { ...params, confirmed: true }), "Couldn't read billing");
  dbxCost.reading = false;
  if (out && !out.ok) toast(`Billing wasn't read: ${out.error}`, true);
  if (box.isConnected) dbxCostLoad(box);
}

function dbxCostTable(r) {
  return h("div", { class: "dbx-cost-result" },
    h("p", { class: "small" }, h("strong", {}, `${Number(r.total_dbu).toLocaleString()} DBU`),
      ` in the last ${r.days === 1 ? "24 hours" : `${r.days} days`} · read ${relTime(r.read_at)} on ${r.warehouse_id}`),
    r.rows.length ? h("div", { class: "table-wrap" }, h("table", { class: "list dbx-dbu" },
      h("thead", {}, h("tr", {}, h("th", {}, "What"), h("th", {}, "SKU"), h("th", {}, "DBU"))),
      h("tbody", {}, r.rows.map((x) => h("tr", {}, h("td", {}, `${DBX_KIND[x.kind] || x.kind} ${x.id}`), h("td", {}, x.sku),
        h("td", { class: "num" }, Number(x.dbu).toLocaleString())))))) : h("p", { class: "muted-text small" }, "No DBUs billed in that time."));
}
