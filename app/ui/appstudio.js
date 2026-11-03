// Fabric App Studio (Preview, AS-07/08): the page from the approved mockup. The start view creates a project
// from a template, opens one or imports an export; the workspace shows the app's pages, a local preview on
// sample numbers, Design (the visual editor), the specification and its history. Every change is a patch the
// engine saves with its inverse (appstudio_service.py), so Undo, Redo and versions work the same for every
// edit. Nothing here calls a model, reads OneLake or writes to Fabric: "Ask for a change" and Publish say
// what they will do, and come in later App Studio updates.

const appStudio = { project: null, opened: null, tab: "preview", page: null, selection: {}, busy: false };
const AS_TABS = [["preview", "Preview"], ["design", "Design"], ["spec", "Spec"], ["history", "History"]];
const AS_TYPES = { card: "Card", bar: "Bar", column: "Column", line: "Line", area: "Area", pie: "Pie", donut: "Donut",
  table: "Table", matrix: "Matrix", slicer: "Slicer", text: "Text", waterfall: "Waterfall", scatter: "Scatter", map: "Map" };
const AS_LOCAL = "Projects are folders on this PC. Nothing here reads OneLake, calls a model or publishes to Fabric.";
const AS_CHART_TYPES = new Set(["bar", "column", "line", "area", "waterfall", "scatter"]);
// Shapes the local preview draws; the rest show their title and that they aren't drawn here.
const AS_DRAWN = new Set(["card", "slicer", "text", "table", "matrix", "pie", "donut", ...AS_CHART_TYPES]);

function appStudioOpen(name = null) {
  Object.assign(appStudio, { project: name, opened: null, tab: "preview", page: null, selection: {} });
  go("appStudio");
}

function appStudioLeave() {
  Object.assign(appStudio, { project: null, opened: null, page: null, selection: {} });
  render();
}

async function renderAppStudio() {
  if (appStudio.project) return renderAppStudioWorkspace();
  const body = h("div", { class: "as-body" }, h("p", { class: "muted-text" }, "Reading your App Studio projects..."));
  setView(h("div", { class: "page appstudio" },
    h("div", { class: "page-head" }, h("div", {},
      h("h1", {}, "App Studio ", previewTag()),
      pageIntro("Describe an application, build it from a template, preview it on this PC and keep every version. ",
        docLink("app-studio.md", "Learn more"))),
    h("div", { class: "head-actions" }, btn("Import…", "arrowUpload", appStudioImport, "tool outline"))),
    withHowItWorks(body, howItWorks("appStudio", {
      reads: "Your App Studio project folders on this PC.",
      changes: "Create, Save As, Clone and Import make a new project folder under this PC's KratosForge data (appstudio/projects). Export writes a new .zip in Downloads. Nothing in Fabric or OneLake changes.",
      results: "This page. Each project keeps its specification, its history and its versions in its own folder.",
    }))));
  const home = await rpc("appstudio_home").catch((err) => { if (body.isConnected) fill(body, h("p", { class: "muted-text" }, friendlyError(err).text)); return null; });
  if (!home || state.view !== "appStudio" || appStudio.project || !body.isConnected) return;
  fill(body, appStudioCreateCard(home), appStudioProjectList(home.projects), h("p", { class: "muted-text small" }, AS_LOCAL));
}

function appStudioCreateCard(home) {
  let template = "sales";
  const name = h("input", { value: "", placeholder: "Sales overview", maxlength: 120, "aria-label": "App name" });
  const target = h("select", { "aria-label": "Where it will run" }, home.targets.map((t) => h("option", { value: t.id }, t.label)));
  const cards = h("div", { class: "as-templates", role: "radiogroup", "aria-label": "Template" });
  const draw = () => fill(cards, home.templates.map((t) => h("button", {
    class: `card pad as-template${t.id === template ? " selected" : ""}`, role: "radio", "aria-checked": t.id === template ? "true" : "false",
    onclick: () => { template = t.id; draw(); } }, h("b", {}, t.title), h("span", { class: "muted-text small" }, t.text))));
  draw();
  const create = async () => {
    const chosen = name.value.trim() || home.templates.find((t) => t.id === template)?.title || "New app";
    const got = await attempt(() => rpc("appstudio_create", { name: chosen, template, target: target.value }), "Could not create the app");
    if (got) { Object.assign(appStudio, { project: got.project, opened: got, tab: "preview", page: null, selection: {} }); go("appStudio"); }
  };
  name.addEventListener("keydown", (e) => { if (e.key === "Enter") create(); });
  return h("section", { class: "card pad as-create" }, h("h2", {}, "Create an app"),
    h("div", { class: "row as-create-fields" }, h("label", { class: "field" }, "Name", name),
      h("label", { class: "field" }, "Where it will run", target)),
    cards, h("div", { class: "row" }, btn("Create", "add", create, "tool primary")));
}

