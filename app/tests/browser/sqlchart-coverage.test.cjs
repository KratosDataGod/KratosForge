// The SQL page's Chart tab (sqlchart.js) beyond warehouse-page.test.cjs: a scatter of two number
// columns coloured by a series, a histogram of a column with no numbers, the note when the result
// has more rows than were read and more X values than are drawn, and a result that can't be read.
// The tab is mounted on its own with the rows a sql_page read would give it.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {} });
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  await page().evaluate(() => {
    document.getElementById("chart-host")?.remove();
    document.body.append(h("div", { id: "chart-host" }));
  });
});

const chart = () => page().locator("#chart-host .sql-chart");
// Mount the tab with this result; fail makes the read throw that message instead.
const mount = (data, fail) => page().evaluate(({ data, fail }) => {
  window.__held = {};
  document.getElementById("chart-host").append(sqlChartView(window.__held,
    () => (fail ? Promise.reject(new Error(fail)) : Promise.resolve(data))));
}, { data, fail });

const CITIES = {
  columns: [{ name: "city" }, { name: "lat" }, { name: "temp" }, { name: "region" }],
  rows: [["Oslo", 59.9, 4, "North"], ["Rome", 41.9, 18, "South"], ["Bergen", 60.4, null, "North"], ["Nice", 43.7, 16, "South"]],
  total: 4,
};

test("Scatter offers only number columns on X, draws a point per row with both numbers, coloured by Series", { skip }, async () => {
  await mount(CITIES);
  await chart().locator("svg.chart rect").first().waitFor();
  await chart().getByLabel("Chart", { exact: true }).selectOption("scatter");
  assert.deepStrictEqual(await chart().getByLabel("X axis").locator("option").allInnerTexts(), ["lat", "temp"]);
  await chart().getByLabel("X axis").selectOption("lat");
  assert.deepStrictEqual(await chart().getByLabel("Values").locator("option").allInnerTexts(), ["lat", "temp"], "no Row count");
  await chart().getByLabel("Values").selectOption("temp");
  assert.strictEqual(await chart().getByLabel("Aggregate").count(), 0, "a scatter isn't aggregated");
  assert.strictEqual(await chart().locator("svg.chart circle").count(), 3, "Bergen has no temperature");
  await chart().getByLabel("Series").selectOption("region");
  assert.deepStrictEqual(await chart().locator("svg.chart circle title").allTextContents(),
    ["North: 59.9, 4", "South: 41.9, 18", "South: 43.7, 16"]);
  assert.match(await chart().textContent(), /From all 4 rows\./);
});

test("a histogram of a result with no number column says there's nothing to chart", { skip }, async () => {
  await mount({ columns: [{ name: "city" }, { name: "note" }], rows: [["Oslo", null], ["Rome", "warm"]], total: 2 });
  await chart().locator("svg.chart").waitFor();
  await chart().getByLabel("Chart", { exact: true }).selectOption("histogram");
  assert.strictEqual(await chart().getByLabel("X axis").count(), 0, "a histogram has no X axis");
  assert.strictEqual(await chart().locator("svg.chart text").textContent(), "Nothing to chart");
});

test("the note says how many rows were read of the result and how many X values are left off", { skip }, async () => {
  const rows = Array.from({ length: 75 }, (_, i) => [`k${i}`, i]);
  await mount({ columns: [{ name: "key" }, { name: "n" }], rows, total: 120000 });
  await chart().locator("svg.chart").waitFor();
  assert.strictEqual(await chart().locator(".more").textContent(),
    "From the first 75 rows of 120,000. Showing the first 60 of 75 values of the X axis.");
  assert.strictEqual(await chart().locator("svg.chart rect").count(), 60);
});

test("a result that can't be read says why in the chart's place", { skip }, async () => {
  await mount(null, "The result has expired. Run the query again.");
  await page().waitForFunction(() => /Could not read/.test(document.querySelector("#chart-host .chart-area").textContent));
  assert.strictEqual(await chart().locator(".chart-area").textContent(),
    "Could not read the result: The result has expired. Run the query again.");
  assert.strictEqual(await chart().locator("svg").count(), 0);
});
