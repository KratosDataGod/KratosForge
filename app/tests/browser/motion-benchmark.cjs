// Motion & effects benchmark and frame sheets (docs/forge-animations.md#performance). Not a test:
// run it by hand to measure what the effects cost, and to draw each effect at four moments.
//   NODE_PATH="$(npm root -g)" node app/tests/browser/motion-benchmark.cjs <out-folder>
// Measures, at motion Off (0) and Expressive (3):
//   1. Run all on a 20-cell notebook against the real engine: wall time, three runs each.
//   2. A burst of 200 forge events in 10 s while a rAF loop records frame intervals: p50, p95 and
//      worst frame, long tasks, effect elements left over, and JS heap before and after.
//   3. The time one forgeEvent() call takes on the main thread.
// Numbers come from headless Chromium in whatever machine runs this; they are a baseline to compare
// against, not a promise about a given PC.
const fs = require("node:fs");
const path = require("node:path");
const { openApp } = require("./harness.cjs");

const OUT = process.argv[2] || ".";
const CELLS = Array.from({ length: 20 }, (_, i) => ({ type: "code", cell_id: `c${i}`, source: `x${i} = sum(range(20000))`, meta: {} }));
const ENGINE = ["start_cell", "poll_cell", "kernel_variables", "app_settings", "save_app_settings"];

async function runAll(page) {
  return page.evaluate(async (cells) => {
    loadIntoState({ name: "Bench", cells: JSON.parse(JSON.stringify(cells)) });
    state.ribbonTab = "run";
    go("notebook");
    const t = performance.now();
    await runCells(state.notebook.cells);
    return performance.now() - t;
  }, CELLS);
}

async function burst(page) {
  return page.evaluate(async () => {
    const host = document.createElement("div");
    host.style.cssText = "position:fixed;left:40px;top:120px;display:grid;grid-template-columns:repeat(8,120px);gap:16px;z-index:5";
    const anchors = Array.from({ length: 24 }, (_, i) => { const s = document.createElement("span"); s.textContent = `Step ${i + 1}`; host.append(s); return s; });
    document.body.append(host);
    const events = Object.keys(FORGE_EVENTS);
    const frames = [];
    let long = 0;
    const obs = new PerformanceObserver((l) => { long += l.getEntries().length; });
    obs.observe({ type: "longtask" });
    const heap0 = performance.memory?.usedJSHeapSize || 0;
    let last = performance.now();
    let on = true;
    const tick = (t) => { frames.push(t - last); last = t; if (on) requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
    const calls = [];
    for (let i = 0; i < 200; i += 1) {
      const t = performance.now();
      forgeEvent(events[i % events.length], anchors[i % anchors.length]);
      calls.push(performance.now() - t);
      await new Promise((r) => setTimeout(r, 50));
    }
    await new Promise((r) => setTimeout(r, 1200));
    on = false;
    obs.disconnect();
    const left = document.querySelectorAll(".fx").length;
    host.remove();
    const q = (a, p) => { const s = [...a].sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.floor(p * s.length))]; };
    const f = frames.slice(2);
    return { frames: f.length, p50: q(f, 0.5), p95: q(f, 0.95), worst: Math.max(...f), long, left,
      heapKb: Math.round(((performance.memory?.usedJSHeapSize || 0) - heap0) / 1024),
      callUs: Math.round(calls.reduce((a, b) => a + b, 0) / calls.length * 1000), played: motionDiagnostics().played, skipped: motionDiagnostics().skipped };
  });
}

// Each effect drawn at 15%, 35%, 60% and 85% of its run, side by side, by pausing its CSS animations.
async function frameSheets(page) {
  await page.evaluate(() => saveSettings({ motion_level: 3 }));
  const kinds = { spark: "Succeeded", fail: "Failed", pulse: "Run all", strike: "Deployed 4 steps.", wolfrun: "Kernel idle", achieve: "First Flame", settle: "Publish" };
  for (const [kind, label] of Object.entries(kinds)) {
    await page.evaluate(({ kind, label }) => {
      document.getElementById("sheet")?.remove();
      const sheet = document.createElement("div");
      sheet.id = "sheet";
      sheet.style.cssText = "position:fixed;left:0;top:0;z-index:99;background:#fff;display:flex;gap:130px;align-items:center;padding:24px 40px 24px 60px;zoom:2;font:600 13px Segoe UI, sans-serif;color:#107c10";
      document.body.append(sheet);
      [0.15, 0.35, 0.6, 0.85].forEach((at) => {
        const s = document.createElement("span");
        s.textContent = label;
        s.style.cssText = "display:inline-block;padding:6px 10px;border:1px solid #e0e0e0;border-radius:6px;min-width:90px";
        sheet.append(s);
        motionLast[kind] = -1e9;
        motionStats.live = 0;
        fx(s, kind);
        s.getAnimations({ subtree: true }).forEach((a) => {
          a.pause();
          const d = a.effect.getComputedTiming().duration + (a.effect.getComputedTiming().delay || 0);
          a.currentTime = d * at;
        });
      });
    }, { kind, label });
    await page.locator("#sheet").screenshot({ path: path.join(OUT, `frames-${kind}.png`) });
  }
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const app = await openApp({ engine: ENGINE, viewport: { width: 2000, height: 900 } });
  const { page } = app;
  await page.evaluate(() => loadSettings());
  const results = {};
  for (const level of [0, 3, 0, 3, 0, 3]) {
    await page.evaluate((l) => saveSettings({ motion_level: l }), level);
    (results[`runAll_level${level}_ms`] ||= []).push(Math.round(await runAll(page)));
  }
  for (const level of [0, 3]) {
    await page.evaluate((l) => saveSettings({ motion_level: l }), level);
    await page.evaluate(() => go("home"));
    results[`burst_level${level}`] = await burst(page);
  }
  await frameSheets(page);
  results.userAgent = await page.evaluate(() => navigator.userAgent);
  fs.writeFileSync(path.join(OUT, "motion-benchmark.json"), JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
  await app.close();
})();