function appStudioProjectList(rows) {
  return h("section", { class: "card pad as-projects" }, h("h2", {}, "Open an app"),
    rows.length ? h("table", { class: "list" },
      h("thead", {}, h("tr", {}, h("th", {}, "App"), h("th", {}, "Last change"), h("th", {}, ""))),
      h("tbody", {}, rows.map((r) => h("tr", {},
        h("td", {}, r.name || r.project, r.broken ? h("span", { class: "muted-text small" }, " · can't be read") : null),
        h("td", {}, r.modified ? dateTimeText(r.modified) : ""),
        h("td", { class: "tick" }, r.broken ? null : btn("Open", null, () => appStudioOpen(r.project), "tool outline small",
          { "aria-label": `Open ${r.name || r.project}` }))))))
      : h("p", { class: "muted-text" }, "No apps yet. Create one above, or import an export."));
}

function appStudioImport() {
  const file = h("input", { type: "file", accept: ".zip", "aria-label": "Choose an App Studio export" });
  // The file is read here and sent as base64; the engine checks every file in it.
  file.onchange = async () => {
    const f = file.files?.[0];
    if (!f) return;
    const content = replayBase64(await f.arrayBuffer());
    const got = await attempt(() => rpc("appstudio_import", { content }), "Could not import the app");
    if (got) { closeDialog(); appStudioOpen(got.project); }
  };
  showDialog("Import an app", h("div", {},
    h("p", { class: "muted small" }, "Imports an App Studio export (.zip) as a new project on this PC. Only the project's own files are taken, and they are checked for keys and passwords first."),
    h("label", { class: "field" }, "Export file", file)), [btn("Cancel", null, closeDialog, "tool outline")]);
}

// ---------- the workspace ----------

async function appStudioLoad() {
  const got = await rpc("appstudio_open", { project_name: appStudio.project });
  appStudio.opened = got;
  return got;
}

async function appStudioApply(method, params, label) {
  if (appStudio.busy) return null;
  appStudio.busy = true;
  try {
    const got = await attempt(() => rpc(method, { project_name: appStudio.project, ...params }), label);
    if (got && got.spec) appStudio.opened = got;
    return got;
  } finally {
    appStudio.busy = false;
    if (state.view === "appStudio") render();
  }
}

function appStudioChange(ops, note) {
  return appStudioApply("appstudio_change", { ops, note, source: "visual" }, "Could not change the app");
}

