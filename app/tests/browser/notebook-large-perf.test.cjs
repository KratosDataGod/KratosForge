// WR-134: a 2,000-cell notebook opens, scrolls and saves within a measured budget.
//
// The notebook comes through the mocked rpc as open_notebook returns it, and is opened the way the
// app opens one (openNotebook in app.js). Timed:
//   - first render: openNotebook until two frames after the cells are drawn;
//   - scrolling the cells from top to bottom, one viewport per frame (much faster than a person
//     scrolls), then back up over the last stretch, where every editor has been built: the mean and
//     slowest frame, and long tasks (over 50 ms) seen by a PerformanceObserver("longtask");
//   - the save: notebookPayload and JSON.stringify of it (what rpc sends), and saveNotebook through
//     the mocked rpc.
// It also pins how a long notebook is drawn (notebook.js EAGER_EDITORS): every cell is in the page,
// but only the code cells near the visible ones have a CodeMirror editor in it.
//
// The target is Windows WebView2 on an ordinary laptop. These numbers come from headless Chromium on
// Linux and are proxies for it: WebView2 runs the same engine, but on slower machines. Measured in a
// Linux container, before -> after WR-134:
//   first render          ~5,300 ms -> ~1,600 ms  (every code cell's CodeMirror editor was built on open)
//   editors in the page   1,600 -> ~15-32
//   scroll down           mean frame ~85 -> ~63 ms, long tasks ~210 -> ~40 (a viewport per frame)
//   scroll back up        mean frame ~30 -> ~23 ms over editors already built
//   payload / save        ~2 ms / ~120 ms (mocked rpc), unchanged
// The timing budgets are 2.5-4x the measurement after, for a slow CI runner, so they catch a gross
// regression (an O(n^2) pass, every editor built again) rather than noise; the editors-in-page check
// is exact and pins the fix. Never widen a budget to make this pass: find what got slow instead.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const CELLS = 2000;
const LOG = !!process.env.PERF_LOG;

const BUDGET = {
  firstRenderMs: 4500,
  downMeanFrameMs: 160,
  downLongestFrameMs: 800,
  downLongTaskShare: 0.5,  // of the frames
  backMeanFrameMs: 70,
  editorsInPage: 80,  // a structural check: the ones near the visible cells, not one per code cell
  payloadMs: 100,
  saveMs: 1500,
};
const BACK_STEPS = 100;

function bigNotebook() {
  const cells = [];
  for (let i = 0; i < CELLS; i += 1) {
    if (i % 5 === 0) {
      cells.push({ type: "markdown", cell_id: `m${i}`, source: `## Section ${i}\n\nSome *text* about step ${i}, with \`code\` and a [link](https://example.com).`, meta: {} });
    } else if (i % 10 === 3) {
      cells.push({ type: "code", cell_id: `s${i}`, source: `%%sql\nSELECT id, amount\nFROM dbo.sales_${i % 7}\nWHERE id > ${i}`, meta: {} });
    } else {
      const lines = [`df_${i} = spark.read.table("dbo.t${i % 7}")`];
      for (let j = 0; j < 8; j += 1) lines.push(`x_${j} = df_${i}.count() + ${j}  # step ${j}`);
      lines.push(`display(df_${i})`);
      cells.push({ type: "code", cell_id: `c${i}`, source: lines.join("\n"), meta: {} });
    }
  }
  return { name: "Big", cells, lakehouse: null, compute: "default", disk_stamp: "stamp-1", check: null };
}

let app;
let saved = null;
const page = () => app.page;

before(async () => {
  if (skip) return;
  const nb = bigNotebook();
  app = await openApp({ mocks: {
    open_notebook: () => nb,
    save_notebook: (p) => { saved = p; return { name: "Big", cell_ids: p.cells.map((c) => c.cell_id), disk_stamp: "stamp-2" }; },
  } });
  await page().evaluate(() => {
    window.__longTasks = [];
    new PerformanceObserver((list) => { for (const e of list.getEntries()) window.__longTasks.push(e.duration); })
      .observe({ type: "longtask" });
  });
});

after(async () => { if (app) await app.close(); });

const inPage = () => document.querySelectorAll(".cells .cm-editor").length;

