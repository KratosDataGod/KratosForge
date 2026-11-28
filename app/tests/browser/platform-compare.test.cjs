// Optimize > Cost and savings: Compare platforms for one workload (Preview, platform-compare.js), in the
// mockup's states (ux/cost-comparison) with the UX review's changes: no Billed badge (billed DBUs are
// Measured with their source), no amber band (the subtitle says it), and the review's copy. The
// engine's answers come from platform-compare-fixtures.json, made by savings/compare.py itself
// (sidecar/tests/test_savings_compare.py's data). Reading Databricks billing or Snowflake query
// history asks first and runs nothing on Cancel. SCREENSHOT_DIR takes one picture per state.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings, settleForAxe } = require("./harness.cjs");
const FIXTURES = require("./platform-compare-fixtures.json");

// The per-run query failed but the totals stood: databricks_billing keeps the totals and records
// runs_error with runs None, and savings/compare.py then gives the not_known card with this reason
// (its card is otherwise the same as not_known's: checked against compare.py).
const RUNS_ERROR = "The query for the linked jobs' runs didn't finish: [TABLE_OR_VIEW_NOT_FOUND] system.lakeflow.job_run_timeline.";
const NO_PER_RUN = "The last billing read has no per-run figures. Read Databricks billing again.";
FIXTURES.runs_failed = JSON.parse(JSON.stringify(FIXTURES.not_known));
FIXTURES.runs_failed.comparison.cards.find((x) => x.platform === "databricks").figure.reason = NO_PER_RUN;
FIXTURES.runs_failed.databricks_read = { ...FIXTURES.runs_failed.databricks_read, runs_error: RUNS_ERROR };
const HOST = "https://adb-1234567890123456.7.azuredatabricks.net";
const TOTALS = { ok: true, schema: "databricks-billing@1", days: 30, warehouse_id: "wh-reporting", read_at: new Date().toISOString(),
  total_dbu: 61.4, rows: [{ kind: "job", id: "8841", sku: "JOBS_COMPUTE", dbu: 42.6 }, { kind: "warehouse", id: "wh-reporting", sku: "PREMIUM_SQL_PRO", dbu: 18.8 }] };

const skip = !playwright && "Playwright is not installed";
const noAxe = skip || (!axeSource && "axe-core is not installed");
const SHOTS = process.env.SCREENSHOT_DIR;

let app;
let which = "three";
let saved = null;
let unread = false;   // the cards as before their first read: no read_at
let runsFail = false; // a confirmed billing read keeps the totals but its per-run query fails
let totalsKept = null; // what databricks_billing_last answers for the Databricks DBUs section
const page = () => app.page;
const calls = (method) => app.calls.filter((c) => c.method === method).map((c) => c.params);

// The fixtures were read at a fixed time; show them as read this morning, as the mockup does.
function answer(p) {
  const out = JSON.parse(JSON.stringify(FIXTURES[which]));
  const today = new Date();
  for (const [c, hh, mm] of [["databricks", 8, 55], ["snowflake", 9, 12]]) {
    const card = out.comparison.cards.find((x) => x.platform === c);
    if (unread) delete card.read_at;
    else if (card.read_at) card.read_at = new Date(today.getFullYear(), today.getMonth(), today.getDate(), hh, mm).toISOString();
  }
  if (saved && p.pricing_version === saved.pricing_version) out.scenarios.push(saved);
  return out;
}

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1480, height: 1200 },
    engine: ["app_settings", "cost_report", "optimize_status"],
    mocks: {
      readiness_check: () => [],
      engine_profiles: () => ({ profiles: [], active: "" }),
      engine_connections: () => ({ connections: runsFail ? [{ key: "cli:prod", label: "prod (Databricks CLI)", source: "cli", host: HOST,
        warehouse_id: "wh-reporting", cluster_id: "", catalog: "", schema: "", remote_compute: "ask" }] : [] }),
      databricks_billing_last: () => totalsKept || {},
      compare_platforms: answer,
      compare_link: (p) => ({ links: { databricks: [{ job_id: p.ref, name: p.name }], snowflake: [] } }),
      compare_unlink: () => ({ links: { databricks: [], snowflake: [] } }),
      compare_pricing_save: (p) => { saved = { ...p, pricing_version: "pv-0123456789abcdef", confidence: "assumption" }; return saved; },
      databricks_billing: (p) => {
        if (!p.confirmed) return { needs_confirm: true, warehouse: { id: "wh-reporting", name: "Reporting", state: "STOPPED" } };
        if (!runsFail) return { ok: true, runs: [] };
        totalsKept = TOTALS;
        which = "runs_failed";
        return { ...TOTALS, runs: null, run_jobs: ["8841"], runs_error: RUNS_ERROR };
      },
      snowflake_query_credits: (p) => (p.confirmed ? { ok: true, queries: [] }
        : { needs_confirm: true, warehouse: { name: "REPORTING_XS", account: "myorg-acct" } }),
    },
  });
  await page().waitForFunction(() => typeof go === "function" && typeof platformCompareSection === "function" && !!document.querySelector(".rail-item"));
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

