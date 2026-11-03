// UI performance baseline (docs/performance.md#the-app-window). Measures what a person waits on in the
// window itself, before any platform skin lands, so later changes are compared against it:
//   startup      page load to the left rail drawn and every script loaded
//   navigation   go(view) for each rail page until it is styled and laid out (median and slowest)
//   appearance   one change of the tokens a skin sets (CSS variables on <html>) or of density, to the
//                page styled and laid out again: what switching a skin will cost
//   large list   a workspace of 5,000 items: Show items to the last row drawn, and the DOM nodes it adds
//   editor       200 characters typed into the SQL editor: total time and long tasks while typing
//   memory       JS heap and DOM nodes after visiting every page, after a garbage collection
//   background   main-thread time used in 10 s of doing nothing after the tour (timers and polls)
// Fakes only: no engine, no Fabric, no network. Numbers come from headless Chromium on whatever machine
// runs this; compare runs on the same machine. ui-budgets.test.cjs holds them to ui-budgets.json.
//   NODE_PATH="$(npm root -g)" node app/tests/browser/ui-benchmark.cjs [--runs 3] [--json out.json]
const { openApp } = require("./harness.cjs");

const WS = "aaaaaaaa-0000-0000-0000-00000000ffff";
const ITEMS = 5000;
const TYPES = ["Lakehouse", "Notebook", "Report", "SemanticModel", "DataPipeline", "Warehouse"];
const mocks = {
  readiness_check: () => [],
  list_workspaces: () => [{ id: WS, displayName: "Large workspace" }],
  workspace_marks: () => ({ favorites: [], pins: [] }),
  list_items: () => Array.from({ length: ITEMS }, (_, i) => ({
    id: `item-${String(i).padStart(5, "0")}`, displayName: `item_${String(i).padStart(5, "0")}`,
    type: TYPES[i % TYPES.length], workspaceId: WS })),
  list_tables: () => ({ dbo: ["sales"] }),
  table_columns: () => [{ name: "order_id", type: "long" }],
};

const median = (xs) => { const s = [...xs].sort((a, b) => a - b); return s.length ? s[Math.floor((s.length - 1) / 2)] : null; };
const round = (x) => (x == null ? null : Math.round(x * 10) / 10);

// Resolves after the change has been styled, laid out and painted: two animation frames.
const PAINTED = "() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))";

