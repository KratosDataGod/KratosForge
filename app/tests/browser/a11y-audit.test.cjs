// PB-063 part 1: an automated accessibility audit of every page in a real browser, light and dark:
// axe-core's WCAG 2.1 A and AA and 2.2 AA rules (contrast and target size among them), every clickable thing reachable from the
// keyboard, and a visible focus ring on every Tab stop. A serious or critical rule violation not in KNOWN_RULES,
// or a keyboard or focus finding not in KNOWN_KEYBOARD or KNOWN_FOCUS, fails the test. A11Y_REPORT=<file> also writes the results there as
// Markdown (docs/ux/accessibility-audit.md is made this way). axe-core is pinned in ci.yml (AXE_CORE_VERSION)
// and found through NODE_PATH like Playwright; without it the test skips here and fails in CI.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp, animationsDone } = require("./harness.cjs");
// axe covers dialogs (#1673, ui.test.cjs): the cases below run axe while these are open.
// axe covers dialogs: features.js:shortcutsDialog, integrations.js:showApproval, learn.js:learnPatternDialog
// axe covers dialogs: meta.js:confirmDialog, settings.js:reportProblem

let axeSource = null;
try { axeSource = fs.readFileSync(require.resolve("axe-core/axe.min.js"), "utf8"); } catch { /* not installed: skip */ }
const skip = (!playwright && "Playwright is not installed") || (!axeSource && "axe-core is not installed");

