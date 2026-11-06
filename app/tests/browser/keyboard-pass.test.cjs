// #1672: a keyboard-only pass over every left-rail page, with the mock engine (see harness.cjs for how
// to run it). The automatable half of the Narrator and keyboard pass (EA-030).
//
// - Focus never gets stuck: on each rail page, Tab from the top of the window goes through the top bar,
//   the rail and the page, reaches every Tab stop in the window, and wraps back to the first one (the Skip
//   to content link) without looping inside a region. Shift+Tab goes the same way backwards and wraps too.
//   a11y-audit.test.cjs already checks, inside each page, that everything clickable is a Tab stop and that
//   every Tab stop shows a focus ring; this walk covers the whole window and both directions.
// - No page is left out: the rail pages walked are exactly the features with a rail entry (features.js),
//   so a new page is in the pass the day it is registered, and one the pass skips fails the guard.
// - The dialogs the rail pages open from a button of their own (axe checks each while it's open, #1673), beyond the shared ones that
//   dialog-keyboard.test.cjs opens by code: Enter opens each one with focus inside, Escape closes it and
//   focus goes back to the button. Opening and closing them sends no write to the engine.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, animationsDone, axeSource, axeFindings } = require("./harness.cjs");
// axe covers dialogs (#1673, ui.test.cjs): the cases below run axe while these are open.
// axe covers dialogs: app.js:pickWorkspace, git.js:gitPathDialog, notebook-formats.js:nbfImportDialog
// axe covers dialogs: pipelines.js:pipelineOpenDialog, platform.js:newRequestDialog

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;

// Reads the rail pages and their dialogs make. Every other call fails as it does with no Fabric tenant,
// so the pages draw their empty or error states, which have Tab stops of their own.
const READS = ["environment", "git_info", "list_workspaces", "pipeline_list", "platform_domains", "platform_products"];
// What these dialogs would change once filled in; the keyboard pass never gets that far.
const WRITES = ["create_lakehouse", "notebook_formats_import", "platform_request", "new_notebook", "save_notebook",
  "git_clone_start", "git_open", "git_init"];
// Any other call that may change something, by its name (the pages' own reads are named for what they read).
const looksLikeWrite = (m) => WRITES.includes(m) ||
  (!READS.includes(m) && /(^|_)(create|save|set|delete|import|request|start|clone|open|init|run|write|update|add|remove|forget|commit|push)(_|$)/.test(m));

before(async () => {
  if (skip) return;
  app = await openApp({
    // show_preview defaults to true (sidecar/fabric_desktop/app_settings.py DEFAULTS), so preview pages are on the rail.
    // git_info reads this PC's Git and the repositories it opened (none, in the engine's fresh home).
    engine: ["app_settings", "git_info"],
    mocks: {
      environment: () => ({ python: "Python 3 (test)", user: "ana@contoso.com", tenant: "t1" }),
      list_workspaces: () => [{ id: "w1", displayName: "Sales Dev", capacityId: "c1" }, { id: "w2", displayName: "Finance", capacityId: null }],
      // sidecar/fabric_desktop/pipeline_service.py pipeline_list
      pipeline_list: () => ({ pipelines: [{ path: "C:/repos/sales/Load orders.DataPipeline", repo: "sales", relative: "Load orders.DataPipeline",
        name: "Load orders", activity_count: 3, last_run: null }], truncated: false }),
      // sidecar/fabric_desktop/central/service.py platform_domains (the catalog's list_domains) and platform_products
      platform_domains: () => [{ id: "d1", name: "Sales", parent_id: null }],
      platform_products: () => ({ products: [] }),
    },
  });
  await page().waitForSelector("#rail-main .rail-item");
  await page().waitForFunction(() => state.settings && "show_preview" in state.settings);
});

after(async () => { await app?.close(); });

// Until the page has made no engine call for 300 ms (at most 4 s) and nothing is still fading in.
async function settle() {
  let last = -1;
  for (let t = 0; t < 4000; t += 300) {
    if (app.calls.length === last) break;
    last = app.calls.length;
    await page().waitForTimeout(300);
  }
  await animationsDone(page());
}