async function renderAppStudioWorkspace() {
  let got = appStudio.opened;
  if (!got || got.project !== appStudio.project) {
    setView(h("div", { class: "page appstudio" }, h("p", { class: "muted-text" }, "Opening the app...")));
    got = await appStudioLoad().catch((err) => { toast(friendlyError(err).text, true); return null; });
    if (!got) { appStudio.project = null; return render(); }
    if (state.view !== "appStudio") return;
  }
  const spec = got.spec || {};
  const pages = Array.isArray(spec.pages) ? spec.pages : [];
  if (!pages.some((p) => p.id === appStudio.page)) appStudio.page = pages[0]?.id || null;
  const page = pages.find((p) => p.id === appStudio.page);
  const chips = h("div", { class: "as-chips" },
    h("span", { class: "as-chip ok" }, icon("ok", 14), "Saved"),
    got.valid ? h("span", { class: "as-chip ok" }, icon("ok", 14), "Valid")
      : h("span", { class: "as-chip warn" }, icon("warning", 14), `${got.problems.length} problem${got.problems.length === 1 ? "" : "s"}`),
    h("span", { class: "as-chip" }, "Not published"));
  const more = btn("More", "chevronDown", (e) => showMenu(e.currentTarget, [
    { label: "Save As…", run: () => appStudioSaveAs(false) },
    { label: "Clone…", run: () => appStudioSaveAs(true) },
    { label: "Export to Downloads", run: appStudioExport },
  ]), "tool outline", { "aria-label": "More project actions" });
  const head = h("div", { class: "page-head" }, h("div", {},
    h("a", { href: "#", class: "small", onclick: (e) => { e.preventDefault(); appStudioLeave(); } }, "App Studio"),
    h("h1", {}, `${spec.name || got.meta?.name || appStudio.project} `, previewTag()),
    chips),
  h("div", { class: "head-actions" },
    btn("Undo", "undo", () => appStudioApply("appstudio_undo", {}, "Could not undo"), "tool outline", { disabled: !got.can_undo }),
    btn("Redo", null, () => appStudioApply("appstudio_redo", {}, "Could not redo"), "tool outline", { disabled: !got.can_redo }),
    btn("Save version", "save", appStudioSaveVersion, "tool outline", { disabled: !got.valid }),
    more,
    btn("Publish…", "publish", () => appStudioPublishDialog(got), "tool primary")));
  const tabs = h("div", { class: "subtabs", role: "tablist", "aria-label": "App Studio" }, AS_TABS.map(([id, label]) =>
    h("button", { class: `tab${appStudio.tab === id ? " active" : ""}`, role: "tab", "aria-selected": appStudio.tab === id ? "true" : "false",
      onclick: () => { appStudio.tab = id; render(); } }, label)));
  const center = h("div", { class: "as-center" });
  setView(h("div", { class: "page appstudio as-workspace" }, head, tabs,
    h("div", { class: "as-split" }, appStudioPagesRail(pages, got.valid), center, appStudioAskPanel())));
  if (!got.valid && appStudio.tab !== "spec" && appStudio.tab !== "history") {
    fill(center, h("div", { class: "callout warn" }, icon("warning", 20),
      h("span", {}, "This app's specification has problems, so it can't be previewed or changed until they are fixed. Spec lists them; History can restore a version.")));
    return;
  }
  if (appStudio.tab === "design") fill(center, appStudioDesign(spec, page));
  else if (appStudio.tab === "spec") fill(center, appStudioSpecView(got));
  else if (appStudio.tab === "history") await appStudioHistory(center);
  else await appStudioPreview(center, page);
}

function appStudioPagesRail(pages, valid) {
  const addPage = async () => {
    const title = await askText("Add a page", "Page title", `Page ${pages.length + 1}`, "Add");
    if (!title) return;
    const id = appStudioNewId("pg", appStudio.opened.spec);
    const got = await appStudioChange([{ op: "add", kind: "page", index: pages.length,
      value: { id, title, components: [] } }], `Add page ${title}`);
    if (got) { appStudio.page = id; render(); }
  };
  return h("nav", { class: "as-pages", "aria-label": "Pages" }, h("div", { class: "as-eyebrow" }, "Pages"),
    pages.map((p) => h("button", { class: `as-page${p.id === appStudio.page ? " active" : ""}`, "aria-current": p.id === appStudio.page ? "page" : null,
      onclick: () => { appStudio.page = p.id; appStudio.selection = {}; render(); } }, p.title,
      h("span", { class: "muted-text small" }, ` ${(p.components || []).length}`))),
    valid ? btn("Add page", "add", addPage, "tool small outline") : null);
}

function appStudioAskPanel() {
  return h("aside", { class: "card pad as-ask", "aria-label": "Ask for a change" },
    h("h2", {}, "Ask for a change"),
    h("textarea", { rows: 4, disabled: true, "aria-label": "Describe a change", placeholder: "Make the region chart a column chart and add a profit card" }),
    h("p", { class: "muted-text small" }, "Asking your own AI model for a change comes in the next App Studio update, once your organization's model policy is in place. Its plan shows here first, and nothing changes until you apply it."),
    h("p", { class: "muted-text small" }, "Until then, change the app in Design. Every change can be undone."));
}