const LH = { workspace_id: "w1", lakehouse_id: "l1", name: "LH_Bronze", workspace_name: "Sales Dev" };
const CELLS = [
  { type: "markdown", cell_id: "m1", source: "# Daily sales\nLoads yesterday's orders.", meta: {} },
  { type: "code", cell_id: "c1", source: "import polars as pl\ndf = pl.read_delta('Tables/dbo/sales')", meta: {} },
  { type: "code", cell_id: "c2", source: "%%sql\nSELECT region, sum(amount) FROM sales GROUP BY region", meta: {} },
];
// A pipeline on this PC (the engine's own test file), its run's mocked activities, an incident and an agent.
const PIPELINE = fs.readFileSync(path.join(__dirname, "..", "..", "..", "sidecar", "tests", "pipelines", "reference_local.json"), "utf8");
const PIPELINE_MOCKS = { "Ingest orders": { output: { rowsCopied: 120 } }, Transform: { output: { exitValue: "ok" } } };
const ago = (s) => new Date(Date.now() - s * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
const INCIDENT = { id: "inc-0a1b2c3d4e5f", key: "run_failed:notebook:daily_sales:prod", kind: "run_failed", kind_label: "Run failed",
  workload: "notebook:daily_sales", name: "daily_sales", env: "prod", title: "daily_sales failed", text: "Path does not exist: Files/landing/orders",
  evidence: { source: "the run log", run_id: "run-1" }, evidence_id: "run-1", state: "open", opened_at: ago(7200), last_seen_at: ago(600),
  occurrences: 2, opened_by: "agent etl-01", owner: "", acknowledged_by: null, acknowledged_at: null, resolved_at: null, resolved_by: null,
  resolution: null, team: "Sales data team", criticality: "high", classification: null, escalated_at: null, escalated_to: null, notes: [],
  number: "INC-PROD-0001", source: "detected", priority: "P2", priority_source: "policy",
  events: [{ at: ago(7200), by: "agent etl-01", what: "opened", text: "daily_sales failed" }] };
const AGENT = { agent: "etl-01", state: "live", host: "etl-01.contoso.com", owner: "ana@contoso.com", version: "0.0.0-test",
  started_at: ago(86400), heartbeat_at: ago(30), status_seconds: 60,
  envs: { prod: { pipeline: { enabled: true, entities: ["silver.orders"] }, concurrency: 2, counts: { done: 4, failed: 1 }, running: [],
    schedules: [{ id: "nightly", entities: ["silver.orders"], next: ago(-3600) }],
    recent: [{ id: "job-1", entity: "silver.orders", state: "done", started_at: ago(3600), ended_at: ago(3500) }] } } };
const WORKLOAD = { id: "notebook:daily_sales", kind: "notebook", kind_label: "Notebook", name: "daily_sales", description: "",
  group: { type: "notebooks", id: "notebooks", label: "Notebooks on this PC" },
  definition: { name: "daily_sales", modified: Date.now() / 1000 - 7200, compute: "local", lakehouse: { name: "LH_Bronze" } },
  capabilities: ["discoverable", "observable", "runnable", "schedulable"], owners: {}, link: null,
  deployments: [{ env: null, env_label: "", location: { kind: "this_pc", label: "This PC" }, identity: { kind: "you", label: "You (ana@contoso.com), signed in on this PC" },
    tables_in: "LH_Bronze", version: null, schedules: [], latest_run: null, freshness: null,
    observation: { source: "Schedules on this PC", state: "live", label: "Live", collected_at: ago(0), age_seconds: 0, detail: "" }, actions: [] }],
  criticality: null, tags: [], health: "idle", health_label: "No recorded runs", health_rank: 5,
  findings: [{ level: "notice", kind: "ownership", title: "No owner", text: "Nobody is accountable for it, so nobody is told when it fails.", evidence: {},
    next: [{ action: "assign", label: "Assign" }], env: null }],
  readiness: { state: "ready", label: "Ready" }, unowned: true, partial: false, errors: [], dependencies: { upstream: [], downstream: [], products: [] },
  observation: { source: "Notebooks on this PC", state: "live", label: "Live", collected_at: ago(0), age_seconds: 0, detail: "" },
  runs: [], actions: [], assignment_history: [], platform_audit: { platform: "none", waiting: 0, error: "" }, timeline: [], consumers: [], available_actions: [] };
const RULES = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
// Each page is checked four ways. Light and dark are the window's color scheme. At 200% (Windows display scale or
// Ctrl+plus) the window's 1440x940 lays out as 720x470: nothing may make the whole page scroll sideways, and a Tab
// stop must be on screen and not under something else when it has focus. A Windows contrast theme (forced colors)
// replaces the app's colors with the theme's, so contrast is the theme's and isn't measured; what can break is a
// focus ring drawn as a box shadow, which forced colors remove. The keyboard walk runs in light, at 200% and in
// the contrast theme; it doesn't depend on light or dark.
const MODES = [
  { id: "light", media: { colorScheme: "light" }, walk: true },
  { id: "dark", media: { colorScheme: "dark" } },
  { id: "zoom-200", media: { colorScheme: "light" }, viewport: { width: 720, height: 470 }, walk: true },
  { id: "high-contrast", media: { colorScheme: "dark", forcedColors: "active" }, walk: true, off: ["color-contrast"] },
];
const SCHEMES = MODES.map((m) => m.id);
const VIEWPORT = { width: 1440, height: 940 };  // harness.cjs's

// Findings this audit recorded that are filed to be fixed (#905): "page: rule @ element" and "page: element".
// A new one fails; remove a line when its fix lands (the test then keeps it fixed).
const KNOWN_RULES = new Set([]);
const KNOWN_KEYBOARD = new Set([]);
const KNOWN_FOCUS = new Set([]);
// At 200%: "page: #view is Npx wide in a Mpx window" and "page: Tab stop" hidden when focused. Listed ones need a
// layout change (docs/ux/accessibility-zoom-contrast.md); a new one fails.
const KNOWN_SIDEWAYS = new Set([]);
const KNOWN_HIDDEN = new Set([]);

// Checks axe can't decide by itself, judged by a person (#945): "page: rule @ element" patterns and the verdict,
// measured in the browser with the app's colors. A new undecided item fails until it's judged here.
const JUDGED = [
  [/^home: color-contrast @ (h1|\.hero-outline|.*\.home-hero-(kicker|title|welcome|text > p|actions\b.*))$/, "Pass: the hero's text and buttons sit on the navy sky: the gradient #01203d to #062e57, and over the picture's empty left quarter, faded into that same sky (its sky runs #10223f to #1b3664; the text always ends before the cape, at 27% of the picture). Worst case, on #1b3664: the title #ffffff 11.9:1, the welcome #e6ecf5 10.1:1, the kicker #f8991c 5.4:1. On the plain gradient (axe names the welcome h1 and the outline button by their short selectors when they sit over the picture): the title #ffffff 13.7:1 at worst, the welcome #e6ecf5 11.5:1, the kicker #f8991c 6.2:1, the outline button's label 13.7:1, and Continue is #0b1420 on its own #f8991c fill, 8.4:1 (axe can't read a picture)."],
  [/^(create|workspaces|metadata|platform|optimize|academy|sourceControl|settings|settings-integrations|profile|monitor)(\/[\w-]+)?: color-contrast @ (h1|\.hero-outline|\.outline|.*\.home-hero-(title|actions\b.*))$/, "Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture)."],
  // axe names the arrow `.chev` or, when it needs more to tell it apart, `.chev[aria-hidden="true"]`.
  [/^(home|notebooks): color-contrast @ .*\.chev(\[aria-hidden="true"\])?$/, "Pass: the fold arrow is #616161 on white, 6.2:1 (3:1 needed for a control's graphic), and hidden from assistive tech (its button is named)."],
  [/^(workspaces(\/[\w-]+)?|powerbi): color-contrast @ .*\.star\[/, "Pass: the favorite star is #8a8a8a, 3.4:1 on white and 3.3:1 on #fafafa (3:1 needed for a control's graphic); its button is named."],
  [/^notebook: color-contrast @ .*\.cell-grip/, "Pass: the drag grip is #616161, 6.2:1, and hidden from assistive tech; the cell's Move up and Move down buttons do the same from the keyboard."],
  [/^(notebook|sql): color-contrast @ .*(cm-gutterElement|cm-activeLineGutter)/, "Pass: line numbers are #616161, 5.3:1 on the active line (#edeef1) and 5.9:1 or more elsewhere."],
  [/^(notebook|sql): color-contrast @ span\[title="Fold (line|code)"\]/, "Pass: the fold marker is #616161 on white, 6.2:1."],
  [/^(entity|data-mesh|mesh\w*): color-contrast @ \.arrow\[aria-hidden="true"\]/, "Pass: the step arrows are #8a8a8a, 3.4:1 on white and 3.3:1 on #fafafa, and hidden from assistive tech (the steps' order is in the text)."],
  [/^learn-lab: color-contrast @ \.learn-mark$/, "Pass: an exercise check's mark is #242424 on white, 15.5:1 (passed is #1f7a4a, 5.3:1), and hidden from assistive tech (the check's text says it)."],
  [/^report-problem: color-contrast @ textarea\[rows="3"\]$/, "Pass: What happened is #242424 on white, 15.5:1, and its placeholder #757575, 4.6:1."],
  [/^report-problem: color-contrast @ \.mono$/, "Pass: the diagnostics are #424242 on #fafafa, 9.6:1."],
  [/^entity: color-contrast @ #entity-toml$/, "Pass: the entity file's text is #242424 on #fafafa, 14.9:1 (axe sees the box as partly covered)."],
  [/^engine-choose: color-contrast @ (p:nth-child\(\d+\)( > (u|\.md-link-off):nth-child\(\d+\))?|u\[title="[\w.#-]+"\]|\.scroll > b|\.scroll > \.row > (\.tool\.small|\.muted-text(\.small)?))$/, "Pass: in both runs the decision pattern's text, links (a link that won't open is plain text with a dotted underline), headings and Copy example are #242424 on the dialog's white, 15.5:1, and the note beside Copy example is #616161, 6.2:1 (axe sees lines that scroll under the dialog's buttons as partly covered)."],
  [/^monitor: color-contrast @ (\.forge-(title|title > span|sub)|\.forge-more)$/, "Pass: on the Forge band's navy gradient (#000f22 to #0a2a4b, lightest taken), the title is #ffffff, 14.5:1, its state word at least 6.6:1 (#f8991c active, #7fd99b done 8.5:1, #ff9e9e failed 7.4:1, #c9d6ea quiet 9.9:1), the line under it #e6ecf5, 12.2:1, and the link to the table #ffcf8a, 10.1:1 (axe can't measure text on a gradient)."],
  [/^databricks_volumes: color-contrast @ \.small$/, "Pass: the volume's More actions button is the glyph ⋯ in #242424 on the card's white, 15.5:1 (axe can't measure a glyph it takes for an icon); its aria-label names it in words."],
  [/^shortcuts: color-contrast @ \.text:nth-child\(\d+\)( > \.learn-more\[href="#"\])?$/, "Pass: in both runs the notebook guide link is #1b3664 on the dialog's white, 12.0:1, and the line it sits in is #616161, 6.2:1 (axe sees the list's last lines, which scroll under the dialog's buttons, as partly covered)."],
];
const judged = (key) => JUDGED.find(([re]) => re.test(key))?.[1];

// Every page on the rail is found at run time; these open the screens that aren't rail pages.
const EXTRA = [
  { id: "notebook", name: "Notebook", open: (p) => p.evaluate(([cells, lh]) => {
    loadIntoState({ name: "Daily sales", cells, lakehouse: lh }); go("notebook");
  }, [CELLS, LH]), ready: ".cell .cm-content" },
  { id: "sql", name: "SQL query", open: (p) => p.evaluate((lh) => openSqlQuery(lh), LH), ready: ".sql-edit .cm-editor" },
  { id: "command-bar", name: "Command bar (Ctrl+K)", open: (p) => p.keyboard.press("Control+k"), ready: "#palette", scope: "#palette" },
  { id: "entity", name: "Data mesh entity", open: (p) => p.evaluate(() => {
    state.meta.project ||= { path: "C:/mesh", name: "Mesh", environments: {} };  // an open project, without changing the Data mesh page's audit
    openEntity("silver.customers");
  }), ready: "#entity-toml" },
  { id: "shell", name: "Shell (top bar and rail)", open: (p) => p.evaluate(() => go("home")), ready: "#rail", scope: "header.topbar, #rail" },
  { id: "confirm", name: "Confirm dialog", open: (p) => p.evaluate(() => {
    confirmDialog("Delete the lakehouse LH_Bronze?", "This removes its tables and files from Fabric.", "Delete", { danger: true });
  }), ready: "#dialog-root .dialog", scope: "#dialog-root" },
  // #1284: Preview screens and the dialogs people meet most, with the real engine where they read data.
  { id: "profile", name: "Profile", open: (p) => p.evaluate(() => { profilePage.tab = "overview"; go("profile"); }), ready: "#view .page.profile" },
  { id: "learn-lab", name: "Learn lab", open: (p) => p.evaluate(async () => {
    await rpc("learn_lab_start", { module_id: "LM-01" });  // the lab's data on this PC, as Start the lab makes it
    await learnOpen("LM-01");
  }), ready: "textarea.learn-editor" },
  { id: "settings-integrations", name: "Settings > Integrations", open: (p) => p.evaluate(async () => {
    await rpc("integrations_set_enabled", { enabled: true });
    state.settingsTab = "integrations";
    go("settings");
  }), ready: "#view .integrations section.card + section.card" },
  { id: "approval", name: "AI tool approval dialog", open: (p) => p.evaluate(() => {
    showApproval({ id: "ap_1", client_name: "Codex", title: "Run Python", permission: "EXECUTE_LOCAL_CODE", created_at: Date.now() / 1000,
      summary: { action: "Run Python on this PC", target: { workspace_id: "w1" }, content: { text: "print(1)" } } }, null);
  }), ready: "#dialog-root .dialog", scope: "#dialog-root" },
  { id: "report-problem", name: "Report a problem dialog", open: (p) => p.evaluate(() => reportProblem()),
    ready: "#dialog-root .dialog", scope: "#dialog-root" },
  // #1673: the keyboard shortcuts list (Help and about, or ?).
  { id: "shortcuts", name: "Keyboard shortcuts dialog", open: (p) => p.evaluate(() => shortcutsDialog()),
    ready: "#dialog-root .dialog", scope: "#dialog-root" },
  // The engine picker's "This PC or Fabric? How to choose" item opens this decision pattern (engines.js).
  { id: "engine-choose", name: "Engine picker's How to choose dialog", open: (p) => p.evaluate(() => learnPatternDialog("DP-02")),
    ready: "#dialog-root .dialog .learn-pattern", scope: "#dialog-root" },
  // Every other page features.js registers (features.views): opened the way the app opens them, with test data
  // where they read some. "every page features.js registers is audited" below fails when a new one isn't here.
  { id: "workspace", name: "A workspace", open: (p) => p.evaluate(() => openWorkspace({ id: "w1", displayName: "Sales Dev", capacityId: "c1" })),
    ready: "#view .page" },
  { id: "lakehouse", name: "A lakehouse", open: (p) => p.evaluate((lh) => openLakehouse(lh), LH), ready: "#view .page" },
  { id: "databricks_sync", name: "Workspaces > Databricks", open: (p) => p.evaluate(() => go("databricks_sync")), ready: "#view .page" },
  { id: "databricks_volumes", name: "Workspaces > Databricks > Volumes on this PC", open: (p) => p.evaluate(() => go("databricks_volumes")),
    ready: "#view .dbx-vol-files" },
  { id: "powerbi", name: "Workspaces > Power BI", open: (p) => p.evaluate(() => go("powerbi")), ready: "#view .page" },
  { id: "embedstudio", name: "Embed Studio", open: (p) => p.evaluate(() => openEmbedStudio({ workspace_id: "w1", workspace_name: "Sales Dev",
    item_id: "r1", type: "Report", name: "Sales performance" })), ready: "#view .page" },
  // The Fabric portal is Fabric's own web page in a window of its own; this audits the app's bar above it.
  { id: "fabric", name: "Fabric portal (the app's bar)", open: (p) => p.evaluate(() => go("fabric")), ready: "#view" },
  { id: "notebooks", name: "All notebooks", open: (p) => p.evaluate(() => go("notebooks")), ready: "#view .page" },
  { id: "pipelines", name: "A pipeline", open: (p) => p.evaluate((text) => openPipeline({ text }, { label: "This PC" }), PIPELINE),
    ready: "#view .page" },
  { id: "pipelineRun", name: "A pipeline run", open: (p) => p.evaluate(async ([text, mocks]) => {
    const started = await rpc("pipeline_run_start", { text, mocks, wait_scale: 0 });
    while (!["succeeded", "failed"].includes((await rpc("pipeline_run_status", { run_id: started.run_id, values: false })).state)) {
      await new Promise((done) => setTimeout(done, 50));
    }
    openPipelineRun(started.run_id);
  }, [PIPELINE, PIPELINE_MOCKS]), ready: "#view .page" },
  { id: "workload", name: "A workload", open: (p) => p.evaluate(() => openWorkload("notebook:daily_sales")), ready: "#view .wl-page h1" },
  { id: "incident", name: "An incident", open: (p) => p.evaluate((inc) => openIncident(inc), INCIDENT), ready: "#view .page h1" },
  { id: "agent", name: "An agent", open: (p) => p.evaluate(() => openAgent("etl-01")), ready: "#view .page h1" },
  { id: "appStudio", name: "App Studio", open: (p) => p.evaluate(() => appStudioOpen()), ready: "#view .page" },
  { id: "learn", name: "Learn", open: (p) => p.evaluate(() => { learnPage.open = null; learnPage.tab = "home"; go("learn"); }), ready: "#view .page" },
];

let app;
const results = [];

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["app_settings", "profile_get", "learn_catalog", "learn_module", "learn_lab_start", "learn_patterns", "integrations_status",
      "integrations_activity", "integrations_pending", "integrations_set_enabled", "editor_status",
      "pipeline_import", "pipeline_run_start", "pipeline_run_status", "pipeline_runs", "appstudio_home", "appstudio_connections",
      "databricks_sync_pairs", "engine_connections", "notebook_listing", "notebook_folders", "notebook_deleted"],
    mocks: {
      environment: () => ({ python: "Python 3 (test)", user: "ana@contoso.com", tenant: "t1" }),
      list_workspaces: () => [{ id: "w1", displayName: "Sales Dev", capacityId: "c1" }, { id: "w2", displayName: "Finance", capacityId: null }],
      list_lakehouses: () => [{ id: "l1", displayName: "LH_Bronze" }],
      databricks_volumes: () => ({ root: "C:\\vol\\main", volumes: [{ name: "landing", catalog: "main", database: "sales", owner: "ana",
        storage_location: "", volume_type: "MANAGED", comment: "raw drops", path: "/Volumes/main/sales/landing",
        folder: "C:\\Users\\ana\\AppData\\Local\\FabricDesktop\\volumes\\main\\sales\\landing", items: 2, created: 1 }] }),
      databricks_volume_files: ({ path }) => ({ path, truncated: false, entries: [
        { path: "dbfs:/Volumes/main/sales/landing/2026-11-22", name: "2026-11-22", size: 0, modification_time: 1, is_dir: true },
        { path: "dbfs:/Volumes/main/sales/landing/customers.csv", name: "customers.csv", size: 1800000, modification_time: 1, is_dir: false }] }),
      list_notebooks: () => [{ name: "Daily sales" }, { name: "Orders" }],
      list_tables: () => ({ dbo: ["sales", "customers"] }),
      list_files: () => [{ name: "orders.csv", path: "Files/raw/orders.csv", is_dir: false, size: 2048 }],
      table_columns: () => [{ name: "region", type: "string" }, { name: "amount", type: "double" }],
      cached_schemas: () => ({ tables: {}, columns: {} }),
      save_notebook: () => ({ saved: true }),
      meta_entity: () => ({ name: "silver.customers", layer: "silver", mode: "merge", watermark: "updated_at", owner: "ana@contoso.com",
        path: "metadata/entities/silver.customers.toml", text: "[entity]\nname = \"silver.customers\"\n", errors: [],
        columns: { id: "bigint", name: "string" }, keys: ["id"], surrogate_keys: [], derived: [], hook_adds: [], runs: [], custom: [],
        checks: 2, reads: ["bronze.customers"], impact: [], source: null }),
      meta_runs_all: () => ({ runs: [] }),
      meta_entity_shortcuts: () => [],
      list_items: () => [{ id: "l1", type: "Lakehouse", displayName: "LH_Bronze" }, { id: "n1", type: "Notebook", displayName: "Daily sales" },
        { id: "r1", type: "Report", displayName: "Sales performance" }, { id: "p1", type: "DataPipeline", displayName: "Nightly load" }],
      workspace_marks: () => ({ favorites: [], pins: [] }),
      workload_detail: () => WORKLOAD,
      incidents_board: ({ env }) => ({ env, incidents: env === "prod" ? [INCIDENT] : [] }),
      agents_list: () => ({ agents: [AGENT], envs: ["prod"], app_version: "0.0.0-test" }),
      pbi_embed_token: () => ({ token: "t", token_type: "Aad", expires_at: 9999999999, seconds_left: 3600 }),
      pbi_embed_content: ({ item_id }) => ({ kind: "report", id: item_id, name: "Sales performance", workspace_id: "w1",
        embed_url: `https://app.powerbi.com/reportEmbed?reportId=${item_id}&groupId=w1`, dataset_id: "d1",
        pages: [{ name: "P1", display_name: "Overview", order: 0 }, { name: "P2", display_name: "Regions", order: 1 }], tiles: [],
        capabilities: { embed_type: "report", pages: true, filters: true, bookmarks: true, slicers: true, visuals: true,
          events: ["loaded", "rendered", "error", "pageChanged"], limits: [] } }),
    },
  });
  await app.page.waitForSelector("#rail-main .rail-item");
  await app.page.waitForFunction(() => state.settings && "show_preview" in state.settings);
});

