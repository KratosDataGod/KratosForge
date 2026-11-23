// Workspaces > Power BI and Embed Studio (powerbi.js) beyond embed-studio.test.cjs: when each item was last
// opened and the order that gives, finding by name and picking a workspace, the empty and still-reading lists,
// a workspace that can't be read, the Power BI page with preview features hidden; then in Embed Studio each
// kind of problem card (an engine error opening it, Power BI's own errors before the content loads), Sign in
// opening it again, the Supports panel, a dashboard's tiles, the Events panel's lines and its limit, the host
// refusing a command, and the token renewed before it runs out. Served under the shell's policy with a
// stand-in embed host, as embed-studio.test.cjs does; no tenant, token or capacity.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const WS = "d64d74d5-58ab-4882-aedc-a7d2b835b094";
const WS2 = "0f0f0f0f-58ab-4882-aedc-a7d2b835b094";
const WS_BAD = "baadbaad-58ab-4882-aedc-a7d2b835b094";
const HOST = "http://pbiembed.localhost";
const TOKEN = "eyJ.secret-embed-token";
const DAY = 86400000;
const ITEMS = {
  [WS]: [{ id: "r-today", type: "Report", displayName: "Sales today" },
    { id: "r-yday", type: "Report", displayName: "Sales yesterday" },
    { id: "r-3d", type: "Report", displayName: "Returns" },
    { id: "p-10d", type: "PaginatedReport", displayName: "Invoice detail" },
    { id: "d-40d", type: "Dashboard", displayName: "Executive overview" },
    { id: "lh", type: "Lakehouse", displayName: "Gold" }],
  [WS2]: [{ id: "r-never", type: "Report", displayName: "Budget" }],
};

// The stand-in host (as in embed-studio.test.cjs): answers each command with its seq, and refuses the commands
// listed in window.refuse. It raises no events itself: each test sends the ones it needs with fromHost.
const STUB = `<!doctype html><meta charset="utf-8"><body>stand-in host<script>
  window.got = []; window.refuse = {};
  const APP = "http://app.test";
  const send = (body) => parent.postMessage({ kfEmbedHost: body }, APP);
  window.raise = (event, detail) => send({ event, detail });
  addEventListener("message", (e) => {
    if (e.source !== parent || e.origin !== APP) return;
    const m = e.data && e.data.kfEmbed;
    if (!m) return;
    window.got.push(m);
    if (m.command in window.refuse) send({ reply: m.seq, ok: false, error: window.refuse[m.command] || undefined });
    else send({ reply: m.seq, ok: true, result: {} });
  });
  send({ event: "ready" });
</script>`;

const world = {};
let app;
let settings;
const page = () => app.page;
const hostFrame = () => page().frames().find((f) => f.url().startsWith(`${HOST}/`));
const fromHost = (event, detail) => hostFrame().evaluate(([e, d]) => window.raise(e, d), [event, detail]);
const hostGot = () => hostFrame().evaluate(() => window.got);
const calls = (m) => app.calls.filter((c) => c.method === m).map((c) => c.params);
const names = () => page().locator(".pbi-list tbody tr td:nth-child(2)").allTextContents();
const card = () => page().locator(".pbi-problem");

const REPORT = (item_id) => ({ kind: "report", id: item_id, name: "Sales today", workspace_id: WS,
  embed_url: `https://app.powerbi.com/reportEmbed?reportId=${item_id}&groupId=${WS}`, dataset_id: "cccccccc-bbbb-cccc-dddd-eeeeeeeeeeee",
  pages: [{ name: "P1", display_name: "Overview", order: 0 }, { name: "P2", order: 1 }], tiles: [],
  capabilities: { embed_type: "report", pages: true, filters: true, bookmarks: false, slicers: true, visuals: true,
    events: ["loaded", "rendered", "error", "pageChanged"], limits: ["Export data follows the tenant setting and your permission."] } });
const DASHBOARD = (item_id) => ({ kind: "dashboard", id: item_id, name: "Executive overview", workspace_id: WS,
  embed_url: `https://app.powerbi.com/dashboardEmbed?dashboardId=${item_id}&groupId=${WS}`, pages: [],
  tiles: [{ id: "t1", title: "Revenue" }, { id: "t2" }],
  capabilities: { embed_type: "dashboard", pages: false, tiles: true, events: ["loaded", "tileClicked", "error"] } });

