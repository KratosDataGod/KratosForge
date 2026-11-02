// Day-to-day data engineering: compare a table across environments or versions (before a
// promote), review schema drift, fix and replay quarantined rows, and freshness promises with
// Teams alerts.
// Loaded after meta.js. The entity page, the promote cards and Home call into this file;
// everything else here is self-contained.

const ENV_ORDER = ["dev", "test", "prod"];

// ---------- compare ----------

function compareSide(name, side, env) {
  const envSel = h("select", { "aria-label": `${side} environment` },
    ENV_ORDER.map((id) => h("option", { value: id, selected: id === env ? true : null }, ENV_LABEL[id])));
  const verSel = h("select", { "aria-label": `${side} version` }, h("option", { value: "" }, "Now"));
  const loadVersions = async () => {
    verSel.replaceChildren(h("option", { value: "" }, "Now"));
    const list = await rpc("meta_versions", { name, env: envSel.value }).catch(() => []);
    for (const v of list.slice(1)) {
      verSel.append(h("option", { value: v.version }, `Version ${v.version} · ${dateTimeText(v.at)}${v.operation ? ` · ${v.operation}` : ""}`));
    }
  };
  envSel.addEventListener("change", loadVersions);
  loadVersions();
  return {
    fields: h("div", { class: "cmp-side" }, h("b", {}, side), h("label", { class: "field" }, "Environment", envSel),
      h("label", { class: "field" }, "Version", verSel)),
    value: () => ({ env: envSel.value, version: verSel.value === "" ? null : Number(verSel.value) }),
  };
}

function sideLabel(s) {
  return `${ENV_LABEL[s.env]}${s.version === null || s.version === undefined ? "" : ` v${s.version}`}`;
}

async function compareDialog(name, leftEnv = "test", rightEnv = state.meta.env === "test" ? "dev" : state.meta.env) {
  if (leftEnv === rightEnv) leftEnv = rightEnv === "dev" ? "test" : "dev";
  const left = compareSide(name, "Left", leftEnv);
  const right = compareSide(name, "Right", rightEnv);
  const out = h("div", { class: "cmp-out" }, h("p", { class: "muted-text" }, "Pick two sides, then Compare. Dev against Test or Prod shows what a promote would change; an earlier version shows what the last run changed."));
  const run = async () => {
    const l = left.value(), r = right.value();
    out.replaceChildren(h("p", { class: "muted-text" }, `Comparing ${sideLabel(l)} with ${sideLabel(r)}...`));
    const res = await attempt(() => rpc("meta_compare", { name, left_env: l.env, right_env: r.env, left_version: l.version, right_version: r.version }), "Could not compare");
    if (res) out.replaceChildren(compareResult(res));
    else out.replaceChildren(h("p", { class: "muted-text" }, "Nothing to show."));
  };
  const overlay = showDialog(`Compare ${name}`, h("div", { class: "scroll dataops" },
    h("div", { class: "cmp-pick" }, left.fields, h("span", { class: "cmp-vs" }, "with"), right.fields), out),
  [btn("Close", null, closeDialog, "tool outline"), btn("Compare", "flow", run, "tool primary")]);
  overlay.querySelector(".dialog").classList.add("wide");
  // Version lists load in the background; the first compare uses "Now" on both sides.
  run();
}