// Runs in the page: where focus is now, as a stable id stamped on the element, its region and a label.
function focusAt() {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return null;
  el.dataset.kpStop ||= String(Math.random()).slice(2);
  const region = el.closest("#rail") ? "rail" : el.closest("header.topbar") ? "top bar" : el.closest("#view") ? "page"
    : el.closest("#dialog-root") ? "dialog" : "window";
  const label = (el.getAttribute("aria-label") || el.textContent || el.value || "").trim().replace(/\s+/g, " ").slice(0, 30);
  return { stop: el.dataset.kpStop, key: `${region}: ${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ""} "${label}"`, region,
    editor: el.matches(".cm-content, [data-esc-tab]") };
}

// Runs in the page: every Tab stop a person can see in the window, as the browser would offer it.
function visibleStops() {
  return [...document.querySelectorAll('a[href], button, input, select, textarea, summary, [tabindex], [contenteditable="true"], .cm-content')]
    .filter((e) => e.tabIndex >= 0 && !e.disabled && !(e.type === "hidden") && e.checkVisibility({ visibilityProperty: true })
      && !e.closest("[inert], [hidden]") && !e.closest("#dialog-root") && !e.closest("#menu"))
    // A radio group is one Tab stop: its checked radio, or its first when none is.
    .filter((e, _, all) => {
      if (e.type !== "radio" || !e.name) return true;
      const group = all.filter((o) => o.type === "radio" && o.name === e.name && o.form === e.form);
      return e === (group.find((o) => o.checked) || group[0]);
    })
    .map((e) => { e.dataset.kpStop ||= String(Math.random()).slice(2); return e.dataset.kpStop; });
}

// Focus the top of the window: blur() alone leaves the browser's Tab starting point where focus was.
const toTop = () => page().evaluate(() => {
  const start = Object.assign(document.createElement("span"), { tabIndex: -1 });
  document.body.prepend(start);
  start.focus();
  start.remove();
});

// Press `key` from the top of the window until focus comes back to a stop it has been on. Focus leaving
// the window between the last stop and the first (the browser's own controls) is allowed once per lap.
// In a code editor Tab indents, so the walk leaves it the documented way: Escape, then Tab.
async function walk(key, limit) {
  await toTop();
  const stops = [];
  const seen = new Set();
  let outside = 0;
  for (let i = 0; i < limit; i++) {
    await page().keyboard.press(key);
    const at = await page().evaluate(focusAt);
    if (!at) {
      if (++outside > 2) return { stops, end: "focus left the window and didn't come back" };
      continue;
    }
    if (seen.has(at.stop)) return { stops, end: at.stop === stops[0].stop ? "wrapped" : `looped back to ${at.key}` };
    seen.add(at.stop);
    stops.push(at);
    if (at.editor) await page().keyboard.press("Escape");
  }
  return { stops, end: `still going after ${limit} presses, last on ${stops.at(-1)?.key}` };
}

