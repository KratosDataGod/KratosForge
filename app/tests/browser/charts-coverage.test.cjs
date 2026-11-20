// The notebook Chart view's builder (charts.js) against mocked notebook_frame_chart replies: each
// chart kind as the person sees it (bars, lines, areas, pie slices, histogram bins, scatter points,
// box plots), the controls each kind offers and the engine call each choice makes, the notes under
// a chart, and its empty, gone and failed states (see harness.cjs for how to run it).
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";

const COLUMNS = ["region", "month", "amount", "units"];
const NUMERIC = [false, false, true, true];
const CATS12 = ["East", "West", "North", "South", "Central", "Islands", "Coast", "Hills", "Lakes", "Plains", "Delta", "Valley"];

// What the engine answers for each kind, unless a test sets `reply` for the next calls.
const answers = {
  bar: { kind: "bar", rows: 3000, truncated: true, categories: ["East", "West", null],
    series: [{ name: "Jan", values: [1200, -300, 5] }, { name: "Feb", values: [2500000, null, 7] }] },
  stacked: { kind: "stacked", rows: 40, categories: ["East", "West"], series: [{ name: "Jan", values: [10, 20] }, { name: "Feb", values: [5, -2] }] },
  line: { kind: "line", rows: 12, categories: ["Jan", "Feb", "Mar"], series: [{ name: null, values: [3, null, 9] }] },
  area: { kind: "area", rows: 12, categories: ["Jan", "Feb", "Mar"], series: [{ name: "East", values: [1, 2, 3] }, { name: "West", values: [null, 4, 1] }] },
  pie: { kind: "pie", rows: 900, categories: CATS12, series: [{ name: null, values: [90, 80, 70, 60, 50, 40, 30, 20, 10, 5, 0, 3] }] },
  histogram: { kind: "histogram", rows: 50, min: 0, max: 40, counts: [5, 0, 30, 15] },
  scatter: { kind: "scatter", rows: 10000, sampled: true, total: 10000, points: [[1, 2, "East"], [3, 4, "West"], [5, -1, null]] },
  box: { kind: "box", rows: 300, boxes: [{ x: "A very long region name", min: 1, q1: 2, median: 3, q3: 4, max: 9, n: 1500 }, { x: "West", min: 0, q1: 1, median: 2, q3: 3, max: 5, n: 20 }] },
};
let reply = null; // (params) => answer, for the next calls; null uses `answers`

let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({
    mocks: {
      notebook_frame_chart: (p) => {
        if (reply) return reply(p);
        return answers[p.kind];
      },
    },
  });
  await page().evaluate(({ COLUMNS, NUMERIC }) => {
    state.notebook = { name: "Sales.ipynb", cells: [] };
    const host = document.createElement("div");
    host.id = "chart-host";
    document.body.prepend(host);
    // Draws chart `i` of `cell` (the cell's result is frame f1) the way the table output does.
    window.__chart = (cell, i = 0, withRemove = false) => {
      window.__removed = [];
      window.__out = window.__out && window.__out.cell === cell ? window.__out : { cell, frame: "f1", columns: COLUMNS };
      host.replaceChildren(renderChartBuilder(window.__out, NUMERIC, cell, i, withRemove ? (n, why) => window.__removed.push([n, why]) : null));
    };
    window.__cell = (meta = {}) => ({ type: "code", cell_id: "c1", meta });
  }, { COLUMNS, NUMERIC });
});

after(async () => { await app?.close(); });

beforeEach(() => {
  if (skip) return;
  reply = null;
  app.calls.length = 0;
  app.errors.length = 0;
});

const chartCalls = () => app.calls.filter((c) => c.method === "notebook_frame_chart").map((c) => c.params);
const host = () => page().locator("#chart-host");
const drawn = () => host().locator(".chart-area svg").waitFor();
const tips = (sel) => host().locator(`.chart-area svg ${sel} > title`).allTextContents();
const legend = () => host().getByRole("list", { name: "Legend" }).locator("li").allTextContents();
const area = () => host().locator(".chart-area").innerText();
const pick = async (label, value) => { await host().getByRole("combobox", { name: label, exact: true }).selectOption(value); };

