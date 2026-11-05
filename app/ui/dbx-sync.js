// Workspaces > Databricks (Preview): folders of notebooks on this PC paired with a Databricks
// workspace folder (engine databricks_sync_*). Compare reads only. Push writes with the Workspace API
// as you, never over a file changed in Databricks since the last sync (that's a conflict), and a
// delete is off unless ticked and asks again. A .ipynb goes across as Databricks source, and what it
// loses is listed before you push. Git folders are read only here.
// Mockup: https://claude.ai/artifact/XgpknxChxChHfHTKHKHugD (Sync)

const dbxSync = { pair: "", compare: null, push: new Set(), del: new Set(), pull: new Set(), adding: false, busy: false };
const DBX_SYNC_ACTION = { push: "info", conflict: "fail", delete: "wrn", blocked: "fail", none: "neu" };

async function renderDatabricksSync() {
  const body = h("div", { class: "dbx-sync" }, loading("Reading synced folders…"));
  setView(h("div", { class: "page" }, h("h1", {}, "Workspaces"), workspacesTabs("databricks_sync"),
    pageIntro("Folders of notebooks on this PC kept in step with a Databricks workspace folder. Comparing only reads; ",
      "pushing writes as you and never overwrites a change made in Databricks. ", learnMore("workspaces")),
    body));
  const [pairs, conns] = await Promise.all([
    attempt(() => rpc("databricks_sync_pairs"), "Couldn't read the synced folders"),
    rpc("engine_connections").catch(() => ({ connections: [] }))]);
  if (state.view !== "databricks_sync") return;
  if (!pairs) { body.replaceChildren(h("p", { class: "muted-text" }, "The synced folders couldn't be read.")); return; }
  const list = pairs.pairs || [];
  if (!list.some((p) => p.id === dbxSync.pair)) { dbxSync.pair = list[0]?.id || ""; dbxSync.compare = null; }
  body.replaceChildren(h("div", { class: "dbx-sync-cols" },
    h("section", { class: "card pad dbx-pairs", "aria-label": "Synced folders" },
      h("h2", {}, "Synced folders"),
      list.map((p) => h("button", { class: `dbx-pair${p.id === dbxSync.pair ? " on" : ""}`, "aria-pressed": String(p.id === dbxSync.pair),
        onclick: () => { dbxSync.pair = p.id; dbxSync.compare = null; renderDatabricksSync(); } },
        h("strong", {}, p.name), h("span", { class: "muted-text small mono" }, p.local), h("span", { class: "muted-text small mono" }, p.remote))),
      dbxSync.adding ? dbxSyncAddForm((conns.connections || []).filter((c) => c.host)) :
        btn("Pair a folder", null, () => { dbxSync.adding = true; renderDatabricksSync(); }, "tool outline"),
      h("p", { class: "muted-text small" }, "Notebooks go across as Databricks source files (# COMMAND ----------). Git folders in Databricks change through Git, so they can't be paired here.")),
    h("section", { class: "card pad dbx-changes", "aria-label": "Changes" },
      dbxSync.pair ? dbxSyncChanges(list.find((p) => p.id === dbxSync.pair)) : h("p", { class: "muted-text" }, "Pair a folder to see what would change."))));
  if (dbxSync.pair && !dbxSync.compare) dbxSyncCompare();
}

function dbxSyncAddForm(conns) {
  if (!conns.length) {
    return h("p", { class: "small" }, "Add a Databricks connection on Settings > Connections first.");
  }
  const name = h("input", { "aria-label": "Pair name", maxlength: 200, placeholder: "sales-etl" });
  const local = h("input", { "aria-label": "Folder on this PC", placeholder: "C:\\repos\\sales-etl\\notebooks", spellcheck: "false" });
  const remote = h("input", { "aria-label": "Path in the Databricks workspace", placeholder: "/Workspace/Users/you@contoso.com/sales-etl", spellcheck: "false" });
  const conn = h("select", { "aria-label": "Databricks connection" }, conns.map((c) => h("option", { value: c.key }, c.label)));
  return h("form", { class: "dbx-add", onsubmit: async (e) => {
    e.preventDefault();
    const out = await attempt(() => rpc("databricks_sync_pair_save", { name: name.value.trim(), connection: conn.value,
      local: local.value.trim(), remote: remote.value.trim() }), "Couldn't pair the folders");
    if (!out) return;
    Object.assign(dbxSync, { adding: false, pair: out.id, compare: null });
    renderDatabricksSync();
  } },
    h("label", { class: "field" }, "Name", name), h("label", { class: "field" }, "Connection", conn),
    h("label", { class: "field" }, "Folder on this PC", folderField(local, "the folder on this PC")),
    h("label", { class: "field" }, "Path in the Databricks workspace", remote),
    h("div", { class: "row" }, btn("Pair", null, null, "tool primary", { type: "submit" }),
      btn("Cancel", null, () => { dbxSync.adding = false; renderDatabricksSync(); }, "tool outline", { type: "button" })));
}

async function dbxSyncCompare() {
  const out = await attempt(() => rpc("databricks_sync_compare", { pair: dbxSync.pair }), "Couldn't compare with Databricks");
  if (state.view !== "databricks_sync") return;
  dbxSync.compare = out || { rows: [], error: true };
  dbxSync.push = new Set((dbxSync.compare.rows || []).filter((r) => r.action === "push").map((r) => r.file));
  dbxSync.del = new Set();
  dbxSync.pull = new Set();
  renderDatabricksSync();
}