// One rail page's keyboard pass: what a failure would say, empty when it passes.
async function keyboardPass(view) {
  await page().evaluate((v) => { closeDialog(); closeMenu?.(); go(v); }, view);
  await settle();
  const all = await page().evaluate(visibleStops);
  const limit = all.length + 20;
  const fwd = await walk("Tab", limit);
  const back = await walk("Shift+Tab", limit);
  const problems = [];
  if (fwd.end !== "wrapped") problems.push(`Tab: ${fwd.end}`);
  if (back.end !== "wrapped") problems.push(`Shift+Tab: ${back.end}`);
  if (fwd.stops[0]?.key !== 'window: a "Skip to content"') problems.push(`Tab starts on ${fwd.stops[0]?.key}, not Skip to content`);
  // A lap that comes back to where it began can still have gone round a region only (from the top,
  // Shift+Tab begins inside the page): each direction must also have reached every stop.
  for (const [name, lap] of [["Tab", fwd], ["Shift+Tab", back]]) {
    const visited = new Set(lap.stops.map((s) => s.stop));
    const missed = all.filter((s) => !visited.has(s));
    if (!missed.length) continue;
    const keys = await page().evaluate((ids) => ids.map((id) => document.querySelector(`[data-kp-stop="${id}"]`))
      .map((e) => `${e.tagName.toLowerCase()} "${(e.getAttribute("aria-label") || e.textContent || "").trim().slice(0, 30)}"`), missed.slice(0, 5));
    problems.push(`${name} never reached ${missed.length} stops: ${keys.join(", ")}${missed.length > 5 ? "..." : ""}`);
  }
  for (const region of ["top bar", "rail", "page"]) {
    if (!fwd.stops.some((s) => s.region === region)) problems.push(`Tab never reached the ${region}`);
  }
  // Backwards is the same lap the other way: from the top, Shift+Tab starts on the window's last stop.
  const fwdOrder = fwd.stops.map((s) => s.key);
  const backOrder = [...back.stops].reverse().map((s) => s.key);
  if (fwd.end === "wrapped" && back.end === "wrapped" && JSON.stringify(fwdOrder) !== JSON.stringify(backOrder)) {
    const at = fwdOrder.findIndex((k, i) => k !== backOrder[i]);
    problems.push(`Shift+Tab doesn't retrace Tab: stop ${at + 1} is ${fwdOrder[at]} going forward, ${backOrder[at]} going back`);
  }
  return { problems, stops: fwd.stops.length, keys: fwdOrder };
}

const walked = [];

test("every rail page: Tab and Shift+Tab reach every stop and wrap round, never stuck in a region", { skip }, async (t) => {
  const rail = await page().evaluate(() => [...railFeatures(), ...railFeatures(true)]
    .map((f) => ({ id: f.id, name: f.rail.label, view: Object.keys(f.views || {})[0] })));
  for (const r of rail) {
    await t.test(r.name, async () => {
      const { problems, stops } = await keyboardPass(r.view);
      walked.push(r.id);
      assert.deepStrictEqual(problems, [], `${r.name}: ${problems.join("; ")}`);
      assert.ok(stops >= 5, `${r.name}: only ${stops} Tab stops`);
    });
  }
  assert.deepStrictEqual(app.errors, []);
});

test("the keyboard pass walked every page with a rail entry, and none was skipped", { skip }, async () => {
  // Every registered feature with a rail entry, whatever railFeatures() shows now, and the rail's buttons as drawn.
  const { registered, shown, drawn, preview } = await page().evaluate(() => ({
    registered: features.list.filter((f) => f.rail).map((f) => f.id),
    shown: [...railFeatures(), ...railFeatures(true)].map((f) => f.id),
    drawn: document.querySelectorAll("#rail .rail-item").length,
    preview: previewShown(),
  }));
  assert.ok(preview, "preview pages are shown, so they're walked too");
  assert.deepStrictEqual([...walked].sort(), [...registered].sort(), "a rail page the keyboard pass didn't walk");
  assert.deepStrictEqual([...shown].sort(), [...registered].sort(), "a registered rail page isn't on the rail");
  assert.strictEqual(drawn, registered.length, "the rail's buttons are the registered rail pages");
  assert.ok(registered.length >= 11, `only ${registered.length} rail pages registered`);
});