test("A new chart is the first text column by the first number column, summed, with every row charted", { skip }, async () => {
  await page().evaluate(() => { window.__out = null; window.__c = window.__cell(); window.__chart(window.__c); });
  await drawn();
  assert.deepStrictEqual(chartCalls(), [{ frame: "f1", notebook: "Sales.ipynb", kind: "bar", x: 0, y: 2, agg: "sum", series: null }]);
  assert.strictEqual(await host().getByRole("combobox", { name: "X axis" }).inputValue(), "region");
  assert.strictEqual(await host().getByRole("combobox", { name: "Values" }).inputValue(), "amount");
  assert.strictEqual(await host().getByRole("combobox", { name: "Aggregate" }).inputValue(), "sum");
  // Values offers the number columns only, and Row count for a kind that aggregates.
  assert.deepStrictEqual(await host().getByRole("combobox", { name: "Values" }).locator("option").allTextContents(), ["Row count", "amount", "units"]);
  // Series never offers the X axis column.
  assert.deepStrictEqual(await host().getByRole("combobox", { name: "Series" }).locator("option").allTextContents(), ["None", "month", "amount", "units"]);
  assert.strictEqual(await host().getByRole("button", { name: "Reset" }).count(), 0, "nothing saved yet, so nothing to reset");
  assert.strictEqual(await host().getByRole("button", { name: "Remove chart" }).count(), 0, "the first chart can't be removed");
  // Grouped bars, one per non-null value, each named for its category and series; big numbers shortened.
  assert.deepStrictEqual(await tips("rect"), ["East, Jan: 1.2K", "West, Jan: -300", "null, Jan: 5", "East, Feb: 2.5M", "null, Feb: 7"]);
  assert.deepStrictEqual(await legend(), ["Jan", "Feb"]);
  assert.match(await host().locator(".chart-area .more").innerText(), /^From all 3,000 rows\. Showing the first 60 values of the X axis\.$/);
  assert.strictEqual(await host().locator(".chart-area svg").getAttribute("aria-label"), "bar chart of amount by region");
  assert.deepStrictEqual(app.errors, []);
});

test("Choosing a series, an aggregate and Row count asks the engine again and keeps the choice in the cell", { skip }, async () => {
  await page().evaluate(() => { window.__out = null; window.__c = window.__cell(); window.__chart(window.__c); });
  await drawn();
  await pick("Series", "month");
  await drawn();
  await pick("Aggregate", "avg");
  await drawn();
  await pick("Values", "");
  await drawn();
  const calls = chartCalls();
  assert.deepStrictEqual(calls.slice(1).map((p) => [p.series, p.agg, p.y]), [[1, "sum", 2], [1, "avg", 2], [1, "count", null]]);
  assert.strictEqual(await host().getByRole("combobox", { name: "Aggregate" }).count(), 0, "Row count has nothing to aggregate");
  assert.strictEqual(await host().locator(".chart-area svg").getAttribute("aria-label"), "bar chart of row count by region");
  const meta = await page().evaluate(() => window.__c.meta.chart);
  assert.deepStrictEqual(meta, { kind: "bar", x: "region", y: null, agg: "avg", series: "month", title: "" });
  // Reset forgets the saved chart and goes back to the default.
  await host().getByRole("button", { name: "Reset" }).click();
  await drawn();
  assert.strictEqual(await page().evaluate(() => window.__c.meta.chart), undefined);
  assert.strictEqual(await host().getByRole("combobox", { name: "Values" }).inputValue(), "amount");
  assert.deepStrictEqual(chartCalls().at(-1), { frame: "f1", notebook: "Sales.ipynb", kind: "bar", x: 0, y: 2, agg: "sum", series: null });
  assert.deepStrictEqual(app.errors, []);
});