function compareResult(res) {
  const L = sideLabel(res.left), R = sideLabel(res.right);
  const n = (x) => (x === null || x === undefined ? "missing" : Number(x).toLocaleString());
  const stat = (label, value, bad) => h("div", { class: `cmp-stat${bad ? " bad" : ""}` }, h("span", { class: "v" }, value), h("span", { class: "k" }, label));
  const cols = res.columns;
  const colNotes = [
    ...cols.only_left.map((c) => `${c} only in ${L}`),
    ...cols.only_right.map((c) => `${c} only in ${R}`),
    ...cols.type_changed.map((c) => `${c.column}: ${c.left} in ${L}, ${c.right} in ${R}`),
  ];
  const hashed = res.method === "hashed";
  const same = (hashed ? res.checksum?.match : res.compared_on_keys && !res.changed.count && !res.only_left.count && !res.only_right.count)
    && !colNotes.length && res.rows.left === res.rows.right;
  const fmt = (v) => (v === null || v === undefined ? "null" : String(v));
  const parts = [
    h("div", { class: "cmp-stats" },
      stat(`rows in ${L}`, n(res.rows.left)), stat(`rows in ${R}`, n(res.rows.right), res.rows.left !== res.rows.right),
      res.compared_on_keys ? stat(`keys only in ${L}`, n(res.only_left.count), res.only_left.count > 0) : null,
      res.compared_on_keys ? stat(`keys only in ${R}`, n(res.only_right.count), res.only_right.count > 0) : null,
      res.compared_on_keys ? stat("rows with changed values", n(res.changed.count), res.changed.count > 0) : null),
    h("p", { class: "muted-text small" }, hashed ? "Compared: counts, totals and a checksum" : "Compared: every row by key"),
    same ? h("div", { class: "pass-box" }, icon("ok", 16), hashed ? `Identical: the same rows in ${L} and ${R}, by checksum.`
      : `Identical: the same keys and the same values in ${L} and ${R}.`) : null,
    res.note ? h("p", { class: "muted-text" }, res.note) : null,
    colNotes.length ? h("div", { class: "problems" }, h("b", {}, "Columns differ"), h("ul", {}, colNotes.map((t) => h("li", {}, t)))) : null,
  ];
  const byCol = Object.entries(res.changed.by_column || {});
  if (byCol.length) {
    parts.push(h("h3", {}, "Which columns changed"), h("div", { class: "row wrap" },
      byCol.sort((a, b) => b[1] - a[1]).map(([c, k]) => h("span", { class: "pill-s info" }, `${c} · ${k.toLocaleString()} row${k === 1 ? "" : "s"}`))));
  }
  const moved = (res.totals || []).filter((t) => t.diff);
  if (res.totals?.length) {
    parts.push(h("h3", {}, "Totals"), h("div", { class: "output" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Column"), h("th", { class: "n" }, L), h("th", { class: "n" }, R), h("th", { class: "n" }, "Difference"))),
      h("tbody", {}, (moved.length ? moved : res.totals).map((t) => h("tr", {},
        h("td", { class: "mono" }, t.column), h("td", { class: "n" }, n(t.left)), h("td", { class: "n" }, n(t.right)),
        h("td", { class: `n${t.diff ? " diff" : ""}` }, t.diff > 0 ? `+${n(t.diff)}` : n(t.diff))))))),
    moved.length && moved.length < res.totals.length ? h("p", { class: "muted-text small" }, `${res.totals.length - moved.length} other number column${res.totals.length - moved.length === 1 ? " has" : "s have"} the same total.`) : null);
  }
  if (res.changed.rows?.length) {
    parts.push(h("h3", {}, `Changed rows${res.changed.count > res.changed.rows.length ? ` (first ${res.changed.rows.length})` : ""}`),
      h("div", { class: "output" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, res.keys.join(", ")), h("th", {}, "Column"), h("th", {}, L), h("th", {}, R))),
        h("tbody", {}, res.changed.rows.flatMap((r) => r.changes.map((c, i) => h("tr", {},
          h("td", { class: "mono" }, i ? "" : Object.values(r.key).join(", ")), h("td", { class: "mono" }, c.column),
          h("td", { class: "old" }, fmt(c.left)), h("td", { class: "new" }, fmt(c.right)))))))));
  }
  const onlyTable = (title, block) => {
    if (!block.rows.length) return null;
    const columns = Object.keys(block.rows[0]);
    return [h("h3", {}, `${title}${block.count > block.rows.length ? ` (first ${block.rows.length} of ${block.count})` : ""}`),
      dataTable({ columns, rows: block.rows })];
  };
  parts.push(onlyTable(`Only in ${L}`, res.only_left), onlyTable(`Only in ${R}`, res.only_right));
  parts.push(h("p", { class: "muted-text small" }, "Surrogate keys, run stamps and Type 2 dates are left out: each environment issues its own, so they always differ. Type 2 tables compare their current rows."));
  return h("div", { class: "cmp-result" }, parts);
}

// What a promote will build differently in the target: tables whose logic changed since their
// last good run there. Shown on each promote card, with a Compare for each changed table.
function promoteImpact(source, target) {
  const box = h("div", { class: "muted-text small" }, "Checking which tables change...");
  rpc("meta_promote_impact", { source, target }).then((res) => {
    if (res.problem) { box.replaceWith(h("p", { class: "muted-text small" }, `Can't read ${ENV_LABEL[target]} yet: ${friendlyError(res.problem).text}`)); return; }
    const changed = res.tables.filter((t) => t.state === "changed");
    const fresh = res.tables.filter((t) => t.state === "new");
    if (!changed.length && !fresh.length) {
      box.replaceWith(h("p", { class: "muted-text small" }, `No table's logic differs from what last ran in ${ENV_LABEL[target]}.`));
      return;
    }
    box.replaceWith(h("div", { class: "promote-impact dataops" },
      h("b", {}, "What this promote changes"),
      changed.map((t) => [h("div", { class: "row" }, h("span", { class: "pill-s wrn" }, "Logic changed"), h("span", { class: "mono grow" }, t.entity),
        h("span", { class: "muted-text small" }, `last ran ${relTime(t.last_run)}`),
        btn(`Compare ${ENV_LABEL[source]} with ${ENV_LABEL[target]}`, "flow", () => compareDialog(t.entity, target, source), "tool link small")),
        // The settings that changed since the version promoted there, by meaning (meta/diff.py).
        (t.changes || []).length ? h("ul", { class: "gate-list small" }, t.changes.slice(0, 6).map((c) => h("li", {}, c.text)),
          t.changes.length > 6 ? h("li", { class: "muted-text" }, `and ${t.changes.length - 6} more`) : null) : null]),
      fresh.length ? h("div", { class: "muted-text small" }, `Never run in ${ENV_LABEL[target]} yet: ${fresh.map((t) => t.entity).join(", ")}`) : null));
  }).catch(() => box.remove());
  return box;
}

// ---------- release records ----------

