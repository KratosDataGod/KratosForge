// App Studio (appstudio.js) against a mocked engine, for what appstudio*.test.cjs leave out: the start
// view's errors, broken projects and Import; opening a project, More (Save As, Clone, Export), pages, every
// shape the local preview draws, Design's edits, an invalid specification, History's kinds, the Describe card's
// semantic model choice and policy, Ask's pending change, the connections page's Test, Make default and
// refused rows, and Publish's other answers (see harness.cjs). The mocks answer in the engine's shapes.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";

const TARGETS = [{ id: "powerbi-report", label: "Power BI report", components: ["area", "bar", "card", "column", "donut", "line", "map", "matrix", "pie", "scatter", "slicer", "table", "text", "waterfall"] },
  { id: "kratosforge-app", label: "KratosForge app", components: ["bar", "card", "slicer", "table", "text"] }];
const TEMPLATES = [{ id: "blank", title: "Blank", text: "One empty page." }, { id: "sales", title: "Sales overview", text: "Revenue, profit and orders." }];
const comp = (id, type, title, extra = {}) => ({ id, type, title, measures: [], fields: [], layout: { x: 0, y: 0, w: 6, h: 4 }, props: {}, ...extra });
const makeSpec = (name) => ({
  format: 1, id: "app", name, target: "powerbi-report", sources: [{ id: "src_sales", kind: "sample", name: "Sample sales" }],
  measures: [{ id: "m_rev", name: "Revenue" }, { id: "m_profit", name: "Profit" }],
  theme: { colors: { primary: "#2A78D6" } },
  pages: [
    { id: "pg_overview", title: "Overview", components: [comp("c_rev", "card", "Revenue", { measures: ["m_rev"], layout: { x: 0, y: 0, w: 3, h: 2 } }),
      comp("c_slicer", "slicer", "Region"), comp("c_note", "text", "Note")] },
    { id: "pg_shapes", title: "Shapes", components: [comp("c_map", "map", "Stores"), comp("c_pie", "pie", "Share"), comp("c_donut", "donut", "Mix"),
      comp("c_area", "area", "Trend"), comp("c_scatter", "scatter", "Spread"), comp("c_matrix", "matrix", "Grid"), comp("c_empty", "bar", "No field")] },
    { id: "pg_blank", title: "Blank", components: [] },
  ],
});
const series = (name, values) => [{ name, values }];
const PREVIEW = {
  pg_overview: (sel) => [
    { id: "c_rev", type: "card", title: "Revenue", value: sel.Region ? 1200 : 98765, measure: "Revenue" },
    { id: "c_slicer", type: "slicer", title: "Region", field: "Region[Name]", values: ["East", "West"], selected: sel["Region[Name]"] || "" },
    { id: "c_note", type: "text", title: "Note", text: "Numbers are made up on this PC." }],
  pg_shapes: () => [
    { id: "c_map", type: "map", title: "Stores" },
    { id: "c_pie", type: "pie", title: "Share", labels: ["A", "B", "C"], series: series("Revenue", [5, 3, 2]) },
    { id: "c_donut", type: "donut", title: "Mix", labels: ["A", "B"], series: series("Revenue", [9, 1]) },
    { id: "c_area", type: "area", title: "Trend", labels: ["Jan", "Feb", "Mar"], series: series("Revenue", [1, 4, 2]) },
    { id: "c_scatter", type: "scatter", title: "Spread", labels: ["x", "y"], series: series("Revenue", [3, 6]) },
    { id: "c_matrix", type: "matrix", title: "Grid", field: "Product[Category]", labels: ["Bikes", "Clothing"],
      series: [{ name: "Revenue", values: [1500, 2500] }, { name: "Profit", values: [300, 400] }] },
    { id: "c_empty", type: "bar", title: "No field", labels: [], series: [] }],
  pg_blank: () => [],
};

let projects;     // project -> { spec, valid, problems, can_undo, can_redo }
let failHome;
let conns;
let versions;
let history;
let checks;       // queue of publish check answers
let publishes;    // queue of publish answers
let app;
const page = () => app.page;
const calls = (method) => app.calls.filter((c) => c.method === method).map((c) => c.params);

const opened = (project) => ({ meta: { name: projects[project].spec.name }, project, components: TARGETS[0].components, ...projects[project] });
const findIn = (spec, id) => {
  for (const p of spec.pages) {
    if (p.id === id) return { list: spec.pages, item: p };
    const c = (p.components || []).find((x) => x.id === id);
    if (c) return { list: p.components, item: c };
  }
  return null;
};
function applyOps(project, ops) {
  const spec = projects[project].spec;
  for (const op of ops) {
    if (op.op === "add" && op.kind === "page") spec.pages.splice(op.index, 0, op.value);
    else if (op.op === "add") spec.pages.find((p) => p.id === op.page).components.push(op.value);
    else if (op.op === "remove") { const f = findIn(spec, op.id); f.list.splice(f.list.indexOf(f.item), 1); }
    else if (op.op === "set") findIn(spec, op.id).item[op.field] = op.value;
  }
  projects[project].can_undo = true;
  return opened(project);
}

const CONN = (id, name, extra = {}) => ({ id, name, provider: "anthropic", model: "claude-model", key_ref: "env:ANTHROPIC_API_KEY",
  consent: { context: true, sample_rows: false }, limits: {}, refused: [], ...extra });
