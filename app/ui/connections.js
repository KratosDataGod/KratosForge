// Settings > Connections (Preview): the connection profiles on this PC, and adding a Databricks CLI
// profile or a Snowflake CLI connection to one. The engine reads the host or account and the sign-in
// from the CLI's own file (engine_connection_save); this page only names which one, so it never
// handles a token. "Check workspace" is discovery: GET requests only, so no warehouse or cluster
// starts and nothing bills DBUs. Mockup: https://claude.ai/artifact/XgpknxChxChHfHTKHKHugD

const cx = { data: null, platform: "databricks", cli: "", found: null, checking: false, pick: { warehouse: "", cluster: "" },
  catalog: "", schema: "", target: "", newName: "" };

const CX_KIND = { local: "This PC only", fabric: "Fabric", databricks: "Databricks", snowflake: "Snowflake", hybrid: "Hybrid" };
const CX_PLATFORM = { fabric: "Fabric", databricks: "Databricks", snowflake: "Snowflake" };

async function renderConnectionsTab(el) {
  el.replaceChildren(h("p", { class: "muted-text small" }, "Reading connection profiles…"));
  const data = await attempt(() => rpc("engine_profiles"), "Couldn't read the connection profiles");
  if (!data) {
    el.replaceChildren(h("p", { class: "muted-text small" }, "The connection profiles couldn't be read."),
      btn("Try again", "sync", () => renderConnectionsTab(el), "tool outline"));
    return;
  }
  cx.data = data;
  if (!cx.target || (cx.target !== "new" && !data.profiles.some((p) => p.id === cx.target))) cx.target = data.active || "default";
  cxDraw(el);
}

function cxDraw(el) {
  el.replaceChildren(h("div", { class: "cx-page" },
    h("p", { class: "muted-text small" }, "A profile keeps references only: which CLI profile or connection signs in, and which compute and catalog to use. ",
      "Tokens stay with the Databricks and Snowflake CLIs."),
    h("div", { class: "cx-cols" }, cxProfiles(el), cxAdd(el))));
}

function cxProfiles(el) {
  return h("section", { class: "card pad cx-profiles", "aria-label": "Profiles" },
    h("div", { class: "row" }, h("h2", { class: "grow" }, "Profiles"),
      btn("Refresh", "sync", () => renderConnectionsTab(el), "tool outline small")),
    cx.data.profiles.map((p) => h("div", { class: `cx-profile${p.active ? " active" : ""}` },
      h("div", { class: "row" }, h("strong", { class: "grow" }, p.name),
        h("span", { class: "pill-s neu" }, CX_KIND[p.kind] || p.kind || "Can't be read"),
        p.active ? h("span", { class: "pill-s info" }, "In use") : null),
      p.problems.length ? h("ul", { class: "plain small cx-problems" }, p.problems.map((x) => h("li", {}, x))) : null,
      p.connections.length ? h("ul", { class: "plain cx-conns" }, p.connections.map((c) => h("li", { class: "row" },
        h("div", { class: "grow" },
          h("div", {}, `${CX_PLATFORM[c.platform] || c.platform}${c.label ? ` · ${c.label}` : ""}`),
          h("div", { class: "muted-text small mono" }, cxWhere(c))),
        c.platform === "fabric" ? null : btn("Remove", null, () => cxRemove(el, p, c), "tool outline small",
          { "aria-label": `Remove ${c.id} from ${p.name}` }))))
        : h("p", { class: "muted-text small" }, "No platform connections: runs stay on this PC."))));
}

