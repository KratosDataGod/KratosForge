// PBE-06: Workspaces > Power BI and Embed Studio (app/ui/powerbi.js), under the shell's policy, with a stand-in
// embed host on the host's origin that answers the host's commands and raises events the way the real one does
// (app/src-tauri/embed-host/host.js). No tenant, token or capacity. See harness.cjs for how to run it.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const WS = "d64d74d5-58ab-4882-aedc-a7d2b835b094";
const RPT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
const PAG = "11111111-2222-3333-4444-555555555555";
const DSH = "66666666-7777-8888-9999-000000000000";
const TOKEN = "eyJ.secret-embed-token";
const HOST = "http://pbiembed.localhost";

// The stand-in host: takes commands only from its parent on the app's origin, answers with the command's seq,
// and raises loaded, rendered and pageChanged. window.got keeps what it was sent, for the test to read.
const STUB = `<!doctype html><meta charset="utf-8"><body>stand-in host<script>
  window.got = [];
  const APP = "http://app.test";
  const send = (body) => parent.postMessage({ kfEmbedHost: body }, APP);
  addEventListener("message", (e) => {
    if (e.source !== parent || e.origin !== APP) return;
    const m = e.data && e.data.kfEmbed;
    if (!m) return;
    window.got.push(m);
    const ok = (result) => send({ reply: m.seq, ok: true, result: result || {} });
    if (m.command === "embed") {
      ok({ kind: m.kind });
      send({ event: "embed", detail: { kind: m.kind } });
      setTimeout(() => send({ event: "loaded", detail: {} }), 10);
      setTimeout(() => send({ event: "rendered", detail: {} }), 20);
    } else if (m.command === "setPage") {
      ok();
      send({ event: "pageChanged", detail: { newPage: { name: m.pageName, displayName: m.pageName === "P2" ? "Regions" : "Overview" } } });
    } else ok();
  });
  send({ event: "ready" });
</script>`;

let app;
let settings;
const page = () => app.page;
const shellCalls = (cmd) => page().evaluate((c) => window.__fdShellCalls.filter((x) => x.cmd === c).map((x) => x.args), cmd);
const hostFrame = () => page().frames().find((f) => f.url().startsWith(`${HOST}/`));
const fromHost = (body) => hostFrame().evaluate((b) => parent.postMessage({ kfEmbedHost: b }, "http://app.test"), body);