// The last release promoted to an environment, from its meta.deployments row: which metadata,
// which commit, which engine, who and when, and whether the stored record still matches its id.
function lastRelease(env, onChange = null) {
  if (!previewShown()) return null;
  const box = h("div", { class: "muted-text small" }, `Reading the last release to ${ENV_LABEL[env]}...`);
  rpc("meta_last_release", { env }).then((res) => {
    if (res.problem) { box.replaceWith(h("p", { class: "muted-text small" }, `Can't read ${ENV_LABEL[env]}'s releases yet: ${friendlyError(res.problem).text}`)); return; }
    const r = res.release;
    if (!r) { box.replaceWith(h("p", { class: "muted-text small" }, `Nothing has been promoted to ${ENV_LABEL[env]} yet.`)); return; }
    const row = (label, ...value) => h("div", { class: "release-row" }, h("span", { class: "muted-text" }, label), h("span", {}, value));
    const record = r.receipt_id ? [h("span", { class: "mono" }, `${r.receipt_id.slice(0, 8)}…`),
      h("span", { class: `pill-s ${r.verified ? "pass" : "fail"}` }, r.verified ? "checks out" : "doesn't match its id"),
      btn("Copy record", null, () => copyText(r.receipt, "Record"), "tool link small")]
      : h("span", { class: "muted-text" }, "none (promoted before release records)");
    box.replaceWith(h("div", { class: "release-card dataops" },
      h("div", { class: "row" }, preview("releases", h("b", { class: "grow" }, `Last release to ${ENV_LABEL[env]}`)),
        env !== "dev" ? preview("rollback", btn("Roll back...", "history", () => rollbackDialog(env, onChange), "tool link small")) : null),
      row("Metadata version", h("span", { class: "mono" }, r.version || "")),
      row("Code", releaseCommitText(r)),
      r.engine_version ? row("Engine", `KratosForge ${r.engine_version}`, r.lock_hash ? h("span", { class: "muted-text" }, `, lock ${r.lock_hash.slice(0, 4)}…${r.lock_hash.slice(-4)}`) : null) : null,
      row("Promoted", `from ${ENV_LABEL[r.source_env] || r.source_env || "?"}${r.by ? ` by ${r.by}` : ""}, `,
        when(r.at)),
      row("Record", record)));
  }).catch(() => box.remove());
  return box;
}

// Put Test or Prod back on an earlier release, from the files that environment keeps
// (meta/snapshot.py). Nothing is read from Git or Dev; tables keep their rows until they run.
async function rollbackDialog(env, onChange) {
  const res = await metaCall("meta_releases", { env }, "Releases not available");
  if (!res) return;
  if (res.problem) { toast(`Can't read ${ENV_LABEL[env]}'s releases: ${friendlyError(res.problem).text}`, true); return; }
  const earlier = res.releases.filter((r) => !r.current && r.kept);
  let pick = earlier.length ? earlier[0].version : null;
  const what = h("div", { class: "rollback-what" });
  const go = btn("Roll back", null, async () => {
    if (!pick) return;
    if (!state.user && !(await signIn())) return;
    const out = await metaCall("meta_rollback", { env, version: pick }, "Rollback blocked");
    if (!out) return;
    closeDialog();
    toast(`${ENV_LABEL[env]} is back on metadata ${out.version}`, false, { log: true });
    if (out.fabric && out.fabric !== "synced") toast(`Fabric runs: ${out.fabric}`, true);
    onChange?.();
  }, "tool primary");
  const label = (r) => `${r.version} (${relTime(r.at)})`;
  const show = async () => {
    go.disabled = !pick;
    go.textContent = pick ? `Roll back ${ENV_LABEL[env]} to ${pick}` : "Roll back";
    if (!pick) { what.replaceChildren(); return; }
    what.replaceChildren(h("p", { class: "muted-text small" }, "Working out what changes..."));
    const p = await metaCall("meta_rollback_preview", { env, version: pick }, "Preview not available");
    if (!p || p.to !== pick) return;
    const pills = { changed: ["wrn", "Logic changes"], removed: ["fail", "Removed"], new: ["info", "Comes back"], unreadable: ["neu", "Can't compare"] };
    const rows = p.entities.map((t) => {
      const [cls, text] = t.state === "changed" && !t.logic ? ["neu", "Settings change"] : pills[t.state] || ["neu", t.state];
      const detail = t.state === "removed" ? h("span", { class: "muted-text" }, `Not in ${pick}. Its table stays; nothing runs it.`)
        : t.problem ? h("span", { class: "muted-text" }, t.problem)
        : h("ul", { class: "gate-list small" }, t.changes.slice(0, 6).map((c) => h("li", {}, c.text)),
          t.changes.length > 6 ? h("li", { class: "muted-text" }, `and ${t.changes.length - 6} more`) : null);
      return h("div", { class: "rollback-entity" }, h("span", { class: "mono" }, t.entity), h("span", {}, h("span", { class: `pill-s ${cls}` }, text)), detail);
    });
    what.replaceChildren(
      h("b", {}, `What goes back in ${ENV_LABEL[env]}`),
      rows.length ? h("div", { class: "rollback-list" }, rows) : h("p", { class: "muted-text small" }, "No entity's settings differ between the two releases; other project files may."),
      h("div", { class: "rollback-warn" }, h("b", {}, "Tables keep their data"),
        h("span", {}, `Rolling back changes which files ${ENV_LABEL[env]} runs, not what its tables hold. A table whose logic changes asks whether to rebuild on its next run. To put its rows back now, restore it from History.`)));
  };
  const row = (r) => {
    const can = !r.current && r.kept;
    const input = h("input", { type: "radio", name: "rollback-to", value: r.version, disabled: !can, checked: r.version === pick && can,
      "aria-label": r.current ? "Current release" : `Roll back to ${r.version}`, onchange: () => { pick = r.version; show(); } });
    const state_ = r.current ? ["neu", "Running now"] : !r.kept ? ["neu", "files not kept"] : r.receipt_id ? (r.verified ? ["pass", "checks out"] : ["fail", "doesn't match its id"]) : ["neu", "no record"];
    return h("label", { class: `rollback-row${can ? "" : " off"}` }, input,
      h("span", { class: "mono" }, r.version),
      h("span", {}, `${relTime(r.at)}${r.by ? `, ${r.by}` : ""}${r.source_env === env ? " (rollback)" : ""}`),
      h("span", {}, releaseCommitText(r)),
      h("span", {}, h("span", { class: `pill-s ${state_[0]}` }, state_[1])));
  };
  const body = h("div", { class: "rollback-view" },
    h("p", { class: "muted-text" }, `Puts ${ENV_LABEL[env]} back on the files an earlier promote kept there, exactly as they were. Nothing is read from Git or from Dev.`),
    h("fieldset", { class: "rollback-releases" }, h("legend", {}, `Releases to ${ENV_LABEL[env]}`), res.releases.map(row)),
    earlier.length ? null : h("p", { class: "muted-text small" }, `${ENV_LABEL[env]} keeps no earlier release to go back to. Releases promoted before this version of the app didn't keep their files.`),
    what,
    h("p", { class: "muted-text small" }, `The rollback is recorded as a new release in ${ENV_LABEL[env]}, naming the release it went back to. Your project folder on this PC is not changed.`));
  showDialog(`Roll back ${ENV_LABEL[env]}`, body, [btn("Cancel", null, closeDialog, "tool"), go])
    .querySelector(".dialog")?.classList.add("wide");
  show();
}

