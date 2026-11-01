// OneLake page, Shortcuts tab: the shortcut command center. Every shortcut in the workspaces you
// can see, whether it works, who can read it, what changed and the requests for new ones.
// The sidecar side is shortcut_service.py; history and requests are kept by the platform.

const scPage = { view: "all", query: "", ws: "all", type: "all", health: "all", data: null, selected: null, panelTab: "overview",
                 access: null, changes: null, days: 30, changeKind: "all", rules: null, checking: false, loading: false };
const SC_VIEWS = [["all", "All shortcuts"], ["health", "Health"], ["access", "Access"], ["changes", "Changes"], ["requests", "Requests"]];
const SC_HEALTH = { healthy: ["pass", "Healthy"], broken: ["fail", "Broken"], unreachable: ["wrn", "Unreachable"], not_checked: ["neu", "Not checked"] };
const SC_CHANGE = { added: ["pass", "Added"], removed: ["fail", "Removed"], repointed: ["info", "Repointed"], broke: ["wrn", "Broke"], fixed: ["pass", "Fixed"] };
const SC_RULE_MODES = [["off", "Off"], ["warn", "Warn"], ["block", "Block"]];
let scCheckOnOpen = true;
try { scCheckOnOpen = localStorage.getItem("fd.shortcuts.checkOnOpen") !== "0"; } catch { /* not remembered: check on open */ }

const scHere = () => state.view === "onelake" && catalogPage.tab === "shortcuts";
const scPlural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
// Risks no platform admin has accepted: the ones that still flag a shortcut.
const scOpenRisks = (s) => s.risks.filter((r) => !r.accepted);
const scEnv = (env) => ({ dev: "Dev", test: "Test", prod: "Prod" }[env] || env);

function scHealthPill(s) {
  const [cls, label] = SC_HEALTH[s.health] || SC_HEALTH.not_checked;
  return h("span", { class: `pill-s ${cls}`, title: s.health_detail || (s.checked_at ? `Checked ${relTime(s.checked_at)}` : "Not checked yet") }, label);
}

function scRefs(lakehouses) {
  return lakehouses.map((l) => ({ workspace_id: l.workspace_id, lakehouse_id: l.item_id, name: l.name, workspace_name: l.workspace_name }));
}