const PROVIDERS = [{ id: "anthropic", label: "Anthropic Claude", endpoint: "https://api.anthropic.com", allowed: true },
  { id: "azure-openai", label: "Azure OpenAI", endpoint: null, allowed: true },
  { id: "ollama", label: "Ollama (this PC)", endpoint: "http://localhost:11434", allowed: true },
  { id: "gemini", label: "Google Gemini", endpoint: "https://g", allowed: false }];
const connsWith = (connections, extra = {}) => ({ policy: { enabled: true, sample_rows: true }, providers: PROVIDERS, connections,
  default: connections[0]?.id || "", ...extra });

before(async () => {
  if (skip) return;
  app = await openApp({
    mocks: {
      appstudio_home: () => {
        if (failHome) throw new Error("The App Studio folder can't be read");
        return { templates: TEMPLATES, targets: TARGETS, projects: [
          ...Object.keys(projects).map((p) => ({ project: p, name: projects[p].spec.name, modified: "2026-11-01T10:00:00Z" })),
          { project: "Old.KFApp", broken: true }] };
      },
      appstudio_create: ({ name }) => { projects[`${name}.KFApp`] = { spec: makeSpec(name), valid: true, problems: [], can_undo: false, can_redo: false }; return opened(`${name}.KFApp`); },
      appstudio_open: ({ project_name }) => { if (!projects[project_name]) throw new Error("ValueError: Choose a project from the list."); return opened(project_name); },
      appstudio_preview: ({ page: pg, selection }) => {
        const p = Object.values(projects)[0].spec.pages.find((x) => x.id === pg);
        if (pg === "pg_broken") throw new Error("The preview couldn't be drawn");
        return { page: { id: pg, title: p?.title || pg }, simulated: true, note: "Sample numbers made on this PC.", selection, components: (PREVIEW[pg] || PREVIEW.pg_blank)(selection) };
      },
      appstudio_change: ({ project_name, ops }) => applyOps(project_name, ops),
      appstudio_versions: () => { if (versions instanceof Error) throw versions; return versions; },
      appstudio_history: () => history,
      appstudio_save_version: ({ label }) => ({ version: 3, label, at: "2026-11-20T10:00:00Z" }),
      appstudio_save_as: ({ project_name, name, clone }) => {
        projects[`${name}.KFApp`] = { ...projects[project_name], spec: { ...makeSpec(name) }, can_undo: !clone && projects[project_name].can_undo };
        return opened(`${name}.KFApp`);
      },
      appstudio_export: ({ project_name }) => ({ path: `C:\\Users\\ana\\Downloads\\${project_name.replace(".KFApp", "")} (App Studio).zip`, bytes: 2287 }),
      appstudio_import: ({ content }) => { projects["Imported.KFApp"] = { spec: makeSpec("Imported"), valid: true, problems: [], can_undo: false, can_redo: false }; return { project: "Imported.KFApp", bytes: content.length }; },
      appstudio_connections: () => conns,
      appstudio_connection_test: ({ conn_id }) => (conn_id === "c1" ? { models: ["claude-model", "claude-other"], model_found: true } : { models: ["only-one"], model_found: false }),
      appstudio_connection_save: ({ connection, make_default }) => {
        const c = { ...CONN("c-new", connection.name), ...connection };
        const kept = conns.connections.some((x) => x.id === c.id);
        conns = { ...conns, connections: kept ? conns.connections.map((x) => (x.id === c.id ? c : x)) : conns.connections.concat(c), default: make_default ? c.id : conns.default };
        return c;
      },
      appstudio_connection_delete: () => ({ deleted: true }),
      appstudio_plan: (p) => ({ plan_id: "plan1", plan: { name: "Model app", purpose: "", audience: [], pages: [{ title: "Main", components: [{ type: "card", title: "Revenue", measure: "Revenue" }] }], questions: [] },
        left_out: [], sent: { tables: 2, columns: 9, measures: 3, expressions: p.include_expressions }, usage: { input_tokens: 1, output_tokens: 0 },
        connection: { name: "Work Claude", model: "claude-model" }, warning: "The model answered in a shape it was asked not to use." }),
      appstudio_create_from_plan: ({ name }) => {
        projects[`${name}.KFApp`] = { spec: makeSpec(name), valid: true, problems: [], can_undo: false, can_redo: false };
        return { ...opened(`${name}.KFApp`), left_out: ["A map: the target can't show it"] };
      },
      appstudio_change_plan: ({ request }) => ({ change_id: "ch1", summary: "Show c_rev as a column", done: ["Change c_rev to column", "Rename pg_overview"],
        changed: ["c_rev"], left_out: ["c_99 isn't in the app"], questions: [`Did you mean pg_shapes? (${request})`] }),
      appstudio_apply_change: () => { throw new Error("The app changed since the plan"); },
      list_workspaces: () => [{ id: "w1", displayName: "Sales Dev" }, { id: "w2", displayName: "Empty WS" }],
      list_items: ({ workspace_id }) => (workspace_id === "w1" ? [{ id: "sm1", type: "SemanticModel", displayName: "Sales model" }, { id: "r1", type: "Report", displayName: "R" }] : []),
      appstudio_publish_check: () => { const c = checks.shift(); if (c instanceof Error) throw c; return c; },
      appstudio_publish: () => publishes.shift(),
      readiness_check: () => [],
    },
  });
});