// A new id no entry in the specification has, like pg_3 or c_7.
function appStudioNewId(prefix, spec) {
  const used = new Set(JSON.stringify(spec).match(/"id":"[^"]*"/g)?.map((m) => m.slice(6, -1)) || []);
  for (let n = 1; ; n++) if (!used.has(`${prefix}_${n}`)) return `${prefix}_${n}`;
}

// ---------- Preview ----------

async function appStudioPreview(center, page) {
  fill(center, h("p", { class: "muted-text" }, "Drawing the preview..."));
  const prev = await rpc("appstudio_preview", { project_name: appStudio.project, page: page?.id || "", selection: appStudio.selection })
    .catch((err) => { if (center.isConnected) fill(center, h("p", { class: "muted-text" }, friendlyError(err).text)); return null; });
  if (!prev || !center.isConnected) return;
  const primary = appStudio.opened.spec.theme?.colors?.primary || CHART_COLORS[0];
  fill(center,
    h("div", { class: "as-sim" }, icon("info", 16), h("b", {}, "Local preview on sample data. "), prev.note),
    prev.components.length
      ? h("div", { class: "as-grid", "aria-label": `${prev.page.title} preview` }, prev.components.map((c) => appStudioTile(c, primary)))
      : emptyState({ icon: "apps", title: "This page is empty", text: "Add components in Design." }));
}

function appStudioTile(c, primary) {
  const l = c.layout || { x: 0, y: 0, w: 4, h: 2 };
  return h("div", { class: `card as-tile as-t-${c.type}`, style: `grid-column:${l.x + 1} / span ${l.w};grid-row:${l.y + 1} / span ${l.h}` },
    c.type === "card" ? null : h("div", { class: "as-tile-title" }, c.title || AS_TYPES[c.type] || c.type),
    appStudioDraw(c, primary));
}

const asNumber = (v) => (typeof v === "number" ? v.toLocaleString() : "");

function appStudioDraw(c, primary) {
  if (!AS_DRAWN.has(c.type)) return h("p", { class: "muted-text small" }, `${AS_TYPES[c.type] || c.type} isn't drawn in the local preview. It is kept in the app and published as it is.`);
  if (c.type === "card") return [h("div", { class: "as-card-value" }, asNumber(c.value)), h("div", { class: "muted-text small" }, c.title || c.measure)];
  if (c.type === "text") return h("p", {}, c.text);
  if (c.type === "slicer") {
    const select = h("select", { "aria-label": c.title || c.field, onchange: (e) => {
      if (e.target.value) appStudio.selection = { ...appStudio.selection, [c.field]: e.target.value };
      else { const { [c.field]: _gone, ...rest } = appStudio.selection; appStudio.selection = rest; }
      render();
    } }, h("option", { value: "" }, "All"), c.values.map((v) => h("option", { value: v, selected: v === c.selected }, v)));
    return select;
  }
  if (!c.labels?.length) return h("p", { class: "muted-text small" }, "Pick a field in Design to show this.");
  if (c.type === "table" || c.type === "matrix") {
    return h("div", { class: "output" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, c.field ? c.field.replace(/^.*\[|\]$/g, "") : ""), c.series.map((s) => h("th", {}, s.name)))),
      h("tbody", {}, c.labels.map((label, i) => h("tr", {}, h("td", {}, label), c.series.map((s) => h("td", {}, asNumber(s.values[i]))))))));
  }
  return appStudioSvg(c, primary);
}