async function until(check, ms = 5000) {
  for (const end = Date.now() + ms; Date.now() < end;) {
    if (check()) return;
    await new Promise((r) => setTimeout(r, 25));
  }
  assert.fail("waited too long");
}

const section = () => page().locator("#view .xcmp");
const card = (p) => section().locator(`.plat[data-platform="${p}"]`);

async function show(state) {
  which = state;
  await page().evaluate(() => {
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    platformCompare.pricing = ""; optimizePage.tab = "cost"; go("optimize");
  });
  if (state !== "no_scenario") {
    await section().getByLabel("Pricing scenario").waitFor({ timeout: 10000 });
    await section().getByLabel("Pricing scenario").selectOption({ label: "List prices, Nov 2026" });
  }
  await section().locator(".xcmp-line").waitFor({ timeout: 10000 });
  await page().waitForFunction((s) => !!document.querySelector(".xcmp .xcmp-grid") &&
    (s === "no_scenario") === !document.querySelector(".xcmp .plat .money .v"), state);
}

async function shot(name) {
  if (!SHOTS) return;
  await page().locator("#dialog-root .overlay").waitFor({ state: "detached" });
  await settleForAxe(page());
  // The Cost tab can draw the section once more as its other reads land: take the one on screen.
  for (let i = 0; ; i++) {
    try {
      await section().scrollIntoViewIfNeeded();
      await section().screenshot({ path: path.join(SHOTS, name) });
      return;
    } catch (err) {
      if (i === 3) throw err;
      await page().waitForTimeout(200);
    }
  }
}