after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  projects = { "Sales.KFApp": { spec: makeSpec("Sales"), valid: true, problems: [], can_undo: false, can_redo: false } };
  failHome = false;
  conns = connsWith([]);
  versions = [];
  history = [];
  checks = [];
  publishes = [];
  await page().evaluate(() => {
    closeDialog(); document.querySelectorAll(".menu").forEach((m) => m.remove());
    Object.assign(appStudio, { project: null, opened: null, tab: "preview", page: null, selection: {}, busy: false, connections: false, editing: null, tested: null, plan: null, change: null, description: "" });
    state.user = "ana@contoso.com"; state.workspaces = null;
    go("appStudio");
  });
  app.calls.length = 0;
  app.errors.length = 0;
});

const startView = () => page().locator(".as-create").waitFor();
async function openSales(tab = null) {
  await page().evaluate(() => appStudioOpen("Sales.KFApp"));
  await page().locator(".as-chips").waitFor();
  if (tab) {
    await page().getByRole("tab", { name: tab }).click();
    await page().waitForFunction((t) => document.querySelector(".subtabs .tab.active")?.textContent === t, tab);
  }
}
async function toast(text) {
  try {
    await page().waitForFunction((t) => document.getElementById("toast").textContent === t, text, { timeout: 10000 });
  } catch (err) {
    throw new Error(`The toast never said ${JSON.stringify(text)}; it says ${JSON.stringify(await page().locator("#toast").textContent())}`);
  }
}

