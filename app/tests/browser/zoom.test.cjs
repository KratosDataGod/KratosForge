// PB-063: KratosForge zooms with Ctrl+= / Ctrl+- / Ctrl+0 (and Ctrl+wheel), and every page still works at 200%.
// Zoom halves the page's width in CSS pixels, so the default 1440 x 900 window zoomed to 200% lays out
// like a 720 x 450 one. At that size no page scrolls sideways as a whole, no control sits past the
// window's right edge or is cut off by a box that doesn't scroll, the rail scrolls to its last page,
// and dialogs fit the window with their buttons in reach.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const ZOOMED = { width: 720, height: 450 };  // 1440 x 900 at 200%
const LH = { workspace_id: "w1", lakehouse_id: "l1", name: "LH_Bronze", workspace_name: "Sales Dev" };
const CELLS = [
  { type: "markdown", cell_id: "m1", source: "# Daily sales\nLoads yesterday's orders.", meta: {} },
  { type: "code", cell_id: "c1", source: "import polars as pl\ndf = pl.read_delta('Tables/dbo/sales')", meta: {} },
];

const skip = !playwright && "Playwright is not installed";
let app;

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: ZOOMED,
    engine: ["app_settings", "integrations_status", "integrations_activity", "integrations_pending", "editor_status"],
    mocks: {
      environment: () => ({ python: "Python 3 (test)", user: "ana@contoso.com", tenant: "t1" }),
      list_workspaces: () => [{ id: "w1", displayName: "Sales Dev", capacityId: "c1" }, { id: "w2", displayName: "Finance", capacityId: null }],
      list_lakehouses: () => [{ id: "l1", displayName: "LH_Bronze" }],
      list_notebooks: () => [{ name: "Daily sales" }, { name: "Orders" }],
      list_tables: () => ({ dbo: ["sales", "customers"] }),
      list_files: () => [{ name: "orders.csv", path: "Files/raw/orders.csv", is_dir: false, size: 2048 }],
      table_columns: () => [{ name: "region", type: "string" }, { name: "amount", type: "double" }],
      cached_schemas: () => ({ tables: {}, columns: {} }),
      readiness_check: () => [],
      save_notebook: () => ({ saved: true }),
    },
  });
  await app.page.waitForSelector("#rail-main .rail-item");
  await app.page.waitForFunction(() => state.settings && "show_preview" in state.settings, null, { polling: 100 });
});
after(async () => { await app?.close(); });

// Runs in the page: what doesn't fit. A control counts as cut off when it reaches past the window's right
// edge, or past a box that hides its overflow before any box that scrolls (a scrolling tab strip is fine:
// Tab and the scroll bar reach what's in it).
function fitScan(scope) {
  const vw = document.documentElement.clientWidth;
  const name = (el) => {
    const text = (el.getAttribute("aria-label") || el.textContent || el.value || "").trim().replace(/\s+/g, " ").slice(0, 30);
    return `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ""}${text ? ` "${text}"` : ""}`;
  };
  const cut = [];
  for (const el of document.querySelectorAll(`:is(${scope}) :is(button, a[href], input, select, textarea, [role=tab])`)) {
    const b = el.getBoundingClientRect();
    if (!b.width || !b.height || !el.checkVisibility({ visibilityProperty: true, opacityProperty: true })) continue;
    if (el.closest("[aria-hidden=true], .sr-only")) continue;
    let clippedBy = null;
    for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
      const ox = getComputedStyle(n).overflowX;
      if (ox === "auto" || ox === "scroll") break;
      if (ox === "hidden" || ox === "clip") {
        const nb = n.getBoundingClientRect();
        if (b.right > nb.right + 1 || b.left < nb.left - 1) { clippedBy = n; break; }
      }
    }
    // Inside a box that scrolls sideways, the box itself has to fit.
    const scroller = [...ancestors(el)].find((n) => /auto|scroll/.test(getComputedStyle(n).overflowX));
    const right = scroller ? Math.min(b.right, scroller.getBoundingClientRect().right) : b.right;
    if (clippedBy || right > vw + 1) cut.push(name(el));
    // Its own text cut short, with no ellipsis and tooltip saying the rest.
    else if (el.scrollWidth > el.clientWidth + 1 && /hidden|clip/.test(getComputedStyle(el).overflowX) &&
      !(getComputedStyle(el).textOverflow === "ellipsis" && el.title)) cut.push(`${name(el)} (its text)`);
  }
  const se = document.scrollingElement;
  return { pageScrollX: se.scrollWidth - se.clientWidth, cut };
  function* ancestors(el) { for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) yield n; }
}

async function open(page, how) {
  await page.evaluate(() => { document.getElementById("palette")?.remove(); closeDialog(); });
  await how(page);
  await page.waitForTimeout(400);  // the page's own calls answer and it draws
}