async function renderShortcuts() {
  const sp = scPage;
  const body = h("div", { class: "sc-body" }, h("p", { class: "muted-text", style: "padding:16px" }, state.user ? "Reading shortcuts in every lakehouse..." : ""));
  const views = h("div", { class: "seg", role: "group", "aria-label": "Show" });
  const checkBtn = btn("Check all now", "ok", () => checkAll(), "tool outline", { disabled: true });
  const newBtn = btn("New shortcut", "add", () => shortcutDialog("create"), "tool primary", { disabled: true });
  setView(h("div", { class: "page monitor onelake" }, signInCallout(), onelakeTabs(),
    h("section", { class: "card onelake-catalog" },
      h("div", { class: "monitor-band" }, h("h2", {}, "Shortcuts"),
        h("span", { class: "muted-text" }, "Every shortcut you can see: whether it works, who reads it, what changed"),
        h("span", { class: "spacer" }),
        btn("Refresh", "sync", () => { sp.data = null; sp.access = null; sp.changes = null; render(); }, "tool outline"), checkBtn, newBtn),
      h("div", { class: "sc-views" }, views),
      body)));
  if (!state.user) return;
  const catalog = await onelakeCatalog();
  if (!catalog || !scHere()) return;
  const lakehouses = lakehouseRows(catalog);
  sp.lakehouses = lakehouses;
  sp.byId = Object.fromEntries(catalog.items.map((r) => [r.item_id, r]));
  const wsNames = Object.fromEntries((catalog.workspaces || []).map((w) => [w.id, w.displayName]));
  const here = () => scHere() && body.isConnected;

  function drawViews() {
    const n = sp.data?.counts || {};
    const badge = { health: (n.broken || 0) + (n.unreachable || 0), requests: n.waiting || 0 };
    fill(views, SC_VIEWS.map(([id, label]) => h("button", { class: sp.view === id ? "on" : null,
      onclick: () => { sp.view = id; drawViews(); draw(); } }, label, badge[id] ? h("span", { class: "sc-badge" }, String(badge[id])) : null)));
  }

  function draw() {
    if (!here()) return;
    checkBtn.disabled = !sp.data || sp.checking;
    checkBtn.lastChild.textContent = sp.checking ? "Checking..." : "Check all now";
    newBtn.disabled = !lakehouses.length;
    if (!sp.data) { fill(body, h("p", { class: "muted-text", style: "padding:16px" }, "Reading shortcuts in every lakehouse...")); return; }
    const top = [
      sp.data.problems?.length ? h("div", { class: "problems" }, h("b", {}, "Couldn't read everything"), gateList(sp.data.problems)) : null,
      Object.keys(sp.data.errors || {}).length ? h("p", { class: "muted-text small" },
        `${scPlural(Object.keys(sp.data.errors).length, "lakehouse")} couldn't be read (a paused capacity, or no access).`) : null];
    const pages = { all: drawAll, health: drawHealth, access: drawAccess, changes: drawChanges, requests: drawRequests };
    fill(body, ...top, pages[sp.view]());
  }

  async function load(refresh = false) {
    sp.loading = true;
    // Only the first call starts a fresh read; the polls after it wait for that read.
    if (refresh) await rpc("shortcut_center", { lakehouses: scRefs(lakehouses), refresh: true }).catch(() => null);
    await pollSidecar("shortcut_center", { lakehouses: scRefs(lakehouses), refresh: false }, (result) => {
      sp.loading = false;
      sp.moved = {};
      if (result.rows) sp.data = result;
      else sp.data = { rows: [], counts: {}, needs: [], requests: [], problems: [result.reason || "The shortcuts couldn't be read"], errors: {} };
      drawViews(); draw();
      if (refresh || !sp.checkedOnce) {
        sp.checkedOnce = true;
        if (scCheckOnOpen && sp.data.rows.length) checkAll(true);
      }
    }, here);
  }

  async function checkAll(quiet = false) {
    if (!sp.data || sp.checking) return;
    sp.checking = true; draw();
    const res = await attempt(() => rpc("shortcut_check", { items: sp.data.rows.map(scCheckItem) }), "The check didn't run");
    sp.checking = false;
    if (res) {
      applyHealth(res.results);
      const broke = (res.changes || []).filter((c) => c.kind === "broke");
      if (broke.length) toast(`${scPlural(broke.length, "shortcut")} broke: ${broke.map((c) => c.name).join(", ")}`, true, { log: true });
      else if (!quiet) toast(`Checked ${scPlural(res.results.length, "shortcut")}`);
      sp.changes = null;
    }
    drawViews(); draw();
  }

  function applyHealth(results) {
    const by = Object.fromEntries(results.map((r) => [r.id, r]));
    const now = new Date().toISOString();
    for (const s of sp.data.rows) {
      const r = by[s.id];
      if (r) Object.assign(s, { health: r.health, health_detail: r.detail, checked_at: now });
    }
    const c = sp.data.counts;
    c.broken = sp.data.rows.filter((s) => s.health === "broken").length;
    c.unreachable = sp.data.rows.filter((s) => s.health === "unreachable").length;
    c.not_checked = sp.data.rows.filter((s) => s.health === "not_checked").length;
    sp.data.checked_at = now;
  }

  // ---- All shortcuts

  function filtered() {
    const q = sp.query.toLowerCase();
    return sp.data.rows.filter((s) => (sp.ws === "all" || s.host_workspace_id === sp.ws)
      && (sp.type === "all" || s.target_type === sp.type) && (sp.health === "all" || s.health === sp.health
        || (sp.health === "risky" && scOpenRisks(s).length))
      && (!q || `${s.name} ${s.full_path} ${s.target} ${s.target_path || ""} ${s.lakehouse_name} ${s.workspace_name}`.toLowerCase().includes(q)))
      .sort((a, b) => scRank(a) - scRank(b) || `${a.workspace_name} ${a.lakehouse_name} ${a.full_path}`.localeCompare(`${b.workspace_name} ${b.lakehouse_name} ${b.full_path}`));
  }

  function drawAll() {
    const c = sp.data.counts;
    const stat = (n, label, note, onClick, cls = "") => h("button", { class: `tile plat-stat ${cls}`, onclick: onClick },
      h("span", { class: "plat-n" }, String(n ?? 0)), h("span", { class: "tt" }, label), note ? h("span", { class: "td" }, note) : null);
    const tiles = h("div", { class: "tiles plat-stats" },
      stat(c.total, "Shortcuts you can see", `in ${scPlural(c.lakehouses || 0, "lakehouse")}, ${scPlural(c.workspaces || 0, "workspace")}`,
        () => { sp.health = "all"; draw(); }),
      stat(c.broken, "Broken", c.unreachable ? `${c.unreachable} more you can't check` : "target gone or refused", () => { sp.view = "health"; drawViews(); draw(); }, c.broken ? "bad" : ""),
      stat(c.connection, "Read with a connection", "storage shortcuts: open to every reader", () => { sp.view = "access"; drawViews(); draw(); }, c.connection ? "warn" : ""),
      stat(c.changed_7d, "Changed in 7 days", "added, repointed or removed", () => { sp.view = "changes"; drawViews(); draw(); }),
      stat(c.waiting, "Waiting on you", "requests for shortcuts", () => { sp.view = "requests"; drawViews(); draw(); }));
    const opt = (v, label, cur) => h("option", { value: v, selected: cur === v ? "" : null }, label);
    const workspaces = [...new Map(sp.data.rows.map((s) => [s.host_workspace_id, s.workspace_name])).entries()].sort((a, b) => String(a[1]).localeCompare(b[1]));
    const types = [...new Set(sp.data.rows.map((s) => s.target_type))].sort();
    const sel = (label, value, options, set) => {
      const el = h("select", { "aria-label": label }, options);
      el.value = value;
      el.addEventListener("change", () => { set(el.value); draw(); });
      return el;
    };
    const search = h("input", { class: "catalog-search", placeholder: "Search name, target or path", value: sp.query, "aria-label": "Search shortcuts" });
    search.addEventListener("input", () => { sp.query = search.value; drawTable(); });
    const table = h("div", { class: "sc-table" });
    const panel = h("section", { class: `card preview-panel${sp.selected ? " open" : ""}` });
    const bar = h("div", { class: "sc-filters" }, search,
      sel("Workspace", sp.ws, [opt("all", "Every workspace", sp.ws), ...workspaces.map(([id, n]) => opt(id, n || id, sp.ws))], (v) => { sp.ws = v; }),
      sel("Points at", sp.type, [opt("all", "Points at anything", sp.type), ...types.map((t) => opt(t, t, sp.type))], (v) => { sp.type = v; }),
      sel("Health", sp.health, [opt("all", "Any health", sp.health), ...Object.entries(SC_HEALTH).map(([k, [, l]]) => opt(k, l, sp.health)), opt("risky", "Worth a look", sp.health)], (v) => { sp.health = v; }),
      h("span", { class: "spacer" }),
      h("span", { class: "muted-text small" }, sp.data.checked_at ? `Last checked ${relTime(sp.data.checked_at)}` : "Not checked yet"));
    function drawTable() {
      const rows = filtered();
      const wide = !sp.selected;  // with the panel open, only what picks a row
      fill(table, rows.length ? h("table", { class: "list" },
        h("thead", {}, h("tr", {}, ["Shortcut", wide ? "Lives in" : null, "Points at", "Health", wide ? "Used by" : null, wide ? "Last change" : null]
          .filter(Boolean).map((x) => h("th", {}, x)))),
        h("tbody", {}, rows.slice(0, 500).map((s) => h("tr", { class: sp.selected === s.id ? "selected" : null, style: "cursor:pointer",
          onclick: () => { sp.selected = sp.selected === s.id ? null : s.id; sp.panelTab = "overview"; draw(); } },
          h("td", {}, h("span", { class: "item" }, icon(s.kind === "table" ? "table" : "folder", 16), h("b", {}, s.name)),
            h("div", { class: "muted-text small mono" }, s.path),
            wide ? null : h("div", { class: "muted-text small" }, `${s.workspace_name} / ${s.lakehouse_name}`)),
          wide ? h("td", {}, h("div", {}, s.workspace_name), h("div", { class: "muted-text small" }, s.lakehouse_name)) : null,
          h("td", {}, scTarget(s)),
          h("td", {}, scHealthPill(s), wide && s.health !== "healthy" && s.health_detail ? h("div", { class: "muted-text small sc-detail" }, s.health_detail) : null,
            s.risks.map((r) => r.accepted ? h("div", { class: "small muted-text sc-detail" }, wide ? `Accepted by ${r.accepted.by || "a platform admin"}` : "Accepted")
              : h("div", { class: "small warn-text sc-detail" }, wide ? r.text : r.kind === "cross_env" ? "Crosses environments" : "Duplicate"))),
          wide ? h("td", {}, s.used_by.length ? s.used_by.map((u) => h("div", { class: "small" }, u.entity)) : h("span", { class: "muted-text small" }, "Not used here")) : null,
          wide ? h("td", {}, scLastChange(s.last_change)) : null))))
        : h("p", { class: "muted-text", style: "padding:16px" }, sp.data.rows.length ? "Nothing matches." : "No shortcuts in any lakehouse yet. Use New shortcut."),
      rows.length > 500 ? h("p", { class: "muted-text small" }, `Showing 500 of ${rows.length}. Search or filter to narrow it.`) : null);
    }
    drawTable();
    const sel_ = sp.data.rows.find((s) => s.id === sp.selected);
    if (sel_) drawPanel(panel, sel_); else sp.selected = null;
    return h("div", { class: "sc-pad" }, tiles, bar, h("div", { class: "preview-layout" }, table, panel));
  }

  // ---- one shortcut, in the panel on the right

  function drawPanel(panel, s) {
    const tabs = h("div", { class: "subtabs", role: "tablist", "aria-label": "Shortcut" }, [["overview", "Overview"], ["readers", "Who can read"], ["history", "History"]].map(([id, label]) =>
      h("button", { class: `tab${sp.panelTab === id ? " active" : ""}`, role: "tab", "aria-selected": String(sp.panelTab === id), onclick: () => { sp.panelTab = id; drawPanel(panel, s); } }, label)));
    const inner = h("div", { class: "preview-body" }, tabs);
    const host = { type: "Lakehouse", workspace_id: s.host_workspace_id, item_id: s.lakehouse_id };
    fill(panel,
      h("div", { class: "preview-head" },
        h("div", { class: "preview-title" }, icon("databaseLink", 18), h("span", { class: "name" }, s.name)),
        h("div", { class: "preview-actions" }, h("button", { class: "tool small", "aria-label": "Close", onclick: () => { sp.selected = null; draw(); } }, icon("dismiss", 16))),
        h("div", { class: "preview-meta muted-text" }, `${s.workspace_name} / ${s.lakehouse_name} / ${s.path}`)),
      inner,
      h("div", { class: "dialog-actions sc-actions" },
        btn("Check now", "ok", async () => {
          const res = await attempt(() => rpc("shortcut_check", { items: [scCheckItem(s)] }), "The check didn't run");
          if (res) { applyHealth(res.results); drawViews(); draw(); }
        }, "tool primary"),
        btn("Repoint", "copyArrowRight", () => shortcutDialog("repoint", { shortcut: s }), "tool outline"),
        btn("Open in Fabric", "open", () => openLink(fabricItemLink(host)), "tool outline"),
        h("span", { class: "spacer" }),
        btn("Remove", "delete", () => removeShortcut(s), "tool outline danger")));
    if (sp.panelTab === "overview") {
      const kv = (k, v) => [h("span", { class: "muted-text" }, k), h("span", {}, v)];
      inner.append(...[
        s.health === "broken" ? h("div", { class: "problems" }, h("b", {}, s.checked_at ? `Broken, checked ${relTime(s.checked_at)}` : "Broken"),
          h("p", { class: "small" }, s.health_detail),
          s.used_by.length ? h("p", { class: "small" }, `Runs of ${s.used_by.map((u) => u.entity).join(", ")} fail until it's fixed.`) : null) : null,
        scOpenRisks(s).length ? h("div", { class: "card pad warn-box" }, scOpenRisks(s).map((r) => h("div", { class: "small" }, r.text,
          r.kind === "cross_env" ? h("div", { class: "row sc-fixes" }, crossEnvActions(s)) : null))) : null,
        ...s.risks.filter((r) => r.accepted).map((r) => h("div", { class: "card pad" },
          h("div", { class: "small" }, h("b", {}, `Accepted by ${r.accepted.by || "a platform admin"}: `), r.accepted.reason),
          h("div", { class: "muted-text small" }, `${r.text}. Accepted ${relTime(r.accepted.at)}; repointing it ends this.`),
          sp.data.admin ? preview("shortcutFixes", btn("Withdraw", null, () => withdrawRisk(s, r), "tool outline small")) : null)),
        h("b", {}, "Points at"),
        h("div", { class: "sc-kv" },
          ...kv("Type", s.target_type),
          ...(s.target_type === "OneLake"
            ? kv("Lakehouse", sp.byId[s.item_id] ? `${sp.byId[s.item_id].workspace_name} / ${sp.byId[s.item_id].name}` : "Not in a workspace you can open")
            : kv("Location", h("span", { class: "mono" }, s.location || s.target))),
          ...kv("Path", h("span", { class: "mono" }, s.target_type === "OneLake" ? s.target_path : (s.subpath || ""))),
          ...(s.connection_id ? kv("Connection", h("span", { class: "mono" }, s.connection_id)) : []),
          ...kv("Data product", s.product ? `${s.product.name} (${s.product.status}, ${s.product.access})` : "Not a data product"),
          ...kv("Environment", s.host_env ? `${s.host_env.toUpperCase()}${s.target_env ? ` reads ${s.target_env.toUpperCase()}` : ""}` : "Unknown"),
          ...kv("First seen", s.first_seen ? when(s.first_seen) : "Now")),
        h("b", {}, "Who can read it"),
        h("p", { class: "small" }, s.exposure === "connection"
          ? "Fabric reads the storage with the connection's account, so everyone who can read this lakehouse reads the storage, whatever the storage itself allows."
          : "Each reader needs access here and in the target lakehouse, so nobody reads more than the target allows."),
        h("b", {}, "Used by"),
        s.used_by.length ? h("ul", { class: "gate-list" }, s.used_by.map((u) => h("li", {},
          h("button", { class: "name-link", onclick: () => openEntity(u.entity) }, u.entity), ` ${u.how}`)))
          : h("p", { class: "muted-text small" }, "No managed table in the open metadata project reads it.")].filter(Boolean));
    } else if (sp.panelTab === "readers") {
      const box = h("div", {}, h("p", { class: "muted-text" }, "Reading workspace roles and data access roles..."));
      inner.append(box);
      const readers = () => rpc("shortcut_readers", { shortcut: s }).then((r) => {
        fill(box, h("p", { class: r.gap || r.partial ? "warn-text" : "" }, r.text),
          r.readers?.length ? h("table", { class: "list" }, h("thead", {}, h("tr", {}, ["Person or group", "Reads it as", "Target"].map((x) => h("th", {}, x)))),
            h("tbody", {}, r.readers.map((p) => h("tr", {}, h("td", {}, p.name, p.email ? h("div", { class: "muted-text small" }, p.email) : null),
              h("td", { class: "small" }, p.how),
              h("td", { class: "small" }, r.mode === "connection" ? "Connection" : r.missing.some((m) => m.id === p.id) ? h("span", { class: "pill-s wrn" }, "No access") : r.target === null ? "Unknown" : h("span", { class: "pill-s pass" }, "Allowed"))))))
            : null,
          s.exposure === "connection" ? h("p", { class: "muted-text small" }, "To limit who reads this storage, add a OneLake data access role on its folder in the Access tab.") : null);
      }).catch((err) => fill(box, errorPanel(err, { label: "Couldn't read who can read this shortcut", retry: readers })));
      readers();
    } else {
      const box = h("div", {}, h("p", { class: "muted-text" }, "Reading the history..."));
      inner.append(box);
      const history = () => rpc("shortcut_changes", { days: 3650, shortcut: s.id }).then((r) => fill(box, scChangeList(r.changes, () => { sp.data = null; load(true); }, false)))
        .catch((err) => fill(box, errorPanel(err, { label: "Couldn't read the history", retry: history })));
      history();
    }
  }

  async function removeShortcut(s) {
    if (!(await confirmDialog(`Remove shortcut ${s.name}?`,
      `Only the shortcut in ${s.lakehouse_name} goes. The data it points at isn't touched.${s.used_by.length ? ` ${s.used_by.map((u) => u.entity).join(", ")} read it and will fail.` : ""} You can recreate it from Changes.`, "Remove", { danger: true }))) return;
    const ok = await attempt(() => rpc("delete_shortcut", { workspace_id: s.host_workspace_id, item_id: s.lakehouse_id, path: s.path, name: s.name,
      lakehouse_name: s.lakehouse_name, workspace_name: s.workspace_name }), "Not removed");
    if (!ok) return;
    toast(`Removed shortcut ${s.name}`, false, { log: true });
    sp.selected = null; sp.data = null; sp.changes = null; sp.access = null;
    load(true);
  }

  // ---- fixes: repoint many at one lakehouse, a crossed environment's same-environment copy, accepting a risk

  async function repointMany(list, t) {
    const n = list.length;
    if (!(await confirmDialog(`Repoint ${scPlural(n, "shortcut")}?`,
      `Each keeps its name and table, and now points at ${t.lakehouse_name} in ${t.workspace_name}. Fabric can't change where a shortcut points, so each is removed and made again. One that fails is put back as it was, and the rest carry on. Each one is in Shortcuts > Changes with Undo.`,
      "Repoint", { danger: true }))) return;
    const res = await attempt(() => rpc("shortcut_repoint_many", { items: list, workspace_id: t.workspace_id, lakehouse_id: t.lakehouse_id,
      workspace_name: t.workspace_name, lakehouse_name: t.lakehouse_name }), "Not repointed");
    if (!res) return;
    const failed = res.failed.map((f) => `${f.name}: ${f.error}`);
    toast([`Repointed ${res.done.length} of ${n}`, ...failed, ...res.problems].join(". "), failed.length > 0, { log: true });
    sp.data = null; sp.changes = null; sp.access = null;
    load(true);
  }

  function pickLakehouse(list) {
    const pick = h("select", { "aria-label": "Lakehouse" }, lakehouses.map((l) => h("option", { value: l.item_id }, `${l.name} · ${l.workspace_name}`)));
    const go_ = () => {
      const l = lakehouses.find((x) => x.item_id === pick.value);
      if (!l) return;
      closeDialog();
      repointMany(list, { workspace_id: l.workspace_id, workspace_name: l.workspace_name, lakehouse_id: l.item_id, lakehouse_name: l.name });
    };
    showDialog(`Repoint ${scPlural(list.length, "shortcut")}`, h("div", { class: "scroll estate" },
      h("p", { class: "text" }, "Pick the lakehouse they point at now. Each keeps its name and its table or folder path."),
      h("label", { class: "field" }, "Lakehouse", pick)),
    [btn("Cancel", null, closeDialog, "tool outline"), btn("Next", null, go_, "tool primary")]);
  }

  function crossEnvActions(s) {
    const t = s.same_env_target;
    return [
      t ? preview("shortcutFixes", btn(`Repoint to ${scEnv(t.env)}'s ${t.lakehouse_name}`, null, () => repointSameEnv(s, t), "tool primary small"))
        : btn("Repoint", null, () => shortcutDialog("repoint", { shortcut: s }), "tool primary small"),
      sp.data.admin ? preview("shortcutFixes", btn("Accept...", null, () => acceptRisk(s), "tool outline small")) : null];
  }

  async function repointSameEnv(s, t) {
    if (!(await confirmDialog(`Repoint ${s.name}?`, `It keeps its name and table, and now points at ${t.lakehouse_name} in ${t.workspace_name}, ${scEnv(t.env)}'s copy. Fabric can't change where a shortcut points, so it's removed and made again. If that fails, it's put back as it was. It's in Shortcuts > Changes with Undo.`, "Repoint", { danger: true }))) return;
    const res = await attempt(() => rpc("shortcut_repoint", { workspace_id: s.host_workspace_id, item_id: s.lakehouse_id, path: s.path, name: s.name,
      source: { type: "oneLake", workspace_id: t.workspace_id, item_id: t.lakehouse_id, path: s.target_path },
      lakehouse_name: s.lakehouse_name, workspace_name: s.workspace_name, target_workspace_name: t.workspace_name }), "Not repointed");
    if (!res) return;
    toast(`Repointed ${s.name}${res.problems?.length ? `; ${res.problems.join("; ")}` : ""}`, false, { log: true });
    sp.data = null; sp.changes = null; sp.access = null;
    load(true);
  }

  function acceptRisk(s) {
    const why = h("textarea", { rows: 3, placeholder: "For example: it reads the live price list on purpose, read-only" });
    const ok = btn("Accept", null, async () => {
      const res = await attempt(() => rpc("shortcut_accept_risk", { shortcut_id: s.id, kind: "cross_env", reason: why.value.trim() }), "Not accepted");
      if (!res) return;
      closeDialog();
      toast(`Accepted ${s.name} reading ${scEnv(s.target_env)}`, false, { log: true });
      sp.data = null;
      load();
    }, "tool primary", { disabled: true });
    why.addEventListener("input", () => { ok.disabled = !why.value.trim(); });
    showDialog(`Accept ${s.name} reading ${scEnv(s.target_env)}?`, h("div", { class: "scroll estate" },
      h("p", { class: "text" }, `${s.name} in ${s.workspace_name} / ${s.lakehouse_name} points at ${scEnv(s.target_env)}. Accepting takes it off Shortcut needs and shows your name and reason on it instead of the warning. Repointing it ends this.`),
      h("label", { class: "field" }, "Why it's fine", why)),
    [btn("Cancel", null, closeDialog, "tool outline"), ok]);
    why.focus();
  }

  async function withdrawRisk(s, r) {
    const ok = await attempt(() => rpc("shortcut_withdraw_risk", { shortcut_id: s.id, kind: r.kind }), "Not withdrawn");
    if (!ok) return;
    toast(`${s.name} is flagged again`, false, { log: true });
    sp.data = null;
    load();
  }

  // A OneLake target lakehouse that isn't in any workspace the person can open: gone, or no longer theirs to see.
  const scGone = (s) => s.health === "broken" && s.target_type === "OneLake" && s.item_id && !sp.byId[s.item_id];

  // The card for broken shortcuts whose lakehouse is gone: where it may have gone, and repointing them all.
  function goneCard(key, list) {
    const s0 = list[0];
    const lh = list.find((s) => s.target_lakehouse_name)?.target_lakehouse_name || "";
    const ws = s0.target_workspace_name || wsNames[s0.workspace_id] || s0.workspace_id;
    const n = list.length;
    const box = h("div", { class: "sc-moved" });
    const another = preview("shortcutFixes", btn("Repoint all to another lakehouse...", null, () => pickLakehouse(list), "tool outline small"));
    // m: what shortcut_find_moved said; null while it's looking (or with no name to look for)
    const show = (m) => {
      const c = m?.candidate;
      const found = list.filter((s) => (m?.found || []).includes(s.id));
      fill(box, c ? h("div", { class: "row" }, h("span", { class: "small grow" }, found.length === n
          ? `A lakehouse called ${c.lakehouse_name} is now in ${c.workspace_name}, and all ${n} tables are there.`
          : `A lakehouse called ${c.lakehouse_name} is now in ${c.workspace_name}, and ${found.length} of ${n} tables are there.`),
        found.length ? preview("shortcutFixes", btn(`Repoint all ${found.length} there`, null, () => repointMany(found, c), "tool primary small")) : null)
        : !lh ? null
          : !m ? h("p", { class: "muted-text small" }, `Looking for a lakehouse called ${lh}...`)
            : m.error ? h("p", { class: "muted-text small" }, `Couldn't look for where it went: ${m.error}`)
              : h("p", { class: "muted-text small" }, `No lakehouse called ${lh} in a workspace you can open.`),
        another);
    };
    show(null);
    if (lh) {
      sp.moved ||= {};
      sp.moved[key] ||= rpc("shortcut_find_moved", { shortcuts: list }).catch((err) => ({ candidate: null, error: friendlyError(err).text }));
      sp.moved[key].then(show);
    }
    return h("div", { class: "card pad" }, h("div", { class: "row" }, h("span", { class: "pill-s fail" }, String(n)),
      h("b", {}, `${lh || "The lakehouse it points at"} in ${ws} isn't in a workspace you can open`)), box);
  }

  // ---- Health

  function drawHealth() {
    const rows = sp.data.rows;
    const groups = new Map();
    for (const s of rows.filter((x) => x.health === "broken" || x.health === "unreachable")) {
      // A lakehouse that's gone is one cause, whatever each probe said: grouped by that lakehouse.
      const key = scGone(s) ? `gone|${s.workspace_id}|${s.item_id}` : `${s.health}|${s.health_detail}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(s);
    }
    const risky = rows.filter((s) => scOpenRisks(s).length);
    const item = (s, why, action) => h("div", { class: "sc-item" },
      h("div", {}, h("b", {}, s.name), h("div", { class: "muted-text small" }, `${s.workspace_name} / ${s.lakehouse_name}`)),
      h("div", { class: "small" }, why), action);
    const open = (s) => btn("Open", null, () => { sp.view = "all"; sp.selected = s.id; sp.panelTab = "overview"; drawViews(); draw(); }, "tool outline small");
    const fix = (s) => h("span", { class: "row" },
      s.health === "broken" && s.target_type === "OneLake" ? btn("Repoint", null, () => shortcutDialog("repoint", { shortcut: s }), "tool primary small") : open(s),
      btn("Remove", null, () => removeShortcut(s), "tool outline small danger"));
    const left = h("div", { class: "sc-col" },
      groups.size || risky.length ? null : h("div", { class: "card pad" }, h("b", {}, sp.data.checked_at ? "Every shortcut that could be checked works." : "Nothing checked yet."),
        h("p", { class: "muted-text small" }, sp.data.checked_at ? `Last check ${relTime(sp.data.checked_at)}.` : "Select Check all now.")),
      ...[...groups.entries()].map(([key, list]) => {
        const readers = (s) => s.used_by.length ? `${s.used_by.map((u) => u.entity).join(", ")} read it` : "Nothing in the open project reads it";
        if (key.startsWith("gone|")) {
          const card = goneCard(key, list);
          card.append(...list.map((s) => item(s, `${s.target_path}. ${readers(s)}`, fix(s))));
          return card;
        }
        const [health, detail] = key.split("|");
        return h("div", { class: "card pad" }, h("div", { class: "row" }, h("span", { class: `pill-s ${SC_HEALTH[health][0]}` }, String(list.length)),
          h("b", {}, detail || SC_HEALTH[health][1])), ...list.map((s) => item(s, readers(s), fix(s))));
      }),
      risky.length ? h("div", { class: "card pad" }, h("div", { class: "row" }, h("span", { class: "pill-s wrn" }, String(risky.length)), h("b", {}, "Worth a look, not broken")),
        ...risky.map((s) => item(s, scOpenRisks(s).map((r) => r.text).join(". "),
          h("span", { class: "row" }, scOpenRisks(s).some((r) => r.kind === "cross_env") ? crossEnvActions(s) : null, open(s))))) : null);
    const toggle = h("input", { type: "checkbox", id: "sc-check-open", checked: scCheckOnOpen ? "" : null });
    toggle.addEventListener("change", () => {
      scCheckOnOpen = toggle.checked;
      try { localStorage.setItem("fd.shortcuts.checkOnOpen", scCheckOnOpen ? "1" : "0"); } catch { /* this session only */ }
    });
    const c = sp.data.counts;
    const right = h("div", { class: "sc-col sc-side" },
      h("div", { class: "card pad" }, h("b", {}, "How health is checked"),
        h("p", { class: "small" }, "A check lists one entry of the shortcut's folder through OneLake, with your sign-in. That proves the target is there and Fabric can reach it. Nothing else is read, and nothing is written."),
        h("p", { class: "muted-text small" }, "Results go to the platform, so your team sees the same health and the same history of breaks and fixes.")),
      h("div", { class: "card pad" }, h("b", {}, "Checks"),
        h("label", { class: "row", for: "sc-check-open" }, toggle, "Check every shortcut when this page opens")),
      h("div", { class: "card pad" }, h("b", {}, "Last check"),
        h("p", { class: "small" }, sp.data.checked_at
          ? `${relTime(sp.data.checked_at)}: ${c.total - c.broken - c.unreachable - c.not_checked} healthy, ${c.broken} broken, ${c.unreachable} you can't check, ${c.not_checked} not checked.`
          : "Not checked yet."),
        btn(sp.checking ? "Checking..." : "Check all now", "ok", () => checkAll(), "tool primary", { disabled: sp.checking })));
    return h("div", { class: "sc-pad sc-two" }, left, right);
  }

  // ---- Access

  function drawAccess() {
    const box = h("div", { class: "card pad" });
    const rulesBox = h("div", { class: "card pad" }, h("p", { class: "muted-text" }, "Reading the shortcut rules..."));
    const draw_ = () => {
      if (!sp.access || sp.access.pending) {
        fill(box, h("b", {}, "Where readers and targets don't match"), h("p", { class: "muted-text" }, "Reading workspace roles and data access roles of every shortcut..."));
        return;
      }
      const rows = sp.data.rows.map((s) => ({ s, a: sp.access.items[s.id] || {} })).filter(({ a }) => a.gap || a.partial);
      fill(box, h("b", {}, "Where readers and targets don't match"),
        rows.length ? h("table", { class: "list" }, h("thead", {}, h("tr", {}, ["Shortcut", "Readers here", "Target lets in", "What it means", ""].map((x) => h("th", {}, x)))),
          h("tbody", {}, rows.map(({ s, a }) => h("tr", {},
            h("td", {}, h("b", {}, s.name), h("div", { class: "muted-text small" }, `${s.workspace_name} / ${s.lakehouse_name}`)),
            h("td", {}, a.here ?? "?"),
            h("td", {}, a.mode === "connection" ? "Connection" : a.target ?? "?"),
            h("td", { class: a.mode === "connection" ? "warn-text small" : "small" }, a.text),
            h("td", {}, a.mode === "connection"
              ? btn("Add data access role", null, () => { accessPage.lakehouse = s.lakehouse_id; accessPage.lh = null; catalogPage.tab = "access"; render(); }, "tool outline small")
              : btn("Open", null, () => { sp.view = "all"; sp.selected = s.id; sp.panelTab = "readers"; drawViews(); draw(); }, "tool outline small"))))))
          : h("p", { class: "muted-text" }, "Every shortcut's readers can read its target too."),
        h("p", { class: "muted-text small" }, "Readers come from workspace roles and OneLake data access roles you can see, as on the Access tab. Workspace Viewers read tables only through data access roles."));
    };
    draw_();
    if (!sp.access || sp.access.pending) {
      const start = sp.access ? Promise.resolve() : rpc("shortcut_access", { rows: sp.data.rows, refresh: true }).catch(() => null);
      sp.access = sp.access || { pending: true, items: {} };
      start.then(() => pollSidecar("shortcut_access", { rows: sp.data.rows, refresh: false },
        (r) => { sp.access = r; if (sp.view === "access") draw_(); }, () => scHere() && sp.view === "access"));
    }
    scRules(rulesBox);
    return h("div", { class: "sc-pad sc-two" },
      h("div", { class: "sc-col" },
        h("div", { class: "card pad" }, h("b", {}, "Two ways a shortcut decides who reads it"),
          h("div", { class: "sc-modes" },
            h("div", {}, h("b", {}, "OneLake shortcuts"), h("p", { class: "small" }, "Each reader needs access in both places: the lakehouse with the shortcut and the target. Nobody gets more than the target allows.")),
            h("div", {}, h("b", {}, "Storage shortcuts (ADLS, S3, Blob)"), h("p", { class: "small" }, "Fabric reads with the connection's key or account. Anyone who can read the lakehouse reads the storage, even if the storage would refuse them.")))),
        box),
      h("div", { class: "sc-col sc-side" }, rulesBox));
  }

  async function scRules(box) {
    const r = await rpc("shortcut_rules").catch((err) => ({ problems: [`Couldn't read the shortcut rules: ${friendlyError(err).text}`], rules: {}, text: {} }));
    if (!box.isConnected) return;
    sp.rules = r;
    const rules = { ...r.rules };
    const modeSel = (key) => {
      const el = h("select", { "aria-label": r.text[key], disabled: r.admin ? null : "" }, SC_RULE_MODES.map(([v, l]) => h("option", { value: v }, l)));
      el.value = rules[key] || "off";
      el.addEventListener("change", () => { rules[key] = el.value; });
      return el;
    };
    const who = h("select", { "aria-label": r.text.who_creates, disabled: r.admin ? null : "" },
      h("option", { value: "anyone" }, "Anyone with the workspace role"), h("option", { value: "admins" }, "Platform admins; others request"));
    who.value = rules.who_creates || "anyone";
    who.addEventListener("change", () => { rules.who_creates = who.value; });
    const conns = h("input", { value: (rules.allowed_connections || []).join(", "), placeholder: "Connection IDs, comma separated", disabled: r.admin ? null : "" });
    const rule = (key, control, note) => h("div", { class: "sc-rule" }, h("div", {}, h("div", {}, r.text[key]), note ? h("div", { class: "muted-text small" }, note) : null), control);
    fill(box, h("b", {}, "Shortcut rules"), h("span", { class: "muted-text small" }, r.admin ? " Platform admins set these." : " Set by your platform admins."),
      r.problems?.length ? gateList(r.problems) : null,
      rule("cross_env", modeSel("cross_env"), "Block stops New shortcut and Repoint; either way it shows on Health"),
      rule("storage_role", modeSel("storage_role")),
      rule("deprecated_product", modeSel("deprecated_product")),
      rule("connections", modeSel("connections")),
      h("label", { class: "field" }, "Allowed connections", conns),
      rule("who_creates", who),
      h("p", { class: "muted-text small" }, "Fabric itself lets any workspace Contributor make a shortcut. These rules apply to what KratosForge makes; anything made elsewhere that breaks them shows on Health."),
      r.admin ? btn("Save rules", "save", async () => {
        rules.allowed_connections = conns.value.split(",").map((x) => x.trim()).filter(Boolean);
        const out = await attempt(() => rpc("shortcut_save_rules", { rules }), "Rules not saved");
        if (out) { toast("Saved the shortcut rules", false, { log: true }); scRules(box); }
      }, "tool primary") : null);
  }

  // ---- Changes

  function drawChanges() {
    const box = h("div", {}, h("p", { class: "muted-text" }, "Reading the history..."));
    const days = h("select", { "aria-label": "Period" }, [[7, "Last 7 days"], [30, "Last 30 days"], [90, "Last 90 days"], [365, "Last year"]].map(([v, l]) => h("option", { value: v }, l)));
    days.value = String(sp.days);
    days.addEventListener("change", () => { sp.days = Number(days.value); sp.changes = null; draw(); });
    const kind = h("select", { "aria-label": "Change" }, [["all", "Every change"], ...Object.entries(SC_CHANGE).map(([k, [, l]]) => [k, l])].map(([v, l]) => h("option", { value: v }, l)));
    kind.value = sp.changeKind;
    kind.addEventListener("change", () => { sp.changeKind = kind.value; show(); });
    const show = () => fill(box, scChangeList((sp.changes || []).filter((c) => sp.changeKind === "all" || c.kind === sp.changeKind), () => { sp.data = null; sp.changes = null; load(true); }, true));
    if (sp.changes) show();
    else {
      const changes = () => rpc("shortcut_changes", { days: sp.days }).then((r) => { sp.changes = r.changes; if (box.isConnected) show(); })
        .catch((err) => fill(box, errorPanel(err, { label: "Couldn't read the changes", retry: changes })));
      changes();
    }
    return h("div", { class: "sc-pad sc-two" },
      h("div", { class: "card pad sc-col" }, h("div", { class: "sc-filters" }, days, kind), box),
      h("div", { class: "sc-col sc-side" },
        h("div", { class: "card pad" }, h("b", {}, "Where changes come from"),
          h("p", { class: "small" }, "Changes made in KratosForge are recorded with who made them, and why when it came from a request."),
          h("p", { class: "small" }, "Fabric doesn't record who changes a shortcut. Each time this page reads the shortcuts it compares them with the last read, so changes made in Fabric or by other tools show as \"seen at scan\"."),
          h("p", { class: "muted-text small" }, "The first read of a lakehouse sets the starting point, so what was already there isn't listed as added.")),
        h("div", { class: "card pad" }, h("b", {}, "Undo and Recreate"),
          h("p", { class: "small" }, "Each change keeps the shortcut's full definition. Undo points a repointed shortcut back or removes an added one, and Recreate makes a removed one again. Both ask first, and neither touches the data the shortcut points at."))));
  }

  // ---- Requests and needs

  function drawRequests() {
    const reqs = sp.data.requests || [];
    const forMe = reqs.filter((r) => r.status === "waiting" && r.can_decide);
    const mine = reqs.filter((r) => r.mine);
    const rerender = () => { sp.data = null; load(true); };
    const needs = sp.data.needs.filter((n) => n.kind !== "request");
    const action = (n) => {
      const s = sp.data.rows.find((x) => x.id === n.id);
      if (!s) return null;
      if (n.kind === "cross_env") return h("span", { class: "row" }, crossEnvActions(s));
      if (n.kind === "deprecated" || (n.kind === "broken_in_use" && s.target_type === "OneLake"))
        return btn("Repoint", null, () => shortcutDialog("repoint", { shortcut: s }), "tool primary small");
      return btn("Open", null, () => { sp.view = "all"; sp.selected = s.id; drawViews(); draw(); }, "tool outline small");
    };
    return h("div", { class: "sc-pad sc-two" },
      h("div", { class: "sc-col" },
        h("div", { class: "card pad" }, h("div", { class: "row" }, h("b", { class: "grow" }, "Waiting on you"),
          btn("Request a shortcut", "add", () => shortcutDialog("request"), "tool primary small")),
        requestTable(forMe, rerender, true)),
        h("div", { class: "card pad" }, h("b", {}, "Your requests"), mine.length ? requestTable(mine, rerender) : h("p", { class: "muted-text" }, "You haven't asked for a shortcut."))),
      h("div", { class: "sc-col sc-side" },
        h("div", { class: "card pad" }, h("b", {}, "Shortcut needs"),
          h("p", { class: "muted-text small" }, "Shortcuts that need changing: broken ones a managed table reads, ones pointing at a deprecated data product, and ones crossing environments."),
          needs.length ? needs.map((n) => h("div", { class: "sc-item sc-need" }, h("div", {}, h("b", {}, n.title), h("div", { class: "muted-text small" }, n.detail)), action(n)))
            : h("p", { class: "muted-text" }, "Nothing needs a shortcut change.")),
        h("div", { class: "card pad" }, h("b", {}, "One request system"),
          h("p", { class: "small" }, "Shortcut requests are the platform's requests, next to key tables, sources and product access. They show here and on Data mesh > Requests. Approving one makes the shortcut as the person approving, then marks it approved."))));
  }

  drawViews(); draw();
  if (!sp.data) load(!sp.loadedOnce);
  sp.loadedOnce = true;
}

