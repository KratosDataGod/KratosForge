// Saved and shared queries on the Warehouse page (WH-06): .sql files in My queries on this PC, or in
// the sql-queries folder of a Git repository opened in Source control, which the team shares by
// committing (sql_queries_service.py). The explorer's Queries tab lists them; Save query writes the
// current tab to one. A tab opened from a file keeps it (tab.file), so Save writes back to it.

const sqlQueries = { state: "idle", queries: [], places: [{ id: "mine", label: "My queries" }], notes: [], error: "" };

async function sqlLoadQueries() {
  sqlQueries.state = "loading";
  try {
    const r = await rpc("sql_queries_list");
    Object.assign(sqlQueries, { state: "done", queries: r.queries, places: r.places, notes: r.notes || [], error: "" });
  } catch (err) {
    Object.assign(sqlQueries, { state: "failed", error: String(err).replace(/^Error: /, "") });
  }
}

// The Queries tab. actions.open(q) opens a saved query.
function whQueries(ref, actions) {
  const box = h("div", { class: "explorer-content wh-queries" });
  const filter = h("input", { type: "search", class: "wh-filter", placeholder: "Filter queries", "aria-label": "Filter queries",
    value: sqlPage.whQueryFilter || "", oninput: (e) => { sqlPage.whQueryFilter = e.target.value; draw(); } });
  async function remove(q) {
    const where = q.place === "mine" ? "My queries on this PC" : `the sql-queries folder of ${q.place_label}; commit the change in Source control to remove it for your team`;
    if (!(await confirmDialog("Delete saved query", `Delete ${q.title}? Its file is removed from ${where}. Open tabs keep their SQL.`, "Delete", { danger: true }))) return;
    try {
      await rpc("sql_query_delete", { place: q.place, name: q.name });
      toast(`Deleted ${q.title}.`);
    } catch (err) { toast(`Couldn't delete the query: ${String(err).replace(/^Error: /, "")}`, true); }
    await sqlLoadQueries();
    sqlPage.whRedraw?.();
  }
  // The filter stays in the page; only the list under it is redrawn, so a redraw (each letter
  // typed, a save elsewhere) never takes the focus from the filter.
  const list = h("div");
  box.append(filter, list);
  function draw() {
    if (sqlQueries.state === "idle" || sqlQueries.state === "loading") {
      filter.hidden = true;
      list.replaceChildren(h("div", { class: "tree-row muted" }, "Reading saved queries..."));
      return;
    }
    if (sqlQueries.state === "failed") {
      filter.hidden = true;
      list.replaceChildren(h("div", { class: "output error wh-note" }, `Could not list saved queries: ${sqlQueries.error}`),
        btn("Try again", "sync", async () => { await sqlLoadQueries(); sqlPage.whRedraw?.(); }, "tool outline small"));
      return;
    }
    filter.hidden = false;
    const term = (sqlPage.whQueryFilter || "").trim().toLowerCase();
    const rows = [];
    for (const place of sqlQueries.places) {
      const qs = sqlQueries.queries.filter((q) => q.place === place.id
        && (!term || `${q.title} ${q.description} ${q.warehouse}`.toLowerCase().includes(term)));
      if (!qs.length && place.id !== "mine") continue;
      rows.push(h("div", { class: "wh-q-head" }, place.id === "mine" ? "My queries · on this PC" : `${place.label} · shared in Git`));
      if (!qs.length) rows.push(h("p", { class: "muted-text wh-note" }, term ? "Nothing matches." : "No saved queries yet. Save query keeps the current tab here."));
      for (const q of qs) {
        const other = q.warehouse && q.warehouse !== ref.name;
        rows.push(h("div", { class: "wh-q-row" },
          h("button", { class: "tree-row", title: q.problem || [q.description, q.warehouse && `Saved from ${q.warehouse}`].filter(Boolean).join(" · ") || q.name,
            disabled: !!q.problem || null, onclick: () => actions.open(q) },
          icon("document", 14), h("span", { class: "label" }, q.title), other ? h("span", { class: "wh-q-from" }, q.warehouse) : null,
          q.problem ? h("span", { class: "error-text wh-q-from" }, "can't be read") : null),
          h("button", { class: "tree-row wh-more", "aria-label": `Delete ${q.title}`, title: "Delete this saved query", onclick: () => remove(q) }, "×")));
      }
    }
    rows.push(...sqlQueries.notes.map((n) => h("p", { class: "muted-text wh-note" }, n)),
      h("p", { class: "muted-text wh-note" }, "Shared queries are files in a Git repository's sql-queries folder: commit them in Source control."));
    list.replaceChildren(...rows);
  }
  // The page may have been drawn again while the list was read: redraw whatever is showing now.
  if (sqlQueries.state === "idle") sqlLoadQueries().then(() => sqlPage.whRedraw?.());
  draw();
  return { el: box, draw };
}

// Save query: writes the tab back to the file it came from, or (asNew, or a tab with no file) asks
// for a name, a description and where to keep it. Resolves to the saved file, or null.
async function saveSqlQuery(ref, tab, asNew = false) {
  const write = (place, title, description, warehouse, file) => rpc("sql_query_save", { place, title, sql: tab.sql, warehouse, description,
    ...(file ? { name: file.name, expect: file.version || "" } : {}) });
  if (tab.file && !asNew) {
    // Save keeps the file's own warehouse, and refuses a file that changed since it was opened.
    const f = tab.file;
    try {
      const out = await write(f.place, f.title || tab.name, f.description || "", f.warehouse ?? ref.name, f);
      toast(`Saved ${f.title || tab.name} to ${f.place === "mine" ? "My queries" : f.place_label}.`);
      return { ...f, version: out.version };
    } catch (err) {
      toast(`Couldn't save the query: ${String(err).replace(/^Error: /, "")}`, true);
      return null;
    }
  }
  if (sqlQueries.state !== "done") await sqlLoadQueries();
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (!done) { done = true; resolve(v); } };
    const name = h("input", { value: tab.file?.title || tab.name, maxlength: 100, "aria-label": "Name" });
    const description = h("input", { value: tab.file?.description || "", maxlength: 200, "aria-label": "Description" });
    const place = h("select", { "aria-label": "Save to" }, sqlQueries.places.map((p) =>
      h("option", { value: p.id }, p.id === "mine" ? "My queries (this PC)" : `${p.label} (Git: sql-queries folder)`)));
    const why = h("p", { class: "error-text", role: "alert" });
    const save = btn("Save", null, async () => {
      save.disabled = true;
      try {
        const out = await write(place.value, name.value, description.value, ref.name);
        const label = sqlQueries.places.find((p) => p.id === out.place)?.label || "";
        toast(`Saved ${name.value.trim()} to ${out.place === "mine" ? "My queries" : label}.`);
        finish({ place: out.place, name: out.name, place_label: label, title: name.value.trim(), description: description.value.trim(),
          warehouse: ref.name, version: out.version });
        closeDialog();
      } catch (err) { why.textContent = String(err).replace(/^Error: /, ""); save.disabled = false; }
    }, "tool primary");
    showDialog("Save query", h("div", {},
      h("label", { class: "field" }, "Name", name),
      h("label", { class: "field" }, "Description (optional)", description),
      h("label", { class: "field" }, "Save to", place),
      h("p", { class: "muted-text" }, "Saved as a .sql file. In a Git repository, commit it in Source control to share it with your team."),
      why), [btn("Cancel", null, () => { closeDialog(); finish(null); }, "tool outline"), save], () => finish(null));
    name.focus();
    name.select();
  });
}