function reset() {
  Object.assign(world, { workspaces: [{ id: WS, displayName: "Sales Analytics [Prod]" }, { id: WS2, displayName: "Finance" },
    { id: WS_BAD, displayName: "Locked" }], items: ITEMS, hold: null, contentFail: [], tokenFail: false, seconds: 3600, tokens: 0 });
}
reset();

async function openList() {
  await page().evaluate(() => { state.workspaces = null; workspacesPage.items = {}; pbiPage.failed.clear();
    Object.assign(pbiPage, { filter: "all", favoritesOnly: false, find: "", workspace: "" }); go("home"); go("powerbi"); });
}

// Opens an item in Embed Studio from the list's own button, and waits for the host to be sent the embed.
async function openStudio(name, { embeds = true } = {}) {
  await openList();
  await page().locator(".pbi-list tr", { hasText: name }).getByRole("button", { name: "Open in Embed Studio" }).click();
  await page().getByRole("heading", { name: new RegExp(name) }).waitFor();
  if (embeds) await page().waitForFunction(() => !!document.querySelector("iframe.pbi-frame"));
  if (embeds) await hostEmbedded();
}

// Waits until the host page in the frame has run its script and received the embed command. The frame can be
// on the page before its script has set window.got, and a new frame can replace it meanwhile, so the wait runs
// inside the frame and starts again on a frame that went away.
async function hostEmbedded() {
  for (const end = Date.now() + 15000; ;) {
    const frame = hostFrame();
    if (frame) {
      try {
        await frame.waitForFunction(() => Array.isArray(window.got) && window.got.some((m) => m.command === "embed"),
          null, { timeout: Math.max(1, end - Date.now()) });
        return;
      } catch (err) {
        if (!/detached|destroyed|navigat/i.test(String(err && err.message))) throw err;
      }
    }
    if (Date.now() > end) throw new Error("the embed host never received the embed command");
    await page().evaluate(() => new Promise((r) => requestAnimationFrame(r)));
  }
}

before(async () => {
  if (skip) return;
  settings = { show_preview: { value: true, source: "default" } };
  const now = Date.now();
  app = await openApp({
    csp: true,
    storage: { "fd.pbi.opened": JSON.stringify({ "r-today": now - 1000, "r-yday": now - DAY, "r-3d": now - 3 * DAY, "p-10d": now - 10 * DAY,
      "d-40d": now - 40 * DAY }) },
    mocks: {
      app_settings: () => settings,
      list_workspaces: () => world.workspaces,
      workspace_marks: () => ({ favorites: [], pins: [] }),
      list_items: async ({ workspace_id }) => {
        if (workspace_id === WS_BAD) throw new Error("PowerBINotAuthorizedException: 403");
        if (world.hold && workspace_id === WS2) await world.hold.promise;
        return world.items[workspace_id] || [];
      },
      pbi_embed_token: () => {
        world.tokens++;
        if (world.tokenFail) throw new Error("Your sign-in has expired. Sign in again.");
        return { token: `${TOKEN}-${world.tokens}`, token_type: "Aad", expires_at: 9999999999, seconds_left: world.seconds };
      },
      pbi_embed_content: ({ item_id, item_type }) => {
        const fail = world.contentFail.shift();
        if (fail) throw new Error(fail);
        return item_type === "Dashboard" ? DASHBOARD(item_id) : REPORT(item_id);
      },
    },
  });
  await page().route(`${HOST}/**`, (route) => route.fulfill({ status: 200, contentType: "text/html", body: STUB }));
  await page().evaluate(() => { state.user = "test@contoso.com"; return loadSettings(); });
  // The test origin isn't a secure context, so the clipboard is a stand-in that keeps what was written.
  await page().evaluate(() => Object.defineProperty(navigator, "clipboard", { configurable: true,
    value: { writeText: async (t) => { window.__copied = t; } } }));
  app.errors.length = 0;
});

after(async () => { await app?.close(); });

test("the list says when each was last opened, newest first, and counts a workspace it couldn't read", { skip }, async () => {
  reset();
  await openList();
  await page().locator(".pbi-list").getByText("Budget").waitFor();
  await page().getByText("1 workspace couldn't be read and aren't listed.").waitFor();
  assert.deepStrictEqual(await names(), ["Sales today", "Sales yesterday", "Returns", "Invoice detail", "Executive overview", "Budget"]);
  const opened = await page().locator(".pbi-list tbody tr td:nth-child(5)").allTextContents();
  const old = await page().evaluate((d) => dateText(Date.now() - 40 * d), DAY);
  assert.deepStrictEqual(opened, ["Today", "Yesterday", "3 days ago", "Last week", old, "Not opened"]);
  assert.deepStrictEqual((await page().locator(".pbi-filters .chip").allTextContents()).slice(0, 4),
    ["All 6", "Reports 4", "Paginated reports 1", "Dashboards 1"]);
  assert.deepStrictEqual(calls("list_items").map((p) => p.workspace_id).sort(), [WS, WS2, WS_BAD].sort());
});

