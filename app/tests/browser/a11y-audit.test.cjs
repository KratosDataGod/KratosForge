// PB-063 part 1: an automated accessibility audit of every page in a real browser, light and dark:
// axe-core's WCAG 2.1 A and AA rules (contrast among them), every clickable thing reachable from the
// keyboard, and a visible focus ring on every Tab stop. A serious or critical rule violation, or a
// new keyboard or focus finding, fails the test. A11Y_REPORT=<file> also writes the results there as
// Markdown (docs/ux/accessibility-audit.md is made this way). axe-core is pinned in ci.yml (AXE_CORE_VERSION)
// and found through NODE_PATH like Playwright; without it the test skips here and fails in CI.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const { playwright, openApp } = require("./harness.cjs");

let axeSource = null;
try { axeSource = fs.readFileSync(require.resolve("axe-core/axe.min.js"), "utf8"); } catch { /* not installed: skip */ }
const skip = (!playwright && "Playwright is not installed") || (!axeSource && "axe-core is not installed");

const LH = { workspace_id: "w1", lakehouse_id: "l1", name: "LH_Bronze", workspace_name: "Sales Dev" };
const CELLS = [
  { type: "markdown", cell_id: "m1", source: "# Daily sales\nLoads yesterday's orders.", meta: {} },
  { type: "code", cell_id: "c1", source: "import polars as pl\ndf = pl.read_delta('Tables/dbo/sales')", meta: {} },
  { type: "code", cell_id: "c2", source: "%%sql\nSELECT region, sum(amount) FROM sales GROUP BY region", meta: {} },
];
const RULES = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];
const SCHEMES = ["light", "dark"];

// Findings this audit recorded that are filed to be fixed (#905): "page: rule" and "page: element".
// A new one fails; remove a line when its fix lands (the test then keeps it fixed).
const KNOWN_RULES = new Set([]);
const KNOWN_KEYBOARD = new Set([]);
const KNOWN_FOCUS = new Set([]);

// Every page on the rail is found at run time; these open the screens that aren't rail pages.
const EXTRA = [
  { id: "notebook", name: "Notebook", open: (p) => p.evaluate(([cells, lh]) => {
    loadIntoState({ name: "Daily sales", cells, lakehouse: lh }); go("notebook");
  }, [CELLS, LH]), ready: ".cell .cm-content" },
  { id: "sql", name: "SQL query", open: (p) => p.evaluate((lh) => openSqlQuery(lh), LH), ready: ".sql-edit .cm-editor" },
  { id: "command-bar", name: "Command bar (Ctrl+K)", open: (p) => p.keyboard.press("Control+k"), ready: "#palette", scope: "#palette" },
  { id: "confirm", name: "Confirm dialog", open: (p) => p.evaluate(() => {
    confirmDialog("Delete the lakehouse LH_Bronze?", "This removes its tables and files from Fabric.", "Delete", { danger: true });
  }), ready: "#dialog-root .dialog", scope: "#dialog-root" },
];

let app;
const results = [];

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["app_settings", "sql_dialects"],
    mocks: {
      environment: () => ({ python: "Python 3 (test)", user: "ana@contoso.com", tenant: "t1" }),
      list_workspaces: () => [{ id: "w1", displayName: "Sales Dev", capacityId: "c1" }, { id: "w2", displayName: "Finance", capacityId: null }],
      list_lakehouses: () => [{ id: "l1", displayName: "LH_Bronze" }],
      list_notebooks: () => [{ name: "Daily sales" }, { name: "Orders" }],
      list_tables: () => ({ dbo: ["sales", "customers"] }),
      list_files: () => [{ name: "orders.csv", path: "Files/raw/orders.csv", is_dir: false, size: 2048 }],
      table_columns: () => [{ name: "region", type: "string" }, { name: "amount", type: "double" }],
      cached_schemas: () => ({ tables: {}, columns: {} }),
      save_notebook: () => ({ saved: true }),
    },
  });
  await app.page.waitForSelector("#rail-main .rail-item");
  await app.page.waitForFunction(() => state.settings && "show_preview" in state.settings);
});

after(async () => {
  if (process.env.A11Y_REPORT && results.length) fs.writeFileSync(process.env.A11Y_REPORT, report(results));
  await app?.close();
});