after(async () => {
  if (process.env.A11Y_REPORT && results.length) fs.writeFileSync(process.env.A11Y_REPORT, report(results));
  await app?.close();
});

// Runs in the page: the outermost things that look clickable (a pointer cursor) but can't be reached
// with Tab, and every Tab stop in `scope` with whether it shows where focus is.
function keyboardScan(scope) {
  const roots = document.querySelectorAll(scope);
  const css = (el) => {
    if (el.id) return `#${el.id}`;
    const cls = [...el.classList].slice(0, 2).map((c) => `.${c}`).join("");
    const text = (el.getAttribute("aria-label") || el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 30);
    return `${el.tagName.toLowerCase()}${cls}${text ? ` "${text}"` : ""}`;
  };
  const focusable = ["button", "a[href]", "input", "select", "textarea", "summary", "[tabindex]", "[contenteditable=true]", ".cm-editor"]
    .map((s) => `${s}:not([tabindex="-1"])`).join(", ");
  // A mouse-only control the keyboard reaches another way: out of the Tab order and hidden from assistive
  // tech, beside a Tab stop that names its key (a tab's ×, closed with Delete on the tab).
  const keyedElsewhere = (el) => el.closest("[aria-hidden=true]") && el.closest("[tabindex='-1']") &&
    [...(el.parentElement?.querySelectorAll("[aria-keyshortcuts]") || [])].some((k) => k.tabIndex >= 0 && k !== el);
  const unreachable = [];
  for (const el of [...roots].flatMap((r) => [...r.querySelectorAll("*")])) {
    const rect = el.getBoundingClientRect();
    if (!rect.width || !rect.height || getComputedStyle(el).cursor !== "pointer") continue;
    if (el.parentElement && getComputedStyle(el.parentElement).cursor === "pointer") continue;  // inherited: judged at the top
    // A label counts through the control it labels; a container through a Tab stop inside it (segmented
    // controls draw the pointer on the whole group). A page can't see click listeners, so a pointer card
    // that does something of its own beside the button it holds isn't caught here.
    const label = el.closest("label");
    if (el.closest(focusable) || el.querySelector(focusable) || (label?.control && label.control.tabIndex >= 0) || keyedElsewhere(el)) continue;
    unreachable.push(css(el));
  }
  // Icon-only controls: a button or link whose visible text has no letter or digit (an icon, or a glyph such as
  // × or ⋯, which axe takes as a name) must be named in words by aria-label, aria-labelledby or title.
  const words = (t) => /[\p{L}\p{N}]/u.test(t || "");
  const unnamed = [];
  for (const el of [...roots].flatMap((r) => [...r.querySelectorAll("button, a[href], [role=button], [role=tab], [role=menuitem]")])) {
    if (!el.checkVisibility() || el.closest("[aria-hidden=true]") || words(el.textContent)) continue;
    const by = (el.getAttribute("aria-labelledby") || "").split(/\s+/).map((id) => document.getElementById(id)?.textContent).join(" ");
    if (!words(el.getAttribute("aria-label")) && !words(by) && !words(el.title)) unnamed.push(css(el));
  }
  return { unreachable, unnamed };
}