test("the keyboard pass catches a page that traps focus", { skip }, async () => {
  // A region that sends Tab back to its own first button, as a hand-made focus trap would.
  await page().evaluate(() => { closeDialog(); go("settings"); });
  await settle();
  await page().evaluate(() => {
    const a = h("button", { id: "kp-a" }, "Trap A");
    const b = h("button", { id: "kp-b", onkeydown: (e) => { if (e.key === "Tab" && !e.shiftKey) { e.preventDefault(); a.focus(); } } }, "Trap B");
    document.querySelector("#view .page").prepend(h("div", {}, a, b));
  });
  const fwd = await walk("Tab", 400);
  assert.match(fwd.end, /looped back to page: button#kp-a/);
});

// The dialogs the rail pages open from a button of their own. Each: the page, the button, the dialog's name.
const DIALOGS = [
  ["home", "Import from Fabric or Databricks", "Import Fabric or Databricks notebooks"],
  ["home", "New lakehouse", "Choose a workspace"],
  ["create", "Pipeline", "Open a pipeline"],
  ["metadata", "Ask for data", "New request"],
  ["source", "Clone a repository", "Clone a repository"],
];

for (const [view, button, title] of DIALOGS) {
  test(`${view}: ${button} opens "${title}" from Enter; Escape closes it and focus goes back to the button`, { skip }, async () => {
    await page().evaluate((v) => { closeDialog(); homeCache.done = false; go(v); }, view);
    await settle();
    const opener = page().locator("#view button").filter({ hasText: button }).first();
    await opener.focus();
    await page().evaluate(() => { document.activeElement.dataset.kpOpener = "1"; });
    const before = app.calls.length;
    await page().keyboard.press("Enter");
    const dialog = page().locator(`#dialog-root .dialog[aria-label="${title}"]`);
    await dialog.waitFor({ timeout: 5000 }).catch(() => assert.fail(`Enter on ${button} didn't open ${title}`));
    await settle();
    assert.ok(await page().evaluate(() => document.getElementById("dialog-root").contains(document.activeElement)),
      "focus didn't move into the dialog");
    // #1673: axe on the dialog while it's open (when axe-core is installed, as in CI).
    if (axeSource) assert.deepStrictEqual(await axeFindings(page(), "#dialog-root .dialog"), [], `axe on ${title}`);
    await page().keyboard.press("Escape");
    await dialog.waitFor({ state: "detached", timeout: 3000 }).catch(() => assert.fail("Escape didn't close the dialog"));
    await page().waitForTimeout(100);
    const at = await page().evaluate(() => {
      const a = document.activeElement;
      return { opener: a?.dataset.kpOpener === "1", label: !a || a === document.body ? "nowhere (the page body)"
        : (a.getAttribute("aria-label") || a.textContent || a.tagName).trim().slice(0, 40) };
    });
    assert.ok(at.opener, `focus went to ${at.label}, not back to ${button}`);
    const made = app.calls.slice(before).map((c) => c.method);
    assert.deepStrictEqual(made.filter((m) => WRITES.includes(m)), [], `writes sent: ${made.join(", ")}`);
    assert.deepStrictEqual(made.filter(looksLikeWrite), [], `calls that may change something: ${made.join(", ")}`);
    assert.deepStrictEqual(app.errors, []);
  });
}

// EA-030 (docs/ux/narrator-checklist.md section 2): a screen reader user finds where they are by the page's
// level 1 heading (Narrator's H key), so every rail page has exactly one, and so does each OneLake tab.
// Read from Chromium's accessibility tree, which is what Narrator reads, so a hidden heading counts.
test("every rail page, and each OneLake tab, has exactly one level 1 heading", { skip }, async (t) => {
  const rail = await page().evaluate(() => [...railFeatures(), ...railFeatures(true)]
    .map((f) => ({ name: f.rail.label, view: Object.keys(f.views || {})[0], open: `go("${Object.keys(f.views || {})[0]}")` })));
  const tabs = ["catalog", "shortcuts", "access"].map((tab) => ({ name: `OneLake: ${tab}`, open: `catalogPage.tab = "${tab}"; go("onelake")` }));
  for (const r of [...rail, ...tabs]) {
    await t.test(r.name, async () => {
      await page().evaluate((open) => { closeDialog(); go("home"); (0, eval)(open); }, r.open);
      await settle();
      const cdp = await page().context().newCDPSession(page());
      const { nodes } = await cdp.send("Accessibility.getFullAXTree");
      await cdp.detach();
      const h1 = nodes.filter((n) => !n.ignored && n.role?.value === "heading"
        && n.properties?.some((p) => p.name === "level" && p.value.value === 1)).map((n) => n.name?.value);
      assert.strictEqual(h1.length, 1, `${r.name}: level 1 headings ${JSON.stringify(h1)}`);
    });
  }
  assert.deepStrictEqual(app.errors, []);
});

test("the whole pass sent no write to the engine", { skip }, async () => {
  assert.deepStrictEqual(app.calls.map((c) => c.method).filter(looksLikeWrite), []);
});