// Where the released files stood in Git, in one line.
function releaseCommitText(r) {
  if (!r.receipt_id) return "not recorded";
  if (!r.commit) return r.dirty === null || r.dirty === undefined ? "not in Git" : "nothing committed yet";
  const at = `commit ${r.commit.slice(0, 7)}${r.branch ? ` on ${r.branch}` : ""}`;
  if (r.dirty) return `${at}, with uncommitted changes`;
  return `${at}, ${r.pushed ? "pushed" : "not pushed"}`;
}

// ---------- column lineage ----------

const LINEAGE_HOW = { source: "source", derived: "derived", lookup: "lookup", key: "key", framework: "framework", custom: "custom" };
const LINEAGE_PILL = { declared: "info", inferred: "neu", opaque: "cus" };

// Every column the entity writes and where it comes from, worked out from the project's files.
// Picking a column shows the steps back to where it starts and what reads it further on.
async function columnLineage(name, pick = null) {
  const res = await metaCall("meta_lineage", { name }, "Lineage not available");
  if (!res) return;
  const columns = Object.entries(res.columns);
  const detail = h("div", { class: "lineage-detail" }, h("p", { class: "muted-text" }, "Pick a column to see where it comes from."));
  const list = h("div", { class: "lineage-cols" }, columns.map(([col, c]) =>
    h("button", { class: "lineage-col", onclick: (ev) => { list.querySelectorAll(".on").forEach((b) => b.classList.remove("on")); ev.currentTarget.classList.add("on"); show(col); } },
      h("span", { class: "mono" }, col), h("span", { class: "muted-text small" }, `${LINEAGE_HOW[c.how] || c.how}, `),
      h("span", { class: `pill-s ${LINEAGE_PILL[c.label] || "neu"}` }, c.label))));
  const show = async (col) => {
    detail.replaceChildren(h("p", { class: "muted-text" }, "Working it out..."));
    const r = await metaCall("meta_lineage", { name, column: col }, "Lineage not available");
    if (!r) return;
    const input = (i) => i.entity ? `${i.entity}.${i.column}` : `${i.value}${i.column ? ` (${i.column})` : ""}`;
    detail.replaceChildren(
      h("b", {}, `Where ${col} comes from`),
      h("table", { class: "grid small" },
        h("thead", {}, h("tr", {}, h("th", {}, "Column"), h("th", {}, "How"), h("th", {}, "Label"), h("th", {}, "Reads"))),
        h("tbody", {}, r.upstream.map((s) => h("tr", {},
          h("td", { class: "mono" }, `${s.entity}.${s.column}`), h("td", {}, LINEAGE_HOW[s.how] || s.how),
          h("td", {}, h("span", { class: `pill-s ${LINEAGE_PILL[s.label] || "neu"}` }, s.label)),
          h("td", { class: "small" }, [s.expr ? h("div", { class: "mono" }, s.expr) : null,
            (s.inputs || []).length ? h("div", {}, s.inputs.map(input).join(", ")) : null,
            s.note ? h("div", { class: "muted-text" }, s.note) : null,
            s.custom_may_change ? h("div", { class: "muted-text" }, `custom code may change it (${s.custom_may_change.join(", ")})`) : null]))))),
      h("b", {}, "Read further on"),
      r.downstream.length ? h("ul", { class: "gate-list small" }, r.downstream.map((d) => h("li", {},
        h("button", { class: "tool link small", onclick: () => { closeDialog(); openEntity(d.entity); } }, `${d.entity}.${d.column}`),
        ` (${LINEAGE_HOW[d.how] || d.how})`)))
        : h("p", { class: "muted-text small" }, `Nothing in this project reads ${name}.${col}. Reports in Power BI aren't shown here yet.`));
  };
  const overlay = showDialog(`Column lineage: ${name}`, h("div", { class: "lineage-view" },
    h("p", { class: "muted-text small" }, "Worked out from the project's files, not from running anything. Declared means a file names the link. Inferred means the name matches. Opaque means your Python decides."),
    h("div", { class: "lineage-split" }, list, detail)), [btn("Close", null, closeDialog, "tool outline")]);
  overlay.querySelector(".dialog").classList.add("wide");
  if (pick && res.columns[pick]) list.querySelectorAll(".lineage-col")[columns.findIndex(([c]) => c === pick)].click();
}