function cxWhere(c) {
  if (c.platform === "fabric") return "The app's own sign-in";
  const at = c.platform === "databricks" ? (c.host || "").replace(/^https:\/\//, "") : c.account;
  const parts = [at, c.cli ? `CLI ${c.cli}` : c.method];
  if (c.warehouse_id) parts.push(`warehouse ${c.warehouse_id}`);
  if (c.cluster_id) parts.push(`cluster ${c.cluster_id}`);
  if (c.catalog) parts.push(c.schema ? `${c.catalog}.${c.schema}` : c.catalog);
  return parts.filter(Boolean).join(" · ");
}

async function cxRemove(el, p, c) {
  const ok = await confirmDialog(`Remove ${c.id} from ${p.name}?`,
    `Notebooks can't pick this connection until you add it again; a notebook already running on it keeps its session until you close it or switch engines. The ${CX_PLATFORM[c.platform]} CLI's own ${c.platform === "databricks" ? "profile" : "connection"} stays as it is.`,
    "Remove", { danger: true });
  if (!ok) return;
  if (await attempt(() => rpc("engine_connection_remove", { profile: p.id, connection: c.id }), "Couldn't remove the connection")) {
    toast(`Removed ${c.id} from ${p.name}.`);
    renderConnectionsTab(el);
  }
}

function cxAdd(el) {
  const switcher = h("div", { class: "seg", role: "group", "aria-label": "Platform" },
    ["databricks", "snowflake"].map((id) => h("button", { class: cx.platform === id ? "on" : "", "aria-pressed": String(cx.platform === id),
      onclick: () => { cx.platform = id; cx.cli = ""; cx.found = null; cxDraw(el); } }, CX_PLATFORM[id])));
  return h("section", { class: "card pad cx-add", "aria-label": "Add a connection" },
    h("h2", {}, "Add a connection"), switcher,
    cx.platform === "databricks" ? cxDatabricks(el) : cxSnowflake(el));
}

function cxCliSelect(el, entries, nameOf, label) {
  return h("label", { class: "field" }, label,
    h("select", { "aria-label": label, onchange: (e) => { cx.cli = e.target.value; cx.found = null; cx.pick = { warehouse: "", cluster: "" }; cxDraw(el); } },
      h("option", { value: "", selected: !cx.cli }, "Pick one…"),
      entries.map((e) => h("option", { value: nameOf(e), selected: cx.cli === nameOf(e) }, nameOf(e)))));
}

function cxDatabricks(el) {
  const cli = cx.data.databricks_cli || {};
  const usable = (cli.profiles || []).filter((p) => p.host);
  if (!usable.length) {
    return h("div", { class: "cx-empty" },
      h("p", {}, cli.error || (cli.found ? "The Databricks CLI's file has no profile with a workspace." : "There's no Databricks CLI profile on this PC yet.")),
      h("p", { class: "muted-text small" }, "Sign in once in a terminal, then Refresh:"),
      h("pre", { class: "mono small" }, "databricks auth login --host https://adb-<id>.azuredatabricks.net --profile dev"));
  }
  const entry = usable.find((p) => p.name === cx.cli);
  return h("div", { class: "cx-form" },
    cxCliSelect(el, usable, (p) => p.name, "Databricks CLI profile"),
    entry ? h("p", { class: "muted-text small mono" }, entry.host) : null,
    entry?.warning ? h("div", { class: "callout git-warn small", role: "note" }, entry.warning) : null,
    entry ? h("div", { class: "row" }, btn(cx.checking ? "Checking…" : "Check workspace", "search", () => cxCheck(el), "tool outline", { disabled: cx.checking || null }),
      h("span", { class: "muted-text small" }, "Reads only: nothing starts and nothing bills DBUs.")) : null,
    cx.found ? cxFound(el) : null,
    entry ? cxSaveRow(el) : null);
}

async function cxCheck(el) {
  cx.checking = true;
  cxDraw(el);
  const found = await attempt(() => rpc("engine_discover", { connection: `cli:${cx.cli}` }), "Couldn't check the workspace");
  cx.checking = false;
  if (found) {
    cx.found = found;
    cx.pick = { warehouse: found.suggested?.warehouse_id || "", cluster: found.suggested?.cluster_id || "" };
    cx.catalog = found.parts?.metastore?.value?.default_catalog || cx.catalog;
  }
  cxDraw(el);
}

function cxPart(name) {
  const part = cx.found.parts?.[name];
  return part?.ok ? part.value : null;
}

function cxPartError(name) {
  const part = cx.found.parts?.[name];
  return part && !part.ok && !part.skipped ? h("p", { class: "muted-text small cx-part-error" }, part.error) : null;
}

function cxFound(el) {
  const f = cx.found;
  if (!f.signed_in) {
    return h("div", { class: "callout git-warn small", role: "alert" },
      f.error || f.parts?.identity?.error || "You aren't signed in to this workspace.",
      h("div", { class: "mono" }, `databricks auth login --profile ${cx.cli}`));
  }
  const who = cxPart("identity");
  const warehouses = cxPart("warehouses") || [];
  const clusters = cxPart("clusters") || [];
  const catalogs = cxPart("catalogs") || [];
  const radio = (kind, id, label) => h("input", { type: "radio", name: `cx-${kind}`, value: id, checked: cx.pick[kind] === id || null,
    "aria-label": label, onchange: () => { cx.pick[kind] = id; } });
  return h("div", { class: "cx-found" },
    who ? h("p", { class: "small" }, `Signed in as ${who.user}.`) : null,
    h("h3", {}, "SQL warehouse"), cxPartError("warehouses"),
    warehouses.length ? h("div", { class: "table-wrap" }, h("table", { class: "list cx-warehouses" },
      h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "Warehouse"), h("th", {}, "State"), h("th", {}, "Size"), h("th", {}, "Auto-stop"))),
      h("tbody", {}, h("tr", {}, h("td", {}, radio("warehouse", "", "No warehouse")), h("td", { colspan: 4 }, "None")),
        warehouses.map((w) => h("tr", {}, h("td", {}, radio("warehouse", w.id, w.name)), h("td", {}, w.name),
          h("td", {}, w.state), h("td", {}, w.serverless ? `${w.size} · serverless` : w.size),
          h("td", { class: w.auto_stop_mins ? "" : "cx-warn" }, w.auto_stop_mins ? `${w.auto_stop_mins} min` : w.notes[0] || "None"))))))
      : cxPartError("warehouses") ? null : h("p", { class: "muted-text small" }, "No warehouses you can see."),
    h("h3", {}, "Cluster for Databricks Connect"), cxPartError("clusters"),
    clusters.length ? h("div", { class: "table-wrap" }, h("table", { class: "list cx-clusters" },
      h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "Cluster"), h("th", {}, "State"), h("th", {}, "Runtime"), h("th", {}, "Databricks Connect"))),
      h("tbody", {}, h("tr", {}, h("td", {}, radio("cluster", "", "No cluster")), h("td", { colspan: 4 }, "None")),
        clusters.map((c) => h("tr", {}, h("td", {}, c.connect.usable ? radio("cluster", c.id, c.name) : null), h("td", {}, c.name),
          h("td", {}, c.state), h("td", {}, c.spark_version),
          h("td", { class: c.connect.usable ? "" : "cx-warn" }, c.connect.usable ? (c.auto_stop_mins ? `Works · ${c.access_mode}` : c.notes[0]) : c.connect.problems.join(" ")))))))
      : cxPartError("clusters") ? null : h("p", { class: "muted-text small" }, "No all-purpose clusters you can see."),
    h("h3", {}, "Catalog and schema"), cxPartError("catalogs"),
    h("div", { class: "row" },
      catalogs.length ? h("label", { class: "field" }, "Catalog", h("select", { "aria-label": "Catalog", onchange: (e) => { cx.catalog = e.target.value; } },
        h("option", { value: "", selected: !cx.catalog }, "None"),
        catalogs.map((c) => h("option", { value: c.name, selected: cx.catalog === c.name }, c.name))))
        : h("label", { class: "field" }, "Catalog", h("input", { "aria-label": "Catalog", value: cx.catalog, oninput: (e) => { cx.catalog = e.target.value.trim(); } })),
      h("label", { class: "field" }, "Schema", h("input", { "aria-label": "Schema", value: cx.schema, placeholder: "default", oninput: (e) => { cx.schema = e.target.value.trim(); } }))));
}

