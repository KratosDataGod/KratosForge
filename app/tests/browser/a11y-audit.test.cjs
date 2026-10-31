// PB-063 part 1: an automated accessibility audit of every page in a real browser, light and dark:
// axe-core's WCAG 2.1 A and AA rules (contrast among them), every clickable thing reachable from the
// keyboard, and a visible focus ring on every Tab stop. A serious or critical rule violation not in KNOWN_RULES,
// or a keyboard or focus finding not in KNOWN_KEYBOARD or KNOWN_FOCUS, fails the test. A11Y_REPORT=<file> also writes the results there as
// Markdown (docs/ux/accessibility-audit.md is made this way). axe-core is pinned in ci.yml (AXE_CORE_VERSION)
// and found through NODE_PATH like Playwright; without it the test skips here and fails in CI.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const { playwright, openApp, animationsDone } = require("./harness.cjs");

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

// Findings this audit recorded that are filed to be fixed (#905): "page: rule @ element" and "page: element".
// A new one fails; remove a line when its fix lands (the test then keeps it fixed).
const KNOWN_RULES = new Set([]);
const KNOWN_KEYBOARD = new Set([]);
const KNOWN_FOCUS = new Set([]);

// Checks axe can't decide by itself, judged by a person (#945): "page: rule @ element" patterns and the verdict,
// measured in the browser with the app's colors. A new undecided item fails until it's judged here.
const JUDGED = [
  [/^home: color-contrast @ .*\.chev$/, "Pass: the fold arrow is #616161 on white, 6.2:1 (3:1 needed for a control's graphic)."],
  [/^workspaces: color-contrast @ .*\.star\[/, "Pass: the favorite star is #8a8a8a, 3.4:1 on white and 3.3:1 on #fafafa (3:1 needed for a control's graphic); its button is named."],
  [/^notebook: color-contrast @ .*\.cell-grip/, "Pass: the drag grip is #616161, 6.2:1, and hidden from assistive tech; the cell's Move up and Move down buttons do the same from the keyboard."],
  [/^(notebook|sql): color-contrast @ .*(cm-gutterElement|cm-activeLineGutter)/, "Pass: line numbers are #616161, 5.3:1 on the active line (#edeef1) and 5.9:1 or more elsewhere."],
  [/^(notebook|sql): color-contrast @ span\[title="Fold (line|code)"\]/, "Pass: the fold marker is #616161 on white, 6.2:1."],
  [/^(entity|data-mesh|mesh\w*): color-contrast @ \.arrow\[aria-hidden="true"\]/, "Pass: the step arrows are #8a8a8a, 3.4:1 on white and 3.3:1 on #fafafa, and hidden from assistive tech (the steps' order is in the text)."],
  [/^learn-lab: color-contrast @ \.learn-mark$/, "Pass: an exercise check's mark is #242424 on white, 15.5:1 (passed is #1f7a4a, 5.3:1), and hidden from assistive tech (the check's text says it)."],
  [/^report-problem: color-contrast @ textarea\[rows="3"\]$/, "Pass: What happened is #242424 on white, 15.5:1, and its placeholder #757575, 4.6:1."],
  [/^report-problem: color-contrast @ \.mono$/, "Pass: the diagnostics are #424242 on #fafafa, 9.6:1."],
  [/^entity: color-contrast @ #entity-toml$/, "Pass: the entity file's text is #242424 on #fafafa, 14.9:1 (axe sees the box as partly covered)."],
];
const judged = (key) => JUDGED.find(([re]) => re.test(key))?.[1];

// Every page on the rail is found at run time; these open the screens that aren't rail pages.
const EXTRA = [
  { id: "notebook", name: "Notebook", open: (p) => p.evaluate(([cells, lh]) => {
    loadIntoState({ name: "Daily sales", cells, lakehouse: lh }); go("notebook");
  }, [CELLS, LH]), ready: ".cell .cm-content" },
  { id: "sql", name: "SQL query", open: (p) => p.evaluate((lh) => openSqlQuery(lh), LH), ready: ".sql-edit .cm-editor" },
  { id: "command-bar", name: "Command bar (Ctrl+K)", open: (p) => p.keyboard.press("Control+k"), ready: "#palette", scope: "#palette" },
  { id: "entity", name: "Data mesh entity", open: (p) => p.evaluate(() => {
    state.meta.project ||= { path: "C:/mesh", name: "Mesh", environments: {} };  // an open project, without changing the Data mesh page's audit
    openEntity("silver.customers");
  }), ready: "#entity-toml" },
  { id: "shell", name: "Shell (top bar and rail)", open: (p) => p.evaluate(() => go("home")), ready: "#rail", scope: "header.topbar, #rail" },
  { id: "confirm", name: "Confirm dialog", open: (p) => p.evaluate(() => {
    confirmDialog("Delete the lakehouse LH_Bronze?", "This removes its tables and files from Fabric.", "Delete", { danger: true });
  }), ready: "#dialog-root .dialog", scope: "#dialog-root" },
  // #1284: Preview screens and the dialogs people meet most, with the real engine where they read data.
  { id: "profile", name: "Profile", open: (p) => p.evaluate(() => { profilePage.tab = "overview"; go("profile"); }), ready: "#view .page.profile" },
  { id: "learn-lab", name: "Learn lab", open: (p) => p.evaluate(async () => {
    await rpc("learn_lab_start", { module_id: "LM-01" });  // the lab's data on this PC, as Start the lab makes it
    await learnOpen("LM-01");
  }), ready: "textarea.learn-editor" },
  { id: "settings-integrations", name: "Settings > Integrations", open: (p) => p.evaluate(async () => {
    await rpc("integrations_set_enabled", { enabled: true });
    state.settingsTab = "integrations";
    go("settings");
  }), ready: "#view .integrations section.card + section.card" },
  { id: "approval", name: "AI tool approval dialog", open: (p) => p.evaluate(() => {
    showApproval({ id: "ap_1", client_name: "Codex", title: "Run Python", permission: "EXECUTE_LOCAL_CODE", created_at: Date.now() / 1000,
      summary: { action: "Run Python on this PC", target: { workspace_id: "w1" }, content: { text: "print(1)" } } }, null);
  }), ready: "#dialog-root .dialog", scope: "#dialog-root" },
  { id: "report-problem", name: "Report a problem dialog", open: (p) => p.evaluate(() => reportProblem()),
    ready: "#dialog-root .dialog", scope: "#dialog-root" },
];

let app;
const results = [];

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["app_settings", "profile_get", "learn_catalog", "learn_module", "learn_lab_start", "learn_patterns", "integrations_status",
      "integrations_activity", "integrations_pending", "integrations_set_enabled", "editor_status"],
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
      meta_entity: () => ({ name: "silver.customers", layer: "silver", mode: "merge", watermark: "updated_at", owner: "ana@contoso.com",
        path: "metadata/entities/silver.customers.toml", text: "[entity]\nname = \"silver.customers\"\n", errors: [],
        columns: { id: "bigint", name: "string" }, keys: ["id"], surrogate_keys: [], derived: [], hook_adds: [], runs: [], custom: [],
        checks: 2, reads: ["bronze.customers"], impact: [], source: null }),
      meta_runs_all: () => ({ runs: [] }),
      meta_entity_shortcuts: () => [],
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
  const roots = document.querySelectorAll(scope);
  const css = (el) => {
    if (el.id) return `#${el.id}`;
    const cls = [...el.classList].slice(0, 2).map((c) => `.${c}`).join("");
    const text = (el.getAttribute("aria-label") || el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 30);
    return `${el.tagName.toLowerCase()}${cls}${text ? ` "${text}"` : ""}`;
  };
  const focusable = ["button", "a[href]", "input", "select", "textarea", "summary", "[tabindex]", "[contenteditable=true]", ".cm-editor"]
    .map((s) => `${s}:not([tabindex="-1"])`).join(", ");
  // A mouse-only control the keyboard reaches another way: out of the Tab order and hidden from assistive
  // tech, beside a Tab stop that names its key (a tab's ×, closed with Delete on the tab).
  const keyedElsewhere = (el) => el.closest("[aria-hidden=true]") && el.closest("[tabindex='-1']") &&
    [...(el.parentElement?.querySelectorAll("[aria-keyshortcuts]") || [])].some((k) => k.tabIndex >= 0 && k !== el);
  const unreachable = [];
  for (const el of [...roots].flatMap((r) => [...r.querySelectorAll("*")])) {
    const rect = el.getBoundingClientRect();
    if (!rect.width || !rect.height || getComputedStyle(el).cursor !== "pointer") continue;
    if (el.parentElement && getComputedStyle(el.parentElement).cursor === "pointer") continue;  // inherited: judged at the top
    // A label counts through the control it labels; a container through a Tab stop inside it (segmented
    // controls draw the pointer on the whole group). A page can't see click listeners, so a pointer card
    // that does something of its own beside the button it holds isn't caught here.
    const label = el.closest("label");
    if (el.closest(focusable) || el.querySelector(focusable) || (label?.control && label.control.tabIndex >= 0) || keyedElsewhere(el)) continue;
    unreachable.push(css(el));
  }
  return { unreachable };
}

// Runs in the page before a Tab walk: each element's outline and box shadow with focus elsewhere, so a
// ring counts only where focus changed it (a dialog's own shadow isn't a focus ring).
function ringBaseline() {
  const look = (n) => { const cs = getComputedStyle(n); return `${cs.outlineStyle} ${cs.outlineWidth} ${cs.outlineColor}|${cs.boxShadow}`; };
  window.__a11yLook = look;
  // Not what holds focus now (the page may have put it somewhere): its look then is already focused.
  const holder = document.activeElement !== document.body ? document.activeElement : null;
  window.__a11yBase = new WeakMap([...document.querySelectorAll("*")].filter((n) => !holder || !n.contains(holder)).map((n) => [n, look(n)]));
}

// Runs in the page after a Tab: where focus is, whether it's inside `scope`, and whether it shows a
// ring: an outline or box shadow on it or a close parent (as :focus-within styles draw it) that it
// didn't have with focus elsewhere.
async function focusNow(scope) {
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));  // a ring that eases in has drawn
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const ring = (n) => {
    const cs = getComputedStyle(n);
    const has = (cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0) || (cs.boxShadow && cs.boxShadow !== "none");
    // An element with no baseline (drawn since the walk began) counts its own ring only, not a parent's.
    const base = window.__a11yBase?.get(n);
    return has && (base === undefined ? n === el : base !== window.__a11yLook(n));
  };
  let shown = false;
  for (let n = el, depth = 0; n && depth < 6 && !shown; n = n.parentElement, depth++) shown = ring(n);
  const rect = el.getBoundingClientRect();
  const label = (el.getAttribute("aria-label") || el.textContent || el.value || "").trim().replace(/\s+/g, " ").slice(0, 30);
  const cls = [...el.classList].slice(0, 2).map((c) => `.${c}`).join("");
  el.dataset.a11yStop ||= String(Math.random());
  return { key: el.id ? `#${el.id}` : `${el.tagName.toLowerCase()}${cls}${label ? ` "${label}"` : ""}`, stop: el.dataset.a11yStop,
    inside: !!el.closest(scope), editor: el.matches(".cm-content, [data-esc-tab]"), shown: shown && rect.width > 0 && rect.height > 0 && getComputedStyle(el).opacity !== "0" };
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
  await page.evaluate(ringBaseline);
  for (let i = 0; i < 400; i++) {
    await page.keyboard.press("Tab");
    const at = await page.evaluate(focusNow, scope);
    if (!at) break;
    if (seen.has(at.stop)) {
      const left = await page.evaluate(([scope, seenIds]) => [...document.querySelectorAll(`:is(${scope}) :is(button, a[href], input, select, textarea, summary, [tabindex], .cm-content):not([tabindex="-1"])`)]
        .filter((e) => !e.disabled && e.checkVisibility({ visibilityProperty: true }) && !e.closest("[inert], [hidden]"))  // a closed <details> menu's items aren't shown
        .filter((e) => !seenIds.includes(e.dataset.a11yStop)).length, [scope, [...seen]]);
      if (left) trap = { key: at.key, left };
      break;
    }
    seen.add(at.stop);
    // A page still drawing can replace the control under focus or restyle it a frame late: read once more
    // before calling a ring missing.
    if (at.inside && !at.shown) { await page.waitForTimeout(150); at.shown = (await page.evaluate(focusNow, scope))?.shown ?? false; }
    if (at.inside) stops.push({ key: at.key, shown: at.shown });
    if (at.editor) await page.keyboard.press("Escape");
  }
  return { stops, trap };
}