// ---------- schema drift ----------

// The entity page's drift card: what the source sends that the contract doesn't expect, and one
// place to settle each difference. Decisions are written into the entity file.
function driftCard(e) {
  const card = h("div", { class: "card pad dataops drift" }, h("div", { class: "row" }, h("b", { class: "grow" }, "Schema drift")),
    h("p", { class: "muted-text small" }, "Checking the source..."));
  rpc("meta_drift", { name: e.name, env: state.meta.env }).then((d) => { if (card.isConnected) card.replaceWith(driftPanel(e, d)); })
    .catch((err) => card.lastChild.replaceWith(h("p", { class: "muted-text small" }, friendlyError(err).text)));
  return card;
}

function driftPanel(e, d) {
  const add = {}, retype = {}, rename = {}, remove = new Set();
  const policy = h("select", { "aria-label": "When the source adds columns" },
    h("option", { value: "", selected: d.own_policy ? null : true }, `Layer default: ${DRIFT_LABEL[d.layer_policy]}`),
    Object.entries(DRIFT_LABEL).map(([v, label]) => h("option", { value: v, selected: d.own_policy === v ? true : null }, label)));
  const applyBtn = btn("Save to config", "save", () => apply(), "tool primary small", { disabled: true });
  const sync = () => {
    applyBtn.disabled = !(Object.keys(add).length || Object.keys(retype).length || Object.keys(rename).length || remove.size
      || policy.value !== (d.own_policy || ""));
  };
  policy.addEventListener("change", sync);
  const addedRows = d.added.map((c) => h("label", { class: "check drift-row" },
    h("input", { type: "checkbox", onchange: (ev) => { if (ev.target.checked) add[c.column] = c.type; else delete add[c.column]; sync(); } }),
    h("span", { class: "pill-s info" }, "New"), h("span", { class: "mono grow" }, c.column), h("span", { class: "muted-text small" }, `${c.type} · add to contract`)));
  const droppedRows = d.dropped.map((c) => {
    const pick = h("select", { "aria-label": `What to do about ${c.column}`, onchange: (ev) => {
      delete rename[c.column]; remove.delete(c.column);
      if (ev.target.value === "__remove") remove.add(c.column);
      else if (ev.target.value) rename[c.column] = ev.target.value;
      sync();
    } }, h("option", { value: "" }, "Leave it (runs stop)"),
    d.added.map((a) => h("option", { value: a.column }, `Renamed to ${a.column}`)),
    c.key ? null : h("option", { value: "__remove" }, "Remove from contract"));
    return h("div", { class: "drift-row" }, h("span", { class: "pill-s fail" }, "Gone"), h("span", { class: "mono grow" }, c.column), pick);
  });
  const retypedRows = d.retyped.map((c) => h("label", { class: "check drift-row" },
    h("input", { type: "checkbox", onchange: (ev) => { if (ev.target.checked) retype[c.column] = c.source; else delete retype[c.column]; sync(); } }),
    h("span", { class: "pill-s wrn" }, "Type"), h("span", { class: "mono grow" }, c.column),
    h("span", { class: "muted-text small" }, `${c.contract} to ${c.source}`)));
  const apply = async () => {
    const res = await metaCall("meta_drift_apply", { name: e.name, add, retype, rename, remove: [...remove], policy: policy.value }, "Not saved");
    if (!res) return;
    toast(res.errors.length ? `Saved with ${res.errors.length} problem${res.errors.length === 1 ? "" : "s"} to fix` : "Saved. The next run uses the new contract.");
    render();
  };
  const any = d.added.length || d.dropped.length || d.retyped.length;
  return h("div", { class: "card pad dataops drift" },
    h("div", { class: "row" }, h("b", { class: "grow" }, "Schema drift"),
      any ? h("span", { class: "pill-s wrn" }, `${d.added.length + d.dropped.length + d.retyped.length} to review`) : null),
    d.problem ? h("p", { class: "muted-text small" }, `Couldn't read the source in ${ENV_LABEL[d.env]}: ${friendlyError(d.problem).text}`)
      : any ? h("p", { class: "muted-text small" }, `${d.source} in ${ENV_LABEL[d.env]} differs from the contract. Tick or pick what to change.`)
        : h("p", { class: "muted-text small" }, `${d.source || "The source"} in ${ENV_LABEL[d.env]} matches the contract.`),
    droppedRows, addedRows, retypedRows,
    h("label", { class: "field" }, "When the source adds columns", policy),
    schemaChanges(d.history),
    h("div", { class: "row" }, h("span", { class: "spacer" }), applyBtn));
}