// Runs in the page: the outermost things that look clickable (a pointer cursor) but can't be reached
// with Tab, and every Tab stop in `scope` with whether it shows where focus is.
function keyboardScan(scope) {
  const root = document.querySelector(scope) || document.body;
  const css = (el) => {
    if (el.id) return `#${el.id}`;
    const cls = [...el.classList].slice(0, 2).map((c) => `.${c}`).join("");
    const text = (el.getAttribute("aria-label") || el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 30);
    return `${el.tagName.toLowerCase()}${cls}${text ? ` "${text}"` : ""}`;
  };
  const focusable = ["button", "a[href]", "input", "select", "textarea", "summary", "label", "[tabindex]", "[contenteditable=true]", ".cm-editor"]
    .map((s) => `${s}:not([tabindex="-1"])`).join(", ");
  // A mouse-only control the keyboard reaches another way: hidden from assistive tech, beside a Tab stop
  // that names its key (a tab's ×, closed with Delete on the tab).
  const keyedElsewhere = (el) => el.closest("[aria-hidden=true]") && el.parentElement?.querySelector("[aria-keyshortcuts]");
  const unreachable = [];
  for (const el of root.querySelectorAll("*")) {
    const rect = el.getBoundingClientRect();
    if (!rect.width || !rect.height || getComputedStyle(el).cursor !== "pointer") continue;
    if (el.parentElement && getComputedStyle(el.parentElement).cursor === "pointer") continue;  // inherited: judged at the top
    if (el.closest(focusable) || el.querySelector(focusable) || keyedElsewhere(el)) continue;
    unreachable.push(css(el));
  }
  return { unreachable };
}

// Runs in the page after a Tab: where focus is, whether it's inside `scope`, and whether it shows a
// ring (an outline or box shadow on it or a close parent, as :focus-within styles draw it).
async function focusNow(scope) {
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));  // a ring that eases in has drawn
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const ring = (n) => {
    const cs = getComputedStyle(n);
    return (cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0) || (cs.boxShadow && cs.boxShadow !== "none");
  };
  let shown = false;
  for (let n = el, depth = 0; n && depth < 6 && !shown; n = n.parentElement, depth++) shown = ring(n);
  const rect = el.getBoundingClientRect();
  const label = (el.getAttribute("aria-label") || el.textContent || el.value || "").trim().replace(/\s+/g, " ").slice(0, 30);
  const cls = [...el.classList].slice(0, 2).map((c) => `.${c}`).join("");
  el.dataset.a11yStop ||= String(Math.random());
  return { key: el.id ? `#${el.id}` : `${el.tagName.toLowerCase()}${cls}${label ? ` "${label}"` : ""}`, stop: el.dataset.a11yStop,
    inside: !!el.closest(scope), editor: el.classList.contains("cm-content"), shown: shown && rect.width > 0 && rect.height > 0 && getComputedStyle(el).opacity !== "0" };
}

// Tab from the top of the page until focus comes back to a stop it has seen. In a code editor Tab
// indents, so the walk leaves it the documented way, Escape then Tab. A walk that comes back while
// Tab stops in `scope` are still unvisited can't get past `trap`.
async function tabStops(page, scope) {
  const stops = [];
  const seen = new Set();
  let trap = null;
  // Start at the top: blur() alone leaves the browser's Tab starting point where focus was.
  await page.evaluate(() => {
    const start = Object.assign(document.createElement("span"), { tabIndex: -1 });
    document.body.prepend(start);
    start.focus();
    start.remove();
  });
  for (let i = 0; i < 400; i++) {
    await page.keyboard.press("Tab");
    const at = await page.evaluate(focusNow, scope);
    if (!at) break;
    if (seen.has(at.stop)) {
      const left = await page.evaluate(([scope, seenIds]) => [...document.querySelectorAll(`${scope} :is(button, a[href], input, select, textarea, summary, [tabindex], .cm-content):not([tabindex="-1"])`)]
        .filter((e) => !e.disabled && e.checkVisibility({ visibilityProperty: true }) && !e.closest("[inert], [hidden]"))  // a closed <details> menu's items aren't shown
        .filter((e) => !seenIds.includes(e.dataset.a11yStop)).length, [scope, [...seen]]);
      if (left) trap = { key: at.key, left };
      break;
    }
    seen.add(at.stop);
    if (at.inside) stops.push({ key: at.key, shown: at.shown });
    if (at.editor) await page.keyboard.press("Escape");
  }
  return { stops, trap };
}