// Until the page has made no engine call for 200 ms (at most 3 s): what it loads has drawn.
async function settle(page) {
  let last = -1;
  for (let t = 0; t < 3000; t += 200) {
    const n = app.calls.length;
    if (n === last) return;
    last = n;
    await page.waitForTimeout(200);
  }
}

async function audit(page, target, scheme) {
  // Reduced motion: a page fading in would be judged on its half-drawn colors.
  await page.emulateMedia({ colorScheme: scheme, reducedMotion: "reduce" });
  await page.evaluate(() => { document.getElementById("palette")?.remove(); closeDialog(); go("home"); });
  await target.open(page);
  if (target.ready) await page.waitForSelector(target.ready);
  await settle(page);  // the page's own calls answer
  await animationsDone(page);  // axe judges contrast as drawn: nothing still fading in (#1337)
  if (!(await page.evaluate(() => typeof axe === "object"))) await page.addScriptTag({ content: axeSource });
  const scope = target.scope || "#view";
  const axeOut = await page.evaluate(async ([scope, tags]) => {
    const res = await axe.run({ include: [...document.querySelectorAll(scope)] }, { runOnly: { type: "tag", values: tags } });
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
        const fresh = v.nodes.filter((n) => !KNOWN_RULES.has(`${r.id}: ${v.id} @ ${n}`));
        if (fresh.length) failures.push(`${r.page} (${scheme}): ${v.id} ${v.impact} at ${fresh.join(", ")}`);
      }
    }
  }
  assert.ok(results.length >= 2 * (EXTRA.length + 5), `audited ${results.length} page views`);
  if (process.env.AXE_CORE_VERSION) assert.strictEqual(rowsVersion(), process.env.AXE_CORE_VERSION, "the axe-core that ran is the one ci.yml pins");
  assert.deepStrictEqual(failures, []);
  const seen = new Set(results.flatMap((r) => r.violations.flatMap((v) => v.nodes.map((n) => `${r.id}: ${v.id} @ ${n}`))));
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