// The table's latest schema changes from meta.schema_history, newest first. Rows from before
// PB-031 have only the columns a drift policy kept; a table's first schema isn't a change.
function schemaChanges(history) {
  const rows = (history || []).filter((x) => x.change !== "created").slice(0, 3);
  if (!rows.length) return null;
  const text = rows.map((x) => (x.detail ? `${x.change} ${x.detail}` : `kept ${x.columns}`)
    + (x.delta_version != null ? ` (Delta version ${x.delta_version})` : ""));
  return h("div", { class: "muted-text small" }, `Schema changes: ${text.join("; ")}`);
}

// ---------- quarantine ----------

// The entity page's quarantine card: hidden until there is something held.
function quarantineCard(e) {
  const slot = h("div", {});
  const env = state.meta.env;
  rpc("meta_quarantine", { name: e.name, env }).then((q) => {
    if (q.total && slot.isConnected) slot.replaceWith(quarantinePanel(e, q));
  }).catch(() => {});
  return slot;
}

function quarantinePanel(e, q) {
  const env = q.env;
  const picked = new Set();
  const edits = {};
  const editable = new Set(q.editable || []);
  const count = h("span", { class: "muted-text" });
  const replayBtn = previewMarked("quarantineReplay", btn("Replay selected", "play", () => replay(), "tool primary small", { disabled: true }));
  const discardBtn = btn("Discard selected", "delete", () => discard(), "tool outline small", { disabled: true });
  const sync = () => {
    const edited = Object.keys(edits).filter((id) => Object.keys(edits[id]).length).length;
    count.textContent = picked.size ? `${picked.size} selected${edited ? `, ${edited} fixed` : ""}` : "";
    replayBtn.disabled = discardBtn.disabled = !picked.size;
  };
  const cell = (row, c) => {
    const v = row[c] === null ? "" : String(row[c]);
    if (!editable.has(c)) return h("td", { class: "ro" }, v || "null");
    const input = h("input", { class: "qcell", value: v, "aria-label": `${c} of row ${row._qid}`,
      oninput: (ev) => {
        edits[row._qid] = edits[row._qid] || {};
        if (ev.target.value === v) delete edits[row._qid][c]; else edits[row._qid][c] = ev.target.value;
        ev.target.classList.toggle("changed", ev.target.value !== v);
        if (ev.target.value !== v && !picked.has(row._qid)) { picked.add(row._qid); boxes[row._qid].checked = true; }
        sync();
      } });
    return h("td", {}, input);
  };
  const boxes = {};
  const all = h("input", { type: "checkbox", "aria-label": "Select all", onchange: (ev) => {
    for (const r of q.rows) { boxes[r._qid].checked = ev.target.checked; if (ev.target.checked) picked.add(r._qid); else picked.delete(r._qid); }
    sync();
  } });
  const rows = q.rows.map((r) => {
    boxes[r._qid] = h("input", { type: "checkbox", "aria-label": `Select row ${r._qid}`, onchange: (ev) => { if (ev.target.checked) picked.add(r._qid); else picked.delete(r._qid); sync(); } });
    return h("tr", {}, h("td", {}, boxes[r._qid]), h("td", {}, h("span", { class: "pill-s fail" }, r._check || "held")),
      h("td", { class: "muted-text small" }, r._loaded_at || ""), q.columns.map((c) => cell(r, c)));
  });

  const replay = async () => {
    const ids = [...picked];
    const res = await attempt(() => rpc("meta_replay", { name: e.name, env, ids, edits }), "Replay failed");
    if (!res) return;
    if (res.status !== "ok") {
      logActivity({ status: "failed", title: `Replay ${e.name} · ${ENV_LABEL[env]}`, text: res.message });
      toast(`Nothing was replayed: ${res.message}`, true);
      return;
    }
    const text = `${res.rows_written} row${res.rows_written === 1 ? "" : "s"} loaded into ${e.name}${res.held ? `, ${res.held} held again (they still fail a check)` : ""}`;
    logActivity({ status: res.held ? "failed" : "ok", title: `Replay ${e.name} · ${ENV_LABEL[env]}`, text });
    if (res.rows_written && res.downstream.length) {
      showDialog("Replayed", h("div", {}, h("p", { class: "text" }, `${text}.`),
        h("p", { class: "text" }, `Downstream tables read ${e.name} incrementally, so they won't pick up older rows on their own. Rebuild them to include the replayed rows: ${res.downstream.join(", ")}.`)),
      [btn("Not now", null, () => { closeDialog(); render(); }, "tool outline"),
        btn("Rebuild downstream", "sync", () => { closeDialog(); runEntities(res.downstream, { backfill: "full" }); }, "tool primary")]);
    } else {
      toast(text);
      render();
    }
  };
  const discard = async () => {
    const ids = [...picked];
    if (!(await confirmDialog(`Discard ${ids.length} row${ids.length === 1 ? "" : "s"}?`, "They leave quarantine for good and are never loaded. Use this for test data or true duplicates.", "Discard", { danger: true }))) return;
    const res = await attempt(() => rpc("meta_discard_quarantine", { name: e.name, env, ids }), "Not discarded");
    if (res) { toast(`Discarded ${ids.length} row${ids.length === 1 ? "" : "s"}`); render(); }
  };

  const reasons = Object.entries(q.checks || {}).map(([c, k]) => `${k} by ${c || "a check"}`).join(" · ");
  return h("div", { class: "card pad dataops quarantine" },
    h("div", { class: "row wrap" }, h("b", {}, `Quarantine in ${ENV_LABEL[env]} · ${q.total} row${q.total === 1 ? "" : "s"} held`),
      h("span", { class: "muted-text" }, reasons), h("span", { class: "spacer" }), count, discardBtn, replayBtn),
    h("p", { class: "muted-text small" }, "Fix values in place, or fix the check in the config and save, then replay. Replayed rows go through the contract, derived columns and every check again; rows that still fail stay here with the new reason."),
    h("div", { class: "output qtable" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, all), h("th", {}, "Held by"), h("th", {}, "Held on"), q.columns.map((c) => h("th", { class: editable.has(c) ? "" : "ro" }, c)))),
      h("tbody", {}, rows))),
    q.total > q.rows.length ? h("p", { class: "muted-text small" }, `Showing the newest ${q.rows.length} of ${q.total}.`) : null);
}

