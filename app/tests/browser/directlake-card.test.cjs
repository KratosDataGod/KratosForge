// Direct Lake fallback risk on the Semantic model tab (docs/direct-lake-check.md), in each state of the approved
// mockup (docs: direct-lake-fallback-predictor): F32 with a mix of risks, F64 picked after the check, a model set to
// Direct Lake only, no OneLake sign-in, and a narrow window. It starts on the workspace's capacity, checks only when
// asked, judges another capacity without reading again, and writes nothing. The check's replies are the engine's
// own output for the mockup's numbers (directlake-risk.json, kept equal by test_model_directlake_predict.py).
const { test, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const SHOTS = process.env.DIRECTLAKE_SHOTS;  // a folder: save screenshots of each state there
const STATES = JSON.parse(fs.readFileSync(path.join(__dirname, "directlake-risk.json"), "utf8"));

const PLAN = { layer: "gold", layers: ["gold"], name: "Sales", workspace_name: "Sales Test", workspace_id: "ws",
  lakehouse: "LH_Gold", existing: { id: "m1", url: "https://app.fabric.microsoft.com/x" },
  tables: [{ name: "fact_sales", entity: "gold.fact_sales", source: "gold.fact_sales", kind: "fact" },
           { name: "dim_customer", entity: "gold.dim_customer", source: "gold.dim_customer", kind: "dimension" }],
  relationships: [] };

const apps = [];
async function open(reply, calls, width = 1280) {
  const app = await openApp({ mocks: {
    meta_model_plan: () => PLAN,
    meta_model_ossie_models: () => ({ env: "test", models: [{ id: "m1", name: "Sales" }, { id: "m2", name: "Finance KPIs" }] }),
    meta_model_directlake_setup: (p) => { calls.push(["setup", p]); return { env: "test", sku: "F32", capacity_sku: "F32", skus: ["F2", "F32", "F64", "P1"] }; },
    meta_model_directlake_check: (p) => { calls.push(["check", p]); return reply; },
    meta_build_model: (p) => { calls.push(["build", p]); return null; },
  } });
  apps.push(app);
  await app.page.setViewportSize({ width, height: 1100 });
  await app.page.waitForFunction(() => typeof renderModelTab === "function" && !!document.querySelector(".rail-item"));
  await app.page.evaluate(async () => {
    await loadSettings();
    state.user = { name: "Chris" };
    state.meta.env = "test";
    state.meta.tab = "model";
    state.view = "metadata";
    const body = document.createElement("div");
    body.id = "model-under-test";
    document.querySelector("main, #main, body").prepend(body);
    await renderModelTab(body);
  });
  const card = app.page.locator("#model-under-test .dl-card");
  await card.waitFor();
  await app.page.waitForFunction(() => document.querySelector(".dl-card select[aria-label='Capacity limits']")?.value === "F32");
  return { ...app, card };
}

async function check(card) {
  await card.locator("button:has-text('Check now')").click();
  await card.locator(".dl-risk").waitFor();
}

const shot = async (page, name) => {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.locator("#model-under-test .dl-card").screenshot({ path: path.join(SHOTS, `${name}.png`) });
};

// Each table's row: [name, risk, rows, files, row groups], read from the screen.
async function rows(card) {
  return card.locator(".dl-risk > tbody > tr:not(.dl-detail)").evaluateAll((trs) => trs.map((tr) =>
    [...tr.children].map((td) => td.innerText.replace(/\s+/g, " ").trim())));
}

after(async () => { for (const app of apps) await app.close(); });

test("F32, mixed: the verdict, the tiles, each table's risk, and the checks of the tables past a limit", { skip }, async () => {
  const calls = [];
  const { page, card, errors } = await open(STATES.f32, calls);
  assert.deepStrictEqual(calls.filter(([n]) => n === "check"), [], "nothing is checked until asked");
  const head = await card.textContent();
  assert.match(head, /Direct Lake fallback risk\s*Preview/);
  assert.match(head, /Predicted from metadata on this PC\. Fabric hasn't run a query\./);
  const pickers = await card.locator("label.field").evaluateAll((ls) => ls.map((l) => [l.firstChild.textContent, l.querySelectorAll("select").length]));
  assert.deepStrictEqual(pickers, [["Model", 1], ["Limits for capacity", 1], ["Fallback setting", 1]], "each picker has its label");
  await check(card);
  assert.deepStrictEqual(calls.find(([n]) => n === "check")[1], { env: "test", name: "Sales", sku: "F32", keys: false });
  assert.strictEqual(await card.locator("select[aria-label='Fallback setting'] option").first().textContent(), "Automatic (from the model)");
  assert.ok(await card.locator("select[aria-label='Fallback setting']").isDisabled(), "the setting comes from the model");
  const tiles = (await card.locator(".dl-tile").allInnerTexts()).map((x) => x.replace(/\s+/g, " ").trim());
  assert.deepStrictEqual(tiles, ["Will fall back 2 tables", "Might fall back 1 table, near a limit", "Within limits 3 tables",
    "Model size on OneLake 36 GB of 40 GB Within the limit, 4 GB left"]);
  assert.match(await card.locator(".dl-verdict").innerText(), /^Will fall back fact_sales is past F32's row group limit\. With the Automatic setting, one table past a limit sends every query on the model to DirectQuery/);
  assert.match(await card.locator(".dl-verdict").innerText(), /Smallest capacity within limits: F64 today, F64 in 90 days/);
  assert.deepStrictEqual(await rows(card), [
    ["fact_sales", "Will fall back", "412 million of 300 million", "840 of 1,000", "1,240 of 1,000"],
    ["fact_returns", "Might fall back", "31 million of 300 million", "960 of 1,000", "610 of 1,000"],
    ["dim_customer", "Within limits", "8 million of 300 million", "12 of 1,000", "14 of 1,000"],
    ["dim_product", "Within limits", "48,000 of 300 million", "3 of 1,000", "3 of 1,000"],
    ["v_sales_targets view", "Will fall back", "n/a", "n/a", "n/a"],
    ["dim_date", "Within limits", "7,305 of 300 million", "1 of 1,000", "1 of 1,000"]]);
  assert.deepStrictEqual(await card.locator(".dl-toggle[aria-expanded='true'] b").allTextContents(), ["fact_sales", "v_sales_targets"],
    "the tables past a limit start open");
  const sales = (await card.locator("#dl-detail-0 .dl-chk").allInnerTexts()).map((x) => x.replace(/\s+/g, " ").trim());
  assert.match(sales[0], /^Row groups 1,240 of 1,000 Queries on this table go to DirectQuery, because the table has more row groups than F32 allows\. Read from the Delta log and Parquet footers\. Fix: Compact the table so its row groups are larger\. Compact in Table health$/);
  assert.match(sales[1], /^Rows 412 million of 300 million Queries on this table go to DirectQuery, because the table has more rows than F32 allows\. Compaction doesn't change this\. Fix: Filter or aggregate the rows the model needs into a Gold table, or move the workspace to F64 or larger\.$/);
  assert.match(sales[2], /^Parquet files 840 of 1,000 Within the limit\.$/);
  assert.match(await card.locator("#dl-detail-4").innerText(), /Source is a view\s+SQL endpoint view\s+Every query on this table goes to DirectQuery, because Direct Lake reads only Delta tables\.\s+Fix: Materialize the view as a Delta table in Gold, then point the model's table at it\./);
  assert.strictEqual(await card.locator(".dl-risk span.over-text").count(), 4, "over a limit in red: rows, row groups, and both in the checks");
  assert.match(await card.innerText(), /Select a table to see each check, its evidence and the fix\./);
  assert.match(await card.innerText(), /How it's judged\. Will fall back means a table is past one of F32's Direct Lake limits, or its source is a view\. Might fall back means it's within 10% of a limit\. Not checked means it couldn't be read\./);
  await shot(page, "1-f32-mixed");

  // A table opens from the keyboard, and shows its forecast.
  const returns = card.locator(".dl-toggle:has-text('fact_returns')");
  await returns.focus();
  await page.keyboard.press("Enter");
  assert.strictEqual(await returns.getAttribute("aria-expanded"), "true");
  const detail = card.locator(`#${await returns.getAttribute("aria-controls")}`);
  assert.ok(await detail.isVisible());
  assert.match(await detail.innerText(), /Parquet files\s+960 of 1,000\s+Within the limit now, but 41 more Parquet files would pass it\. Each small append adds one\.\s+Fix: Compact the table, and batch the appends\./);
  assert.match(await detail.innerText(), /Forecast\s+16 commits read\s+Crosses F32's Parquet file limit in about 11 days at the current rate \(16 commits over 15 days\)\./);
  await page.keyboard.press("Space");
  assert.strictEqual(await returns.getAttribute("aria-expanded"), "false");
  assert.ok(!(await detail.isVisible()));

  await page.evaluate(() => { window.goMetaCalls = []; goMeta = (tab) => window.goMetaCalls.push(tab); });
  await card.locator("#dl-detail-0 button:has-text('Compact in Table health')").first().click();
  assert.deepStrictEqual(await page.evaluate(() => window.goMetaCalls), ["health"], "compacting happens in Table health");
  assert.deepStrictEqual(calls.filter(([n]) => n === "build"), [], "checking builds nothing");
  assert.deepStrictEqual(errors, []);
});

test("F64 picked: another capacity judges the same numbers again, with no new read", { skip }, async () => {
  const calls = [];
  const { page, card, errors } = await open(STATES.f32, calls);
  await check(card);
  await card.locator("select[aria-label='Capacity limits']").selectOption("F64");
  await card.locator(".dl-risk").waitFor();
  assert.strictEqual(calls.filter(([n]) => n === "check").length, 1, "nothing is read again");
  const tiles = (await card.locator(".dl-tile").allInnerTexts()).map((x) => x.replace(/\s+/g, " ").trim());
  assert.deepStrictEqual(tiles, ["Will fall back 1 table", "Might fall back 0 tables, near a limit", "Within limits 5 tables",
    "Model size on OneLake 36 GB, no limit on F64 Within the limit"]);
  const r = await rows(card);
  assert.deepStrictEqual(r[0], ["fact_sales", "Within limits", "412 million of 1.5 billion", "840 of 5,000", "1,240 of 5,000"]);
  assert.deepStrictEqual(r[4], ["v_sales_targets view", "Will fall back", "n/a", "n/a", "n/a"]);
  assert.match(await card.locator(".dl-verdict").innerText(), /^Will fall back v_sales_targets's source is a SQL view/);
  assert.match(await card.locator(".dl-verdict").innerText(), /v_sales_targets will fall back on every capacity/);
  assert.match(await card.innerText(), /past one of F64's Direct Lake limits/);
  await shot(page, "2-f64");
  assert.deepStrictEqual(errors, []);
});

test("Direct Lake only: past a limit the label reads Will fail", { skip }, async () => {
  const calls = [];
  const { page, card, errors } = await open(STATES.dlonly, calls);
  await check(card);
  assert.strictEqual(await card.locator("select[aria-label='Fallback setting'] option").first().textContent(), "Direct Lake only (from the model)");
  assert.match((await card.locator(".dl-tile").first().innerText()).replace(/\s+/g, " "), /^Will fail 2 tables$/);
  const r = await rows(card);
  assert.deepStrictEqual(r.map((x) => x[1]), ["Will fail", "Might fall back", "Within limits", "Within limits", "Will fail", "Within limits"]);
  assert.match(await card.locator("#dl-detail-0").innerText(), /Queries on this table fail, because the table has more row groups than F32 allows/);
  assert.match(await card.locator("#dl-detail-4").innerText(), /Every query on this table fails, because Direct Lake reads only Delta tables and the model doesn't allow DirectQuery\./);
  assert.match(await card.locator(".dl-verdict").innerText(), /With Direct Lake only, queries on the model will fail instead of going to DirectQuery/);
  await shot(page, "3-direct-lake-only");
  assert.deepStrictEqual(errors, []);
});

test("Not checked: without a OneLake sign-in the tables say Not read, with the next step", { skip }, async () => {
  const calls = [];
  const { page, card, errors } = await open(STATES.nc, calls);
  await check(card);
  assert.strictEqual(await card.locator(".dl-banner").innerText(),
    "The tables weren't read, because there's no OneLake sign-in on this PC. Sign in to OneLake in Settings, then check again.");
  const tiles = (await card.locator(".dl-tile").allInnerTexts()).map((x) => x.replace(/\s+/g, " ").trim());
  assert.deepStrictEqual(tiles, ["Will fall back 0 tables", "Might fall back 0 tables, near a limit", "Within limits 0 tables",
    "Model size on OneLake Not read Needs a OneLake sign-in"]);
  const r = await rows(card);
  assert.deepStrictEqual(r[0], ["fact_sales", "Not checked", "Not read", "Not read", "Not read"]);
  assert.ok(r.every((x) => x[1] === "Not checked"), "a table that wasn't read is never called within limits");
  assert.strictEqual(await card.locator(".dl-toggle[aria-expanded='true']").count(), 0, "nothing starts open");
  assert.doesNotMatch(await card.innerText(), /Select a table to see each check/);
  await card.locator(".dl-toggle:has-text('dim_date')").click();
  assert.match(await card.locator("#dl-detail-5").innerText(), /Not read\s+There's no OneLake sign-in on this PC\./);
  await card.locator(".dl-toggle:has-text('dim_date')").click();
  await shot(page, "4-not-checked");
  assert.deepStrictEqual(errors, []);
});

for (const width of [760, 420]) {
  test(`Narrow window (${width}px): nothing makes the page scroll sideways`, { skip }, async () => {
    const calls = [];
    const { page, card, errors } = await open(STATES.f32, calls, width);
    await check(card);
    const wide = await page.evaluate(() => {
      const c = document.querySelector("#model-under-test .dl-card");
      return { page: document.documentElement.scrollWidth - document.documentElement.clientWidth, card: c.scrollWidth - c.clientWidth };
    });
    assert.ok(wide.card <= 1, `the card fits its width (${wide.card}px over)`);
    if (width === 760) await shot(page, "5-narrow");
    assert.deepStrictEqual(errors, []);
  });
}

test("The other checks keep their own list, with fixes that open where the change is made", { skip }, async () => {
  const calls = [];
  const reply = structuredClone(STATES.f32);
  reply.findings.unshift(
    { severity: "fail", table: "dim_customer", check: "Key not unique", column: "customer_sk", fix: "show_rows",
      message: "customer_sk has 3 values that appear more than once, so the relationship from fact_sales fails at query time." },
    { severity: "fail", table: "fact_sales", check: "Column missing", column: "discount_pct", fix: "update_model",
      message: "The model reads discount_pct, which gold.fact_sales no longer has. Visuals that use it show an error." });
  const { card, errors } = await open(reply, calls);
  await card.locator("label:has-text('relationship keys') input").check();
  await check(card);
  assert.strictEqual(calls.find(([n]) => n === "check")[1].keys, true);
  const others = await card.locator(".dl-others tbody tr").allInnerTexts();
  assert.strictEqual(others.length, 2, "the guardrail findings are in the risk table, not here");
  assert.match(others[0], /^Would fail.*Key not unique.*Open the table/s);
  assert.match(others[1], /Column missing.*Update model/s);
  await card.locator(".dl-others button:has-text('Update model')").click();
  assert.deepStrictEqual(errors, []);
});

test("axe finds nothing serious on the card in any state, light or dark", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  for (const [name, reply] of Object.entries(STATES)) {
    for (const colorScheme of ["light", "dark"]) {
      const { page, card } = await open(reply, []);
      await page.emulateMedia({ colorScheme });
      await check(card);
      await card.locator(".dl-toggle[aria-expanded='false']").first().click();  // a closed one opened too
      await page.evaluate(`${axeSource};0`);  // the shell's CSP refuses an inline script tag
      assert.deepStrictEqual(await axeFindings(page, "#model-under-test .dl-card"), [], `axe on ${name}, ${colorScheme}`);
    }
  }
});

test("With preview features hidden the card isn't there", { skip }, async () => {
  const app = await openApp({ mocks: { meta_model_plan: () => PLAN, app_settings: () => ({ show_preview: { value: false } }) } });
  apps.push(app);
  await app.page.waitForFunction(() => typeof renderModelTab === "function" && !!document.querySelector(".rail-item"));
  const count = await app.page.evaluate(async () => {
    await loadSettings();
    state.user = { name: "Chris" };
    Object.assign(state.meta, { env: "test", tab: "model" });
    state.view = "metadata";
    const body = document.createElement("div");
    document.body.prepend(body);
    await renderModelTab(body);
    return body.querySelectorAll(".dl-card").length;
  });
  assert.strictEqual(count, 0);
});