test("a home that can't be read says why", { skip }, async () => {
  failHome = true;
  await page().evaluate(() => render());
  await page().getByText("The App Studio folder can't be read").waitFor();
  assert.strictEqual(await page().locator(".as-create").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("a broken project is listed without Open, and Enter in the name creates the app", { skip }, async () => {
  await startView();
  const rows = await page().locator(".as-projects tbody tr").allInnerTexts();
  assert.match(rows.join("|"), /Old\.KFApp · can't be read/);
  assert.strictEqual(await page().getByRole("button", { name: "Open Old.KFApp" }).count(), 0);
  assert.strictEqual(await page().getByRole("button", { name: "Open Sales" }).count(), 1);
  await page().getByLabel("Where it will run").first().selectOption("kratosforge-app");
  await page().locator(".as-template", { hasText: "Blank" }).click();
  assert.strictEqual(await page().locator(".as-template", { hasText: "Blank" }).getAttribute("aria-checked"), "true");
  await page().getByLabel("App name").fill("Ops board");
  await page().getByLabel("App name").press("Enter");
  await page().locator(".as-chips").waitFor();
  assert.deepStrictEqual(calls("appstudio_create"), [{ name: "Ops board", template: "blank", target: "kratosforge-app" }]);
  assert.match(await page().locator(".page.appstudio h1").textContent(), /Ops board/);
});

test("Import sends the chosen export and opens the new project", { skip }, async () => {
  await startView();
  await page().getByRole("button", { name: "Import…" }).click();
  const dialog = page().getByRole("dialog", { name: "Import an app" });
  await dialog.getByLabel("Choose an App Studio export").setInputFiles({ name: "app.zip", mimeType: "application/zip", buffer: Buffer.from("PK\u0003\u0004demo") });
  await page().locator(".as-chips").waitFor();
  assert.strictEqual(await dialog.count(), 0);
  const sent = calls("appstudio_import");
  assert.strictEqual(sent.length, 1);
  assert.strictEqual(Buffer.from(sent[0].content, "base64").toString("latin1"), "PK\u0003\u0004demo");
  assert.match(await page().locator(".page.appstudio h1").textContent(), /Imported/);
  assert.deepStrictEqual(calls("appstudio_open"), [{ project_name: "Imported.KFApp" }]);
});

test("a project that can't be opened goes back to the list with the reason", { skip }, async () => {
  await page().evaluate(() => appStudioOpen("Gone.KFApp"));
  await toast("Choose a project from the list.");
  await startView();
  assert.strictEqual(await page().evaluate(() => appStudio.project), null);
});

test("the preview draws cards, slicers, text, and an empty page says so", { skip }, async () => {
  await openSales();
  await page().locator(".as-t-text").getByText("Numbers are made up on this PC.").waitFor();
  assert.strictEqual(await page().locator(".as-t-card .as-card-value").textContent(), (98765).toLocaleString());
  await page().locator(".as-t-slicer select").selectOption("West");
  await page().waitForFunction(() => appStudio.selection["Region[Name]"] === "West");
  await page().waitForFunction(() => document.querySelector(".as-t-slicer select")?.value === "West");
  await page().locator(".as-t-slicer select").selectOption("");
  await page().waitForFunction(() => !("Region[Name]" in appStudio.selection));
  assert.deepStrictEqual(calls("appstudio_preview").map((p) => p.selection), [{}, { "Region[Name]": "West" }, {}]);
  await page().locator(".as-page", { hasText: "Blank" }).click();
  await page().getByText("This page is empty").waitFor();
  assert.strictEqual(await page().locator(".as-page.active").getAttribute("aria-current"), "page");
  assert.deepStrictEqual(app.errors, []);
});

test("the preview draws pies, donuts, areas, scatters and matrices, and names what it can't draw", { skip }, async () => {
  await openSales();
  await page().locator(".as-page", { hasText: "Shapes" }).click();
  await page().locator(".as-t-pie svg").waitFor();
  assert.strictEqual(await page().locator(".as-t-pie svg path").count(), 3);
  assert.strictEqual(await page().locator(".as-t-pie svg").getAttribute("aria-label"), "Share: A 5, B 3, C 2");
  assert.strictEqual(await page().locator(".as-t-donut svg circle.as-hole").count(), 1);
  assert.strictEqual(await page().locator(".as-t-area svg path").count(), 1);
  assert.strictEqual(await page().locator(".as-t-area svg polyline").count(), 1);
  assert.strictEqual(await page().locator(".as-t-scatter svg circle").count(), 2);
  assert.strictEqual(await page().locator(".as-t-scatter svg polyline").count(), 0);
  assert.deepStrictEqual(await page().locator(".as-t-matrix th").allTextContents(), ["Category", "Revenue", "Profit"]);
  assert.match(await page().locator(".as-t-matrix tbody").innerText(), new RegExp(`Bikes\\s+${(1500).toLocaleString()}\\s+300`));
  assert.match(await page().locator(".as-t-map").innerText(), /Map isn't drawn in the local preview/);
  assert.match(await page().locator(".as-t-bar").innerText(), /Pick a field in Design to show this\./);
  assert.deepStrictEqual(app.errors, []);
});

test("Add page asks for a title and adds it as a new page", { skip }, async () => {
  await openSales();
  await page().getByRole("button", { name: "Add page" }).click();
  const ask = page().getByRole("dialog", { name: "Add a page" });
  assert.strictEqual(await ask.getByRole("textbox").inputValue(), "Page 4");
  await ask.getByRole("textbox").fill("Customers");
  await ask.getByRole("button", { name: "Add" }).click();
  await page().waitForFunction(() => document.querySelector(".as-page.active")?.textContent.startsWith("Customers"));
  const ops = calls("appstudio_change")[0].ops;
  assert.deepStrictEqual(ops, [{ op: "add", kind: "page", index: 3, value: { id: "pg_1", title: "Customers", components: [] } }]);
  assert.strictEqual(calls("appstudio_change")[0].note, "Add page Customers");
  // Undo is on once there's something to undo.
  assert.strictEqual(await page().getByRole("button", { name: "Undo" }).isEnabled(), true);
});

test("Design edits a component's title, measure and width, renames and removes a page", { skip }, async () => {
  await openSales("Design");
  await page().getByLabel("Title of c_rev").fill("Sales");
  await page().getByLabel("Title of c_rev").press("Tab");
  const spec = (fn) => page().waitForFunction(fn);
  await spec(() => !appStudio.busy && appStudio.opened.spec.pages[0].components[0].title === "Sales");
  assert.deepStrictEqual(calls("appstudio_change")[0].ops, [{ op: "set", id: "c_rev", field: "title", value: "Sales" }]);
  await page().getByLabel("Measure of c_rev").selectOption("m_profit");
  await spec(() => !appStudio.busy && appStudio.opened.spec.pages[0].components[0].measures[0] === "m_profit");
  assert.deepStrictEqual(calls("appstudio_change")[1].ops, [{ op: "set", id: "c_rev", field: "measures", value: ["m_profit"] }]);
  await page().getByLabel("Measure of c_rev").selectOption("");
  await spec(() => !appStudio.busy && appStudio.opened.spec.pages[0].components[0].measures.length === 0);
  assert.deepStrictEqual(calls("appstudio_change")[2].ops[0].value, []);
  // A card 3 wide at x 0 made 12 wide stays at x 0; the width list has the card's own width too.
  assert.deepStrictEqual(await page().getByLabel("Width of c_rev").locator("option").allTextContents(),
    ["3 of 12", "4 of 12", "6 of 12", "8 of 12", "9 of 12", "12 of 12"]);
  await page().getByLabel("Width of c_rev").selectOption("12");
  await spec(() => !appStudio.busy && appStudio.opened.spec.pages[0].components[0].layout.w === 12);
  assert.deepStrictEqual(calls("appstudio_change")[3].ops[0].value, { x: 0, y: 0, w: 12, h: 2 });
  assert.strictEqual(await page().getByLabel("Measure of c_slicer").count(), 0);
  await page().getByLabel("Page title").fill("Summary");
  await page().getByLabel("Page title").press("Tab");
  await spec(() => !appStudio.busy && appStudio.opened.spec.pages[0].title === "Summary");
  assert.deepStrictEqual(calls("appstudio_change")[4].ops, [{ op: "set", id: "pg_overview", field: "title", value: "Summary" }]);
  await page().waitForFunction(() => document.querySelector(".as-page.active")?.textContent.startsWith("Summary"));
  await page().getByRole("button", { name: "Remove page" }).click();
  const ask = page().getByRole("dialog", { name: "Remove this page?" });
  assert.match(await ask.innerText(), /Summary and its 3 components leave the app\. Undo brings them back\./);
  assert.match(await ask.getByRole("button", { name: "Remove page" }).getAttribute("class"), /danger/);
  await ask.getByRole("button", { name: "Remove page" }).click();
  await spec(() => !appStudio.busy && appStudio.opened.spec.pages[0].id === "pg_shapes");
  assert.deepStrictEqual(calls("appstudio_change")[5].ops, [{ op: "remove", id: "pg_overview" }]);
  await page().waitForFunction(() => document.querySelector(".as-page.active")?.textContent.startsWith("Shapes"));
  assert.deepStrictEqual(app.errors, []);
});

test("Design on an empty page offers only Add, and the last page can't be removed", { skip }, async () => {
  projects["Sales.KFApp"].spec.pages = [{ id: "pg_blank", title: "Blank", components: [] }];
  await openSales("Design");
  await page().getByText("No components on this page yet.").waitFor();
  assert.strictEqual(await page().getByRole("button", { name: "Remove page" }).count(), 0);
  await page().getByLabel("Component to add").selectOption("text");
  await page().getByRole("button", { name: "Add component" }).click();
  await page().getByLabel("Title of c_1").waitFor();
  assert.deepStrictEqual(calls("appstudio_change")[0].ops[0].value,
    { id: "c_1", type: "text", title: "Text", measures: [], fields: [], layout: { x: 0, y: 0, w: 6, h: 4 }, props: {} });
});

test("an app with no pages has nothing to design", { skip }, async () => {
  projects["Sales.KFApp"].spec.pages = [];
  await openSales("Design");
  await page().getByText("No page", { exact: true }).waitFor();
  assert.strictEqual(await page().locator(".as-page").count(), 0);
});

test("an invalid specification can't be previewed, lists its problems in Spec and can't be published", { skip }, async () => {
  Object.assign(projects["Sales.KFApp"], { valid: false, problems: ["c_rev: the measure m_gone isn't in the app"] });
  await openSales();
  await page().getByText(/This app's specification has problems/).waitFor();
  assert.match(await page().locator(".as-chips").innerText(), /1 problem\b/);
  assert.strictEqual(await page().getByRole("button", { name: "Save version" }).isDisabled(), true);
  assert.strictEqual(await page().getByRole("button", { name: "Add page" }).count(), 0);
  await page().locator(".as-ask").getByText("Add your own AI model to describe apps and ask for changes.").waitFor();
  await page().getByRole("tab", { name: "Spec" }).click();
  await page().locator(".as-spec li", { hasText: "the measure m_gone isn't in the app" }).waitFor();
  assert.match(await page().getByLabel("Specification (app.json)").textContent(), /"name": "Sales"/);
  await page().getByRole("button", { name: "Publish…" }).click();
  await page().getByRole("dialog", { name: "Publish" }).getByText("Fix the specification's problems first.", { exact: false }).waitFor();
  assert.deepStrictEqual(calls("appstudio_publish_check"), []);
  assert.deepStrictEqual(calls("appstudio_preview"), []);
});

test("History lists versions and every kind of change, and Restore asks first", { skip }, async () => {
  versions = [{ version: 2, label: "Before review", at: "2026-11-19T09:00:00Z" }];
  history = [
    { kind: "change", note: "Add page Customers", source: "visual", at: "2026-11-19T10:00:00Z" },
    { kind: "undo", changed: ["c_rev", "c_month"], source: "ai" },
    { kind: "restore", version: 1, source: "" },
    { kind: "publish", state: "failed", name: "Sales", replaced: true, note: "the token expired" },
    { kind: "publish", state: "queued", name: "Sales" },
  ];
  await openSales("History");
  await page().locator(".as-changes li").first().waitFor();
  const items = await page().locator(".as-changes li").allInnerTexts();
  assert.match(items[0], /^Changed Add page Customers · Design /);
  assert.match(items[1], /^Undid c_rev, c_month · ai/);
  assert.match(items[2], /^Restored version 1/);
  assert.match(items[3], /^Publish didn't finish: Sales \(replaced\) · the token expired/);
  assert.match(items[4], /^Publish queued: Sales/);
  await page().getByRole("button", { name: "Restore version 2" }).click();
  const ask = page().getByRole("dialog", { name: "Restore version 2?" });
  assert.match(await ask.innerText(), /Before review becomes the app/);
  await ask.getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(calls("appstudio_restore_version"), []);
  // Save version on History redraws it.
  await page().getByRole("button", { name: "Save version" }).click();
  await page().getByRole("dialog", { name: "Save a version" }).getByRole("textbox").fill("After review");
  await page().getByRole("dialog", { name: "Save a version" }).getByRole("button", { name: "Save version" }).click();
  await toast("Saved version 3.");
  assert.deepStrictEqual(calls("appstudio_save_version"), [{ project_name: "Sales.KFApp", label: "After review" }]);
  await page().waitForFunction(() => document.querySelectorAll(".as-changes li").length === 5);
  assert.ok(calls("appstudio_versions").length >= 2);
});

test("History with nothing yet, and History that can't be read", { skip }, async () => {
  await openSales("History");
  await page().getByText("No versions yet. Save version keeps the app as it is now.").waitFor();
  await page().getByText("No changes yet.").waitFor();
  versions = new Error("The history file is damaged");
  await page().evaluate(() => render());
  await page().getByText("The history file is damaged").waitFor();
});

test("More saves the app as a new name, clones it, and exports it to Downloads", { skip }, async () => {
  await openSales();
  await page().getByRole("button", { name: "More project actions" }).click();
  await page().getByRole("menuitem", { name: "Export to Downloads" }).click();
  await toast("Exported to C:\\Users\\ana\\Downloads\\Sales (App Studio).zip");

  await page().getByRole("button", { name: "More project actions" }).click();
  await page().getByRole("menuitem", { name: "Save As…" }).click();
  const ask = page().getByRole("dialog", { name: "Save the app as" });
  assert.strictEqual(await ask.getByRole("textbox").inputValue(), "Sales copy");
  await ask.getByRole("textbox").fill("Sales v2");
  await ask.getByRole("button", { name: "Save As" }).click();
  await toast("Saved as Sales v2.");
  await page().waitForFunction(() => /Sales v2/.test(document.querySelector(".page.appstudio h1")?.textContent));

  await page().getByRole("button", { name: "More project actions" }).click();
  await page().getByRole("menuitem", { name: "Clone…" }).click();
  await page().getByRole("dialog", { name: "Clone the app" }).getByRole("button", { name: "Cancel" }).click();
  assert.strictEqual(calls("appstudio_save_as").length, 1);
  await page().getByRole("button", { name: "More project actions" }).click();
  await page().getByRole("menuitem", { name: "Clone…" }).click();
  await page().getByRole("dialog", { name: "Clone the app" }).getByRole("textbox").fill("Sales clone");
  await page().getByRole("dialog", { name: "Clone the app" }).getByRole("button", { name: "Clone" }).click();
  await toast("Cloned as Sales clone, with no history.");
  assert.deepStrictEqual(calls("appstudio_save_as").map((p) => [p.project_name, p.name, p.clone]),
    [["Sales.KFApp", "Sales v2", false], ["Sales v2.KFApp", "Sales clone", true]]);
  // The App Studio link goes back to the list, which now has the new projects.
  await page().locator(".page-head a", { hasText: "App Studio" }).click();
  await page().getByRole("button", { name: "Open Sales clone" }).waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("a policy that turns connections off says so on the start view and on Model connections", { skip }, async () => {
  conns = connsWith([], { policy: { enabled: false, sample_rows: false } });
  await page().evaluate(() => render());
  await page().locator(".as-describe").getByText("Your organization's policy turns off AI model connections in App Studio.", { exact: false }).waitFor();
  assert.strictEqual(await page().locator(".as-describe a", { hasText: "Model connections" }).count(), 0);
  await page().getByRole("button", { name: "Model connections" }).click();
  await page().getByText("No model connections yet.", { exact: false }).waitFor();
  assert.match(await page().locator(".callout.warn").innerText(), /policy turns off AI model connections/);
  assert.strictEqual(await page().getByRole("button", { name: "Save connection" }).isDisabled(), true);
  assert.match(await page().locator(".as-consent").innerText(), /Sample rows: off by your organization's policy\./);
});

test("the start view's link opens Model connections when there's none", { skip }, async () => {
  await page().locator(".as-describe a", { hasText: "Model connections" }).click();
  await page().getByRole("heading", { name: /Model connections/ }).waitFor();
  assert.strictEqual(await page().evaluate(() => appStudio.connections), true);
});

test("Describe on a semantic model lists its workspaces and models, and sends the source and formulas", { skip }, async () => {
  conns = connsWith([CONN("c1", "Work Claude")]);
  await page().evaluate(() => render());
  const card = page().locator(".as-describe");
  await card.getByLabel("Describe what the app should do").fill("Revenue by region");
  await card.getByLabel("Data").selectOption("model");
  await card.getByLabel("Semantic model").locator("option", { hasText: "Sales model" }).waitFor({ state: "attached" });
  assert.match(await card.innerText(), /Sends your description and the semantic model's table, column and measure names/);
  await card.getByLabel("Workspace").selectOption("w2");
  await card.getByLabel("Semantic model").locator("option", { hasText: "No semantic models here" }).waitFor({ state: "attached" });
  await card.getByRole("button", { name: "Make a plan" }).click();
  await toast("Choose a semantic model first.");
  assert.deepStrictEqual(calls("appstudio_plan"), []);
  await card.getByLabel("Workspace").selectOption("w1");
  await card.getByLabel("Semantic model").locator("option", { hasText: "Sales model" }).waitFor({ state: "attached" });
  await card.locator("input[type=checkbox]").check();
  assert.match(await card.innerText(), /Never rows\. Measure formulas too\./);
  await card.getByRole("button", { name: "Make a plan" }).click();
  const plan = page().getByRole("region", { name: "Plan" });
  await plan.getByText("Plan: Model app").waitFor();
  assert.deepStrictEqual(calls("appstudio_plan")[0].source, { workspace_id: "w1", item_id: "sm1", name: "Sales model" });
  assert.strictEqual(calls("appstudio_plan")[0].include_expressions, true);
  const text = await plan.innerText();
  assert.match(text, /2 table, 9 column and 3 measure names, with formulas\. No rows\. About 1 token\./);
  assert.match(text, /The model answered in a shape it was asked not to use\./);
  assert.match(text, /Revenue \(card\)/);
  await plan.getByRole("button", { name: "Build it" }).click();
  await toast("Built Model app. 1 thing was left out; the plan listed them.");
  await page().locator(".as-chips").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("Ask keeps a change plan across tabs; an Apply that fails keeps it, Discard drops it", { skip }, async () => {
  conns = connsWith([CONN("c1", "Work Claude")]);
  await openSales();
  const ask = page().locator(".as-ask");
  await ask.getByLabel("What should change?").fill("Revenue as a column");
  await ask.getByRole("button", { name: "Plan the change" }).click();
  await ask.getByText("Change plan").waitFor();
  const text = await ask.innerText();
  assert.match(text, /Show "Revenue" as a column/);
  assert.match(text, /Rename "Overview"/);
  assert.match(text, /Changes 1 item\. Nothing else is touched/);
  assert.match(text, /Did you mean "Shapes"\? \(Revenue as a column\)/);
  await ask.locator("summary", { hasText: "1 thing left out" }).click();
  await ask.getByText("c_99 isn't in the app").waitFor();
  // Switching tab draws the panel again with the pending change.
  await page().getByRole("tab", { name: "Spec" }).click();
  await page().locator(".as-ask").getByText("Change plan").waitFor();
  await page().locator(".as-ask").getByRole("button", { name: "Apply" }).click();
  await page().waitForFunction(() => document.getElementById("toast").textContent.startsWith("Could not apply the change"));
  await page().locator(".as-ask").getByText("Change plan").waitFor();
  assert.ok(await page().evaluate(() => appStudio.change?.change_id === "ch1"));
  await page().locator(".as-ask").getByRole("button", { name: "Discard" }).click();
  await page().waitForFunction(() => appStudio.change === null);
  await page().locator(".as-ask").getByRole("button", { name: "Plan the change" }).waitFor();
  assert.strictEqual(await page().locator(".as-ask .as-change").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("Model connections: Test, Make default, a refused row and this month's use", { skip }, async () => {
  conns = connsWith([
    CONN("c1", "Work Claude", { usage: { requests: 2, input_tokens: 1200, output_tokens: 300 } }),
    CONN("c2", "Spare", { key_ref: "credman:KratosForge/spare", consent: { context: false, sample_rows: true } }),
    CONN("c3", "Blocked", { refused: ["Your organization doesn't allow Anthropic Claude."] }),
  ]);
  await page().evaluate(() => { appStudio.connections = true; render(); });
  const list = page().getByRole("region", { name: "Your connections" });
  await list.getByText("Work Claude").waitFor();
  const rows = await list.locator(".as-conn").allInnerTexts();
  assert.match(rows[0], /Default/);
  assert.match(rows[0], /This month: 2 requests, 1,500 tokens/);
  assert.match(rows[0], /Key: environment variable ANTHROPIC_API_KEY/);
  assert.match(rows[1], /sends nothing until you allow app context · sample rows allowed/);
  assert.match(rows[1], /Key: Windows Credential Manager \(KratosForge\/spare\)/);
  assert.match(rows[2], /Policy stops it/);
  assert.match(rows[2], /Your organization doesn't allow Anthropic Claude\./);
  assert.strictEqual(await list.getByRole("button", { name: "Test Blocked" }).isDisabled(), true);
  assert.strictEqual(await list.getByRole("button", { name: "Make Blocked the default" }).count(), 0);
  assert.strictEqual(await list.getByRole("button", { name: "Make Work Claude the default" }).count(), 0);

  await list.getByRole("button", { name: "Test Work Claude" }).click();
  await toast("Work Claude works: its key can see 2 models, including claude-model. No tokens were spent.");
  await list.getByRole("button", { name: "Test Spare" }).click();
  await toast("Spare's key works, but it can't see claude-model. Edit the connection and choose one of the 1 it can see.");
  assert.strictEqual(await page().locator("#toast.error").count(), 1);

  // Editing the tested connection offers the models its key can see.
  await list.getByRole("button", { name: "Edit Spare" }).click();
  await page().getByRole("heading", { name: "Edit Spare" }).waitFor();
  assert.deepStrictEqual(await page().locator("#as-models option").evaluateAll((o) => o.map((x) => x.value)), ["only-one"]);
  assert.strictEqual(await page().getByLabel("Where the key is kept").inputValue(), "credman");
  assert.strictEqual(await page().getByLabel("Key name").inputValue(), "spare");

  await page().getByRole("button", { name: "Make Spare the default" }).click();
  await page().waitForFunction(() => document.querySelector(".as-conn")?.parentElement.children[1]?.textContent.includes("Default"));
  assert.deepStrictEqual(calls("appstudio_connection_save").map((p) => [p.connection.id, p.make_default]), [["c2", true]]);
  // Add a model connection goes back to a new one.
  await page().getByRole("button", { name: "Add a model connection" }).click();
  await page().getByRole("heading", { name: "Add a model connection" }).waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("a connection to Azure OpenAI sends its address and deployment; Ollama needs no key", { skip }, async () => {
  conns = connsWith([CONN("c1", "Work Claude")]);
  await page().evaluate(() => { appStudio.connections = true; render(); });
  const form = page().getByRole("region", { name: "Add a model connection" });
  await form.waitFor();
  await form.getByLabel("Provider").selectOption("ollama");
  await form.getByText("Ollama on this PC doesn't use a key.").waitFor();
  assert.strictEqual(await form.getByLabel("Key name").isVisible(), false);
  assert.strictEqual(await form.getByLabel("Address").isVisible(), true);
  assert.match(await form.getByLabel("Provider").locator("option[value=gemini]").textContent(), /not allowed by your organization/);
  await form.getByLabel("Provider").selectOption("azure-openai");
  assert.strictEqual(await form.getByLabel("Deployment name").isVisible(), true);
  await form.getByLabel("Connection name").fill("Azure");
  await form.getByLabel("Model", { exact: true }).fill("gpt-model");
  await form.getByLabel("Address").fill("https://contoso.openai.azure.com");
  await form.getByLabel("Deployment name").fill("prod");
  await form.getByLabel("Where the key is kept").selectOption("env");
  await form.getByLabel("Key name").fill("kratosforge_azure");
  assert.match(await form.innerText(), /environment variable KRATOSFORGE_AZURE\. The variable's name must start KRATOSFORGE_/);
  await form.locator("summary", { hasText: "Spend limit" }).click();
  await form.getByLabel("Monthly spend limit").fill("20");
  await form.getByLabel("Price per million input tokens").fill("2.5");
  await form.getByRole("button", { name: "Save connection" }).click();
  await toast("Saved Azure. Test it to check the key works.");
  const sent = calls("appstudio_connection_save")[0];
  assert.deepStrictEqual(sent.connection, { name: "Azure", provider: "azure-openai", model: "gpt-model", limits: { monthly_limit: 20, input_price_per_million: 2.5 },
    key_ref: "env:KRATOSFORGE_AZURE", consent: { context: false, sample_rows: false }, endpoint: "https://contoso.openai.azure.com", deployment: "prod" });
  assert.strictEqual(sent.make_default, false);
});

test("Publish a sample app: a check that fails, then one with problems, then a choice of name", { skip }, async () => {
  await openSales();
  checks = [
    { ready: false, problems: ["The app uses sample data. Connect a semantic model first."], warnings: [], name: "Sales", workspace_id: "", workspace_name: "",
      confirm_text: "", model: null, conflict: null, unfinished: { at: "2026-11-18T10:00:00Z" } },
  ];
  await page().getByRole("button", { name: "Publish…" }).click();
  const dialog = page().getByRole("dialog", { name: "Publish to Power BI" });
  await dialog.getByText("Can't publish yet").waitFor();
  const text = await dialog.innerText();
  assert.match(text, /The app uses sample data\./);
  assert.match(text, /No semantic model yet/);
  assert.match(text, /The last publish \(.+\) didn't finish\./);
  // Not ready: the button checks again, and is on.
  const act = dialog.getByRole("button", { name: "Check again" });
  assert.strictEqual(await act.isEnabled(), true);
  assert.deepStrictEqual(calls("list_workspaces"), []);
  checks.push({ ready: true, problems: [], warnings: [], name: "Sales", workspace_id: "w1", workspace_name: "Sales Dev", confirm_text: "Sales Dev",
    model: { name: "Sales model" }, conflict: { ours: true }, unfinished: null, fingerprint: "f1" });
  await act.click();
  await dialog.getByText('A report called "Sales" is already in Sales Dev.').waitFor();
  assert.match(await dialog.innerText(), /This project published it\. Publishing replaces its pages with this app's\./);
  assert.match(await dialog.innerText(), /Replaces one report's definition/);
  // A new name checks again with that name.
  checks.push({ ready: true, problems: [], warnings: [], name: "Sales 2", workspace_id: "w1", workspace_name: "Sales Dev", confirm_text: "Sales Dev",
    model: { name: "Sales model" }, conflict: null, unfinished: null, fingerprint: "f2" });
  await dialog.getByLabel("Report name").fill("Sales 2");
  await dialog.getByLabel("Report name").press("Tab");
  await dialog.getByText("One new report. Nothing else in Fabric changes.").waitFor();
  assert.deepStrictEqual(calls("appstudio_publish_check").map((p) => p.name), ["Sales", "Sales", "Sales 2"]);
  // Typing the name and an answer that says it wasn't typed exactly shows the check again with that note.
  publishes.push({ ready: true, done: false, problems: [], warnings: [], name: "Sales 2", workspace_id: "w1", workspace_name: "Sales Dev", confirm_text: "Sales Dev",
    model: { name: "Sales model" }, conflict: null, fingerprint: "f3" });
  await dialog.getByLabel("Type Sales Dev to publish").fill("Sales Dev");
  await dialog.getByRole("button", { name: "Publish" }).click();
  await dialog.getByText("Type the workspace's name exactly to publish.").waitFor();
  publishes.push({ done: true, replaced: false, name: "Sales 2", workspace_id: "w1", workspace_name: "Sales Dev" });
  await dialog.getByLabel("Type Sales Dev to publish").fill("Sales Dev");
  await dialog.getByRole("button", { name: "Publish" }).click();
  await dialog.getByText("Published Sales 2 in Sales Dev.").waitFor();
  assert.strictEqual(await dialog.getByRole("button", { name: "Open in Power BI" }).count(), 0);
  // Cancel becomes Close, and the Publish button is gone.
  assert.strictEqual(await dialog.getByRole("button", { name: "Cancel" }).count(), 0);
  assert.strictEqual(await dialog.getByRole("button", { name: "Close", exact: true }).last().isVisible(), true);
  assert.strictEqual(await dialog.getByRole("button", { name: "Publish", exact: true }).count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("Publish from a semantic model app lets the person pick the workspace, and an engine error is shown", { skip }, async () => {
  projects["Sales.KFApp"].spec.sources = [{ id: "s", kind: "semantic-model", workspace_id: "w2", item_id: "m" }];
  await openSales("Spec");
  const base = { ready: true, problems: [], warnings: ["Map visuals show as a placeholder."], name: "Sales", workspace_name: "Sales Dev", confirm_text: "Sales Dev",
    model: { name: "M" }, conflict: null, unfinished: null, fingerprint: "f" };
  checks = [{ ...base, workspace_id: "w1" }];
  await page().getByRole("button", { name: "Publish…" }).click();
  const dialog = page().getByRole("dialog", { name: "Publish to Power BI" });
  await dialog.getByLabel("Workspace").waitFor();
  assert.deepStrictEqual(await dialog.getByLabel("Workspace").locator("option").allTextContents(), ["Sales Dev", "Empty WS (the model's workspace)"]);
  assert.match(await dialog.locator("summary").textContent(), /1 thing Power BI shows differently/);
  // Another workspace checks again; a check that fails shows the engine's reason in the dialog.
  checks.push(new Error("The workspace can't be read"));
  app.calls.length = 0;
  await dialog.getByLabel("Workspace").selectOption("w2");
  await dialog.getByText("The workspace can't be read").waitFor();
  assert.deepStrictEqual(calls("appstudio_publish_check").map((p) => p.workspace_id), ["w2"]);
});