async function audit(page, target, scheme) {
  // Reduced motion: a page fading in would be judged on its half-drawn colors.
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: "reduce" });
  await page.evaluate(() => { document.getElementById("palette")?.remove(); closeDialog(); go("home"); });
  await target.open(page);
  if (target.ready) await page.waitForSelector(target.ready);
  await page.waitForTimeout(300);  // the page's own calls answer
  if (!(await page.evaluate(() => typeof axe === "object"))) await page.addScriptTag({ content: axeSource });
  const scope = target.scope || "#view";
  const axeOut = await page.evaluate(async ([scope, tags]) => {
    const res = await axe.run(document.querySelector(scope) || document, { runOnly: { type: "tag", values: tags } });
    const brief = (v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.map((n) => n.target.join(" ")).slice(0, 5) });
    return { violations: res.violations.map(brief), incomplete: res.incomplete.map(brief), passes: res.passes.length };
  }, [scope, RULES]);
  const { unreachable } = await page.evaluate(keyboardScan, scope);
  // The keyboard walk doesn't depend on the color scheme, so it runs once, in light.
  const { stops, trap } = scheme === "light" ? await tabStops(page, scope) : { stops: [], trap: null };
  if (trap) unreachable.push(`Tab returns to ${trap.key}, ${trap.left} Tab stops later never reached`);
  const result = { page: target.name, id: target.id, scheme, ...axeOut, unreachable, stops,
    noRing: stops.filter((s) => !s.shown).map((s) => s.key) };
  results.push(result);
  return result;
}

async function targets(page) {
  const rail = await page.evaluate(() => [...railFeatures(), ...railFeatures(true)]
    .map((f) => ({ id: f.id, name: f.rail.label || f.id, view: Object.keys(f.views || {})[0] })));
  return [...rail.map((r) => ({ id: r.id, name: r.name, open: (p) => p.evaluate((v) => go(v), r.view), ready: "#view" })), ...EXTRA];
}

test("every page passes the WCAG A and AA rules, light and dark, with no serious or critical violation", { skip }, async () => {
  const failures = [];
  for (const target of await targets(app.page)) {
    for (const scheme of SCHEMES) {
      const r = await audit(app.page, target, scheme);
      for (const v of r.violations.filter((v) => v.impact === "serious" || v.impact === "critical")) {
        if (KNOWN_RULES.has(`${r.id}: ${v.id}`)) continue;
        failures.push(`${r.page} (${scheme}): ${v.id} ${v.impact} at ${v.nodes.join(", ")}`);
      }
    }
  }
  assert.ok(results.length >= 2 * (EXTRA.length + 5), `audited ${results.length} page views`);
  assert.deepStrictEqual(failures, []);
  const seen = new Set(results.flatMap((r) => r.violations.map((v) => `${r.id}: ${v.id}`)));
  assert.deepStrictEqual([...KNOWN_RULES].filter((k) => !seen.has(k)), [], "fixed: remove these from KNOWN_RULES");
});

test("everything clickable is reached with Tab, and every Tab stop shows focus", { skip }, async () => {
  const light = results.filter((r) => r.scheme === "light");
  const keyboard = light.flatMap((r) => r.unreachable.map((k) => `${r.id}: ${k}`)).filter((k) => !KNOWN_KEYBOARD.has(k));
  const focus = light.flatMap((r) => r.noRing.map((k) => `${r.id}: ${k}`)).filter((k) => !KNOWN_FOCUS.has(k));
  assert.deepStrictEqual(keyboard, [], "clickable but not reachable with Tab");
  assert.deepStrictEqual(focus, [], "Tab stops with no visible focus");
  const found = new Set(light.flatMap((r) => [...r.unreachable, ...r.noRing].map((k) => `${r.id}: ${k}`)));
  assert.deepStrictEqual([...KNOWN_KEYBOARD, ...KNOWN_FOCUS].filter((k) => !found.has(k)), [], "fixed: remove these from the known lists");
  assert.ok(light.filter((r) => r.stops.length).length >= light.length - 1, "Tab reached the pages' controls");
});