function scRank(s) {
  return s.health === "broken" ? 0 : s.health === "unreachable" ? 1 : scOpenRisks(s).length ? 2 : 3;
}

function scCheckItem(s) {
  return { id: s.id, lakehouse_id: s.lakehouse_id, host_workspace_id: s.host_workspace_id, path: s.path, full_path: s.full_path, target_type: s.target_type };
}

function scTarget(s) {
  if (s.target_type !== "OneLake") return h("div", {}, h("span", { class: "pill-s neu" }, s.target_type), h("div", { class: "muted-text small mono" }, s.target));
  const item = scPage.byId?.[s.item_id];
  return h("div", {}, h("span", { class: "pill-s info" }, "OneLake"), h("b", {}, item ? item.name : "OneLake"),
    h("div", { class: "muted-text small" }, item ? `${item.workspace_name} · ${s.target_path}` : s.target_path),
    s.product ? h("div", { class: "small" }, `Data product ${s.product.name}${s.product.status === "deprecated" ? " (deprecated)" : ""}`) : null,
    s.item_id && !item ? h("span", { class: "pill-s wrn", title: "Not in any workspace you can open. It may have been deleted, or you can't read it." }, "Target not found") : null);
}

function scLastChange(c) {
  if (!c) return h("span", { class: "muted-text small" }, "");
  const [, label] = SC_CHANGE[c.kind] || ["", c.kind];
  return h("div", { class: "small" }, `${label} ${relTime(c.at)}`,
    h("div", { class: "muted-text small" }, c.how === "app" ? (c.by_user ? `by ${c.by_user}` : "in KratosForge") : c.how === "check" ? "health check" : "seen at scan"));
}