before(async () => {
  if (skip) return;
  settings = { show_preview: { value: true, source: "default" } };
  app = await openApp({
    csp: true,
    mocks: {
      app_settings: () => settings,
      save_app_settings: ({ changes }) => { for (const [k, v] of Object.entries(changes)) settings[k] = { value: v, source: "user" }; return settings; },
      list_workspaces: () => [{ id: WS, displayName: "Sales Analytics [Prod]" }],
      list_items: () => [{ id: RPT, type: "Report", displayName: "Sales performance" },
        { id: PAG, type: "PaginatedReport", displayName: "Invoice detail" },
        { id: DSH, type: "Dashboard", displayName: "Executive overview" },
        { id: "bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee", type: "Lakehouse", displayName: "Gold" }],
      workspace_marks: () => ({ favorites: [], pins: [] }),
      pbi_embed_token: () => ({ token: TOKEN, token_type: "Aad", expires_at: 9999999999, seconds_left: 3600 }),
      pbi_embed_content: ({ item_id }) => ({
        kind: "report", id: item_id, name: "Sales performance", workspace_id: WS,
        embed_url: `https://app.powerbi.com/reportEmbed?reportId=${item_id}&groupId=${WS}`, dataset_id: "cccccccc-bbbb-cccc-dddd-eeeeeeeeeeee",
        pages: [{ name: "P2", display_name: "Regions", order: 1 }, { name: "P1", display_name: "Overview", order: 0 }], tiles: [],
        capabilities: { embed_type: "report", pages: true, filters: true, bookmarks: true, slicers: true, visuals: true,
          events: ["loaded", "rendered", "error", "pageChanged"], limits: ["Export data follows the tenant setting and your permission."] },
      }),
    },
  });
  await page().route(`${HOST}/**`, (route) => route.fulfill({ status: 200, contentType: "text/html", body: STUB }));
  await page().evaluate(() => { state.user = "test@contoso.com"; return loadSettings(); });
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

test("Workspaces > Power BI lists only reports, paginated reports and dashboards, with Preview", { skip }, async () => {
  await page().evaluate(() => go("workspaces"));
  const tab = page().getByRole("tab", { name: /Power BI/ });
  assert.match(await tab.textContent(), /Preview/);
  await tab.click();
  await page().locator(".pbi-list").getByText("Executive overview").waitFor();
  const names = await page().locator(".pbi-list tbody tr td:nth-child(2)").allTextContents();
  assert.deepStrictEqual(names.sort(), ["Executive overview", "Invoice detail", "Sales performance"]);
  assert.strictEqual(await page().locator(".pbi-list").getByText("Gold").count(), 0, "a lakehouse isn't listed");
  await page().getByRole("button", { name: /^Paginated reports 1$/ }).click();
  assert.deepStrictEqual(await page().locator(".pbi-list tbody tr td:nth-child(2)").allTextContents(), ["Invoice detail"]);
  await page().getByRole("button", { name: /^All 3$/ }).click();
  const star = page().getByRole("button", { name: "Favorite Invoice detail" });
  assert.strictEqual(await star.getAttribute("aria-pressed"), "false");
  await star.click();
  assert.strictEqual(await page().getByRole("button", { name: "Favorite Invoice detail" }).getAttribute("aria-pressed"), "true");
  await page().getByRole("button", { name: "★ Favorites only" }).click();
  assert.deepStrictEqual(await page().locator(".pbi-list tbody tr td:nth-child(2)").allTextContents(), ["Invoice detail"]);
  await page().getByRole("button", { name: "★ Favorites only" }).click();
  // Refresh reads the items again, so a report published since shows up.
  const reads = app.calls.filter((c) => c.method === "list_items").length;
  await page().getByRole("button", { name: "Refresh" }).click();
  await page().locator(".pbi-list").getByText("Executive overview").waitFor();
  assert.ok(app.calls.filter((c) => c.method === "list_items").length > reads, "Refresh read the items again");
  assert.deepStrictEqual(app.errors, []);
});

test("Embed Studio embeds through the host on its own origin, and the token goes nowhere else", { skip }, async () => {
  await page().locator("tr", { hasText: "Sales performance" }).getByRole("button", { name: "Open in Embed Studio" }).click();
  await page().getByRole("heading", { name: /Sales performance/ }).waitFor();
  await page().locator(".pbi-events li", { hasText: "rendered" }).waitFor();
  const frame = page().locator("iframe.pbi-frame");
  assert.strictEqual(await frame.getAttribute("src"), `${HOST}/host.html`);
  const sandbox = (await frame.getAttribute("sandbox")).split(" ");
  assert.ok(!sandbox.includes("allow-popups") && !sandbox.includes("allow-top-navigation"), sandbox.join(" "));
  const got = await hostFrame().evaluate(() => window.got);
  assert.strictEqual(got[0].command, "embed");
  assert.deepStrictEqual({ ...got[0], seq: 0 }, { command: "embed", kind: "report", id: RPT, seq: 0, pageName: "P1",
    embedUrl: `https://app.powerbi.com/reportEmbed?reportId=${RPT}&groupId=${WS}`, accessToken: TOKEN });
  // The token was posted to the host and nowhere else: not in the page, its state, storage or the events.
  const leaks = await page().evaluate((t) => ({
    dom: document.documentElement.outerHTML.includes(t),
    storage: Object.keys(localStorage).some((k) => String(localStorage.getItem(k)).includes(t)),
    session: Object.keys(sessionStorage).some((k) => String(sessionStorage.getItem(k)).includes(t)),
    state: JSON.stringify({ pbiStudio: { item: pbiStudio.item, side: pbiStudio.side }, events: pbiStudio.session.events }).includes(t),
  }), TOKEN);
  assert.deepStrictEqual(leaks, { dom: false, storage: false, session: false, state: false });
  assert.match(await page().locator(".page-head .sub").textContent(), /Runs in Power BI and uses its capacity/);
  assert.deepStrictEqual(app.violations, [], "the shell's policy lets the app frame the host and nothing it blocks was tried");
});

test("the page strip is the only page switcher and follows pageChanged", { skip }, async () => {
  const tabs = page().getByRole("tablist", { name: "Report pages" }).getByRole("tab");
  assert.deepStrictEqual(await tabs.allTextContents(), ["Overview", "Regions"], "in Power BI's order");
  await page().getByRole("tab", { name: "Regions" }).click();
  await page().waitForFunction(() => document.querySelector('.pbi-pages [aria-selected="true"]')?.textContent === "Regions");
  const got = await hostFrame().evaluate(() => window.got.map((m) => m.command));
  assert.ok(got.includes("setPage"), got.join(", "));
});

test("messages from anything but the host frame are ignored", { skip }, async () => {
  const before = await page().locator(".pbi-events li").count();
  await page().evaluate(() => window.postMessage({ kfEmbedHost: { event: "error", detail: { message: "forged" } } }, "*"));
  await page().waitForTimeout(200);
  assert.strictEqual(await page().locator(".pbi-events li").count(), before);
  assert.strictEqual(await page().locator(".pbi-problem").count(), 0);
});

test("a link in the report opens in the default browser, only over https", { skip }, async () => {
  await fromHost({ event: "dataHyperlinkClicked", detail: { url: "https://contoso.example/sales" } });
  await fromHost({ event: "dataHyperlinkClicked", detail: { url: "javascript:alert(1)" } });
  await page().waitForFunction(() => window.__fdShellCalls.some((c) => c.cmd === "open_link"));
  await page().waitForTimeout(100);
  assert.deepStrictEqual(await shellCalls("open_link"), [{ url: "https://contoso.example/sales" }]);
});

test("Power BI's error shows what to do, never offers to resume capacity, and Copy events has no token", { skip }, async () => {
  // A visual that fails on a working report is only an event: the report stays in view.
  await fromHost({ event: "error", detail: { message: "visualRenderFailed", level: 3 } });
  await page().locator(".pbi-events li", { hasText: "visualRenderFailed" }).waitFor();
  assert.strictEqual(await page().locator(".pbi-problem").count(), 0, "a non-fatal error after loading keeps the report");
  assert.strictEqual(await page().locator("iframe.pbi-frame.pbi-hidden").count(), 0);
  await fromHost({ event: "error", detail: { message: "LoadReportFailed", errorCode: "403", detailedMessage: "Forbidden", level: 6,
    technicalDetails: { requestId: "req-1" } } });
  const card = page().locator(".pbi-problem");
  await card.waitFor();
  assert.match(await card.textContent(), /Your account can't open this/);
  await fromHost({ event: "error", detail: { message: "CapacityNotActive", detailedMessage: "The capacity is paused", level: "Fatal" } });
  await page().getByText("The workspace's capacity isn't running").waitFor();
  assert.strictEqual(await page().getByRole("button", { name: /resume/i }).count(), 0, "Embed Studio never resumes a capacity");
  await fromHost({ event: "rendered", detail: {} });
  await page().locator(".pbi-problem").waitFor({ state: "detached" });
  // The test origin isn't a secure context, so the clipboard is a stand-in that keeps what was written.
  await page().evaluate(() => Object.defineProperty(navigator, "clipboard", { configurable: true,
    value: { writeText: async (t) => { window.__copied = t; } } }));
  await page().getByRole("button", { name: "Copy events" }).click();
  const copied = await page().evaluate(() => window.__copied);
  assert.ok(copied.includes("req-1") && !copied.includes(TOKEN), copied.slice(0, 200));
});

test("with preview features hidden, there's no Power BI tab", { skip }, async () => {
  settings.show_preview = { value: false, source: "user" };
  await page().evaluate(() => loadSettings().then(() => go("workspaces")));
  await page().getByRole("tab", { name: "Your workspaces" }).waitFor();
  assert.strictEqual(await page().getByRole("tab", { name: /Power BI/ }).count(), 0);
  settings.show_preview = { value: true, source: "default" };
  await page().evaluate(() => loadSettings());
  assert.deepStrictEqual(app.errors, []);
});