test("every check axe couldn't decide has a person's verdict", { skip }, async () => {
  const open = results.filter((r) => r.scheme === "light").flatMap((r) => r.incomplete.flatMap((v) =>
    v.nodes.filter((n) => !judged(`${r.id}: ${v.id} @ ${n}`)).map((n) => `${r.id}: ${v.id} @ ${n}`)));
  assert.deepStrictEqual(open, [], "judge these and add them to JUDGED");
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
      h("button", { class: "a11y-bare", style: "outline:none" }),
      // A button with no ring of its own inside a container that always has a shadow, as a dialog has.
      h("div", { style: "box-shadow:0 4px 16px rgba(0,0,0,.2);padding:8px" }, h("button", { class: "a11y-shadowed", style: "outline:none" }, "Shadowed")));
  }) }, "light");
  results.length = before;  // not part of the report
  const ids = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id);
  for (const id of ["image-alt", "color-contrast", "button-name"]) assert.ok(ids.includes(id), `${id} in ${ids.join(", ")}`);
  assert.ok(r.unreachable.includes("#a11y-injected"), r.unreachable.join(", "));
  assert.ok(r.noRing.some((k) => k.startsWith("button.a11y-bare")), r.noRing.join(", "));
  assert.ok(r.noRing.some((k) => k.startsWith("button.a11y-shadowed")), `a container's own shadow isn't a ring: ${r.noRing.join(", ")}`);
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
  const looks = rows.filter((r) => r.scheme === "light").flatMap((r) => r.incomplete.map((v) => {
    const verdicts = [...new Set(v.nodes.map((n) => judged(`${r.id}: ${v.id} @ ${n}`) || "Not judged yet."))];
    return `- **${r.page}**: ${v.id}: ${v.help}. At ${v.nodes.map((n) => `\`${n}\``).join(", ")}. ${verdicts.join(" ")}`;
  }));
  lines.push("", "## Findings", "", "Filed to be fixed in [#905](https://github.com/KratosDataGod/KratosForge/issues/905); the test fails on any serious or critical rule violation, or keyboard or focus finding, not listed here.", "",
    ...(findings.length ? findings : ["None."]));
  lines.push("", "## Needs a look", "",
    "Checks axe couldn't decide by itself (for example a glyph, a line number, or text over a gradient or an image), each with a person's verdict and the measured contrast.", "",
    ...(looks.length ? looks : ["None."]), "");
  return lines.join("\n");
}

function rowsVersion() {
  const m = axeSource && axeSource.match(/axe v([\d.]+)/);
  return m ? m[1] : "?";
}