function scDefText(d) {
  if (!d?.target) return "";
  const [kind, t] = Object.entries(d.target)[0] || [];
  if (!kind) return "";
  if (kind === "oneLake") {
    const item = scPage.byId?.[t.itemId];
    return `OneLake: ${item ? `${item.workspace_name} / ${item.name}` : t.itemId} / ${t.path}`;
  }
  return `${kind === "adlsGen2" ? "ADLS Gen2" : kind === "amazonS3" ? "Amazon S3" : kind}: ${t.location}${t.subpath || ""}`;
}

function scChangeList(changes, after, showWhere) {
  if (!changes.length) return h("p", { class: "muted-text" }, "No changes in this period. The first read of a lakehouse sets the starting point.");
  const undo = async (c) => {
    const what = { removed: `Recreate shortcut ${c.name} in ${c.lakehouse_name}, pointing at ${scDefText(c.before)}?`,
      repointed: `Point ${c.name} back at ${scDefText(c.before)}?`, added: `Remove shortcut ${c.name} from ${c.lakehouse_name}? The data it points at isn't touched.` }[c.kind];
    // Undoing an added shortcut removes it from OneLake, and undoing a repoint removes it and makes it again.
    if (!(await confirmDialog(c.kind === "removed" ? "Recreate shortcut" : "Undo change", what, c.kind === "removed" ? "Recreate" : "Undo",
      { danger: c.kind !== "removed" }))) return;
    const res = await attempt(() => rpc("shortcut_undo", { change_id: c.id }), "Not undone");
    if (!res) return;
    toast(c.kind === "removed" ? `Recreated ${c.name}` : `Undid the change to ${c.name}`, !!res.problems?.length, { log: true });
    after();
  };
  let day = "";
  const out = [];
  for (const c of changes) {
    const d = dateText(c.at);
    if (d !== day) { day = d; out.push(h("div", { class: "sc-day" }, d)); }
    const [cls, label] = SC_CHANGE[c.kind] || ["neu", c.kind];
    out.push(h("div", { class: "sc-change" },
      h("span", { class: "muted-text small" }, timeText(c.at)),
      h("span", { class: `pill-s ${cls}` }, label),
      h("div", {}, h("b", {}, c.name), showWhere ? h("span", { class: "muted-text" }, ` in ${c.workspace_name} / ${c.lakehouse_name}`) : null,
        c.kind === "repointed" ? h("div", { class: "small mono" }, h("s", {}, scDefText(c.before)), " → ", scDefText(c.after))
          : c.kind === "added" ? h("div", { class: "small mono" }, `→ ${scDefText(c.after)}`)
            : c.kind === "removed" ? h("div", { class: "small mono" }, `was → ${scDefText(c.before)}`) : null,
        c.note ? h("div", { class: "muted-text small" }, c.note) : null),
      h("span", { class: "small" }, c.how === "app" ? (c.by_user || "KratosForge") : c.how === "check" ? "Health check" : "Seen at scan",
        c.how === "scan" ? h("div", { class: "muted-text small" }, "made outside the app") : null),
      c.undoable && ["removed", "repointed", "added"].includes(c.kind) && (c.before || c.after)
        ? btn(c.kind === "removed" ? "Recreate" : "Undo", null, () => undo(c), "tool link small") : h("span", {})));
  }
  return h("div", { class: "sc-changes" }, out);
}