function dbxSyncChanges(pair) {
  const c = dbxSync.compare;
  const head = h("div", { class: "row" }, h("h2", { class: "grow" }, `Changes in ${pair.name}`),
    btn("Compare again", "sync", () => { dbxSync.compare = null; renderDatabricksSync(); }, "tool outline small"),
    btn("Forget this pair", null, () => dbxSyncForget(pair), "tool outline small"));
  if (!c) return [head, loading("Comparing… reading the workspace starts nothing.")];
  if (c.error) return [head, h("p", { class: "muted-text" }, "The workspace couldn't be compared.")];
  const rows = c.rows || [];
  const box = (set, file, label, disabled) => h("input", { type: "checkbox", "aria-label": label, checked: set.has(file) || null, disabled: disabled || null,
    onchange: (e) => { if (e.target.checked) set.add(file); else set.delete(file); renderDatabricksSync(); } });
  const pushN = dbxSync.push.size;
  const delN = dbxSync.del.size;
  return [head,
    c.remote_exists === false ? h("p", { class: "small" }, "The workspace folder isn't there yet: the first push makes it.") : null,
    c.skipped?.length ? h("p", { class: "muted-text small" }, `Left out: ${c.skipped.join(", ")} (a name with odd characters, a link out of the folder, or over 10 MB).`) : null,
    rows.length ? h("div", { class: "table-wrap" }, h("table", { class: "list dbx-sync-table" },
      h("thead", {}, h("tr", {}, ["Push", "File", "This PC", "Databricks", "Note"].map((t) => h("th", {}, t)))),
      h("tbody", {}, rows.map((r) => h("tr", { class: `dbx-${r.action}` },
        h("td", {}, r.action === "push" ? box(dbxSync.push, r.file, `Push ${r.file}`)
          : r.action === "delete" ? box(dbxSync.del, r.file, `Delete ${r.remote} in Databricks`)
            : r.there === "Changed there" && r.here === "Same" && !r.file.endsWith(".ipynb") ? box(dbxSync.pull, r.file, `Pull ${r.file}`)
              : box(new Set(), r.file, `${r.file} can't be pushed`, true)),
        h("td", {}, r.file), h("td", {}, r.here), h("td", { class: r.action === "conflict" ? "cx-warn" : "" }, r.there),
        h("td", {}, h("span", { class: `pill-s ${DBX_SYNC_ACTION[r.action] || "neu"}` }, r.action === "none" ? "In step" : r.action),
          r.note ? h("div", { class: "small" }, r.note) : null,
          r.losses?.length ? h("ul", { class: "plain small dbx-losses" }, r.losses.map((x) => h("li", {}, x))) : null))))))
      : h("p", { class: "muted-text" }, "No notebooks (.py, .ipynb, .sql) in this folder."),
    h("div", { class: "row" },
      btn(`Push ${pushN + delN} change${pushN + delN === 1 ? "" : "s"}`, null, () => dbxSyncPush(pair), delN ? "tool danger-solid" : "tool primary",
        { disabled: dbxSync.busy || !(pushN + delN) || null }),
      dbxSync.pull.size ? btn(`Pull ${dbxSync.pull.size} from Databricks`, null, () => dbxSyncPull(pair), "tool outline", { disabled: dbxSync.busy || null }) : null),
    h("p", { class: "muted-text small" }, "Push writes files with the Workspace API as you. It never overwrites a file that changed in Databricks since the last sync, and never deletes without a second confirm.")];
}

async function dbxSyncPush(pair) {
  const files = [...dbxSync.push];
  const deletes = [...dbxSync.del];
  const losses = (dbxSync.compare.rows || []).filter((r) => dbxSync.push.has(r.file) && r.losses?.length);
  let confirmDeletes = false;
  if (deletes.length) {
    confirmDeletes = await confirmDialog(`Delete ${deletes.length} file${deletes.length === 1 ? "" : "s"} in Databricks?`,
      `${deletes.map((d) => d.replace(/\.[^.]+$/, "")).join(", ")} will be deleted from ${pair.remote}. Databricks keeps no copy here to undo it.`,
      "Delete in Databricks", { danger: true });
    if (!confirmDeletes) return;
  } else if (losses.length) {
    const ok = await confirmDialog(`Push ${files.length} file${files.length === 1 ? "" : "s"} to Databricks?`,
      `${losses.map((r) => r.file).join(", ")} lose${losses.length === 1 ? "s" : ""} something in Databricks source: see the Note column.`, "Push");
    if (!ok) return;
  }
  dbxSync.busy = true;
  renderDatabricksSync();
  const out = await attempt(() => rpc("databricks_sync_push", { pair: pair.id, files, deletes, confirm_deletes: confirmDeletes }), "Couldn't push");
  dbxSync.busy = false;
  if (out) {
    toast(`${out.done.length} change${out.done.length === 1 ? "" : "s"} pushed${out.skipped.length ? `; ${out.skipped.length} skipped: ${out.skipped.map((s) => `${s.file} (${s.why})`).join("; ")}` : ""}.`,
      out.skipped.length > 0);
  }
  dbxSync.compare = null;
  renderDatabricksSync();
}

async function dbxSyncPull(pair) {
  dbxSync.busy = true;
  const out = await attempt(() => rpc("databricks_sync_pull", { pair: pair.id, files: [...dbxSync.pull] }), "Couldn't pull");
  dbxSync.busy = false;
  if (out) toast(`${out.done.length} file${out.done.length === 1 ? "" : "s"} pulled from Databricks.`);
  dbxSync.compare = null;
  renderDatabricksSync();
}

async function dbxSyncForget(pair) {
  const ok = await confirmDialog(`Forget ${pair.name}?`, "The pair is removed from this PC. No file changes here or in Databricks.", "Forget");
  if (!ok) return;
  if (await attempt(() => rpc("databricks_sync_pair_remove", { pair: pair.id }), "Couldn't forget the pair")) {
    Object.assign(dbxSync, { pair: "", compare: null });
    renderDatabricksSync();
  }
}