test("A saved chart comes back, unless a column it charts is gone from the result", { skip }, async () => {
  await page().evaluate(() => {
    window.__out = null;
    window.__chart(window.__cell({ chart: { kind: "line", x: "month", y: "units", agg: "max", series: null, title: "Units by month" } }));
  });
  await drawn();
  assert.deepStrictEqual(chartCalls()[0], { frame: "f1", notebook: "Sales.ipynb", kind: "line", x: 1, y: 3, agg: "max", series: null });
  assert.strictEqual(await host().locator(".chart-title").innerText(), "Units by month");
  assert.strictEqual(await host().locator(".chart-area svg").getAttribute("aria-label"), "Units by month");
  // A line chart: points for the values there are, the gap skipped, no legend for one unnamed series.
  assert.deepStrictEqual(await tips("circle"), ["Jan: 3", "Mar: 9"]);
  assert.strictEqual(await host().locator(".chart-area svg path").count(), 1);
  assert.strictEqual(await host().getByRole("list", { name: "Legend" }).count(), 0);
  app.calls.length = 0;
  await page().evaluate(() => {
    window.__out = null;
    window.__chart(window.__cell({ chart: { kind: "line", x: "week", y: "units", agg: "max", series: null, title: "Old" } }));
  });
  await drawn();
  assert.deepStrictEqual(chartCalls()[0], { frame: "f1", notebook: "Sales.ipynb", kind: "bar", x: 0, y: 2, agg: "sum", series: null });
  assert.strictEqual(await host().locator(".chart-title").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});

test("Stacked bars and stacked areas pile each series on the last", { skip }, async () => {
  await page().evaluate(() => { window.__out = null; window.__c = window.__cell(); window.__chart(window.__c); });
  await drawn();
  await pick("Chart", "stacked");
  await drawn();
  assert.strictEqual(chartCalls().at(-1).kind, "stacked");
  assert.deepStrictEqual(await tips("rect"), ["East, Jan: 10", "West, Jan: 20", "East, Feb: 5", "West, Feb: -2"]);
  await pick("Chart", "area");
  await drawn();
  assert.strictEqual(chartCalls().at(-1).kind, "area");
  // Two series: a filled band and a line each, a point per value.
  assert.strictEqual(await host().locator('.chart-area svg path[fill-opacity="0.18"]').count(), 2);
  assert.deepStrictEqual(await tips("circle"), ["Jan, East: 1", "Feb, East: 2", "Mar, East: 3", "Feb, West: 4", "Mar, West: 1"]);
  assert.deepStrictEqual(await legend(), ["East", "West"]);
  assert.deepStrictEqual(app.errors, []);
});

test("A pie folds everything after the sixth slice into Other and leaves out empty slices", { skip }, async () => {
  await page().evaluate(() => { window.__out = null; window.__c = window.__cell(); window.__chart(window.__c); });
  await drawn();
  await pick("Chart", "pie");
  await drawn();
  const call = chartCalls().at(-1);
  assert.deepStrictEqual([call.kind, call.series], ["pie", null], "a pie has no series");
  assert.strictEqual(await host().getByRole("combobox", { name: "Series" }).count(), 0);
  const slices = await tips("path");
  assert.strictEqual(slices.length, 7);
  assert.strictEqual(slices[0], "East: 90 (19.7%)");
  assert.strictEqual(slices[6], "Other: 68 (14.8%)", "Hills, Lakes, Plains, Valley and the empty Delta");
  assert.strictEqual(await host().locator(".chart-area svg path").last().getAttribute("fill"), "#8a8986", "Other is grey");
  assert.deepStrictEqual(await legend(), CATS12.slice(0, 7));
  // One slice is a whole circle; nothing above zero is nothing to chart.
  reply = () => ({ kind: "pie", rows: 4, categories: ["Only"], series: [{ name: null, values: [12] }] });
  await pick("Values", "units");
  await drawn();
  assert.deepStrictEqual(await tips("path"), ["Only: 12 (100.0%)"]);
  assert.match(await host().locator(".chart-area svg path").getAttribute("d"), /a\d+,\d+ 0 1,0/);
  reply = () => ({ kind: "pie", rows: 4, categories: ["A", "B"], series: [{ name: null, values: [0, null] }] });
  await pick("Values", "amount");
  await drawn();
  assert.match(await area(), /Nothing to chart/);
  assert.deepStrictEqual(app.errors, []);
});

test("A histogram bins one number column: no X axis, no aggregate, a bin's range in its tooltip", { skip }, async () => {
  await page().evaluate(() => { window.__out = null; window.__c = window.__cell(); window.__chart(window.__c); });
  await drawn();
  await pick("Chart", "histogram");
  await drawn();
  const call = chartCalls().at(-1);
  assert.deepStrictEqual([call.kind, call.x, call.y, call.series], ["histogram", null, 2, null]);
  assert.strictEqual(await host().getByRole("combobox", { name: "X axis" }).count(), 0);
  assert.strictEqual(await host().getByRole("combobox", { name: "Aggregate" }).count(), 0);
  assert.deepStrictEqual(await host().getByRole("combobox", { name: "Values" }).locator("option").allTextContents(), ["amount", "units"],
    "no Row count for a histogram");
  assert.deepStrictEqual(await tips("rect"), ["0 to 10: 5 rows", "10 to 20: 0 rows", "20 to 30: 30 rows", "30 to 40: 15 rows"]);
  assert.strictEqual(await host().locator(".chart-area svg").getAttribute("aria-label"), "histogram chart of amount");
  reply = () => ({ kind: "histogram", rows: 0, min: 0, max: 0, counts: [0, 0] });
  await pick("Values", "units");
  await drawn();
  assert.match(await area(), /Nothing to chart/);
  assert.deepStrictEqual(app.errors, []);
});

test("A scatter says when its points are a sample, and names each point's series", { skip }, async () => {
  await page().evaluate(() => { window.__out = null; window.__c = window.__cell(); window.__chart(window.__c); });
  await drawn();
  await pick("Chart", "scatter");
  await drawn();
  // Region is the X axis, so it is offered as a series only once X moves off it.
  assert.strictEqual(await host().getByRole("combobox", { name: "Series" }).locator("option", { hasText: "region" }).count(), 0);
  await pick("X axis", "units");
  await drawn();
  await pick("Series", "region");
  await drawn();
  const call = chartCalls().at(-1);
  assert.deepStrictEqual([call.kind, call.x, call.y, call.series], ["scatter", 3, 2, 0]);
  assert.deepStrictEqual(await tips("circle"), ["East: 1, 2", "West: 3, 4", "5, -1"]);
  assert.deepStrictEqual(await legend(), ["East", "West"]);
  assert.match(await host().locator(".chart-area .more").innerText(), /From all 10,000 rows\. 3 of 10,000 points, sampled evenly\./);
  reply = () => ({ kind: "scatter", rows: 0, points: [] });
  await pick("Values", "units");
  await drawn();
  assert.match(await area(), /Nothing to chart/);
  assert.deepStrictEqual(app.errors, []);
});

test("A box plot gives each group's five numbers and shortens long labels", { skip }, async () => {
  await page().evaluate(() => { window.__out = null; window.__c = window.__cell(); window.__chart(window.__c); });
  await drawn();
  await pick("Chart", "box");
  await drawn();
  assert.strictEqual(chartCalls().at(-1).kind, "box");
  assert.strictEqual(await host().getByRole("combobox", { name: "Series" }).count(), 0);
  assert.deepStrictEqual(await tips("g"), ["A very long region name: min 1, Q1 2, median 3, Q3 4, max 9 (1,500 rows)",
    "West: min 0, Q1 1, median 2, Q3 3, max 5 (20 rows)"]);
  const labels = await host().locator(".chart-area svg text.chart-label").allTextContents();
  assert.ok(labels.includes("A very long…"), labels.join("|"));
  assert.ok(labels.includes("West"));
  reply = () => ({ kind: "box", rows: 0, boxes: [] });
  await pick("Values", "units");
  await drawn();
  assert.match(await area(), /Nothing to chart/);
  // Bars with no categories at all.
  reply = () => ({ kind: "bar", rows: 0, categories: [], series: [] });
  await pick("Chart", "bar");
  await drawn();
  assert.match(await area(), /Nothing to chart/);
  assert.deepStrictEqual(app.errors, []);
});

test("A result the kernel no longer holds asks for a rerun; a failed chart says why", { skip }, async () => {
  reply = () => ({ gone: true });
  await page().evaluate(() => { window.__out = null; window.__c = window.__cell(); window.__chart(window.__c); });
  await host().getByText("Run the cell again to chart this result: the kernel no longer holds it.").waitFor();
  assert.strictEqual(await host().locator(".chart-area svg").count(), 0);
  // A gone answer isn't kept, so drawing again asks again.
  reply = () => { throw new Error("KeyError: frame f1"); };
  await page().evaluate(() => window.__chart(window.__c));
  await host().getByText(/^Could not draw the chart: /).waitFor();
  assert.strictEqual(chartCalls().length, 2);
  // A drawn answer is kept: drawing the same chart again doesn't ask the engine.
  reply = null;
  await pick("Chart", "line");
  await drawn();
  const n = chartCalls().length;
  await page().evaluate(() => window.__chart(window.__c));
  await drawn();
  assert.strictEqual(chartCalls().length, n);
  assert.deepStrictEqual(app.errors, []);
});

test("+ Chart adds a chart after the first; its title renames the view and Remove chart takes it out", { skip }, async () => {
  const added = await page().evaluate(({ COLUMNS, NUMERIC }) => {
    window.__out = null;
    window.__c = window.__cell();
    window.__out = { cell: window.__c, frame: "f1", columns: COLUMNS };
    const i = addChart(window.__out, NUMERIC, window.__c);
    return { i, count: chartCount(window.__c), first: window.__c.meta.chart.x, names: [chartName(window.__c, 0), chartName(window.__c, 1)] };
  }, { COLUMNS, NUMERIC });
  assert.deepStrictEqual(added, { i: 1, count: 2, first: "region", names: ["Chart", "Chart 2"] });
  await page().evaluate(() => window.__chart(window.__c, 1, true));
  await drawn();
  assert.strictEqual(await host().getByRole("button", { name: "Reset" }).count(), 0, "Reset is for the first chart");
  await pick("X axis", "month");
  await drawn();
  assert.strictEqual(await page().evaluate(() => window.__c.meta.charts[0].x), "month");
  assert.strictEqual(await page().evaluate(() => window.__c.meta.chart.x), "region", "the first chart keeps its settings");
  await host().getByRole("textbox", { name: "Chart title" }).fill("Monthly amount by region and channel");
  await host().getByRole("textbox", { name: "Chart title" }).press("Enter");
  assert.deepStrictEqual(await page().evaluate(() => window.__removed), [[1, "renamed"]]);
  assert.strictEqual(await page().evaluate(() => chartName(window.__c, 1)), "Monthly amount by regio…");
  await page().evaluate(() => window.__chart(window.__c, 1, true));
  await drawn();
  await host().getByRole("button", { name: "Remove chart" }).click();
  assert.deepStrictEqual(await page().evaluate(() => window.__removed), [[1, "removed"]]);
  assert.deepStrictEqual(await page().evaluate(() => ["charts" in window.__c.meta, chartCount(window.__c), window.__out.charts.length]), [false, 1, 1]);
  assert.deepStrictEqual(app.errors, []);
});

test("Two charts with the same title read apart by number", { skip }, async () => {
  const names = await page().evaluate(() => {
    const c = window.__cell({ chart: { title: "Sales" }, charts: [{ title: "Sales" }, { title: "" }, { title: "Other" }] });
    return [0, 1, 2, 3].map((i) => chartName(c, i));
  });
  assert.deepStrictEqual(names, ["Sales", "Sales (2)", "Chart 3", "Other"]);
});
