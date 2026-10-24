// Version history (lifecycle L1): every save keeps a version on this PC (one per few minutes while
// autosave runs), named checkpoints, Compare with a version or the last commit, and Restore. The
// engine keeps versions outside every repository (notebook_versions.py); Restore keeps the notebook
// as it is now first, so a restore can be undone.

const NBV_KIND = { auto: "Saved", checkpoint: "Checkpoint", "before restore": "Before a restore", "changed outside": "Changed outside the app" };

// Versions grouped by day, newest first: [{ day, rows }].
function nbvGroups(versions, today = new Date()) {
  const label = (iso) => {
    const d = new Date(iso);
    const days = Math.round((new Date(today.toDateString()) - new Date(d.toDateString())) / 86400000);
    return days === 0 ? "Today" : days === 1 ? "Yesterday" : d.toLocaleDateString();
  };
  const out = [];
  for (const v of versions) {
    const day = label(v.at);
    if (out.length && out[out.length - 1].day === day) out[out.length - 1].rows.push(v);
    else out.push({ day, rows: [v] });
  }
  return out;
}

function nbvTitle(v) {
  return v.kind === "checkpoint" ? v.label : NBV_KIND[v.kind] || v.kind;
}

// "2 cells changed, 1 added" for a list of cell changes.
function nbvChangeSummary(changes) {
  if (!changes.length) return "No changes to cells.";
  const n = (kind) => changes.filter((c) => c.kind === kind).length;
  const parts = [["changed", n("changed")], ["added", n("added")], ["removed", n("removed")]]
    .filter(([, k]) => k).map(([kind, k]) => `${k} ${k === 1 ? "cell" : "cells"} ${kind}`);
  if (n("reordered")) parts.push("cells reordered");
  return `${parts.join(", ")}.`.replace(/^./, (c) => c.toUpperCase());
}

async function nbVersions() {
  const nb = state.notebook;
  if (!nb) return;
  if (nb.dirty && !(await saveNotebook())) return;
  const list = await attempt(() => rpc("notebook_versions", { name: nb.name }), "Couldn't read the versions");
  if (!list) return;
  const row = (v) => h("div", { class: "nbv-row" },
    h("div", { class: "nbv-what" }, h("b", {}, nbvTitle(v)), v.kind === "checkpoint" ? h("span", { class: "badge" }, "Checkpoint") : null,
      v.current ? h("span", { class: "pill-s pass" }, "Same as now") : null,
      h("span", { class: "muted-text small" }, when(v.at), ` · ${v.cells} ${v.cells === 1 ? "cell" : "cells"}`)),
    h("div", { class: "nbv-acts" },
      btn("View", null, () => nbVersionView(v), "tool outline small", { "aria-label": `View ${nbvTitle(v)} ${relTime(v.at)}` }),
      btn("Compare", null, () => nbVersionCompare(v), "tool outline small", { disabled: v.current, "aria-label": `Compare with ${nbvTitle(v)} ${relTime(v.at)}` }),
      btn("Restore", null, () => nbVersionRestore(v), "tool small", { disabled: v.current, "aria-label": `Restore ${nbvTitle(v)} ${relTime(v.at)}` }),
      h("button", { class: "tool small", "aria-label": `Delete ${nbvTitle(v)} ${relTime(v.at)}`, title: "Delete this version",
        onclick: () => nbVersionDelete(v) }, icon("delete", 16))));
  const groups = nbvGroups(list.versions);
  showDialog(`Versions of ${nb.name}`, h("div", { class: "nbv" },
    h("p", { class: "muted-text" }, `Each save keeps a version on this PC, one every ${list.coalesce_minutes} minutes while you edit, for ${list.keep_days} days. Checkpoints are kept until you delete them. Versions aren't added to Git or published.`),
    h("div", { class: "row wrap" }, btn("Create checkpoint…", "add", nbCheckpoint, "tool primary small"),
      btn("Compare with last commit", "branch", () => nbVersionCompare(null), "tool outline small")),
    groups.length ? groups.map((g) => h("div", { class: "nbv-day" }, h("h3", { class: "nbv-h" }, g.day), g.rows.map(row)))
      : h("p", { class: "muted-text" }, "No versions yet. The next save keeps one.")),
    [btn("Close", null, closeDialog, "tool outline")]);
}