// Runs in the page before a page opens: with reduced motion asked for (every mode here asks for it, as Windows does
// with Show animations off), records each animation a script starts (Element.animate, which the stylesheet's
// reduced-motion rules can't stop), unless the person chose motion anyway in Settings (data-motion-override).
function watchMotion() {
  window.__a11yAnimated = [];
  if (window.__a11yWatching) return;
  window.__a11yWatching = true;
  const animate = Element.prototype.animate;
  Element.prototype.animate = function (frames, opts) {
    const ms = typeof opts === "number" ? opts : opts?.duration || 0;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches && !document.documentElement.dataset.motionOverride && ms > 1) {
      window.__a11yAnimated.push(`${this.id ? `#${this.id}` : this.tagName.toLowerCase() + [...this.classList].slice(0, 2).map((c) => `.${c}`).join("")} animated for ${ms} ms`);
    }
    return animate.call(this, frames, opts);
  };
}

// Runs in the page once it has settled: what still moves with reduced motion asked for. A CSS animation or
// transition is cut to 0.01 ms and one round by the stylesheet, so one still running loops or outlasts that.
function motionNow() {
  const where = (el) => (!el ? "?" : el.id ? `#${el.id}` : el.tagName.toLowerCase() + [...el.classList].slice(0, 2).map((c) => `.${c}`).join(""));
  if (!matchMedia("(prefers-reduced-motion: reduce)").matches || document.documentElement.dataset.motionOverride) return [];
  const running = document.getAnimations().filter((a) => a.playState === "running").filter((a) => {
    const t = a.effect?.getComputedTiming();
    return t && (t.iterations === Infinity || t.duration > 1);
  }).map((a) => `${where(a.effect.target)} ${a.animationName || a.transitionProperty || "animation"} runs`);
  return [...new Set([...running, ...window.__a11yAnimated])];
}

