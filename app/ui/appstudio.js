// Fabric App Studio (Preview, AS-07/08): the page from the approved mockup. The start view creates a project
// from a template, opens one or imports an export; the workspace shows the app's pages, a local preview on
// sample numbers, Design (the visual editor), the specification and its history. Every change is a patch the
// engine saves with its inverse (appstudio_service.py), so Undo, Redo and versions work the same for every
// edit. Model connections (AS-06) keep where the person's own AI key is, never the key. Publish (AS-13) checks
// first and writes one Power BI report only after the person types the workspace's name; replacing a report
// already there needs its own tick. Nothing here reads OneLake.

const appStudio = { project: null, opened: null, tab: "preview", page: null, selection: {}, busy: false, connections: false, editing: null, tested: null };
const AS_TABS = [["preview", "Preview"], ["design", "Design"], ["spec", "Spec"], ["history", "History"]];
const AS_TYPES = { card: "Card", bar: "Bar", column: "Column", line: "Line", area: "Area", pie: "Pie", donut: "Donut",
  table: "Table", matrix: "Matrix", slicer: "Slicer", text: "Text", waterfall: "Waterfall", scatter: "Scatter", map: "Map" };
const AS_LOCAL = "Projects are folders on this PC. Nothing here reads OneLake. Only Publish writes to Fabric, after you confirm it.";
const AS_CHART_TYPES = new Set(["bar", "column", "line", "area", "waterfall", "scatter"]);
// Shapes the local preview draws; the rest show their title and that they aren't drawn here.
const AS_DRAWN = new Set(["card", "slicer", "text", "table", "matrix", "pie", "donut", ...AS_CHART_TYPES]);

function appStudioOpen(name = null) {
  Object.assign(appStudio, { project: name, opened: null, tab: "preview", page: null, selection: {} });
  go("appStudio");
}

function appStudioLeave() {
  Object.assign(appStudio, { project: null, opened: null, page: null, selection: {}, connections: false, editing: null, tested: null });
  render();
}