// ---------- freshness ----------

const fresh = { items: null, alerts: false, alertsUnreadable: false, timer: null };

function promiseText(f) {
  if (!f) return "";
  const parts = [];
  if (f.within_hours) parts.push(`every ${f.within_hours} h`);
  if (f.by) parts.push(`by ${f.by} each day`);
  return `${parts.join(" and ")} in ${f.envs.map((x) => ENV_LABEL[x]).join(", ")}`;
}

function freshnessCard(e) {
  const f = e.freshness;
  const status = h("div", { class: "muted-text small" });
  if (f) {
    rpc("meta_freshness", {}).then((res) => {
      const mine = res.items.filter((x) => x.entity === e.name);
      status.replaceChildren(...mine.map((x) => h("div", { class: "row" }, freshPill(x), h("span", {}, ENV_LABEL[x.env]),
        h("span", { class: "muted-text small" }, x.status === "late" ? x.reasons.join("; ") : x.last_ok ? ["updated ", when(x.last_ok)] : "never loaded"))));
    }).catch(() => {});
  }
  return h("div", { class: "card pad dataops" },
    h("div", { class: "row" }, h("b", { class: "grow" }, "Freshness"), btn(f ? "Change" : "Set a promise", "history", () => freshnessDialog(e), "tool link small")),
    h("div", { class: f ? "" : "muted-text" }, f ? `Updated ${promiseText(f)}` : "No promise yet. Say when this table should be updated, and late runs show on Home and in the bell."),
    status);
}

function freshPill(x) {
  const [cls, label] = x.status === "late" ? ["fail", "Late"] : x.status === "never" ? ["neu", "Never loaded"] : x.last_failed ? ["wrn", "Last run failed"] : ["pass", "Fresh"];
  return h("span", { class: `pill-s ${cls}` }, label);
}

function freshnessDialog(e) {
  const f = e.freshness || { within_hours: 24, by: "", envs: ["test", "prod"] };
  const hours = h("input", { type: "number", min: "0", step: "1", value: f.within_hours ?? "", placeholder: "for example 24" });
  const by = h("input", { type: "time", value: f.by || "" });
  const envBoxes = ENV_ORDER.map((id) => [id, h("input", { type: "checkbox", checked: f.envs.includes(id) ? true : null })]);
  const save = async (clear) => {
    const params = clear ? { name: e.name } : {
      name: e.name, within_hours: hours.value ? Number(hours.value) : null, by: by.value || null,
      envs: envBoxes.filter(([, b]) => b.checked).map(([id]) => id),
    };
    if (!clear && !params.within_hours && !params.by) { toast("Give a number of hours, a time of day, or both", true); return; }
    const res = await metaCall("meta_set_freshness", params, "Not saved");
    if (!res) return;
    closeDialog();
    toast(clear ? "Freshness promise removed" : `Saved: ${e.name} is updated ${promiseText(res.freshness)}`);
    checkFreshness();
    render();
  };
  showDialog(`Freshness for ${e.name}`, h("div", { class: "dataops" },
    h("p", { class: "text" }, "When this table should have a successful run. Saved in the entity file, so it is promoted with the rest of the config."),
    h("label", { class: "field" }, "Updated at least every (hours)", hours),
    h("label", { class: "field" }, "And updated each day by (this PC's time, optional)", by),
    h("div", { class: "field" }, "In", h("div", { class: "row" }, envBoxes.map(([id, b]) => h("label", { class: "check" }, b, ENV_LABEL[id])))),
    alertsLine()),
  [e.freshness ? btn("Remove promise", "delete", () => save(true), "tool outline") : null, h("span", { class: "spacer" }),
    btn("Cancel", null, closeDialog, "tool outline"), btn("Save", "save", () => save(false), "tool primary")].filter(Boolean));
}

// Shown when the saved Teams address is encrypted for another Windows account or PC (sealed_settings.py).
const ALERTS_UNREADABLE = "The saved Teams address can't be read on this PC or account. Enter it again.";

function alertsLine() {
  if (fresh.alertsUnreadable) {
    return h("div", { class: "row wrap wrn-text small" }, icon("alert", 14), ALERTS_UNREADABLE,
      btn("Set up Teams alerts", null, alertsDialog, "tool link small"));
  }
  return h("div", { class: "row wrap muted-text small" }, icon("alert", 14),
    fresh.alerts ? "New incidents are also posted to your Teams channel, unless an agent already checks that environment." : "New incidents can also be posted to a Teams channel.",
    btn(fresh.alerts ? "Change" : "Set up Teams alerts", null, alertsDialog, "tool link small"));
}