test("Find narrows by name, the workspace pick by workspace, and an empty result says so", { skip }, async () => {
  reset();
  await openList();
  await page().locator(".pbi-list").getByText("Budget").waitFor();
  await page().getByLabel("Find content").fill("SALES");
  assert.deepStrictEqual(await names(), ["Sales today", "Sales yesterday"]);
  await page().getByLabel("Find content").fill("forecast");
  await page().getByText("Nothing matches.").waitFor();
  await page().getByLabel("Find content").fill("");
  await page().getByLabel("Workspace", { exact: true }).selectOption({ label: "Finance" });
  assert.deepStrictEqual(await names(), ["Budget"]);
  assert.deepStrictEqual(await page().locator(".pbi-list tbody tr td:nth-child(4)").allTextContents(), ["Finance"]);
  await page().getByLabel("Workspace", { exact: true }).selectOption({ label: "All workspaces" });
  assert.strictEqual((await names()).length, 6);
});

test("Favorites only with no stars asks for one; a star comes off again", { skip }, async () => {
  reset();
  await openList();
  await page().locator(".pbi-list").getByText("Budget").waitFor();
  await page().getByRole("button", { name: "★ Favorites only" }).click();
  await page().getByText("No favorites here yet. Click the star next to one.").waitFor();
  await page().getByRole("button", { name: "★ Favorites only" }).click();
  const star = () => page().getByRole("button", { name: "Favorite Budget" });
  await star().click();
  assert.strictEqual(await star().getAttribute("title"), "Remove from favorites");
  assert.deepStrictEqual(await page().evaluate(() => JSON.parse(localStorage.getItem("fd.pbi.favorites"))), ["r-never"]);
  await star().click();
  assert.strictEqual(await star().getAttribute("aria-pressed"), "false");
  assert.deepStrictEqual(await page().evaluate(() => JSON.parse(localStorage.getItem("fd.pbi.favorites"))), []);
});

test("while a workspace is read the list says so; with nothing embeddable it says that", { skip }, async () => {
  reset();
  let release;
  world.hold = { promise: new Promise((r) => { release = r; }) };
  await openList();
  await page().getByRole("status").getByText("Reading 1 more workspace...").waitFor();
  assert.ok((await names()).includes("Sales today"), "what has been read is listed meanwhile");
  release();
  await page().locator(".pbi-list").getByText("Budget").waitFor();
  assert.strictEqual(await page().getByText(/Reading \d+ more/).count(), 0);

  reset();
  world.workspaces = [{ id: "w-lh", displayName: "Lakehouses only" }];
  world.items = { "w-lh": [{ id: "lh", type: "Lakehouse", displayName: "Gold" }] };
  await openList();
  await page().getByText("No reports, paginated reports or dashboards in your workspaces.").waitFor();
});

test("Open in Fabric opens the item's own page in Fabric", { skip }, async () => {
  reset();
  await openList();
  await page().locator(".pbi-list tr", { hasText: "Invoice detail" }).getByRole("button", { name: "Open in Fabric" }).click();
  await page().waitForFunction(() => state.view === "fabric");
  const link = await page().evaluate(() => fabricPage.page);
  assert.ok(link.startsWith(`https://app.fabric.microsoft.com/groups/${WS}/`) && link.includes("p-10d"), link);
});