// Runs in the page in a contrast theme: the theme picks every color except where the app keeps its own
// (forced-color-adjust: none, as on a selected tab), which axe isn't run on there. Each such element's text must
// still reach 4.5:1 against what it sits on (3:1 for large text, WCAG 1.4.3).
function forcedContrast(scope) {
  const rgb = (c) => { const m = c.match(/[\d.]+/g)?.map(Number) || [0, 0, 0, 0]; return { r: m[0], g: m[1], b: m[2], a: m.length > 3 ? m[3] : 1 }; };
  const lum = ({ r, g, b }) => [r, g, b].map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; })
    .reduce((acc, v, i) => acc + v * [0.2126, 0.7152, 0.0722][i], 0);
  // What it sits on: each background up to the first solid one, see-through ones laid over it (the theme's
  // Highlight is drawn at 80%).
  const back = (el) => {
    const layers = [];
    for (let n = el; n; n = n.parentElement) { const c = rgb(getComputedStyle(n).backgroundColor); if (c.a > 0) layers.push(c); if (c.a >= 0.99) break; }
    let base = layers.at(-1)?.a >= 0.99 ? layers.pop() : rgb(getComputedStyle(document.body).backgroundColor);
    for (const c of layers.reverse()) base = { r: c.r * c.a + base.r * (1 - c.a), g: c.g * c.a + base.g * (1 - c.a), b: c.b * c.a + base.b * (1 - c.a), a: 1 };
    return base;
  };
  const low = [];
  for (const el of [...document.querySelectorAll(scope)].flatMap((r) => [r, ...r.querySelectorAll("*")])) {
    const cs = getComputedStyle(el);
    if (cs.forcedColorAdjust !== "none" || !el.checkVisibility()) continue;
    if (![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) continue;  // its own words
    const [a, b] = [lum(rgb(cs.color)), lum(back(el))];
    const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    const size = parseFloat(cs.fontSize), bold = Number(cs.fontWeight) >= 700;
    const need = size >= 24 || (bold && size >= 18.66) ? 3 : 4.5;
    if (ratio < need) low.push(`${el.id ? `#${el.id}` : el.tagName.toLowerCase() + [...el.classList].slice(0, 2).map((c) => `.${c}`).join("")} "${el.textContent.trim().slice(0, 30)}" ${ratio.toFixed(2)}:1`);
  }
  return [...new Set(low)];
}

// Runs in the page: with text spacing raised as WCAG 1.4.12 asks (line height 1.5, paragraphs 2 em apart, letters
// 0.12 em and words 0.16 em apart), the words a box holds that it now cuts off. A box that shortens its text on
// purpose (an ellipsis or a line clamp) is left out: those show the whole text in a tooltip or on the next screen.
async function spacingClips(scope) {
  const style = document.createElement("style");
  style.nonce = document.getElementById("style-nonce")?.nonce || document.querySelector("style[nonce]")?.nonce || "";
  style.textContent = "* { line-height: 1.5 !important; letter-spacing: 0.12em !important; word-spacing: 0.16em !important; } p { margin-bottom: 2em !important; }";
  document.head.append(style);
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  const cut = [];
  for (const el of [...document.querySelectorAll(scope)].flatMap((r) => [...r.querySelectorAll("*")])) {
    const cs = getComputedStyle(el);
    if (!/hidden|clip/.test(cs.overflowX + cs.overflowY) || cs.textOverflow === "ellipsis" || cs.webkitLineClamp !== "none") continue;
    if (!el.checkVisibility() || !el.textContent.trim() || el.closest(".cm-editor, [aria-hidden=true], svg, .sr-only")) continue;
    // Words, not decoration (a glow drawn past the edge): a line of its text that ends outside the box.
    const box = el.getBoundingClientRect();
    const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let n; (n = walk.nextNode());) {
      if (!n.textContent.trim() || n.parentElement.closest("[aria-hidden=true]") || !n.parentElement.checkVisibility()) continue;
      let scrolls = false;  // inside a box of its own that scrolls (a list in a card): reached by scrolling
      for (let a = n.parentElement; a && a !== el && !scrolls; a = a.parentElement) {
        const ac = getComputedStyle(a);
        scrolls = /auto|scroll/.test(ac.overflowX + ac.overflowY) || ac.textOverflow === "ellipsis";
      }
      if (scrolls) continue;
      const range = document.createRange();
      range.selectNodeContents(n);
      const out = [...range.getClientRects()].find((r) => r.width && (r.right > box.right + 2 || r.bottom > box.bottom + 2 || r.left < box.left - 2));
      if (!out) continue;
      cut.push(`${el.id ? `#${el.id}` : el.tagName.toLowerCase() + [...el.classList].slice(0, 2).map((c) => `.${c}`).join("")} cuts off "${n.textContent.trim().replace(/\s+/g, " ").slice(0, 30)}"`);
      break;
    }
  }
  style.remove();
  await new Promise((r) => requestAnimationFrame(r));
  return [...new Set(cut)];
}

// Runs in the page before a Tab walk: each element's outline and box shadow with focus elsewhere, so a
// ring counts only where focus changed it (a dialog's own shadow isn't a focus ring).
function ringBaseline() {
  const look = (n) => { const cs = getComputedStyle(n); return `${cs.outlineStyle} ${cs.outlineWidth} ${cs.outlineColor}|${cs.boxShadow}`; };
  window.__a11yLook = look;
  // Not what holds focus now (the page may have put it somewhere): its look then is already focused.
  const holder = document.activeElement !== document.body ? document.activeElement : null;
  window.__a11yBase = new WeakMap([...document.querySelectorAll("*")].filter((n) => !holder || !n.contains(holder)).map((n) => [n, look(n)]));
}

// Runs in the page after a Tab: where focus is, whether it's inside `scope`, and whether it shows a
// ring: an outline or box shadow on it or a close parent (as :focus-within styles draw it) that it
// didn't have with focus elsewhere.
async function focusNow(scope) {
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));  // a ring that eases in has drawn
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const ring = (n) => {
    const cs = getComputedStyle(n);
    const has = (cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0) || (cs.boxShadow && cs.boxShadow !== "none");
    // An element with no baseline (drawn since the walk began) counts its own ring only, not a parent's.
    const base = window.__a11yBase?.get(n);
    return has && (base === undefined ? n === el : base !== window.__a11yLook(n));
  };
  let shown = false;
  for (let n = el, depth = 0; n && depth < 6 && !shown; n = n.parentElement, depth++) shown = ring(n);
  const rect = el.getBoundingClientRect();
  // On screen and on top: the middle of what has focus is inside the window and is it (or inside it), not
  // something drawn over it (WCAG 2.4.11). A control the browser can't scroll to fails too.
  // Taken at the middle of its part inside the window: a code editor taller than the window still shows.
  let [l, t, r, btm] = [Math.max(rect.left, 0), Math.max(rect.top, 0), Math.min(rect.right, innerWidth), Math.min(rect.bottom, innerHeight)];
  for (let n = el.parentElement; n; n = n.parentElement) {  // and inside what clips it (a code editor's wide lines scroll)
    if (getComputedStyle(n).overflow === "visible") continue;
    const c = n.getBoundingClientRect();
    [l, t, r, btm] = [Math.max(l, c.left), Math.max(t, c.top), Math.min(r, c.right), Math.min(btm, c.bottom)];
  }
  const top = r > l && btm > t ? document.elementFromPoint((l + r) / 2, (t + btm) / 2) : null;
  // A toast is passing over (it hides itself within 8 seconds, and while it has focus or the mouse it stays).
  const visible = !!top && (top === el || el.contains(top) || top.contains(el) || !!top.closest("label")?.contains(el) || !!top.closest("#toast"));
  const label = (el.getAttribute("aria-label") || el.textContent || el.value || "").trim().replace(/\s+/g, " ").slice(0, 30);
  const cls = [...el.classList].slice(0, 2).map((c) => `.${c}`).join("");
  el.dataset.a11yStop ||= String(Math.random());
  return { key: el.id ? `#${el.id}` : `${el.tagName.toLowerCase()}${cls}${label ? ` "${label}"` : ""}`, stop: el.dataset.a11yStop,
    inside: !!el.closest(scope), editor: el.matches(".cm-content, [data-esc-tab]"), visible, shown: shown && rect.width > 0 && rect.height > 0 && getComputedStyle(el).opacity !== "0" };
}

// Tab from the top of the page until focus comes back to a stop it has seen. In a code editor Tab
// indents, so the walk leaves it the documented way, Escape then Tab. A walk that comes back while
// Tab stops in `scope` are still unvisited can't get past `trap`.
async function tabStops(page, scope) {
  const stops = [];
  const seen = new Set();
  let trap = null;
  // Start at the top: blur() alone leaves the browser's Tab starting point where focus was.
  await page.evaluate(() => {
    const start = Object.assign(document.createElement("span"), { tabIndex: -1 });
    document.body.prepend(start);
    start.focus();
    start.remove();
  });
  await page.evaluate(ringBaseline);
  for (let i = 0; i < 400; i++) {
    await page.keyboard.press("Tab");
    const at = await page.evaluate(focusNow, scope);
    if (!at) break;
    if (seen.has(at.stop)) {
      const left = await page.evaluate(([scope, seenIds]) => [...document.querySelectorAll(`:is(${scope}) :is(button, a[href], input, select, textarea, summary, [tabindex], .cm-content):not([tabindex="-1"])`)]
        .filter((e) => !e.disabled && e.checkVisibility({ visibilityProperty: true }) && !e.closest("[inert], [hidden]"))  // a closed <details> menu's items aren't shown
        .filter((e) => !seenIds.includes(e.dataset.a11yStop)).length, [scope, [...seen]]);
      if (left) trap = { key: at.key, left };
      break;
    }
    seen.add(at.stop);
    // A page still drawing can replace the control under focus or restyle it a frame late: read once more
    // before calling a ring missing.
    if (at.inside && !at.shown) { await page.waitForTimeout(150); at.shown = (await page.evaluate(focusNow, scope))?.shown ?? false; }
    if (at.inside) stops.push({ key: at.key, shown: at.shown, visible: at.visible });
    if (at.editor) await page.keyboard.press("Escape");
  }
  return { stops, trap };
}