// New shortcut, a request for one, or pointing one somewhere else. opts.shortcut: the one to repoint.
async function shortcutDialog(mode, opts = {}) {
  const data = await onelakeCatalog();
  if (!data) return;
  const lakehouses = lakehouseRows(data);
  const s = opts.shortcut;
  const hostSel = h("select", { "aria-label": "Lakehouse" }, lakehouses.map((l) => h("option", { value: l.item_id }, `${l.name} · ${l.workspace_name}`)));
  if (s) hostSel.value = s.lakehouse_id;
  const host = () => lakehouses.find((l) => l.item_id === hostSel.value);
  const where = h("select", { "aria-label": "Put it in" });
  const loadWhere = async () => {
    const l = host();
    const own = l ? await rpc("list_tables", { workspace_id: l.workspace_id, lakehouse_id: l.item_id }).catch(() => ({ dbo: [] })) : { dbo: [] };
    fill(where, Object.keys(own || { dbo: [] }).map((x) => h("option", { value: `Tables/${x}` }, `Tables / ${x} (a table)`)), h("option", { value: "Files" }, "Files (a folder)"));
    sync();
  };
  hostSel.addEventListener("change", loadWhere);
  const folder = h("input", { placeholder: "landing/crm (optional)" });
  const folderField = h("label", { class: "field" }, "Folder under Files", folder);
  const name = h("input", { placeholder: "orders", value: s ? s.name : "" });
  const reason = h("textarea", { rows: 2, placeholder: "What it's for, so whoever decides knows" });
  let kind = s && s.target_type !== "OneLake" ? (s.target_type === "Amazon S3" ? "amazonS3" : "adlsGen2") : "oneLake";
  const seg = h("div", { class: "seg", role: "group", "aria-label": "Points at" });
  const source = h("div", { class: "form-grid" });
  const targetLh = h("select", { "aria-label": "Target lakehouse" }, lakehouses.map((l) => h("option", { value: l.item_id }, `${l.name} · ${l.workspace_name}`)));
  if (s?.item_id) targetLh.value = s.item_id;
  const targetTable = h("select", { "aria-label": "Table" });
  const targetPath = h("input", { placeholder: "Files/exports" });
  const notice = h("div", { class: "sc-notice" });
  let verdict = { blocked: [], warnings: [] };
  const loadTables = async () => {
    const l = lakehouses.find((x) => x.item_id === targetLh.value);
    fill(targetTable, h("option", { value: "" }, "Loading tables..."));
    const tables = l ? await rpc("list_tables", { workspace_id: l.workspace_id, lakehouse_id: l.item_id }).catch(() => ({})) : {};
    fill(targetTable, h("option", { value: "" }, "A folder in Files instead"),
      ...Object.entries(tables).flatMap(([sc, ts]) => ts.map((t) => h("option", { value: `Tables/${sc}/${t}` }, `${sc}.${t}`))));
    if (targetTable.options.length > 1) targetTable.selectedIndex = 1;
    if (s?.target_path && l?.item_id === s.item_id) {
      if ([...targetTable.options].some((o) => o.value === s.target_path)) targetTable.value = s.target_path;
      else { targetTable.value = ""; targetPath.value = s.target_path; }
    }
    sync();
  };
  targetLh.addEventListener("change", loadTables);
  const conn = h("select", { "aria-label": "Connection" }, h("option", { value: "" }, "Loading connections..."));
  const location = h("input", { placeholder: "https://account.dfs.core.windows.net", value: s?.location || "" });
  const subpath = h("input", { placeholder: "container/folder", value: (s?.subpath || "").replace(/^\//, "") });
  let connections = null;
  const loadConnections = async () => {
    if (!connections) connections = (await attempt(() => rpc("shortcut_connections"), "Could not list connections")) || [];
    const fitting = connections.filter((c) => c.type === kind);
    fill(conn, fitting.length ? fitting.map((c) => h("option", { value: c.id }, c.name))
      : [h("option", { value: "" }, `No ${kind === "adlsGen2" ? "ADLS Gen2" : "Amazon S3"} connection yet. Create one in Fabric (Settings > Manage connections).`)]);
    if (s?.connection_id) conn.value = s.connection_id;
    const c = fitting[0];
    if (c?.path && !location.value) location.value = c.path;
    sync();
  };
  const src = () => {
    const l = lakehouses.find((x) => x.item_id === targetLh.value);
    return kind === "oneLake"
      ? { type: "oneLake", workspace_id: l?.workspace_id, item_id: l?.item_id, path: targetTable.value || targetPath.value }
      : { type: kind, connection_id: conn.value, location: location.value, subpath: subpath.value };
  };
  const targetWs = () => lakehouses.find((x) => x.item_id === targetLh.value)?.workspace_name || "";
  let timer = null;
  let checked = null;  // the rules check for what the dialog shows now, once it has started
  let seq = 0;
  const runPreflight = async () => {
    const l = host();
    if (!l) return;
    const n = ++seq;
    // A check that couldn't run blocks like a rule would: the rules are unknown, not absent (QA-005).
    const v = await rpc("shortcut_preflight", { workspace_id: l.workspace_id, source: src(), workspace_name: l.workspace_name,
      target_workspace_name: targetWs() }).catch((err) => ({ blocked: [`The shortcut rules couldn't be checked: ${friendlyError(err).text}`], warnings: [], unchecked: true }));
    if (n !== seq) return;  // a newer check, of what the dialog shows now, answers instead
    verdict = v;
    fill(notice,
      verdict.blocked.length ? h("div", { class: "problems" }, h("b", {}, verdict.unchecked ? "The shortcut rules couldn't be checked"
        : mode === "request" ? "Whoever approves this will be stopped by the rules" : "The shortcut rules stop this"), gateList(verdict.blocked)) : null,
      verdict.warnings.length ? h("div", { class: "card pad warn-box" }, gateList(verdict.warnings)) : null);
    primary.lastChild.textContent = mode === "create" && verdict.blocked.length && !verdict.unchecked ? "Send as a request instead" : label;
    primary.disabled = !!verdict.unchecked || (mode === "repoint" && verdict.blocked.length > 0);
  };
  const preflight = () => {
    clearTimeout(timer);
    checked = null;
    timer = setTimeout(() => { checked = runPreflight(); }, 250);
  };
  const sync = () => {
    folderField.hidden = where.value !== "Files";
    targetPath.disabled = !!targetTable.value;
    if (mode !== "repoint" && (!name.value || name.dataset.auto === "1")) {
      const guess = kind === "oneLake" ? (targetTable.value || targetPath.value).split("/").pop() : subpath.value.split("/").pop();
      name.value = guess || "";
      name.dataset.auto = "1";
    }
    preflight();
  };
  name.addEventListener("input", () => { name.dataset.auto = "0"; });
  [where, targetTable, conn].forEach((x) => x.addEventListener("change", sync));
  [targetPath, subpath, location].forEach((x) => x.addEventListener("input", sync));
  const drawSource = () => {
    fill(seg, ...[["oneLake", "OneLake"], ["adlsGen2", "ADLS Gen2"], ["amazonS3", "Amazon S3"]].map(([id, l]) =>
      h("button", { class: kind === id ? "on" : null, onclick: () => { kind = id; drawSource(); } }, l)));
    if (kind === "oneLake") {
      fill(source, h("label", { class: "field" }, "Lakehouse", targetLh), h("label", { class: "field" }, "Table", targetTable),
        h("label", { class: "field" }, "Folder (when not a table)", targetPath));
      if (!targetTable.options.length) loadTables();
    } else {
      fill(source, h("label", { class: "field" }, "Connection", conn), h("label", { class: "field" }, "Location (URL)", location),
        h("label", { class: "field" }, "Subpath", subpath));
      loadConnections();
    }
    sync();
  };
  const label = { create: "Create shortcut", request: "Send request", repoint: "Repoint" }[mode];
  const whereTo = () => where.value === "Files" ? `Files/${folder.value.trim().replace(/^\/+|\/+$/g, "")}`.replace(/\/$/, "") : where.value;
  const done = (text) => {
    closeDialog();
    toast(text, false, { log: true });
    Object.assign(scPage, { data: null, changes: null, access: null });
    if (scHere()) render();
  };
  const submit = async () => {
    const l = host();
    if (!l) return;
    // Nothing is made or asked for before the rules have answered for what the dialog shows: a click
    // before the check answered waits for it, and stops when the answer changed what the button does.
    if (!checked) { clearTimeout(timer); checked = runPreflight(); }
    const shown = primary.lastChild.textContent;
    await checked;
    if (primary.disabled || primary.lastChild.textContent !== shown) return;
    const common = { workspace_id: l.workspace_id, item_id: l.item_id, source: src(), lakehouse_name: l.name,
      workspace_name: l.workspace_name, target_workspace_name: targetWs() };
    if (mode === "repoint") {
      if (!(await confirmDialog(`Repoint ${s.name}?`, `Fabric can't change where a shortcut points, so ${s.name} is removed and made again with the same name, pointing at the new target. If that fails, it's put back as it was. Runs reading it in between may fail.`, "Repoint", { danger: true }))) return;
      const res = await attempt(() => rpc("shortcut_repoint", { ...common, path: s.path, name: s.name }), "Not repointed");
      if (res) done(`Repointed ${s.name}${res.problems?.length ? `; ${res.problems.join("; ")}` : ""}`);
      return;
    }
    if (mode === "request" || verdict.blocked.length) {
      const res = await attempt(() => rpc("shortcut_request", { ...common, where: whereTo(), name: name.value.trim(), reason: reason.value.trim() }), "Request not sent");
      if (res) done(`Asked for shortcut ${name.value.trim()}. It shows on Shortcuts > Requests and Data mesh > Requests.`);
      return;
    }
    const rows = await attempt(() => rpc("create_shortcut", { ...common, where: whereTo(), name: name.value.trim() }), "Shortcut not created");
    if (rows) done(`Created shortcut ${name.value.trim()} in ${l.name}`);
  };
  const primary = btn(label, "databaseLink", submit, "tool primary");
  const intro = { create: "A shortcut shows data from somewhere else as a table or folder in a lakehouse. Nothing is copied; Fabric reads it where it is.",
    request: "Ask for a shortcut you can't make yourself, for example into another team's lakehouse. A platform admin, or the owner of the data product it points at, approves it, and approving makes it.",
    repoint: `Point ${s?.name} at something else. It keeps its name and place, so whatever reads it carries on.` }[mode];
  const overlay = showDialog({ create: "New shortcut", request: "Request a shortcut", repoint: `Repoint ${s?.name}` }[mode], h("div", { class: "scroll estate" },
    h("p", { class: "text" }, intro),
    mode === "repoint" ? h("p", { class: "small" }, h("b", {}, "Now: "), s.target_type === "OneLake" ? `${scPage.byId?.[s.item_id]?.name || "OneLake"} / ${s.target_path}` : s.target)
      : h("div", { class: "form-grid" }, h("label", { class: "field" }, "In lakehouse", hostSel), h("label", { class: "field" }, "Put it in", where), folderField,
        h("label", { class: "field" }, "Name", name)),
    h("div", { class: "row" }, h("b", {}, mode === "repoint" ? "Point it at" : "Points at"), seg), source,
    mode === "request" ? h("label", { class: "field" }, "Why", reason) : null,
    notice,
    h("p", { class: "muted-text small" }, "A table shortcut must point at a Delta table. ADLS Gen2 and S3 use a connection set up once in Fabric.")),
  [btn("Cancel", null, closeDialog, "tool outline"), primary]);
  overlay.querySelector(".dialog").classList.add("roomy");
  drawSource();
  if (mode !== "repoint") loadWhere();
}