async function once() {
  const t0 = Date.now();
  const app = await openApp({ mocks, viewport: { width: 1440, height: 900 } });
  const page = app.page;
  try {
    await page.waitForSelector(".rail-item");
    const startup = await page.evaluate(() => performance.now());
    const wall = Date.now() - t0;  // with Chromium's own start, for reference only
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("Performance.enable");

    // Every rail page, three times; the first visit to a page is kept apart (it builds the page's caches).
    const views = await page.evaluate(() => [...railFeatures(), ...railFeatures(true)].map((f) => Object.keys(f.views)[0]));
    const first = {};
    const again = [];
    for (let round_ = 0; round_ < 3; round_++) {
      for (const view of views) {
        const ms = await page.evaluate(async ({ view, painted }) => {
          const done = eval(painted);
          const t = performance.now();
          go(view);
          void document.body.offsetHeight;  // styled and laid out now, not at the next frame
          const ms = performance.now() - t;
          await done();
          return ms;
        }, { view, painted: PAINTED });
        if (round_ === 0) first[view] = ms; else again.push(ms);
      }
    }

    // What a skin switch does: new values for the tokens on <html>, then (separately) a density change.
    await page.evaluate(() => go("home"));
    const tokenSwitch = [];
    const densitySwitch = [];
    for (let i = 0; i < 10; i++) {
      tokenSwitch.push(await page.evaluate(async (painted) => {
        const done = eval(painted);
        const root = document.documentElement.style;
        const t = performance.now();
        if (root.getPropertyValue("--accent")) {
          for (const k of ["--accent", "--accent-dark", "--accent-soft", "--surface", "--canvas", "--text", "--border"]) root.removeProperty(k);
        } else {
          Object.entries({ "--accent": "#0f6cbd", "--accent-dark": "#0c3b5e", "--accent-soft": "#ebf3fc", "--surface": "#fafafa",
            "--canvas": "#f0f0f0", "--text": "#1a1a1a", "--border": "#d6d6d6" }).forEach(([k, v]) => root.setProperty(k, v));
        }
        void document.body.offsetHeight;
        const ms = performance.now() - t;
        await done();
        return ms;
      }, PAINTED));
      densitySwitch.push(await page.evaluate(async (painted) => {
        const done = eval(painted);
        const t = performance.now();
        document.body.classList.toggle("density-compact");
        void document.body.offsetHeight;
        const ms = performance.now() - t;
        await done();
        return ms;
      }, PAINTED));
    }
    await page.evaluate(() => { document.documentElement.removeAttribute("style"); document.body.classList.remove("density-compact"); });

    // A large workspace.
    await page.evaluate(() => { state.user = "chris@contoso.com"; state.workspaces = null; go("workspaces"); });
    await page.getByRole("button", { name: "Show items" }).first().waitFor();
    const nodesBefore = await page.evaluate(() => document.getElementsByTagName("*").length);
    const listStart = await page.evaluate(() => performance.now());
    await page.getByRole("button", { name: "Show items" }).first().click();
    await page.locator(".ws-items tr").first().waitFor();
    await page.evaluate(PAINTED);
    const listMs = (await page.evaluate(() => performance.now())) - listStart;
    const listNodes = (await page.evaluate(() => document.getElementsByTagName("*").length)) - nodesBefore;
    const listRows = await page.locator(".ws-items tr").count();

    // The SQL editor: 200 characters typed, and long tasks (50 ms or more) while typing.
    await page.evaluate(() => {
      window.__longTasks = [];
      new PerformanceObserver((l) => window.__longTasks.push(...l.getEntries().map((e) => e.duration))).observe({ type: "longtask" });
      openSqlQuery({ workspace_id: "ws1", lakehouse_id: "lh1", name: "Bench" });
    });
    await page.waitForSelector(".sql-edit .cm-editor");
    await page.locator(".sql-edit .cm-content").click();
    const text = "SELECT order_id, amount FROM dbo.sales WHERE amount > 10 ORDER BY amount DESC; ".repeat(3).slice(0, 200);
    const typeStart = Date.now();
    await page.keyboard.type(text);
    const typeMs = Date.now() - typeStart;
    const longTasks = await page.evaluate(() => window.__longTasks);

    // Memory after the tour, after a collection.
    await page.evaluate(() => go("home"));
    await page.evaluate(PAINTED);
    await cdp.send("HeapProfiler.collectGarbage");
    const liveNodes = await page.evaluate(() => document.getElementsByTagName("*").length);
    const metrics = Object.fromEntries((await cdp.send("Performance.getMetrics")).metrics.map((m) => [m.name, m.value]));

    // Background: main-thread time in 10 s of nothing.
    const idleStart = metrics.TaskDuration;
    await page.waitForTimeout(10000);
    const idle = Object.fromEntries((await cdp.send("Performance.getMetrics")).metrics.map((m) => [m.name, m.value]));

    return {
      startup_ms: startup, startup_wall_ms: wall,
      nav_first_median_ms: median(Object.values(first)), nav_first_max_ms: Math.max(...Object.values(first)),
      nav_first_slowest: Object.entries(first).sort((a, b) => b[1] - a[1])[0][0],
      nav_median_ms: median(again), nav_max_ms: Math.max(...again),
      token_switch_median_ms: median(tokenSwitch), token_switch_max_ms: Math.max(...tokenSwitch),
      density_switch_median_ms: median(densitySwitch), density_switch_max_ms: Math.max(...densitySwitch),
      large_list_ms: listMs, large_list_rows: listRows, large_list_nodes: listNodes,
      editor_type_200_ms: typeMs, editor_long_tasks: longTasks.length, editor_longest_task_ms: longTasks.length ? Math.max(...longTasks) : 0,
      heap_mb: metrics.JSHeapUsedSize / 1048576, dom_nodes: metrics.Nodes, dom_nodes_in_page: liveNodes, listeners: metrics.JSEventListeners,
      idle_task_ms_per_10s: (idle.TaskDuration - idleStart) * 1000,
      errors: app.errors.length,
    };
  } finally {
    await app.close();
  }
}

// Runs the whole measurement `runs` times and keeps each number's median.
// warmup: uncounted runs first; the first Chromium and page start on a machine is slower (file cache).
async function measure(runs = 3, warmup = 1) {
  for (let i = 0; i < warmup; i++) await once();
  const all = [];
  for (let i = 0; i < runs; i++) all.push(await once());
  const out = { runs };
  for (const key of Object.keys(all[0])) {
    const vals = all.map((r) => r[key]);
    out[key] = typeof vals[0] === "number" ? round(median(vals)) : vals[0];
  }
  return out;
}

module.exports = { measure };

if (require.main === module) {
  const args = process.argv.slice(2);
  const runs = Number(args[args.indexOf("--runs") + 1]) || 3;
  const json = args.includes("--json") ? args[args.indexOf("--json") + 1] : null;
  measure(args.includes("--runs") ? runs : 3).then((r) => {
    console.log(JSON.stringify(r, null, 2));
    if (json) require("node:fs").writeFileSync(json, JSON.stringify(r, null, 2) + "\n");
  }).catch((err) => { console.error(err); process.exit(1); });
}