async function alertsDialog() {
  const cur = await rpc("meta_alerts", {}).catch(() => ({ teams_webhook: "" }));
  const url = h("input", { value: cur.teams_webhook || "", placeholder: "https://...", spellcheck: "false" });
  const save = async (test) => {
    const res = await attempt(() => rpc("meta_set_alerts", { teams_webhook: url.value.trim(), test }), test ? "Test message not sent" : "Not saved");
    if (!res) return;
    fresh.alerts = !!res.teams_webhook;
    fresh.alertsUnreadable = !!res.unreadable;
    closeDialog();
    toast(res.teams_webhook ? (test ? "Sent a test message to Teams and saved" : "Teams alerts are on") : "Teams alerts are off");
  };
  showDialog("Teams alerts", h("div", { class: "dataops" },
    h("p", { class: "text" }, "In the Teams channel, add the Workflows template \"Post to a channel when a webhook request is received\", then paste its address here. Each new incident (a failed or late table, a missed objective, an agent that stopped) is posted once, and again when it recovers. Environments that a running agent checks are left to the agent."),
    cur.unreadable ? h("p", { class: "wrn-text small" }, ALERTS_UNREADABLE) : null,
    h("label", { class: "field" }, "Webhook address", url),
    h("p", { class: "muted-text small" }, "This PC sends them while the app is open; the address stays on this PC. A message Teams doesn't take is sent with the next check.")),
  [btn("Cancel", null, closeDialog, "tool outline"), btn("Save", null, () => save(false), "tool outline"), btn("Send a test and save", "alert", () => save(true), "tool primary")]);
}

// Checked at startup and every 10 minutes. Late tables show on Home; problems become incidents
// (incident_service.incidents_watch): an environment a running agent checks is only read, any other
// is checked here, and its new incidents go, once each, to this PC's Teams webhook, which hears
// about a table only from its incident. With Preview features on they go to the bell as well.
async function checkFreshness() {
  clearTimeout(fresh.timer);
  fresh.timer = setTimeout(checkFreshness, 600000);
  const [res, watch] = await Promise.all([
    rpc("meta_freshness", {}).catch(() => null),
    rpc("incidents_watch", { notify: true }).catch(() => null),
  ]);
  if (res) {
    fresh.items = res.items;
    fresh.alerts = res.alerts;
    fresh.alertsUnreadable = !!res.alerts_unreadable;
    for (const x of previewShown() ? [] : res.items) {
      const id = `fresh:${x.env}:${x.entity}:${x.last_ok}`;
      if (x.status === "late" && !activity.items.some((i) => i.id === id)) {
        logActivity({ id, status: "failed", title: `${x.entity} is late · ${ENV_LABEL[x.env]}`, text: `${x.reasons.join("; ")}.` });
      }
    }
    if (res.project_error) logActivity({ id: "fresh:project-error", status: "error", title: "Freshness not checked", text: "The metadata project couldn't be read, so late tables can't be found.", details: res.project_error });
  }
  for (const e of watch?.envs || []) {
    for (const i of previewShown() ? e.incidents : []) {
      const id = `inc:${i.id}`;
      if (i.state !== "open" || activity.items.some((x) => x.id === id)) continue;
      logActivity({ id, status: "failed", title: i.title, text: [i.text, e.mode === "agent" ? `Raised in ${ENV_LABEL[e.env]}'s incident list.` : ""].filter(Boolean).join(" ") });
    }
    if (e.notified?.error) logActivity({ id: "inc:teams-error", status: "error", title: "Teams alert not sent", text: `It will go with the next check. ${friendlyError(e.notified.error).text}`, details: e.notified.error });
  }
  if (state.view === "home") renderFreshHome();
}

// Home: late tables under "Needs your attention", and a quiet line when every promise is kept.
function renderFreshHome() {
  const slot = document.getElementById("home-fresh");
  if (!slot || !fresh.items) return;
  const late = fresh.items.filter((x) => x.status === "late");
  const kept = fresh.items.filter((x) => x.status === "fresh").length;
  slot.replaceChildren(...(late.length ? [h("h2", {}, "Late tables"), ...late.slice(0, 6).map((x) => h("div", { class: "card attention" },
    h("span", { class: "pill-s fail" }, "Late"),
    h("div", { class: "grow" },
      h("div", {}, h("b", { class: "mono" }, x.entity), ` in ${ENV_LABEL[x.env]} · promised ${x.expected}`),
      h("div", { class: "reason" }, x.reasons.join("; ")),
      h("div", { class: "muted-text small" }, x.last_ok ? ["Last updated ", when(x.last_ok)] : "Never loaded"),
      x.failure ? h("div", { class: "muted-text small" }, `Its last run failed: ${x.failure}`) : null),
    btn("Open entity", null, () => { setGlobalEnv(x.env); openEntity(x.entity); }, "tool outline"),
    x.failed_run ? btn("Open run", null, () => showRun(x.failed_run, x.env), "tool primary") : null))]
    : kept ? [h("p", { class: "muted-text small fresh-ok" }, icon("ok", 14), `Every freshness promise is kept (${kept} table${kept === 1 ? "" : "s"}).`)] : []));
  state.homeSummary?.();
}