test("three platforms: each figure in its own unit with its badge, money under one version, and a closing line", { skip }, async () => {
  await show("three");
  const text = await section().textContent();
  assert.match(text, /Compare platforms for one workload/);
  assert.match(text, /What one workload used on each platform, in each platform's own unit\. Units aren't converted into each other\. This isn't an invoice, and a lower figure isn't a recommendation\./);
  assert.doesNotMatch(text, /Estimate comparison|savings|recommend |—/i, "no amber band, no savings, no recommendation, no em-dash");
  const badges = await section().locator(".pill-s").allTextContents();
  assert.ok(!badges.includes("Billed"), "billed DBUs are Measured, with their source");
  assert.deepStrictEqual(await section().locator(".fig .pill-s").allTextContents(), ["Measured", "Estimated", "Measured", "Estimated"]);
  assert.match(await card("pc").textContent(), /4 min 12 s\s*Measured.*Not priced\. This PC's hardware and power aren't counted\./);
  assert.match(await card("fabric").textContent(), /2,880 CU seconds\s*Estimated.*0\.80 CU hours.*\$0\.14\s*Scenario0\.80 CU hours × \$0\.18 per CU hour/);
  const dbx = await card("databricks").textContent();
  assert.match(dbx, /Job daily_sales_rollup \(8841\), linked to this workload by you\. 3 runs\. Unlink/);
  assert.strictEqual(await card("databricks").locator(".matched").textContent(),
    "Job daily_sales_rollup (8841), linked to this workload by you. 3 runs. Unlink", "Unlink ends the matching line");
  assert.strictEqual(await section().locator(".xcmp-links, .matched + .xcmp-link").count(), 0, "no outlined Unlink above the figure");
  assert.match(await card("fabric").textContent(), /MethodRun time × configured CUs/);
  assert.match(await card("snowflake").textContent(), /MethodWarehouse time 16 min 12 s × 1 credit per hour/);
  assert.doesNotMatch(text, / x configured| times \d/, "the method lines use ×");
  assert.match(dbx, /1\.42 DBU\s*Measured/);
  assert.match(dbx, /Billed usage, system\.billing\.usage, read today \d\d:\d\d/);
  assert.match(dbx, /1\.42 DBU × \$0\.15 per DBU/);
  assert.match(await card("snowflake").textContent(), /0\.27 credits\s*Estimated.*X-Small, 1 credit per hour.*0\.27 credits × \$2\.00 per credit/);
  assert.strictEqual(await section().locator(".xcmp-line").textContent(),
    "ScenarioAt these prices, the Fabric estimate ($0.14) is lower than the Databricks figure ($0.21) and the Snowflake estimate ($0.54).");
  assert.match(text, /A run elsewhere counts only when it's matched to this workload, by definition for Fabric, by a job you link for Databricks or by a query tag for Snowflake\. A price isn't a charge\./);
  assert.match(text, /Version pv-[0-9a-f]{16} · Each platform's public price page · Assumption/);
  await shot("1-three-platforms.png");
});

test("one platform not connected: only the badge in its header, how to connect, no figure and no 0", { skip }, async () => {
  await show("not_connected");
  const sf = card("snowflake");
  assert.strictEqual(await sf.locator(".ph").textContent(), "SnowflakeNot connectedcredits");
  const text = await sf.textContent();
  assert.match(text, /add a Snowflake connection in Settings > Connections/);
  assert.match(text, /No figure\. Snowflake isn't connected\./);
  assert.doesNotMatch(text, /\b0 credits|\$0\.00/);
  assert.match(await section().locator(".xcmp-line").textContent(), /Snowflake isn't compared because it isn't connected\.$/);
  await shot("2-not-connected.png");
  await sf.getByRole("button", { name: "Open Connections" }).click();
  await page().waitForFunction(() => state.view === "settings" && state.settingsTab === "connections");
});

test("a figure not known: Not known with the reason, and reading billing asks first", { skip }, async () => {
  await show("not_known");
  const dbx = card("databricks");
  const text = await dbx.textContent();
  assert.match(text, /Not known\s*Unknown/);
  assert.match(text, /No billing rows for this job's runs yet\. Billing rows can arrive hours after a run\./);
  assert.match(text, /Last read today \d\d:\d\d[^.]*\. Reading runs one query on a SQL warehouse you pick, which may bill a few DBUs\./);
  assert.match(text, /There's no DBU figure to price\./);
  assert.match(text, /SourceBilled usage, system\.billing\.usage, read today/);
  assert.match(await section().locator(".xcmp-line").textContent(),
    /At these prices, the Fabric estimate \(\$0\.14\) is lower than the Snowflake estimate \(\$0\.54\)\. Databricks isn't compared because its figure isn't known\./);
  await shot("3-not-known.png");
  await dbx.getByRole("button", { name: "Read again…" }).click();
  const dialog = page().locator("#dialog-root .dialog");
  assert.match(await dialog.textContent(), /Read billing on Reporting\?.*If it is stopped it starts, and it bills DBUs while it runs\./);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  assert.ok(calls("databricks_billing").every((p) => !p.confirmed), "Cancel runs nothing");
  await dbx.getByRole("button", { name: "Read again…" }).click();
  await page().locator("#dialog-root .dialog").getByRole("button", { name: "Run the query" }).click();
  await until(() => calls("databricks_billing").some((p) => p.confirmed));
  const confirmed = calls("databricks_billing").filter((p) => p.confirmed);
  assert.deepStrictEqual(confirmed, [{ connection: "cli:prod", warehouse_id: "wh-reporting", days: 30, job_ids: ["8841"], confirmed: true }]);
});

test("reading Snowflake query history asks first too", { skip }, async () => {
  await show("three");
  await card("snowflake").getByRole("button", { name: "Read again…" }).click();
  const dialog = page().locator("#dialog-root .dialog");
  assert.match(await dialog.textContent(), /Read query history on REPORTING_XS\?.*If it is suspended it resumes, and it uses credits while it runs\./);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(calls("snowflake_query_credits"), [{ connection: "profile:p1:sf", warehouse: "REPORTING_XS", days: 30, tags: ["kf:daily-sales"] }]);
});

test("before the first read the buttons name what they read; after it they say Read again", { skip }, async () => {
  unread = true;
  try {
    await show("three");
    assert.strictEqual(await card("databricks").locator(".xcmp-read button").textContent(), "Read Databricks billing…");
    assert.strictEqual(await card("snowflake").locator(".xcmp-read button").textContent(), "Read Snowflake query history…");
    assert.doesNotMatch(await section().textContent(), /Last read|Read again/);
  } finally { unread = false; }
  await show("three");
  assert.deepStrictEqual(await section().locator(".xcmp-read button").allTextContents(), ["Read again…", "Read again…"]);
});

test("Unlink unlinks at once, with no confirm, and the toast's Undo links the job again", { skip }, async () => {
  await show("three");
  const before = calls("compare_link").length;
  await card("databricks").getByRole("button", { name: "Unlink job 8841" }).click();
  await until(() => calls("compare_unlink").length > 0);
  assert.deepStrictEqual(calls("compare_unlink").at(-1), { workload: "meta:daily_sales", platform: "databricks", ref: "8841" });
  assert.strictEqual(await page().locator("#dialog-root .dialog").count(), 0, "no confirm");
  const t = page().locator("#toast");
  await t.getByRole("button", { name: "Undo" }).waitFor();
  assert.match(await t.textContent(), /^Unlinked job 8841 from this workload\./);
  await t.getByRole("button", { name: "Undo" }).click();
  await until(() => calls("compare_link").length > before);
  assert.deepStrictEqual(calls("compare_link").at(-1), { workload: "meta:daily_sales", platform: "databricks", ref: "8841", name: "daily_sales_rollup" });
});

test("no pricing scenario: native units only, no money and no comparison", { skip }, async () => {
  await show("no_scenario");
  assert.strictEqual(await section().locator(".plat .money .v").count(), 0);
  assert.strictEqual(await section().locator(".xcmp-line").textContent(),
    "No money shownChoose a pricing scenario to see money. Until then each platform shows its own unit, and nothing is compared.");
  assert.match(await card("databricks").textContent(), /1\.42 DBU/);
  await shot("4-no-scenario.png");
});

test("how this was worked out: the measure dialog with the unit, matched by, assumptions and the money", { skip }, async () => {
  await show("three");
  await card("snowflake").locator(".measure-fig").click();
  const dialog = page().locator("#dialog-root .dialog");
  const text = await dialog.textContent();
  assert.match(text, /^Snowflake: credits per run/);
  assert.match(text, /0\.27 credits\s*Estimated\s*Worked out from the assumptions below, not read from Snowflake's billing\./);
  assert.match(text, /UnitSnowflake credits\. Not converted to CU or DBU\./);
  assert.match(text, /Runs counted3 of 3/);
  assert.match(text, /Matched byQueries tagged kf:daily-sales on warehouse REPORTING_XS\. 3 runs\./);
  assert.match(text, /Money at this scenario\$0\.54\s*Scenario\s*0\.27 credits × \$2\.00 per credit, from List prices, Nov 2026 \(version pv-[0-9a-f]{16}, from each platform's public price page\)\./);
  assert.match(text, /Worked out asWarehouse time 16 min 12 s × 1 credit per hour/);
  assert.match(text, /WAREHOUSE_METERING_HISTORY .* can't be split into single runs, and it can be up to 3 hours late\./);
  if (SHOTS) { await settleForAxe(page()); await page().screenshot({ path: path.join(SHOTS, "5-how-worked-out.png") }); }
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root .dialog"), []);
  await dialog.getByRole("button", { name: "Close" }).last().click();
  await dialog.waitFor({ state: "detached" });
});

test("Prices keeps a scenario with a DBU and a credit rate, and the section uses it", { skip }, async () => {
  // axe covers dialogs: platform-compare.js:pcmpPricesDialog
  await section().getByRole("button", { name: "Prices" }).click();
  const dialog = page().locator("#dialog-root .dialog");
  await dialog.getByLabel("Name").fill("Reserved, Dec 2026");
  await dialog.getByLabel("Rate per DBU, by SKU").fill("JOBS_COMPUTE=0.12, PREMIUM_SQL_PRO=0.55");
  await dialog.getByLabel("Rate per Snowflake credit").fill("1.8");
  if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root .dialog"), []);
  await dialog.getByRole("button", { name: "Save" }).click();
  await page().waitForFunction(() => platformCompare.pricing === "pv-0123456789abcdef");
  const p = calls("compare_pricing_save").at(-1);
  assert.deepStrictEqual(p.rate_per_dbu, { JOBS_COMPUTE: 0.12, PREMIUM_SQL_PRO: 0.55 });
  assert.strictEqual(p.rate_per_credit, 1.8);
  assert.strictEqual(p.rate_per_cu_hour, 0.18);
});

test("the section has no serious axe findings", { skip: noAxe }, async () => {
  await show("three");
  assert.deepStrictEqual(await axeFindings(page(), "#view .xcmp"), []);
});

test("the per-run read fails and the totals stay: Unknown with the reason, no figure, the read still offered", { skip }, async () => {
  runsFail = true;
  try {
    await show("three");
    await card("databricks").getByRole("button", { name: "Read again…" }).click();
    await page().locator("#dialog-root .dialog").getByRole("button", { name: "Run the query" }).click();
    await page().locator("#toast", { hasText: "runs didn't finish" }).waitFor({ timeout: 5000 });
    assert.strictEqual((await page().locator("#toast").textContent()).startsWith(RUNS_ERROR), true, "the toast says the per-run query failed");
    await card("databricks").locator(".why.notknown", { hasText: NO_PER_RUN }).waitFor({ timeout: 5000 });
    // Open the Cost tab again: both sections draw from what this PC kept.
    await show("runs_failed");
    await page().locator("#view .dbx-cost .dbx-cost-result").waitFor({ timeout: 10000 });
    const dbx = card("databricks");
    const text = await dbx.textContent();
    assert.match(text, /Not known\s*Unknown/);
    assert.strictEqual(await dbx.locator(".why.notknown").textContent(), NO_PER_RUN);
    assert.strictEqual(await dbx.locator(".fig .v").textContent(), "Not known");
    assert.ok(await dbx.locator(".fig .v.unknown").count(), "the figure is drawn as unknown");
    assert.doesNotMatch(await dbx.locator(".fig").textContent(), /\d/, "no figure");
    assert.doesNotMatch(text, /\b0(\.0+)? DBU|\$0\.00|\b0 runs/, "no 0");
    assert.doesNotMatch(text, /61\.4|42\.6/, "the totals aren't taken as a per-run figure");
    assert.match(await dbx.locator(".money").textContent(), /Not known\s*Unknown\s*There's no DBU figure to price\./);
    const read = dbx.locator(".xcmp-read button");
    assert.strictEqual(await read.textContent(), "Read again…");
    assert.ok(await read.isEnabled(), "the read is still offered");
    assert.match(await section().locator(".xcmp-line").textContent(), /Databricks isn't compared because its figure isn't known\.$/);
    const totals = page().locator("#view .dbx-cost .dbx-cost-result");
    assert.match(await totals.textContent(), /61\.4 DBU in the last 30 days · read .* on wh-reporting/);
    assert.deepStrictEqual(await totals.locator(".dbx-dbu tbody tr").allTextContents(),
      ["Job 8841JOBS_COMPUTE42.6", "SQL warehouse wh-reportingPREMIUM_SQL_PRO18.8"]);
    await shot("6-runs-failed.png");
  } finally { runsFail = false; totalsKept = null; }
});

test("a narrow window (390 px): the four cards stack, the page doesn't scroll sideways and no figure is clipped", { skip }, async () => {
  const wide = page().viewportSize();
  await page().setViewportSize({ width: 390, height: 844 });
  try {
    await show("three");
    await page().waitForFunction(() => document.querySelectorAll(".xcmp .plat").length === 4);
    const m = await page().evaluate(() => {
      const se = document.scrollingElement;
      const box = (el) => el.getBoundingClientRect();
      const cards = [...document.querySelectorAll(".xcmp .plat")].map((c) => {
        const r = box(c);
        const inner = [...c.querySelectorAll(".fig .v, .fig .measure-fig, .money .v, .ph, .ph b, .ph .unit")].map((el) => {
          const e = box(el);
          return { cls: el.className, text: el.textContent, clipped: el.scrollWidth > el.clientWidth + 1 || e.left < r.left - 1 || e.right > r.right + 1 };
        });
        return { platform: c.dataset.platform, left: r.left, top: r.top, bottom: r.bottom, right: r.right, inner };
      });
      return { scrollWidth: se.scrollWidth, clientWidth: se.clientWidth, viewport: innerWidth, cards };
    });
    assert.ok(m.scrollWidth <= m.clientWidth, `no horizontal page scroll (${m.scrollWidth} > ${m.clientWidth})`);
    assert.deepStrictEqual(m.cards.map((c) => c.platform), ["pc", "fabric", "databricks", "snowflake"]);
    for (let i = 1; i < m.cards.length; i++) {
      assert.ok(Math.abs(m.cards[i].left - m.cards[0].left) < 1, `${m.cards[i].platform} lines up under the first card`);
      assert.ok(m.cards[i].top >= m.cards[i - 1].bottom, `${m.cards[i].platform} is below ${m.cards[i - 1].platform}`);
    }
    for (const c of m.cards) {
      assert.ok(c.right <= m.viewport, `${c.platform} fits the window`);
      assert.deepStrictEqual(c.inner.filter((x) => x.clipped), [], `${c.platform} has no clipped figure text`);
    }
    await shot("7-narrow.png");
  } finally { await page().setViewportSize(wide); }
});

test("hidden when preview features are hidden", { skip }, async () => {
  await page().evaluate(() => { state.settings = state.settings || {}; state.settings.show_preview = { value: false }; optimizePage.tab = "cost"; go("optimize"); });
  await page().locator("#view .optimize-body").waitFor();
  assert.strictEqual(await page().locator("#view .xcmp").count(), 0);
  await page().evaluate(() => { state.settings = state.settings || {}; state.settings.show_preview = { value: true }; });
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