// A plain SVG chart: bars, a line or a pie of the first series. Sizes come from the viewBox, so it scales with the tile.
function appStudioSvg(c, primary) {
  const NS = "http://www.w3.org/2000/svg";
  const el = (tag, attrs, ...kids) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    kids.forEach((k) => n.append(k));
    return n;
  };
  const values = c.series[0]?.values || [];
  const max = Math.max(1, ...values);
  const W = 320, H = 150, pad = 18;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H + 18}`, class: "as-svg", role: "img",
    "aria-label": `${c.title}: ${c.labels.map((l, i) => `${l} ${asNumber(values[i])}`).join(", ")}` });
  if (c.type === "pie" || c.type === "donut") {
    const total = values.reduce((a, b) => a + b, 0) || 1;
    let angle = -Math.PI / 2;
    const cx = W / 2, cy = H / 2 + 6, r = H / 2 - 4;
    values.forEach((v, i) => {
      const a2 = angle + (v / total) * Math.PI * 2;
      const large = a2 - angle > Math.PI ? 1 : 0;
      svg.append(el("path", { d: `M${cx},${cy} L${cx + r * Math.cos(angle)},${cy + r * Math.sin(angle)} A${r},${r} 0 ${large} 1 ${cx + r * Math.cos(a2)},${cy + r * Math.sin(a2)} Z`,
        fill: CHART_COLORS[i % CHART_COLORS.length] }));
      angle = a2;
    });
    if (c.type === "donut") svg.append(el("circle", { cx, cy, r: r * 0.55, class: "as-hole" }));
    return svg;
  }
  const n = values.length;
  const step = (W - pad * 2) / n;
  if (c.type === "line" || c.type === "area" || c.type === "scatter") {
    const pts = values.map((v, i) => [pad + step * (i + 0.5), H - (v / max) * (H - 10)]);
    if (c.type === "area") svg.append(el("path", { d: `M${pts[0][0]},${H} ${pts.map((p) => `L${p[0]},${p[1]}`).join(" ")} L${pts[n - 1][0]},${H} Z`, fill: primary, opacity: "0.25" }));
    if (c.type !== "scatter") svg.append(el("polyline", { points: pts.map((p) => p.join(",")).join(" "), fill: "none", stroke: primary, "stroke-width": "2.5" }));
    else pts.forEach((p) => svg.append(el("circle", { cx: p[0], cy: p[1], r: 4, fill: primary })));
  } else {
    values.forEach((v, i) => {
      const bh = (v / max) * (H - 10);
      const bw = Math.min(step * 0.7, 56);
      svg.append(el("rect", { x: pad + step * (i + 0.5) - bw / 2, y: H - bh, width: bw, height: bh, rx: 2, fill: primary }));
    });
  }
  const every = Math.ceil(n / 8);
  c.labels.forEach((label, i) => {
    if (i % every) return;
    const t = el("text", { x: pad + step * (i + 0.5), y: H + 14, "text-anchor": "middle", class: "as-axis" });
    t.textContent = String(label).slice(0, 10);
    svg.append(t);
  });
  return svg;
}

// ---------- Design ----------

function appStudioDesign(spec, page) {
  if (!page) return emptyState({ icon: "apps", title: "No page", text: "Add a page." });
  const allowed = appStudio.opened.components || Object.keys(AS_TYPES);
  const measures = spec.measures || [];
  const rows = (page.components || []).map((c) => {
    const title = h("input", { value: c.title || "", maxlength: 200, "aria-label": `Title of ${c.id}`,
      onchange: (e) => appStudioChange([{ op: "set", id: c.id, field: "title", value: e.target.value }], `Rename ${c.id}`) });
    const type = h("select", { "aria-label": `Type of ${c.id}`, onchange: (e) => appStudioChange([{ op: "set", id: c.id, field: "type", value: e.target.value }], `Change ${c.id} to ${e.target.value}`) },
      allowed.map((t) => h("option", { value: t, selected: t === c.type }, AS_TYPES[t] || t)));
    const width = h("select", { "aria-label": `Width of ${c.id}`, onchange: (e) => {
      const w = Number(e.target.value);
      const x = Math.min(c.layout.x, 12 - w);
      appStudioChange([{ op: "set", id: c.id, field: "layout", value: { ...c.layout, x, w } }], `Resize ${c.id}`);
    } }, [3, 4, 6, 8, 9, 12].concat(c.layout.w).filter((v, i, a) => a.indexOf(v) === i).sort((a, b) => a - b)
      .map((w) => h("option", { value: w, selected: w === c.layout.w }, `${w} of 12`)));
    const measure = c.type === "slicer" || c.type === "text" ? h("span", { class: "muted-text small" }, "") :
      h("select", { "aria-label": `Measure of ${c.id}`, onchange: (e) => appStudioChange([{ op: "set", id: c.id, field: "measures", value: e.target.value ? [e.target.value] : [] }], `Measure for ${c.id}`) },
        h("option", { value: "" }, "None"), measures.map((m) => h("option", { value: m.id, selected: (c.measures || [])[0] === m.id }, m.name)));
    return h("tr", {}, h("td", {}, title), h("td", {}, type), h("td", {}, measure), h("td", {}, width),
      h("td", { class: "tick" }, btn("Remove", "delete", () => appStudioChange([{ op: "remove", id: c.id }], `Remove ${c.id}`), "tool small outline",
        { "aria-label": `Remove ${c.title || c.id}` })));
  });
  const addType = h("select", { "aria-label": "Component to add" }, allowed.map((t) => h("option", { value: t }, AS_TYPES[t] || t)));
  const add = () => {
    const bottom = Math.max(0, ...(page.components || []).map((c) => c.layout.y + c.layout.h));
    const t = addType.value;
    const m = measures[0];
    const value = { id: appStudioNewId("c", spec), type: t, title: AS_TYPES[t] || t,
      measures: m && !["slicer", "text"].includes(t) ? [m.id] : [], fields: [], layout: { x: 0, y: bottom, w: t === "card" ? 3 : 6, h: t === "card" ? 2 : 4 }, props: {} };
    appStudioChange([{ op: "add", kind: "component", page: page.id, index: (page.components || []).length, value }], `Add ${value.title}`);
  };
  const pageTitle = h("input", { value: page.title, maxlength: 120, "aria-label": "Page title",
    onchange: (e) => appStudioChange([{ op: "set", id: page.id, field: "title", value: e.target.value }], "Rename page") });
  return h("div", { class: "as-design" },
    h("div", { class: "row" }, h("label", { class: "field" }, "Page title", pageTitle), h("span", { class: "spacer" }),
      (appStudio.opened.spec.pages || []).length > 1 ? btn("Remove page", "delete", async () => {
        if (!(await confirmDialog("Remove this page?", `${page.title} and its ${page.components.length} components leave the app. Undo brings them back.`, "Remove page", { danger: true }))) return;
        appStudioChange([{ op: "remove", id: page.id }], `Remove page ${page.title}`);
      }, "tool outline small") : null),
    (page.components || []).length ? h("table", { class: "list as-design-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Title"), h("th", {}, "Shows"), h("th", {}, "Measure"), h("th", {}, "Width"), h("th", {}, ""))),
      h("tbody", {}, rows)) : h("p", { class: "muted-text" }, "No components on this page yet."),
    h("div", { class: "row" }, h("label", { class: "field" }, "Add", addType), btn("Add component", "add", add, "tool outline")),
    h("p", { class: "muted-text small" }, "Fields come from the source. Connecting a semantic model, and choosing its fields here, come in the next update; templates use sample fields."));
}

// ---------- Spec and History ----------

function appStudioSpecView(got) {
  return h("div", { class: "as-spec" },
    got.valid ? h("p", { class: "muted-text small" }, "The specification is the app: Design and, later, your model change it only through patches you can undo.")
      : h("div", { class: "callout warn" }, icon("warning", 20), h("ul", {}, got.problems.slice(0, 50).map((p) => h("li", {}, p)))),
    h("pre", { class: "as-json", tabindex: "0", "aria-label": "Specification (app.json)" }, JSON.stringify(got.spec, null, 2)));
}

async function appStudioHistory(center) {
  fill(center, h("p", { class: "muted-text" }, "Reading the history..."));
  const [versions, changes] = await Promise.all([
    rpc("appstudio_versions", { project_name: appStudio.project }), rpc("appstudio_history", { project_name: appStudio.project, limit: 100 })])
    .catch((err) => { if (center.isConnected) fill(center, h("p", { class: "muted-text" }, friendlyError(err).text)); return [null, null]; });
  if (!versions || !center.isConnected) return;
  const restore = async (v) => {
    if (!(await confirmDialog(`Restore version ${v.version}?`, `${v.label} becomes the app. What's there now is kept as a new version first, so you can come back to it.`, "Restore"))) return;
    const got = await appStudioApply("appstudio_restore_version", { version: v.version }, "Could not restore the version");
    if (got) toast(`Restored version ${v.version}. The app as it was is version ${got.kept_as}.`);
  };
  const KIND = { change: "Changed", undo: "Undid", redo: "Redid", restore: "Restored" };
  fill(center,
    h("section", { class: "card pad" }, h("h2", {}, "Versions"),
      versions.length ? h("table", { class: "list" }, h("tbody", {}, versions.map((v) => h("tr", {},
        h("td", {}, `${v.version}`), h("td", {}, v.label), h("td", {}, v.at ? dateTimeText(v.at) : ""),
        h("td", { class: "tick" }, btn("Restore", "history", () => restore(v), "tool small outline", { "aria-label": `Restore version ${v.version}` }))))))
        : h("p", { class: "muted-text" }, "No versions yet. Save version keeps the app as it is now.")),
    h("section", { class: "card pad" }, h("h2", {}, "Changes"),
      changes.length ? h("ol", { class: "as-changes" }, changes.map((c) => h("li", {},
        h("b", {}, KIND[c.kind] || c.kind), " ",
        c.kind === "restore" ? `version ${c.version}` : c.note || (c.changed || []).join(", "),
        h("span", { class: "muted-text small" }, ` · ${c.source === "visual" ? "Design" : c.source || ""} ${c.at ? dateTimeText(c.at) : ""}`))))
        : h("p", { class: "muted-text" }, "No changes yet.")));
}