const PAGES = async (page) => {
  const rail = await page.evaluate(() => [...railFeatures(), ...railFeatures(true)]
    .map((f) => ({ id: f.id, view: Object.keys(f.views || {})[0] })));
  return [
    ...rail.map((r) => ({ id: r.id, open: (p) => p.evaluate((v) => go(v), r.view) })),
    { id: "notebook", open: (p) => p.evaluate(([cells, lh]) => { loadIntoState({ name: "Daily sales", cells, lakehouse: lh }); go("notebook"); }, [CELLS, LH]) },
    // A selected code cell shows its whole toolbar, the widest cell head (Delete cell was cut off in CI).
    { id: "notebook-code-cell", open: async (p) => {
      await p.evaluate(([cells, lh]) => { loadIntoState({ name: "Daily sales", cells, lakehouse: lh }); go("notebook"); }, [CELLS, LH]);
      await p.click(".cell.code .cm-content");
    } },
    { id: "sql", open: (p) => p.evaluate((lh) => openSqlQuery(lh), LH) },
    // With a project open, Data mesh adds its environment picker and where it runs to the top bar.
    { id: "metadata-project", open: (p) => p.evaluate(() => {
      state.meta.project ||= { path: "C:/mesh", name: "Mesh", environments: {} };
      go("metadata");
    }), check: (p) => p.evaluate(() => {
      const pill = document.querySelector("#env-slot .pill.compute");
      return pill ? (pill.scrollWidth > pill.clientWidth + 1 ? [`"${pill.textContent}" is cut short`] : []) : ["no Runs on pill"];
    }) },
    { id: "settings-integrations", open: (p) => p.evaluate(() => { state.settingsTab = "integrations"; go("settings"); }) },
  ];
};

test("the shell lets people zoom the main window and pop-outs with Ctrl+= and Ctrl+-", () => {
  const root = path.join(__dirname, "..", "..", "src-tauri");
  const conf = JSON.parse(fs.readFileSync(path.join(root, "tauri.conf.json"), "utf8"));
  const main = conf.app.windows.find((w) => w.label === "main");
  assert.strictEqual(main.zoomHotkeysEnabled, true, "tauri.conf.json: the main window has zoomHotkeysEnabled");
  const rs = fs.readFileSync(path.join(root, "src", "main.rs"), "utf8");
  const popOut = rs.slice(rs.indexOf("fn pop_out_open"), rs.indexOf("fn pop_out_open") + 2500);
  assert.match(popOut, /\.zoom_hotkeys_enabled\(true\)/, "main.rs: pop-out windows zoom too");
});

test("every page fits the default window zoomed to 200%", { skip }, async () => {
  const page = app.page;
  const misfits = [];
  const seen = [];
  for (const target of await PAGES(page)) {
    await open(page, target.open);
    const r = await page.evaluate(fitScan, "header.topbar, #view");
    seen.push(target.id);
    if (r.pageScrollX > 0) misfits.push(`${target.id}: the page scrolls sideways by ${r.pageScrollX}px`);
    for (const c of r.cut) misfits.push(`${target.id}: ${c} is cut off`);
    for (const c of (await target.check?.(page)) || []) misfits.push(`${target.id}: ${c}`);
  }
  assert.ok(seen.length >= 10, `checked ${seen.join(", ")}`);
  assert.deepStrictEqual(misfits, []);
});

test("at 200% the rail scrolls to its last page and the account button is on screen", { skip }, async () => {
  const page = app.page;
  await open(page, (p) => p.evaluate(() => go("home")));
  const last = page.locator("#rail-bottom .rail-item").last();
  await last.scrollIntoViewIfNeeded();
  const box = await last.boundingBox();
  assert.ok(box && box.y >= 0 && box.y + box.height <= ZOOMED.height + 1, `the last rail item is in view: ${JSON.stringify(box)}`);
  const account = await page.locator("#account").boundingBox();
  assert.ok(account && account.x + account.width <= ZOOMED.width, `the account button is on screen: ${JSON.stringify(account)}`);
});

test("at 200% dialogs fit the window and their buttons are in reach", { skip }, async () => {
  const page = app.page;
  const dialogs = [
    ["confirm", (p) => p.evaluate(() => { confirmDialog("Delete the lakehouse LH_Bronze?", "This removes its tables and files from Fabric.", "Delete", { danger: true }); })],
    ["report-problem", (p) => p.evaluate(() => reportProblem())],
  ];
  for (const [id, how] of dialogs) {
    await open(page, how);
    await page.waitForSelector("#dialog-root .dialog");
    const r = await page.evaluate(() => {
      const d = document.querySelector("#dialog-root .dialog").getBoundingClientRect();
      const buttons = [...document.querySelectorAll("#dialog-root .dialog button")].filter((b) => b.checkVisibility());
      // A button below the fold is in reach when the dialog scrolls to it.
      const out = buttons.filter((b) => { b.scrollIntoView({ block: "nearest" }); const r = b.getBoundingClientRect(); return r.bottom > innerHeight + 1 || r.right > innerWidth + 1 || r.top < -1; });
      return { w: d.width, h: d.height, out: out.map((b) => b.textContent.trim()) };
    });
    assert.ok(r.w <= ZOOMED.width, `${id}: ${r.w}px wide in a ${ZOOMED.width}px window`);
    assert.ok(r.h <= ZOOMED.height, `${id}: ${r.h}px tall in a ${ZOOMED.height}px window`);
    assert.deepStrictEqual(r.out, [], `${id}: buttons out of reach`);
  }
  await page.evaluate(() => closeDialog());
});

test("the fit check catches a control pushed past the window's edge", { skip }, async () => {
  const page = app.page;
  await open(page, (p) => p.evaluate(() => {
    go("home");
    const row = h("div", { style: "display:flex;overflow:hidden;width:300px" },
      h("div", { style: "flex:none;width:400px" }, "wide"), h("button", { id: "zoom-hidden" }, "Hidden"));
    const wide = h("button", { id: "zoom-wide", style: "position:relative;left:900px" }, "Past the edge");
    document.getElementById("view").append(row, wide);
  }));
  const r = await page.evaluate(fitScan, "#view");
  assert.ok(r.cut.some((c) => c.startsWith("button#zoom-hidden")), r.cut.join(", "));
  assert.ok(r.cut.some((c) => c.startsWith("button#zoom-wide")), r.cut.join(", "));
  await page.evaluate(() => go("home"));
});