// Until the page has made no engine call for 200 ms (at most 3 s): what it loads has drawn.
async function settle(page) {
  let last = -1;
  for (let t = 0; t < 3000; t += 200) {
    const n = app.calls.length;
    if (n === last) return;
    last = n;
    await page.waitForTimeout(200);
  }
}

async function audit(page, target, scheme) {
  const mode = MODES.find((m) => m.id === scheme);
  // Reduced motion: a page fading in would be judged on its half-drawn colors.
  await page.emulateMedia({ forcedColors: "none", ...mode.media, reducedMotion: "reduce" });
  await page.setViewportSize(mode.viewport || VIEWPORT);
  assert.strictEqual(await page.evaluate(() => matchMedia("(forced-colors: active)").matches), mode.media.forcedColors === "active", `${scheme} is drawn as it says`);
  await page.evaluate(() => { document.getElementById("palette")?.remove(); closeDialog(); go("home"); });
  await page.evaluate(watchMotion);
  await target.open(page);
  if (target.ready) await page.waitForSelector(target.ready);
  await settle(page);  // the page's own calls answer
  await animationsDone(page);  // axe judges contrast as drawn: nothing still fading in (#1337)
  const moving = await page.evaluate(motionNow);
  if (!(await page.evaluate(() => typeof axe === "object"))) await page.addScriptTag({ content: axeSource });
  const scope = target.scope || "#view";
  const forcedLow = mode.media.forcedColors === "active" ? await page.evaluate(forcedContrast, scope) : [];
  // Text spacing changes only sizes, never colours, so one scheme measures it for all: light, at full size.
  const clipped = scheme === "light" ? await page.evaluate(spacingClips, scope) : [];
  const axeOut = await page.evaluate(async ([scope, tags, off]) => {
    const rules = Object.fromEntries(off.map((id) => [id, { enabled: false }]));
    const res = await axe.run({ include: [...document.querySelectorAll(scope)] }, { runOnly: { type: "tag", values: tags }, rules });
    const brief = (v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.map((n) => n.target.join(" ")).slice(0, 5) });
    return { violations: res.violations.map(brief), incomplete: res.incomplete.map(brief), passes: res.passes.length };
  }, [scope, RULES, mode.off || []]);
  const { unreachable, unnamed } = await page.evaluate(keyboardScan, scope);
  const { stops, trap } = mode.walk ? await tabStops(page, scope) : { stops: [], trap: null };
  if (trap) unreachable.push(`Tab returns to ${trap.key}, ${trap.left} Tab stops later never reached`);
  // At 200%, the page as a whole must not need scrolling sideways (WCAG 1.4.10); a table or code inside it may.
  const sideways = mode.viewport ? await page.evaluate(() =>
    [document.scrollingElement, document.getElementById("view")].filter((e) => e && e.scrollWidth > e.clientWidth + 1)
      .map((e) => `${e.id ? `#${e.id}` : "the page"} is ${e.scrollWidth}px wide in a ${e.clientWidth}px window`)) : [];
  const view = await page.evaluate(() => state.view);
  const result = { page: target.name, id: target.id, scheme, view, ...axeOut, unreachable, unnamed, moving, forcedLow, clipped, stops, sideways,
    noRing: stops.filter((s) => !s.shown).map((s) => s.key),
    hidden: mode.viewport ? stops.filter((s) => !s.visible).map((s) => s.key) : [] };
  results.push(result);
  return result;
}

// A rail page's other tabs (its first tablist's tabs that aren't open when it opens), found at run time. A tab that
// opens another page (Data products, a tab-jump) isn't a role=tab, so it isn't here.
const TABS = [];
async function pageTabs(page, rail) {
  await page.emulateMedia({ forcedColors: "none", colorScheme: "light", reducedMotion: "reduce" });
  await page.setViewportSize(VIEWPORT);
  for (const r of rail) {
    await page.evaluate((v) => { closeDialog(); go("home"); go(v); }, r.view);
    await settle(page);
    const labels = await page.evaluate(() => {
      const list = document.querySelector("#view [role=tablist]");
      return list ? [...list.querySelectorAll("[role=tab]")].filter((t) => t.getAttribute("aria-selected") !== "true" && !t.disabled)
        .map((t) => t.textContent.trim()) : [];
    });
    for (const label of labels) {
      const name = label.replace(/Preview$/, "");
      TABS.push({ id: `${r.id}/${name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`, name: `${r.name} > ${name}`, ready: "#view",
        open: async (p) => {
          await p.evaluate((v) => go(v), r.view);
          await p.waitForSelector("#view [role=tablist] [role=tab]");
          await p.evaluate((label) => [...document.querySelector("#view [role=tablist]").querySelectorAll("[role=tab]")]
            .find((t) => t.textContent.trim() === label).click(), label);
        } });
    }
  }
  return TABS;
}

async function targets(page) {
  const rail = await page.evaluate(() => [...railFeatures(), ...railFeatures(true)]
    .map((f) => ({ id: f.id, name: f.rail.label || f.id, view: Object.keys(f.views || {})[0] })));
  const tabs = await pageTabs(page, rail);
  return [...rail.map((r) => ({ id: r.id, name: r.name, open: (p) => p.evaluate((v) => go(v), r.view), ready: "#view" })), ...tabs, ...EXTRA];
}

test("every page passes the WCAG A and AA rules, light, dark, at 200% and in a contrast theme, with no serious or critical violation", { skip }, async () => {
  const failures = [];
  for (const target of await targets(app.page)) {
    for (const scheme of SCHEMES) {
      const r = await audit(app.page, target, scheme);
      for (const v of r.violations.filter((v) => v.impact === "serious" || v.impact === "critical")) {
        const fresh = v.nodes.filter((n) => !KNOWN_RULES.has(`${r.id}: ${v.id} @ ${n}`));
        if (fresh.length) failures.push(`${r.page} (${scheme}): ${v.id} ${v.impact} at ${fresh.join(", ")}`);
      }
    }
  }
  assert.ok(results.length >= MODES.length * (EXTRA.length + 5), `audited ${results.length} page views`);
  if (process.env.AXE_CORE_VERSION) assert.strictEqual(rowsVersion(), process.env.AXE_CORE_VERSION, "the axe-core that ran is the one ci.yml pins");
  assert.deepStrictEqual(failures, []);
  const seen = new Set(results.flatMap((r) => r.violations.flatMap((v) => v.nodes.map((n) => `${r.id}: ${v.id} @ ${n}`))));
  assert.deepStrictEqual([...KNOWN_RULES].filter((k) => !seen.has(k)), [], "fixed: remove these from KNOWN_RULES");
});

// A finding's key: "page: what" in light, "page (mode): what" in the other modes.
const where = (r) => (r.scheme === "light" ? r.id : `${r.id} (${r.scheme})`);

test("everything clickable is reached with Tab, and every Tab stop shows focus, light, at 200% and in a contrast theme", { skip }, async () => {
  const walked = results.filter((r) => MODES.find((m) => m.id === r.scheme).walk);
  const keyboard = walked.flatMap((r) => r.unreachable.map((k) => `${where(r)}: ${k}`)).filter((k) => !KNOWN_KEYBOARD.has(k));
  const focus = walked.flatMap((r) => r.noRing.map((k) => `${where(r)}: ${k}`)).filter((k) => !KNOWN_FOCUS.has(k));
  assert.deepStrictEqual(keyboard, [], "clickable but not reachable with Tab");
  assert.deepStrictEqual(focus, [], "Tab stops with no visible focus");
  const found = new Set(walked.flatMap((r) => [...r.unreachable, ...r.noRing].map((k) => `${where(r)}: ${k}`)));
  assert.deepStrictEqual([...KNOWN_KEYBOARD, ...KNOWN_FOCUS].filter((k) => !found.has(k)), [], "fixed: remove these from the known lists");
  for (const id of MODES.filter((m) => m.walk).map((m) => m.id)) {
    const rows = walked.filter((r) => r.scheme === id);
    assert.ok(rows.filter((r) => r.stops.length).length >= rows.length - 1, `Tab reached the pages' controls (${id})`);
  }
});

test("every icon-only button and link on every page is named in words", { skip }, async () => {
  const light = results.filter((r) => r.scheme === "light");
  assert.ok(light.length >= EXTRA.length + 5, `checked ${light.length} pages`);
  assert.deepStrictEqual(light.flatMap((r) => r.unnamed.map((k) => `${r.id}: ${k}`)), [], "icon-only controls with no name in words");
});

test("in a contrast theme, the colors the app keeps for itself still have enough contrast", { skip }, async () => {
  const hc = results.filter((r) => r.scheme === "high-contrast");
  assert.ok(hc.length >= EXTRA.length + 5, `checked ${hc.length} pages`);
  assert.deepStrictEqual(hc.flatMap((r) => r.forcedLow.map((k) => `${r.id}: ${k}`)), [], "low contrast where the app keeps its own colors");
});

test("with text spacing raised (WCAG 1.4.12), no page cuts off its words", { skip }, async () => {
  const light = results.filter((r) => r.scheme === "light");
  assert.ok(light.length >= EXTRA.length + 5, `checked ${light.length} pages`);
  assert.deepStrictEqual(light.flatMap((r) => r.clipped.map((k) => `${r.id}: ${k}`)), [], "words cut off with more text spacing");
});

test("with reduced motion asked for, nothing on any page moves", { skip }, async () => {
  assert.ok(results.length >= MODES.length * (EXTRA.length + 5), `checked ${results.length} page views`);
  assert.deepStrictEqual([...new Set(results.flatMap((r) => r.moving.map((k) => `${r.id}: ${k}`)))], [], "motion with reduced motion on");
});

test("at 200% no page scrolls sideways, and focus is never off screen or under something", { skip }, async () => {
  const zoomed = results.filter((r) => r.scheme === "zoom-200");
  assert.ok(zoomed.length >= EXTRA.length + 5, `checked ${zoomed.length} pages at 200%`);
  const sideways = zoomed.flatMap((r) => r.sideways.map((k) => `${r.id}: ${k}`)).filter((k) => !KNOWN_SIDEWAYS.has(k));
  const hidden = zoomed.flatMap((r) => r.hidden.map((k) => `${r.id}: ${k}`)).filter((k) => !KNOWN_HIDDEN.has(k));
  assert.deepStrictEqual(sideways, [], "pages that scroll sideways at 200%");
  assert.deepStrictEqual(hidden, [], "Tab stops off screen or covered when they have focus, at 200%");
  const found = new Set(zoomed.flatMap((r) => [...r.sideways, ...r.hidden].map((k) => `${r.id}: ${k}`)));
  assert.deepStrictEqual([...KNOWN_SIDEWAYS, ...KNOWN_HIDDEN].filter((k) => !found.has(k)), [], "fixed: remove these from the known lists");
});

test("every tab of every rail page is audited, not only the one a page opens on", { skip }, async () => {
  assert.ok(TABS.length >= 20, `found ${TABS.length} tabs: the rail pages' tablists weren't found`);
  const audited = new Set(results.map((r) => r.id));
  assert.deepStrictEqual(TABS.filter((t) => !audited.has(t.id)).map((t) => t.name), []);
});

test("every page features.js registers is audited", { skip }, async () => {
  const views = await app.page.evaluate(() => Object.keys(features.views));
  const audited = new Set(results.map((r) => r.view));
  assert.deepStrictEqual(views.filter((v) => !audited.has(v)), [], "add these pages to EXTRA");
});

test("every check axe couldn't decide has a person's verdict", { skip }, async () => {
  const open = results.filter((r) => r.scheme === "light").flatMap((r) => r.incomplete.flatMap((v) =>
    v.nodes.filter((n) => !judged(`${r.id}: ${v.id} @ ${n}`)).map((n) => `${r.id}: ${v.id} @ ${n}`)));
  assert.deepStrictEqual(open, [], "judge these and add them to JUDGED");
});

test("the audit catches a violation put into a page", { skip }, async () => {
  const page = app.page;
  const before = results.length;
  const r = await audit(page, { id: "injected", name: "Injected", ready: "#a11y-injected", open: (p) => p.evaluate(() => {
    // A bare element with a click listener: h() would have made it a Tab stop.
    const clicky = Object.assign(document.createElement("div"), { id: "a11y-injected", textContent: "Open" });
    clicky.style.cssText = "cursor:pointer;color:#bbb;background:#fff";
    clicky.addEventListener("click", () => {});
    clicky.animate([{ opacity: 0.5 }, { opacity: 1 }], { duration: 1000, iterations: Infinity });  // with reduced motion on
    document.getElementById("view").append(clicky,
      h("img", { src: "data:image/gif;base64,R0lGODlhAQABAAAAACw=" }),
      h("button", { class: "a11y-bare", style: "outline:none" }),
      h("button", { class: "a11y-glyph" }, "×"),  // axe takes the glyph as its name
      // A fixed-height box that hides what doesn't fit: more text spacing pushes its last line out.
      h("div", { class: "a11y-short", style: "height:18px;overflow:hidden;width:200px;font-size:13px;line-height:18px" }, "One line now, and more than one line once spacing grows"),
      h("button", { class: "a11y-glyph-named", "aria-label": "Close" }, "×"),
      // A button with no ring of its own inside a container that always has a shadow, as a dialog has.
      h("div", { style: "box-shadow:0 4px 16px rgba(0,0,0,.2);padding:8px" }, h("button", { class: "a11y-shadowed", style: "outline:none" }, "Shadowed")));
  }) }, "light");
  results.length = before;  // not part of the report
  const ids = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id);
  for (const id of ["image-alt", "color-contrast", "button-name"]) assert.ok(ids.includes(id), `${id} in ${ids.join(", ")}`);
  assert.ok(r.unreachable.includes("#a11y-injected"), r.unreachable.join(", "));
  assert.ok(r.moving.some((k) => k.startsWith("#a11y-injected")), `moves: ${r.moving.join(", ")}`);
  assert.ok(r.clipped.some((k) => k.startsWith("div.a11y-short")), `cut off: ${r.clipped.join(", ")}`);
  assert.ok(r.unnamed.some((k) => k.startsWith("button.a11y-glyph")) && !r.unnamed.some((k) => k.startsWith("button.a11y-glyph-named")), r.unnamed.join(", "));
  assert.ok(r.noRing.some((k) => k.startsWith("button.a11y-bare")), r.noRing.join(", "));
  assert.ok(r.noRing.some((k) => k.startsWith("button.a11y-shadowed")), `a container's own shadow isn't a ring: ${r.noRing.join(", ")}`);
});