test("an engine error opening it is a card that says what to do; Sign in opens it again", { skip }, async () => {
  reset();
  world.contentFail = ["Your sign-in has expired. Sign in again."];
  await openStudio("Sales today", { embeds: false });
  await card().getByText("Your sign-in to Power BI has expired").waitFor();
  assert.ok(await page().locator("iframe.pbi-frame.pbi-hidden").count(), "the card covers the frame");
  await card().getByRole("button", { name: "Sign in" }).click();
  await page().waitForFunction(() => !document.querySelector(".pbi-problem"));
  await hostEmbedded();
  assert.strictEqual(calls("pbi_embed_content").length, 2, "opened again");

  world.contentFail = ["The caller isn't allowed to read this report (no access)."];
  await openStudio("Sales yesterday", { embeds: false });
  await card().getByText("Your account can't open this").waitFor();
  assert.match(await card().innerText(), /isn't allowed to read this report/);

  world.contentFail = ["Power BI answered 500: internal error"];
  await openStudio("Returns", { embeds: false });
  await card().getByText("Embed Studio couldn't open this").waitFor();
  assert.match(await card().innerText(), /Power BI answered 500/);
  await card().getByRole("button", { name: "Open in Fabric" }).click();
  await page().waitForFunction(() => state.view === "fabric");
  assert.ok((await page().evaluate(() => fabricPage.page)).includes("r-3d"));
});

test("Power BI's errors before the content loads: licence, sign-in, or its own words; Copy the details copies the error", { skip }, async () => {
  reset();
  await openStudio("Sales today");
  await fromHost("error", { message: "LicenseRequired", detailedMessage: "This content needs a Power BI Pro license." });
  await card().getByText("This content needs a Power BI licence").waitFor();
  await fromHost("error", { message: "TokenExpired", errorCode: "401" });
  await card().getByText("Your sign-in to Power BI has expired").waitFor();
  await fromHost("error", { message: "ReportNotFound", detailedMessage: "The report was deleted.", technicalDetails: { requestId: "req-9" } });
  await card().getByText("Power BI couldn't open it").waitFor();
  assert.match(await card().innerText(), /The report was deleted\./);
  await card().getByRole("button", { name: "Copy the details" }).click();
  const copied = JSON.parse(await page().evaluate(() => window.__copied));
  assert.strictEqual(copied.name, "error");
  assert.strictEqual(copied.detail.technicalDetails.requestId, "req-9");
  await fromHost("error", {});
  await card().getByText("Power BI didn't say why.").waitFor();
  // Once it loads the card goes.
  await fromHost("loaded", { initiator: null });
  await card().waitFor({ state: "detached" });
});

test("Troubleshooting: Details and Supports, and each event's line in Events", { skip }, async () => {
  reset();
  await openStudio("Sales today");
  if ((await page().getByRole("button", { name: "Troubleshooting" }).getAttribute("aria-pressed")) !== "true") {
    await page().getByRole("button", { name: "Troubleshooting" }).click();
  }
  const side = page().locator(".pbi-side");
  await side.getByText("Semantic model").waitFor();
  assert.match(await side.innerText(), /Signed in as\s+test@contoso\.com, with your Power BI access/);
  await side.getByRole("tab", { name: "Supports" }).click();
  assert.strictEqual(await side.getByRole("tab", { name: "Supports" }).getAttribute("aria-selected"), "true");
  const facts = await side.locator(".pbi-fact").allInnerTexts();
  assert.deepStrictEqual(facts.map((f) => f.replace(/\s+/g, " ").trim()), ["Pages Yes", "Filters Yes", "Bookmarks No", "Slicers Yes",
    "Visuals Yes", "Events loaded, rendered, error, pageChanged", "Limit Export data follows the tenant setting and your permission."]);
  await side.getByRole("tab", { name: "Details" }).click();
  // A page without a display name is named by its name.
  assert.deepStrictEqual(await page().getByRole("tablist", { name: "Report pages" }).getByRole("tab").allTextContents(), ["Overview", "P2"]);

  await fromHost("pageChanged", { newPage: { name: "P2" } });
  await page().waitForFunction(() => document.querySelector('.pbi-pages [aria-selected="true"]')?.textContent === "P2");
  // Choosing the page already shown sends nothing.
  const sent = (await hostGot()).length;
  await page().getByRole("tab", { name: "P2" }).click();
  assert.strictEqual((await hostGot()).length, sent);
  await fromHost("rendered", {});
  await fromHost("error", { message: "visualFailed", errorCode: "VF1", technicalDetails: { requestId: "req-2" } });
  await fromHost("dataHyperlinkClicked", { url: "javascript:alert(1)" });
  await fromHost("buttonClicked", { title: "Next", initiator: null, extra: "" });
  await fromHost("saved", null);
  await fromHost("commandTriggered", {});
  await page().locator(".pbi-events li", { hasText: "commandTriggered" }).waitFor();
  const line = async (name) => (await page().locator(".pbi-events li", { has: page().locator("b", { hasText: new RegExp(`^${name}$`) }) })
    .last().locator(".pbi-event-detail").textContent());
  assert.strictEqual(await line("pageChanged"), "P2");
  assert.strictEqual(await line("error"), "visualFailed (VF1), request req-2");
  assert.strictEqual(await line("dataHyperlinkClicked"), "javascript:alert(1)");
  assert.strictEqual(await line("buttonClicked"), '{"title":"Next"}');
  assert.strictEqual(await line("saved"), "");
  assert.strictEqual(await line("commandTriggered"), "");
  assert.strictEqual(await card().count(), 0, "a non-fatal error after the content rendered is only an event");
});

test("Events keeps the last 500", { skip }, async () => {
  reset();
  await openStudio("Returns");
  await hostFrame().evaluate(() => { for (let i = 0; i < 520; i++) window.raise("tick", { i }); });
  await page().locator(".pbi-events li", { hasText: '{"i":519}' }).waitFor();
  assert.strictEqual(await page().locator(".pbi-events li").count(), 500);
  assert.strictEqual(await page().evaluate(() => pbiStudio.session.events.length), 500);
  assert.strictEqual(await page().evaluate(() => pbiStudio.session.events[0].detail.i), 20);
});

test("a dashboard shows its tiles; a command the host refuses is toasted", { skip }, async () => {
  reset();
  await openStudio("Executive overview");
  const side = page().locator(".pbi-side");
  await side.getByText("Tiles").waitFor();
  assert.match(await side.innerText(), /Tiles\s+Revenue, t2/);
  assert.ok(await page().locator(".pbi-pages").isHidden(), "a dashboard has no page strip");
  const got = (await hostGot()).find((m) => m.command === "embed");
  assert.strictEqual(got.kind, "dashboard");
  assert.strictEqual(got.pageName, undefined);
  await hostFrame().evaluate(() => { window.refuse.reload = ""; });
  await page().getByRole("button", { name: "Reload" }).click();
  await page().locator("#toast", { hasText: "The embed host refused it." }).waitFor();
  await page().evaluate(() => { document.querySelector(".pbi-frame").requestFullscreen = () => Promise.reject(new Error("Full screen was refused.")); });
  await page().getByRole("button", { name: "Full screen" }).click();
  await page().locator("#toast", { hasText: "Full screen was refused." }).waitFor();
  await page().getByRole("button", { name: "Power BI ›" }).click();
  await page().waitForFunction(() => state.view === "powerbi");
});

test("a page change the host refuses is toasted", { skip }, async () => {
  reset();
  await openStudio("Sales today");
  await hostFrame().evaluate(() => { window.refuse.setPage = "That page is hidden."; });
  await page().getByRole("tab", { name: "P2" }).click();
  await page().locator("#toast", { hasText: "That page is hidden." }).waitFor();
});

test("with preview features hidden, Power BI and Embed Studio go back to Your workspaces", { skip }, async () => {
  settings.show_preview = { value: false, source: "user" };
  await page().evaluate(() => loadSettings());
  await page().evaluate(() => go("powerbi"));
  await page().waitForFunction(() => state.view === "workspaces");
  await page().evaluate(() => go("embedstudio"));
  await page().waitForFunction(() => state.view === "workspaces");
  await page().evaluate(() => { pbiStudio.item = null; go("embedstudio"); });
  await page().waitForFunction(() => state.view === "workspaces");
  settings.show_preview = { value: true, source: "default" };
  await page().evaluate(() => loadSettings());
});

test("the token is renewed five minutes before it runs out; a renewal that fails asks to sign in", { skip }, async () => {
  reset();
  await page().clock.install();
  world.seconds = 900;  // renewed after 600 s
  await openStudio("Sales today");
  const tokens = world.tokens;
  await page().clock.runFor(601 * 1000);
  for (let i = 0; i < 400 && !(await hostGot()).some((m) => m.command === "setToken"); i++) await hostGot();
  const set = (await hostGot()).filter((m) => m.command === "setToken");
  assert.strictEqual(set.length, 1);
  assert.strictEqual(set[0].accessToken, `${TOKEN}-${tokens + 1}`);
  // The next renewal, 600 s on, fails: the sign-in has run out.
  world.tokenFail = true;
  await page().clock.runFor(601 * 1000);
  for (let i = 0; i < 400 && !(await card().count()); i++) await hostGot();
  assert.match(await card().innerText(), /Your sign-in to Power BI has expired/);
});

test("no page errors and nothing the policy blocks", { skip }, () => {
  assert.deepStrictEqual(app.errors, []);
  assert.deepStrictEqual(app.violations, []);
});