async function appStudioSaveVersion() {
  const label = await askText("Save a version", "Label", "", "Save version");
  if (label === null) return;
  const v = await attempt(() => rpc("appstudio_save_version", { project_name: appStudio.project, label }), "Could not save the version");
  if (v) { toast(`Saved version ${v.version}.`); if (appStudio.tab === "history") render(); }
}

async function appStudioSaveAs(clone) {
  const name = await askText(clone ? "Clone the app" : "Save the app as", "New name", `${appStudio.opened?.spec?.name || ""} copy`, clone ? "Clone" : "Save As");
  if (!name) return;
  const got = await attempt(() => rpc("appstudio_save_as", { project_name: appStudio.project, name, clone }), clone ? "Could not clone the app" : "Could not save the app");
  if (got) { toast(clone ? `Cloned as ${got.spec.name}, with no history.` : `Saved as ${got.spec.name}.`); appStudioOpen(got.project); }
}

async function appStudioExport() {
  const got = await attempt(() => rpc("appstudio_export", { project_name: appStudio.project }), "Could not export the app");
  if (got) toast(`Exported to ${got.path}`);
}

// ---------- Publish ----------

// What publishing would do, from the specification. Publishing itself comes in a later update and asks for
// approval before its first write to Fabric; this dialog changes nothing.
function appStudioPublishDialog(got) {
  const spec = got.spec || {};
  const pages = spec.pages || [];
  const visuals = pages.reduce((n, p) => n + (p.components || []).length, 0);
  const sample = (spec.sources || []).filter((s) => s.kind === "sample");
  const ready = [
    [got.valid, got.valid ? "The specification is valid." : "Fix the specification's problems first (Spec lists them)."],
    [!sample.length && (spec.sources || []).length > 0, sample.length ? "It reads sample data. Connect a semantic model before publishing." : (spec.sources || []).length ? "It reads a semantic model." : "It has no source yet."],
    [false, "Publishing from App Studio comes in a later update. Its first write to Fabric asks for your approval."],
  ];
  const where = spec.target === "kratosforge-app" ? "A KratosForge app, kept with this project" : "A Power BI report in a Fabric workspace you choose";
  showDialog("Publish", h("div", { class: "as-publish" },
    h("dl", { class: "as-facts" },
      h("dt", {}, "Where"), h("dd", {}, where),
      h("dt", {}, "Creates"), h("dd", {}, `${spec.name}: ${pages.length} page${pages.length === 1 ? "" : "s"}, ${visuals} visual${visuals === 1 ? "" : "s"}`),
      h("dt", {}, "If it exists"), h("dd", {}, "Nothing is replaced without your approval."),
      h("dt", {}, "Capacity"), h("dd", {}, "Creating a report starts no Fabric job; viewing it uses the workspace's capacity."),
      h("dt", {}, "Undo"), h("dd", {}, "This project keeps every version. A published report is removed in Fabric.")),
    h("h3", {}, "Ready to publish?"),
    h("ul", { class: "as-ready" }, ready.map(([ok, text]) => h("li", { class: ok ? "ok" : "todo" }, icon(ok ? "ok" : "warning", 14), text)))),
  [btn("Close", null, closeDialog, "tool outline"), btn("Publish", "publish", null, "tool primary", { disabled: true, title: "Comes in a later update" })]);
}

registerFeature({
  id: "appStudio",
  preview: true,
  views: { appStudio: renderAppStudio },
  commands: [
    { label: "Open App Studio", icon: "apps", keywords: "app studio application builder report dashboard ai design publish", run: () => { appStudio.project = null; go("appStudio"); } },
  ],
});