test("the audit catches a page that breaks at 200% or in a contrast theme", { skip }, async () => {
  const page = app.page;
  const before = results.length;
  const plant = (p) => p.evaluate(() => {
    const ringByShadow = h("button", { class: "a11y-shadow-ring", style: "outline:none" }, "Shadow ring");
    ringByShadow.addEventListener("focus", () => { ringByShadow.style.boxShadow = "0 0 0 3px #1b3664"; });  // a contrast theme removes it
    ringByShadow.addEventListener("blur", () => { ringByShadow.style.boxShadow = ""; });
    const covered = h("button", { class: "a11y-covered" }, "Covered");
    // Colors kept from a contrast theme, too close to each other.
    const kept = h("span", { class: "a11y-kept", style: "forced-color-adjust:none;color:#777777;background:#888888" }, "Kept colors");
    document.getElementById("view").append(h("div", { id: "a11y-zoomed", style: "width:1100px" }, "Wide"), ringByShadow, covered, kept);
    // Something drawn over the button wherever it is (a fixed bar would be the real case).
    const r = covered.getBoundingClientRect();
    document.body.append(h("div", { class: "a11y-cover",
      style: `position:fixed;left:${r.left - 4}px;top:${r.top - 4}px;width:${r.width + 8}px;height:${r.height + 8}px;background:#fff;z-index:5` }));
  });
  const zoomed = await audit(page, { id: "injected", name: "Injected", ready: "#a11y-zoomed", open: plant }, "zoom-200");
  await page.evaluate(() => document.querySelectorAll(".a11y-cover").forEach((e) => e.remove()));
  const contrast = await audit(page, { id: "injected", name: "Injected", ready: "#a11y-zoomed", open: plant }, "high-contrast");
  await page.evaluate(() => document.querySelectorAll(".a11y-cover").forEach((e) => e.remove()));
  results.length = before;  // not part of the report
  assert.ok(zoomed.sideways.some((k) => k.startsWith("#view is ")), zoomed.sideways.join(", "));
  assert.ok(zoomed.hidden.some((k) => k.startsWith("button.a11y-covered")), zoomed.hidden.join(", "));
  assert.ok(contrast.noRing.some((k) => k.startsWith("button.a11y-shadow-ring")), contrast.noRing.join(", "));
  assert.ok(contrast.forcedLow.some((k) => k.startsWith("span.a11y-kept")), contrast.forcedLow.join(", "));
  assert.ok(!zoomed.noRing.some((k) => k.startsWith("button.a11y-shadow-ring")), "outside a contrast theme the shadow ring shows");
});