function cxSnowflake(el) {
  const sf = cx.data.snowflake_cli || {};
  const conns = sf.connections || [];
  if (!conns.length) {
    return h("div", { class: "cx-empty" },
      h("p", {}, sf.error || "There's no Snowflake CLI connection on this PC yet."),
      h("p", { class: "muted-text small" }, "Add one with the Snowflake CLI, then Refresh:"),
      h("pre", { class: "mono small" }, "snow connection add"));
  }
  const entry = conns.find((c) => c.name === cx.cli);
  return h("div", { class: "cx-form" },
    cxCliSelect(el, conns, (c) => c.name, "Snowflake CLI connection"),
    entry ? h("p", { class: "muted-text small mono" }, [entry.account, entry.user, entry.warehouse, entry.database && `${entry.database}${entry.schema ? `.${entry.schema}` : ""}`].filter(Boolean).join(" · ")) : null,
    (entry?.warnings || []).map((w) => h("div", { class: "callout git-warn small", role: "note" }, w)),
    (sf.notes || []).map((n) => h("p", { class: "muted-text small" }, n)),
    entry ? h("p", { class: "muted-text small" }, "Checking a Snowflake account from here comes later; the connection is saved by name.") : null,
    entry ? cxSaveRow(el) : null);
}

function cxSaveRow(el) {
  const profiles = cx.data.profiles.filter((p) => !p.problems.length);
  return h("div", { class: "cx-save" },
    h("div", { class: "row" },
      h("label", { class: "field" }, "Save to", h("select", { "aria-label": "Save to profile", onchange: (e) => { cx.target = e.target.value; cxDraw(el); } },
        profiles.map((p) => h("option", { value: p.id, selected: cx.target === p.id }, p.name)),
        h("option", { value: "new", selected: cx.target === "new" }, "New profile…"))),
      cx.target === "new" ? h("label", { class: "field" }, "Profile name",
        h("input", { "aria-label": "New profile name", maxlength: 80, value: cx.newName, oninput: (e) => { cx.newName = e.target.value; } })) : null),
    btn("Save connection", null, () => cxSave(el), "tool primary"));
}

async function cxSave(el) {
  const params = cx.platform === "databricks"
    ? { databricks_cli: cx.cli, warehouse_id: cx.pick.warehouse, cluster_id: cx.pick.cluster, catalog: cx.catalog, schema: cx.schema }
    : { snowflake_cli: cx.cli };
  if (cx.target === "new") params.profile_name = cx.newName.trim();
  else params.profile = cx.target;
  const saved = await attempt(() => rpc("engine_connection_save", params), "Couldn't save the connection");
  if (!saved) return;
  toast(`Saved ${saved.connection}.`);
  Object.assign(cx, { cli: "", found: null, pick: { warehouse: "", cluster: "" }, catalog: "", schema: "", newName: "", target: saved.profile });
  renderConnectionsTab(el);
}