test("a 2,000-cell notebook draws its first cells within budget, with editors only near the screen", { skip }, async () => {
  const opened = await page().evaluate(async () => {
    const frame = () => new Promise((r) => requestAnimationFrame(() => r()));
    const t0 = performance.now();
    await openNotebook("Big");
    await frame();
    await frame();
    return { ms: performance.now() - t0, cells: document.querySelectorAll(".cell").length,
      editors: document.querySelectorAll(".cells .cm-editor").length };
  });
  if (LOG) console.log("open", opened);
  assert.strictEqual(opened.cells, CELLS);
  assert.ok(opened.editors > 0 && opened.editors <= BUDGET.editorsInPage, `${opened.editors} editors in the page on open`);
  assert.ok(opened.ms < BUDGET.firstRenderMs, `first render took ${Math.round(opened.ms)} ms`);
});

test("scrolling top to bottom and back keeps frames and editors within budget", { skip }, async () => {
  const run = (dir, limit) => page().evaluate(async ([dir, limit, inPageSrc]) => {
    const inPage = new Function(`return (${inPageSrc})()`);
    const frame = () => new Promise((r) => requestAnimationFrame(() => r()));
    const scroller = document.querySelector(".cells");
    await frame();
    await new Promise((r) => setTimeout(r, 200));  // the open's own work is over
    window.__longTasks.length = 0;
    const frames = [];
    let most = 0;
    let last = performance.now();
    const more = () => (dir > 0 ? scroller.scrollTop + scroller.clientHeight < scroller.scrollHeight - 1 : scroller.scrollTop > 0);
    while (more() && frames.length < limit) {
      scroller.scrollTop += dir * scroller.clientHeight;
      await frame();
      const now = performance.now();
      frames.push(now - last);
      last = now;
      most = Math.max(most, inPage());
    }
    return { steps: frames.length, mean: frames.reduce((a, b) => a + b, 0) / frames.length, longest: Math.max(...frames),
      longTasks: window.__longTasks.length, mostEditors: most, atEnd: dir > 0 ? !more() : null };
  }, [dir, limit, inPage.toString()]);

  const down = await run(1, Infinity);
  if (LOG) console.log("down", down);
  assert.ok(down.atEnd && down.steps > 300, `scrolled ${down.steps} viewports`);
  assert.ok(down.mostEditors <= BUDGET.editorsInPage, `${down.mostEditors} editors in the page while scrolling`);
  assert.ok(down.mean < BUDGET.downMeanFrameMs, `mean frame ${down.mean.toFixed(1)} ms scrolling down`);
  assert.ok(down.longest < BUDGET.downLongestFrameMs, `slowest frame ${Math.round(down.longest)} ms scrolling down`);
  assert.ok(down.longTasks < BUDGET.downLongTaskShare * down.steps, `${down.longTasks} long tasks in ${down.steps} frames`);

  const back = await run(-1, BACK_STEPS);
  if (LOG) console.log("back", back);
  assert.strictEqual(back.steps, BACK_STEPS);
  assert.ok(back.mostEditors <= BUDGET.editorsInPage, `${back.mostEditors} editors in the page scrolling back`);
  assert.ok(back.mean < BUDGET.backMeanFrameMs, `mean frame ${back.mean.toFixed(1)} ms scrolling back over built editors`);
});

test("saving the 2,000 cells serializes and saves within budget, every cell as it is", { skip }, async () => {
  const out = await page().evaluate(async () => {
    const cell = state.notebook.cells[1777];
    editors.get(cell.id).dispatch({ changes: { from: 0, insert: "# edited\n" } });  // a far cell: its editor comes back
    const t0 = performance.now();
    const text = JSON.stringify({ ...notebookPayload(), disk_stamp: state.notebook.diskStamp });
    const payloadMs = performance.now() - t0;
    const t1 = performance.now();
    const ok = await saveNotebook();
    return { payloadMs, saveMs: performance.now() - t1, ok, bytes: text.length, dirty: state.notebook.dirty };
  });
  if (LOG) console.log("save", out);
  assert.ok(out.ok && !out.dirty);
  assert.strictEqual(saved.cells.length, CELLS);
  assert.ok(saved.cells[1777].source.startsWith("# edited\ndf_1777 = "), saved.cells[1777].source);
  assert.strictEqual(saved.cells[1999].source, bigNotebook().cells[1999].source);
  assert.ok(out.payloadMs < BUDGET.payloadMs, `payload took ${out.payloadMs.toFixed(1)} ms`);
  assert.ok(out.saveMs < BUDGET.saveMs, `save took ${Math.round(out.saveMs)} ms`);
});