test("the audit catches a violation put into a page", { skip }, async () => {
  const page = app.page;
  const before = results.length;
  const r = await audit(page, { id: "injected", name: "Injected", ready: "#a11y-injected", open: (p) => p.evaluate(() => {
    // A bare element with a click listener: h() would have made it a Tab stop.
    const clicky = Object.assign(document.createElement("div"), { id: "a11y-injected", textContent: "Open" });
    clicky.style.cssText = "cursor:pointer;color:#bbb;background:#fff";
    clicky.addEventListener("click", () => {});
    document.getElementById("view").append(clicky,
      h("img", { src: "data:image/gif;base64,R0lGODlhAQABAAAAACw=" }),
      h("button", { class: "a11y-bare", style: "outline:none" }));
  }) }, "light");
  results.length = before;  // not part of the report
  const ids = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id);
  for (const id of ["image-alt", "color-contrast", "button-name"]) assert.ok(ids.includes(id), `${id} in ${ids.join(", ")}`);
  assert.ok(r.unreachable.includes("#a11y-injected"), r.unreachable.join(", "));
  assert.ok(r.noRing.some((k) => k.startsWith("button.a11y-bare")), r.noRing.join(", "));
});

function report(rows) {
  const ids = [...new Set(rows.map((r) => r.id))];
  const byId = (id, scheme) => rows.find((r) => r.id === id && r.scheme === scheme);
  const count = (list, impact) => list.filter((v) => !impact || v.impact === impact).length;
  const lines = [
    "# Accessibility audit: automated, per page",
    "",
    "*Made by `app/tests/browser/a11y-audit.test.cjs` (PB-063 part 1). Do not edit by hand: run*",
    "`A11Y_REPORT=docs/ux/accessibility-audit.md node --test app/tests/browser/a11y-audit.test.cjs` *to refresh it.*",
    "",
    `Rules: axe-core ${rowsVersion()} with the WCAG 2.1 A and AA tags (${RULES.join(", ")}), color contrast among them. ` +
      "Keyboard: everything with a pointer cursor must be a Tab stop or inside one, and pressing Tab from the top " +
      "must reach every Tab stop without looping (Escape then Tab leaves a code editor). Focus: each Tab stop on the page " +
      "must show an outline or ring. Each page is checked in light and dark (the window's color scheme); the app has " +
      "one theme, so dark is checked to show nothing breaks when Windows is set to dark.",
    "",
    "The pages are opened signed in with test data (two workspaces, one lakehouse, two notebooks), so lists show rows " +
      "where they can. Screens that need a live tenant to fill are audited in their empty or signed-in state. " +
      "Part 2 (Narrator and keyboard on Windows) is a separate pass.",
    "",
    "| Page | Scheme | Rules passed | Violations (serious + critical) | Needs a look | Tab stops | Unreachable | No focus ring |",
    "|---|---|---|---|---|---|---|---|",
  ];
  for (const id of ids) {
    for (const scheme of SCHEMES) {
      const r = byId(id, scheme);
      if (!r) continue;
      lines.push(`| ${r.page} | ${scheme} | ${r.passes} | ${count(r.violations)} (${count(r.violations, "serious") + count(r.violations, "critical")}) | ` +
        `${r.incomplete.map((v) => `${v.id} (${v.nodes.length})`).join(", ") || "none"} | ${r.stops.length} | ${r.unreachable.length} | ${r.noRing.length} |`);
    }
  }
  const findings = rows.flatMap((r) => [
    ...r.violations.map((v) => `- **${r.page}** (${r.scheme}): ${v.id}, ${v.impact}: ${v.help}. At ${v.nodes.map((n) => `\`${n}\``).join(", ")}`),
    ...(r.scheme === "light" ? r.unreachable.map((k) => (k.startsWith("Tab returns")
      ? `- **${r.page}**: keyboard: ${k} (Escape, then Tab, used to leave each code editor)`
      : `- **${r.page}**: \`${k}\` looks clickable but isn't reached with Tab`)) : []),
    ...(r.scheme === "light" ? r.noRing.map((k) => `- **${r.page}**: \`${k}\` shows no focus ring`) : []),
  ]);
  const looks = rows.filter((r) => r.scheme === "light").flatMap((r) => r.incomplete.map((v) =>
    `- **${r.page}**: ${v.id}: ${v.help}. At ${v.nodes.map((n) => `\`${n}\``).join(", ")}`));
  lines.push("", "## Findings", "", "Filed to be fixed in [#905](https://github.com/KratosDataGod/KratosForge/issues/905); the test fails on any finding not listed here.", "",
    ...(findings.length ? findings : ["None."]));
  lines.push("", "## Needs a look", "",
    "Checks axe couldn't decide by itself (for example text over a gradient or an image). Each needs a person to judge.", "",
    ...(looks.length ? looks : ["None."]), "");
  return lines.join("\n");
}

function rowsVersion() {
  const m = axeSource && axeSource.match(/axe v([\d.]+)/);
  return m ? m[1] : "?";
}