async function nbCheckpoint() {
  const nb = state.notebook;
  const label = await askText("Create a checkpoint", "Name", "", "Create");
  if (!label) return nbVersions();
  if (nb.dirty && !(await saveNotebook())) return;
  const ok = await attempt(() => rpc("notebook_checkpoint", { name: nb.name, label }), "Couldn't create the checkpoint");
  if (ok) toast(`Checkpoint "${ok.label}" kept`);
  nbVersions();
}

async function nbVersionCompare(v) {
  const nb = state.notebook;
  const params = v ? { name: nb.name, version_id: v.id, cells: notebookPayload().cells } : { name: nb.name, cells: notebookPayload().cells, against: "head" };
  const out = await attempt(() => rpc("notebook_version_compare", params), "Couldn't compare");
  if (!out) return;
  const title = v ? `Now compared with ${nbvTitle(v).toLowerCase() === "saved" ? "the version saved" : nbvTitle(v)} ${relTime(v.at)}` : "Now compared with the last commit";
  showDialog(title, h("div", { class: "nbv" },
    out.problem ? h("p", { class: "muted-text" }, out.problem) : h("p", {}, nbvChangeSummary(out.changes)),
    out.changes.map((c) => h("div", { class: "nbv-change" },
      h("b", {}, c.kind === "reordered" ? "Cells were reordered" : `Cell ${c.position}: ${c.kind}`),
      c.diff?.length ? h("pre", { class: "nbv-diff" }, c.diff.slice(0, 40).map((l) => h("span", { class: l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : "" }, l))) : null))),
    [btn("Back to versions", null, nbVersions, "tool outline"), v && !v.current ? btn("Restore this version", null, () => nbVersionRestore(v), "tool primary") : null].filter(Boolean));
}

// The version's cells as they were, read only.
async function nbVersionView(v) {
  const nb = state.notebook;
  const out = await attempt(() => rpc("notebook_version", { name: nb.name, version_id: v.id }), "Couldn't read the version");
  if (!out) return;
  showDialog(`${nbvTitle(v)}, ${relTime(v.at)}`, h("div", { class: "nbv" },
    h("p", { class: "muted-text" }, `${out.cells.length} ${out.cells.length === 1 ? "cell" : "cells"}, as saved. Outputs aren't kept in versions.`),
    out.cells.map((c, i) => h("div", { class: "nbv-change" }, h("b", {}, `Cell ${i + 1}${c.type === "markdown" ? " (Markdown)" : ""}`),
      h("pre", { class: "nbv-diff" }, c.source || " ")))),
    [btn("Back to versions", null, nbVersions, "tool outline"), v.current ? null : btn("Restore this version", null, () => nbVersionRestore(v), "tool primary")].filter(Boolean));
}

async function nbVersionRestore(v) {
  const nb = state.notebook;
  const ok = await confirmDialog(`Restore ${nbvTitle(v).toLowerCase() === "saved" ? "this version" : nbvTitle(v)}?`,
    `The cells go back to how they were ${relTime(v.at)}. The notebook as it is now is kept as a version first, so you can restore it again. The lakehouse, where it runs and where it publishes stay as they are.`, "Restore");
  if (!ok) return nbVersions();
  if (nb.dirty && !(await saveNotebook())) return;
  const out = await attempt(() => rpc("notebook_version_restore", { name: nb.name, version_id: v.id }), "Couldn't restore");
  if (!out) return;
  loadIntoState(out);
  go("notebook");
  toast(`Restored the version from ${relTime(v.at)}. The previous cells are kept as a version.`, false, { log: true });
}

async function nbVersionDelete(v) {
  const nb = state.notebook;
  const ok = await confirmDialog(`Delete ${v.kind === "checkpoint" ? `the checkpoint "${v.label}"` : "this version"}?`,
    `The version from ${relTime(v.at)} is removed from this PC. The notebook itself doesn't change.`, "Delete", { danger: true });
  if (ok) await attempt(() => rpc("notebook_version_delete", { name: nb.name, version_id: v.id }), "Couldn't delete the version");
  nbVersions();
}

registerFeature({
  id: "notebookVersions",
  commands: [
    { label: "Version history", icon: "history", keywords: "versions checkpoint restore revision undo compare", run: () => state.notebook ? nbVersions() : toast("Open a notebook first.") },
    { label: "Create a checkpoint", icon: "add", keywords: "version save point", run: () => state.notebook ? nbCheckpoint() : toast("Open a notebook first.") },
  ],
});