async function renderAppStudio() {
  if (appStudio.connections) return renderAppStudioConnections();
  if (appStudio.project) return renderAppStudioWorkspace();
  const body = h("div", { class: "as-body" }, h("p", { class: "muted-text" }, "Reading your App Studio projects..."));
  setView(h("div", { class: "page appstudio" },
    h("div", { class: "page-head" }, h("div", {},
      h("h1", {}, "App Studio ", previewTag()),
      pageIntro("Describe an application, build it from a template, preview it on this PC and keep every version. ",
        docLink("app-studio.md", "Learn more"))),
    h("div", { class: "head-actions" },
      btn("Model connections", "plugConnected", () => { appStudio.connections = true; render(); }, "tool outline"),
      btn("Import…", "arrowUpload", appStudioImport, "tool outline"))),
    withHowItWorks(body, howItWorks("appStudio", {
      reads: "Your App Studio project folders on this PC.",
      changes: "Create, Save As, Clone and Import make a new project folder under this PC's KratosForge data (appstudio/projects). Export writes a new .zip in Downloads. Only Publish changes Fabric: one report, after you type the workspace's name.",
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
    h("p", { class: "muted-text small" }, "Asking your own AI model for a change comes in the next App Studio update. You can add your model now, under Model connections on App Studio's first page. Its plan shows here first, and nothing changes until you apply it."),
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
  const KIND = { change: "Changed", undo: "Undid", redo: "Redid", restore: "Restored", publish: "Publish" };
  const PUBLISHED = { started: "started", done: "done", failed: "didn't finish" };
  fill(center,
    h("section", { class: "card pad" }, h("h2", {}, "Versions"),
      versions.length ? h("table", { class: "list" }, h("tbody", {}, versions.map((v) => h("tr", {},
        h("td", {}, `${v.version}`), h("td", {}, v.label), h("td", {}, v.at ? dateTimeText(v.at) : ""),
        h("td", { class: "tick" }, btn("Restore", "history", () => restore(v), "tool small outline", { "aria-label": `Restore version ${v.version}` }))))))
        : h("p", { class: "muted-text" }, "No versions yet. Save version keeps the app as it is now.")),
    h("section", { class: "card pad" }, h("h2", {}, "Changes"),
      changes.length ? h("ol", { class: "as-changes" }, changes.map((c) => h("li", {},
        h("b", {}, KIND[c.kind] || c.kind), " ",
        c.kind === "restore" ? `version ${c.version}`
          : c.kind === "publish" ? `${PUBLISHED[c.state] || c.state}: ${c.name}${c.replaced ? " (replaced)" : ""}${c.note ? ` · ${c.note}` : ""}`
            : c.note || (c.changed || []).join(", "),
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

// ---------- Model connections (AS-06) ----------

// The person's own AI models. A connection keeps where its key is (Credential Manager or an environment
// variable), never the key; what may be sent to it is the person's consent, and the organization's policy can
// turn connections off or narrow them. Test lists the models the key can see and spends no tokens.
async function renderAppStudioConnections() {
  const body = h("div", { class: "as-body" }, h("p", { class: "muted-text" }, "Reading your model connections..."));
  setView(h("div", { class: "page appstudio" },
    h("div", { class: "page-head" }, h("div", {},
      h("a", { href: "#", class: "small", onclick: (e) => { e.preventDefault(); appStudioLeave(); } }, "App Studio"),
      h("h1", {}, "Model connections ", previewTag()),
      pageIntro("Your own AI model, for describing an app and asking for changes. KratosForge keeps where the key is, never the key. ",
        docLink("app-studio.md", "Learn more")))),
    withHowItWorks(body, howItWorks("appStudioConnections", {
      reads: "Your connections on this PC. Test reads the connection's key from Credential Manager or the environment variable and lists the models it can see.",
      changes: "Save and Remove change appstudio/connections.json under this PC's KratosForge data. The key itself stays where you keep it.",
      results: "This page. Test spends no tokens and sends no app or data.",
    }))));
  const got = await rpc("appstudio_connections").catch((err) => { if (body.isConnected) fill(body, h("p", { class: "muted-text" }, friendlyError(err).text)); return null; });
  if (!got || state.view !== "appStudio" || !appStudio.connections || !body.isConnected) return;
  const split = h("div", { class: "as-conn-split" }, appStudioConnectionList(got), appStudioConnectionForm(got));
  fill(body, got.policy.enabled ? null : h("div", { class: "callout warn", role: "status" }, icon("warning", 20),
    h("span", {}, "Your organization's policy turns off AI model connections in App Studio. Templates and Design still work.")), split);
}

const AS_KEYLESS = new Set(["ollama"]);

// A key reference as people read it: where the key is, in words.
function appStudioKeyText(ref) {
  if (!ref) return "none needed";
  if (ref.startsWith("env:")) return `environment variable ${ref.slice(4)}`;
  return `Windows Credential Manager (${ref.replace(/^credman:/, "")})`;
}

function appStudioProviderLabel(got, id) {
  return got.providers.find((p) => p.id === id)?.label || id;
}

function appStudioConnectionList(got) {
  const test = async (c) => {
    const r = await attempt(() => rpc("appstudio_connection_test", { conn_id: c.id }), `Could not test ${c.name}`);
    if (!r) return;
    appStudio.tested = { id: c.id, models: r.models || [] };
    toast(r.model_found ? `${c.name} works: its key can see ${r.models.length} model${r.models.length === 1 ? "" : "s"}, including ${c.model}. No tokens were spent.`
      : `${c.name}'s key works, but it can't see ${c.model}. Edit the connection and choose one of the ${r.models.length} it can see.`, !r.model_found);
  };
  const remove = async (c) => {
    if (!(await confirmDialog(`Remove ${c.name}?`, "App Studio stops using this connection. Its key stays where you keep it (Credential Manager or the environment variable).", "Remove", { danger: true }))) return;
    if (await attempt(() => rpc("appstudio_connection_delete", { conn_id: c.id }), "Could not remove the connection")) render();
  };
  const makeDefault = async (c) => {
    if (await attempt(() => rpc("appstudio_connection_save", { connection: c, make_default: true }), "Could not make it the default")) render();
  };
  const row = (c) => {
    const refused = (c.refused || []).length > 0;
    const isDefault = c.id === got.default;
    const sends = [c.consent?.context ? "app context allowed" : "sends nothing until you allow app context",
      c.consent?.sample_rows ? "sample rows allowed" : null].filter(Boolean).join(" · ");
    return h("li", { class: "card pad as-conn" },
      h("div", { class: "as-conn-name" }, h("b", {}, c.name),
        refused ? h("span", { class: "as-chip warn" }, "Policy stops it") : isDefault ? h("span", { class: "as-chip ok" }, "Default") : null),
      h("div", { class: "small" }, `${appStudioProviderLabel(got, c.provider)} · ${c.model}`),
      h("div", { class: "muted-text small" }, sends),
      h("div", { class: "muted-text small" }, `Key: ${appStudioKeyText(c.key_ref)}`),
      c.usage?.requests ? h("div", { class: "muted-text small" }, `This month: ${c.usage.requests} request${c.usage.requests === 1 ? "" : "s"}, ${(c.usage.input_tokens + c.usage.output_tokens).toLocaleString()} tokens`) : null,
      refused ? h("ul", { class: "as-ready" }, c.refused.map((t) => h("li", { class: "todo" }, icon("warning", 14), t))) : null,
      h("div", { class: "row" },
        btn("Test", null, () => test(c), "tool small outline", { disabled: refused, "aria-label": `Test ${c.name}` }),
        btn("Edit", null, () => { appStudio.editing = c.id; render(); }, "tool small outline", { "aria-label": `Edit ${c.name}` }),
        isDefault || refused ? null : btn("Make default", null, () => makeDefault(c), "tool small outline", { "aria-label": `Make ${c.name} the default` }),
        btn("Remove…", "delete", () => remove(c), "tool small outline danger", { "aria-label": `Remove ${c.name}` })));
  };
  return h("section", { class: "as-conn-list", "aria-label": "Your connections" }, h("h2", {}, "Your connections"),
    got.connections.length ? h("ul", { class: "as-conn-rows" }, got.connections.map(row))
      : h("p", { class: "muted-text" }, "No model connections yet. Add one to describe apps and ask for changes in your own words."),
    appStudio.editing ? btn("Add a model connection", "add", () => { appStudio.editing = null; render(); }, "tool outline") : null);
}

// A key reference as App Studio keeps it: credman:KratosForge/<name> or env:<NAME>.
function appStudioKeyRef(kind, name) {
  const n = name.trim();
  if (!n) return "";
  return kind === "env" ? `env:${n.toUpperCase()}` : `credman:KratosForge/${n.replace(/^KratosForge\//i, "")}`;
}

function appStudioConnectionForm(got) {
  const editing = got.connections.find((c) => c.id === appStudio.editing) || null;
  const allowed = got.providers.filter((p) => p.allowed);
  const c = editing || { provider: allowed[0]?.id || "anthropic", consent: { context: false, sample_rows: false }, limits: {} };
  const name = h("input", { value: editing?.name || "", maxlength: 80, placeholder: "My Claude", "aria-label": "Connection name" });
  const provider = h("select", { "aria-label": "Provider" }, got.providers.map((p) =>
    h("option", { value: p.id, disabled: !p.allowed, selected: p.id === c.provider }, p.allowed ? p.label : `${p.label} (not allowed by your organization)`)));
  const tested = editing && appStudio.tested?.id === editing.id ? appStudio.tested.models : [];
  const models = h("datalist", { id: "as-models" }, tested.map((m) => h("option", { value: m })));
  const model = h("input", { value: editing?.model || "", maxlength: 128, list: "as-models", placeholder: "claude-sonnet-5-5", "aria-label": "Model" });
  const endpoint = h("input", { value: editing?.endpoint || "", placeholder: "https://", "aria-label": "Address" });
  const deployment = h("input", { value: editing?.deployment || "", maxlength: 64, "aria-label": "Deployment name" });
  const ref = editing?.key_ref || "";
  const keyKind = h("select", { "aria-label": "Where the key is kept" },
    h("option", { value: "credman", selected: !ref.startsWith("env:") }, "Windows Credential Manager"),
    h("option", { value: "env", selected: ref.startsWith("env:") }, "Environment variable"));
  const keyName = h("input", { value: ref.replace(/^credman:KratosForge\//, "").replace(/^env:/, ""), maxlength: 180, "aria-label": "Key name",
    placeholder: "my-claude" });
  const keyHelp = h("p", { class: "muted-text small" });
  const context = h("input", { type: "checkbox", checked: !!c.consent?.context });
  const rows = h("input", { type: "checkbox", checked: !!c.consent?.sample_rows && got.policy.sample_rows, disabled: !got.policy.sample_rows });
  const limit = h("input", { type: "number", min: 0, step: "any", value: c.limits?.monthly_limit ?? "", "aria-label": "Monthly spend limit" });
  const inPrice = h("input", { type: "number", min: 0, step: "any", value: c.limits?.input_price_per_million ?? "", "aria-label": "Price per million input tokens" });
  const outPrice = h("input", { type: "number", min: 0, step: "any", value: c.limits?.output_price_per_million ?? "", "aria-label": "Price per million output tokens" });
  const makeDefault = h("input", { type: "checkbox", checked: !got.connections.length || editing?.id === got.default });
  const endpointField = h("label", { class: "field" }, "Address", endpoint);
  const deploymentField = h("label", { class: "field" }, "Deployment", deployment);
  const keyFields = h("div", { class: "as-conn-grid" }, h("label", { class: "field" }, "Where the key is kept", keyKind), h("label", { class: "field" }, "Key name", keyName));
  const sync = () => {
    const info = got.providers.find((p) => p.id === provider.value) || {};
    endpointField.hidden = !!info.endpoint && provider.value !== "ollama";
    if (!endpoint.value && info.endpoint) endpoint.placeholder = info.endpoint;
    deploymentField.hidden = provider.value !== "azure-openai";
    keyFields.hidden = AS_KEYLESS.has(provider.value);
    const r = appStudioKeyRef(keyKind.value, keyName.value);
    const where = r ? appStudioKeyText(r) : keyKind.value === "env" ? "an environment variable" : "Windows Credential Manager";
    keyHelp.textContent = AS_KEYLESS.has(provider.value) ? "Ollama on this PC doesn't use a key."
      : `App Studio keeps only where the key is, never the key: ${where}. ` + (keyKind.value === "env"
        ? "The variable's name must start KRATOSFORGE_ or be the provider's own, such as ANTHROPIC_API_KEY."
        : `Add the key in Windows Credential Manager as a Generic credential, address KratosForge/${keyName.value.trim() || "<name>"}, with the key as its password.`);
  };
  [provider, keyKind].forEach((el) => el.addEventListener("change", sync));
  keyName.addEventListener("input", sync);
  sync();
  const save = async () => {
    const num = (el) => (el.value === "" ? undefined : Number(el.value));
    const limits = Object.fromEntries(Object.entries({ monthly_limit: num(limit), input_price_per_million: num(inPrice), output_price_per_million: num(outPrice) })
      .filter(([, v]) => v !== undefined));
    const conn = { name: name.value.trim(), provider: provider.value, model: model.value.trim(), limits,
      key_ref: AS_KEYLESS.has(provider.value) ? "" : appStudioKeyRef(keyKind.value, keyName.value),
      consent: { context: context.checked, sample_rows: rows.checked && !rows.disabled } };
    if (editing) conn.id = editing.id;
    if (!endpointField.hidden && endpoint.value.trim()) conn.endpoint = endpoint.value.trim();
    if (!deploymentField.hidden) conn.deployment = deployment.value.trim();
    const saved = await attempt(() => rpc("appstudio_connection_save", { connection: conn, make_default: makeDefault.checked }), "Could not save the connection");
    if (saved) { appStudio.editing = saved.id; toast(`Saved ${saved.name}. Test it to check the key works.`); render(); }
  };
  const off = !got.policy.enabled;
  return h("section", { class: "card pad as-conn-form", "aria-label": editing ? `Edit ${editing.name}` : "Add a model connection" },
    h("h2", {}, editing ? `Edit ${editing.name}` : "Add a model connection"),
    h("div", { class: "as-conn-grid" },
      h("label", { class: "field" }, "Name", name),
      h("label", { class: "field" }, "Provider", provider),
      h("label", { class: "field" }, "Model", model, models),
      endpointField, deploymentField),
    keyFields, keyHelp,
    h("fieldset", { class: "as-consent" }, h("legend", {}, "What this model may be sent"),
      h("label", { class: "check" }, context, h("span", {}, "App context: your descriptions, the app's pages and components, and the semantic model's table, column and measure names, types and descriptions. Emails, tenant and site names and ids are taken out first. ", h("b", {}, "Never rows."))),
      h("p", { class: "muted-text small" }, "Measure formulas (DAX) are sent only when you tick them for one request, because a formula can hold literal business values such as a customer name or a price."),
      h("label", { class: "check" }, rows, h("span", {}, got.policy.sample_rows ? "Sample rows, when you ask for them." : "Sample rows: off by your organization's policy."))),
    h("details", { class: "as-limits" }, h("summary", {}, "Spend limit"),
      h("div", { class: "as-conn-grid" },
        h("label", { class: "field" }, "Monthly limit", limit),
        h("label", { class: "field" }, "Input price per million tokens", inPrice),
        h("label", { class: "field" }, "Output price per million tokens", outPrice)),
      h("p", { class: "muted-text small" }, "With prices set, a request that would pass the limit is refused, and you're warned at 80 %.")),
    h("label", { class: "check" }, makeDefault, h("span", {}, "Use for new requests (default)")),
    h("div", { class: "row" }, btn(editing ? "Save changes" : "Save connection", "save", save, "tool primary", { disabled: off })));
}

// ---------- Publish (AS-13) ----------

// The check reads the app's semantic model, the workspace's name and its reports, and writes nothing. Publish
// sends that check's fingerprint back: if the app, the workspace or a report there changed, the engine says
// so and nothing is written. One report is created, or replaced only with its own tick.
async function appStudioPublishDialog(got) {
  if (!got.valid) {
    showDialog("Publish", h("div", {}, h("p", { class: "text" }, "Fix the specification's problems first. Spec lists them; History can restore a version.")),
      [btn("Close", null, closeDialog, "tool outline")]);
    return;
  }
  const sample = !(got.spec?.sources || []).some((s) => s.kind === "semantic-model");
  if (!sample && !(await ensureSignedIn())) return;
  const pane = h("div", { class: "as-publish" }, h("p", { class: "muted-text" }, "Checking what publishing would do..."));
  const closeBtn = btn("Cancel", null, closeDialog, "tool outline");
  const act = btn("Publish", "publish", null, "tool primary", { disabled: true });
  showDialog("Publish to Power BI", pane, [closeBtn, act]);
  const opts = { workspace_id: "", name: got.spec?.name || "", replace: false };
  const workspaces = sample ? [] : await loadWorkspaces();
  const check = async () => {
    act.disabled = true;
    const c = await rpc("appstudio_publish_check", { project_name: appStudio.project, workspace_id: opts.workspace_id, name: opts.name })
      .catch((err) => { if (pane.isConnected) fill(pane, h("div", { class: "callout warn", role: "alert" }, icon("warning", 20), h("span", {}, friendlyError(err).text))); return null; });
    if (c && pane.isConnected) draw(c);
  };
  const draw = (c, note = "") => {
    opts.workspace_id = c.workspace_id || opts.workspace_id;
    const ws = h("select", { "aria-label": "Workspace", onchange: (e) => { opts.workspace_id = e.target.value; opts.replace = false; check(); } },
      (workspaces.length ? workspaces : [{ id: c.workspace_id, displayName: c.workspace_name || c.workspace_id }]).filter((w) => w.id).map((w) =>
        h("option", { value: w.id, selected: w.id === c.workspace_id }, w.displayName + (w.id === (got.spec?.sources || [])[0]?.workspace_id ? " (the model's workspace)" : ""))));
    const name = h("input", { value: c.name || opts.name, maxlength: 256, "aria-label": "Report name",
      onchange: (e) => { opts.name = e.target.value; opts.replace = false; check(); } });
    const confirm = h("input", { autocomplete: "off", spellcheck: "false", "aria-label": `Type ${c.confirm_text} to publish` });
    const replace = h("input", { type: "checkbox", checked: opts.replace, onchange: (e) => { opts.replace = e.target.checked; ready(); } });
    const ready = () => { act.disabled = !c.ready || confirm.value !== c.confirm_text || (!!c.conflict && !replace.checked); };
    confirm.addEventListener("input", ready);
    const conflict = c.conflict ? h("div", { class: "callout warn" }, icon("warning", 20), h("div", {},
      h("b", {}, `A report called "${c.name}" is already in ${c.workspace_name}.`),
      h("p", {}, c.conflict.ours ? "This project published it. Publishing replaces its pages with this app's."
        : "This project didn't publish it, so it may be someone else's. Replacing it changes its pages for everyone who uses it."),
      h("label", { class: "check" }, replace, h("span", {}, "Replace that report")))) : null;
    fill(pane,
      note ? h("div", { class: "callout", role: "status" }, icon("info", 20), h("span", {}, note)) : null,
      h("dl", { class: "as-facts" },
        h("dt", {}, "Workspace"), h("dd", { class: "field" }, c.ready ? ws : c.workspace_name || "—"),
        h("dt", {}, "Report name"), h("dd", { class: "field" }, name),
        h("dt", {}, "Bound to"), h("dd", {}, c.model?.name ? `${c.model.name} (semantic model)` : "No semantic model yet"),
        h("dt", {}, "Writes"), h("dd", {}, c.conflict ? "Replaces one report's definition. Nothing else in Fabric changes." : "One new report. Nothing else in Fabric changes.")),
      c.problems.length ? h("div", { class: "callout error", role: "alert" }, icon("error", 20), h("div", {}, h("b", {}, "Can't publish yet"),
        h("ul", {}, c.problems.slice(0, 20).map((p) => h("li", {}, p))), h("p", {}, "Fix it in Design, then check again."))) : null,
      c.warnings.length ? h("details", { class: "as-warnings" }, h("summary", {}, `${c.warnings.length} thing${c.warnings.length === 1 ? "" : "s"} Power BI shows differently`),
        h("ul", {}, c.warnings.map((w) => h("li", {}, w)))) : null,
      c.unfinished ? h("div", { class: "callout warn", role: "status" }, icon("warning", 20),
        h("span", {}, `The last publish (${c.unfinished.at ? dateTimeText(c.unfinished.at) : "earlier"}) didn't finish. Check the workspace; a report it left behind is listed as this project's, so publishing again replaces it.`)) : null,
      conflict,
      c.ready ? h("label", { class: "field" }, `Type the workspace's name to publish: ${c.confirm_text}`, confirm) : null,
      h("p", { class: "muted-text small" }, "Publishing writes to Fabric. Building and previewing here use no capacity; viewing the report uses the workspace's capacity."));
    act.textContent = "";
    act.append(icon("publish", 16), c.ready ? (c.conflict ? "Replace and publish" : "Publish") : "Check again");
    act.className = c.conflict ? "tool danger-solid" : "tool primary";
    act.onclick = c.ready ? () => publish(c, confirm.value) : check;
    ready();
    if (!c.ready) act.disabled = false;
  };
  const publish = async (c, typed) => {
    act.disabled = true;
    fill(act, icon("publish", 16), "Publishing...");
    const r = await attempt(() => rpc("appstudio_publish", { project_name: appStudio.project, fingerprint: c.fingerprint, confirm_name: typed,
      workspace_id: c.workspace_id, name: c.name, replace: !!c.conflict && opts.replace }), "Could not publish");
    if (!pane.isConnected) return;
    if (!r) return check();
    if (r.stale) return draw(r, "Something changed since the check (the app, the workspace or a report there). Look again, then publish.");
    if (!r.done) return draw(r, r.needs_replace ? "Tick Replace that report to replace it." : "Type the workspace's name exactly to publish.");
    fill(pane, h("div", { class: "callout ok", role: "status" }, icon("ok", 20), h("div", {},
      h("b", {}, `${r.replaced ? "Replaced" : "Published"} ${r.name} in ${r.workspace_name}.`),
      h("p", {}, "Recorded in this app's History."),
      h("div", { class: "row" },
        r.url ? btn("Open in Power BI", "open", () => openLink(r.url), "tool small outline") : null,
        r.report_id && typeof openEmbedStudio === "function" && previewShown()
          ? btn("Open in Embed Studio", null, () => { closeDialog(); openEmbedStudio({ workspace_id: r.workspace_id, workspace_name: r.workspace_name, item_id: r.report_id, type: "Report", name: r.name }); }, "tool small outline")
          : null))));
    act.remove();
    fill(closeBtn, "Close");
    if (appStudio.tab === "history") render();
  };
  await check();
}

registerFeature({
  id: "appStudio",
  preview: true,
  views: { appStudio: renderAppStudio },
  commands: [
    { label: "Open App Studio", icon: "apps", keywords: "app studio application builder report dashboard ai design publish", run: () => { appStudio.project = null; go("appStudio"); } },
  ],
});