function report(rows) {
  const ids = [...new Set(rows.map((r) => r.id))];
  const byId = (id, scheme) => rows.find((r) => r.id === id && r.scheme === scheme);
  const count = (list, impact) => list.filter((v) => !impact || v.impact === impact).length;
  const lines = [
    "# Accessibility audit: automated, per page",
    "",
    "*Made by `app/tests/browser/a11y-audit.test.cjs` (PB-063 part 1). Do not edit by hand: run*",
    "`A11Y_REPORT=docs/ux/accessibility-audit.md node --test app/tests/browser/a11y-audit.test.cjs` *to refresh it.*",
    "",
    `Rules: axe-core ${rowsVersion()} with the WCAG 2.1 A and AA and WCAG 2.2 AA tags (${RULES.join(", ")}), color contrast and target size (24 by 24 px, or spaced apart) among them. ` +
      "Keyboard: everything with a pointer cursor must be a Tab stop or inside one, and pressing Tab from the top " +
      "must reach every Tab stop without looping (Escape then Tab leaves a code editor). Focus: each Tab stop on the page " +
      "must show an outline or ring. Each page is checked in light and dark (the window's color scheme); the app has " +
      "one theme, so dark is checked to show nothing breaks when Windows is set to dark. At 200% (the 1440x940 window laid out " +
      "as 720x470) no page may scroll sideways as a whole, and each Tab stop must be on screen and not under something when " +
      "it has focus. In a Windows contrast theme (forced colors) the theme picks the colors, so axe's contrast rule is off there; " +
      "where the app keeps its own colors (forced-color-adjust: none, as on a selected tab or choice), the text must still " +
      "reach 4.5:1 against what it sits on (3:1 for large text), measured with the theme's colors. Each Tab stop must still " +
      "show a ring there too (the theme removes rings drawn as box shadows). The keyboard walk runs in light, at 200% and in " +
      "the contrast theme. Every page is opened with reduced motion asked for, as Windows asks when Show animations is off: " +
      "nothing may still be moving once it has drawn (a loop, or an animation longer than the stylesheet's cap), and no script " +
      "may start an animation, unless the person chose motion anyway in Settings. A button or link that shows only an icon or " +
      "a symbol must be named in words (aria-label, aria-labelledby or title). With text spacing raised as WCAG 1.4.12 asks " +
      "(line height 1.5, paragraphs 2 em apart, letters 0.12 em and words 0.16 em apart), no box may cut off its words, unless " +
      "it shortens them on purpose with an ellipsis or a line clamp. Dialogs opened from buttons are checked the " +
      "same way in `a11y-dialogs.test.cjs`.",
    "",
    "The pages are opened signed in with test data (two workspaces, one lakehouse, two notebooks), so lists show rows " +
      "where they can. Screens that need a live tenant to fill are audited in their empty or signed-in state. " +
      "Part 2 (Narrator and keyboard on Windows) is a separate pass.",
    "",
    "| Page | Scheme | Rules passed | Violations (serious + critical) | Needs a look | Tab stops | Unreachable | No focus ring |",
    "|---|---|---|---|---|---|---|---|",
  ];
  for (const id of ids) {
    for (const scheme of SCHEMES) {
      const r = byId(id, scheme);
      if (!r) continue;
      lines.push(`| ${r.page} | ${scheme} | ${r.passes} | ${count(r.violations)} (${count(r.violations, "serious") + count(r.violations, "critical")}) | ` +
        `${r.incomplete.map((v) => `${v.id} (${v.nodes.length})`).join(", ") || "none"} | ${r.stops.length} | ${r.unreachable.length} | ${r.noRing.length} |`);
    }
  }
  const findings = rows.flatMap((r) => [
    ...r.violations.map((v) => `- **${r.page}** (${r.scheme}): ${v.id}, ${v.impact}: ${v.help}. At ${v.nodes.map((n) => `\`${n}\``).join(", ")}`),
    ...r.unreachable.map((k) => (k.startsWith("Tab returns")
      ? `- **${r.page}** (${r.scheme}): keyboard: ${k} (Escape, then Tab, used to leave each code editor)`
      : `- **${r.page}** (${r.scheme}): \`${k}\` looks clickable but isn't reached with Tab`)),
    ...r.noRing.map((k) => `- **${r.page}** (${r.scheme}): \`${k}\` shows no focus ring`),
    ...(r.sideways || []).map((k) => `- **${r.page}** (${r.scheme}): ${k}`),
    ...(r.hidden || []).map((k) => `- **${r.page}** (${r.scheme}): \`${k}\` is off screen or covered when it has focus`),
    ...(r.forcedLow || []).map((k) => `- **${r.page}** (${r.scheme}): \`${k}\` keeps its own colors with too little contrast`),
    ...(r.moving || []).map((k) => `- **${r.page}** (${r.scheme}): ${k} with reduced motion on`),
    ...(r.unnamed || []).map((k) => `- **${r.page}** (${r.scheme}): \`${k}\` shows only an icon and isn't named in words`),
    ...(r.clipped || []).map((k) => `- **${r.page}** (${r.scheme}): \`${k}\` with more text spacing`),
  ]);
  const looks = rows.filter((r) => r.scheme === "light").flatMap((r) => r.incomplete.map((v) => {
    const verdicts = [...new Set(v.nodes.map((n) => judged(`${r.id}: ${v.id} @ ${n}`) || "Not judged yet."))];
    return `- **${r.page}**: ${v.id}: ${v.help}. At ${v.nodes.map((n) => `\`${n}\``).join(", ")}. ${verdicts.join(" ")}`;
  }));
  lines.push("", "## Findings", "", "Filed to be fixed in [#905](https://github.com/KratosDataGod/KratosForge/issues/905); the test fails on any serious or critical rule violation, or keyboard or focus finding, not listed here.", "",
    ...(findings.length ? findings : ["None."]));
  lines.push("", "## Needs a look", "",
    "Checks axe couldn't decide by itself (for example a glyph, a line number, or text over a gradient or an image), each with a person's verdict and the measured contrast.", "",
    ...(looks.length ? looks : ["None."]), "");
  return lines.join("\n");
}

function rowsVersion() {
  const m = axeSource && axeSource.match(/axe v([\d.]+)/);
  return m ? m[1] : "?";
}
