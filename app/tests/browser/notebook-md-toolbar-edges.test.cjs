// Markdown toolbar and preview (nbmarkdown.js, N23, Preview): the edges a Preview-exit review found
// untested.
//   - The View choice (Edit, Split or Preview) is kept in localStorage under the notebook's name
//     (fd.mdview.<name>). Names with quotes, slashes and backslashes, accented and CJK letters, emoji, a
//     500-character name, and names that differ only in case or a trailing space each keep their own
//     choice, none overwrites another's, and nothing throws.
//   - localStorage that throws on every read and write (a locked-down WebView2): the app opens, the
//     toolbar and all three views still work, there are no uncaught errors, and the choice lasts only
//     while the notebook is open; reopening it starts on Split again.
//   - Two notebooks of one name in different notebooks repositories (UX review, minor): each keeps its
//     own View choice; a notebook in the app's own folder keeps the key it had (fd.mdview.<name>).
//   - A narrow cell (UX review, must fix 1): in Split, the Edit and Preview tabs take the View switch's
//     place, so there is one Edit and one Preview control, never both rows; Edit and Preview views
//     keep the switch and show no tabs; the toolbar's Tab stop stays on a button that is shown. At a
//     700px window with the explorer open and at 390px.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const MOCKS = { save_notebook: () => ({ saved: true }), notebook_syntax: () => [] };
let app;
let locked;  // a second window whose localStorage throws

const md = (p) => p.locator(".cell.markdown").first();
const bar = (p) => md(p).getByRole("toolbar", { name: "Markdown formatting" });
const viewBtn = (p, name) => bar(p).getByRole("button", { name, exact: true });
const pressed = (p) => bar(p).locator("[aria-pressed=true]").innerText();

// Opens a notebook named `name` with one Markdown cell being edited, as notebook-md-toolbar does.
async function open(p, name, text = "Hello **there**", place = null) {
  await p.evaluate(({ name, text, place }) => {
    state.settings = { ...(state.settings || {}), show_preview: { ...(state.settings?.show_preview || {}), value: true } };
    loadIntoState({ name, place, cells: [{ type: "markdown", cell_id: "m1", source: text, meta: {} },
      { type: "code", cell_id: "c1", source: "x = 1", meta: {} }] });
    go("notebook");
    const cell = state.notebook.cells[0];
    cell.editing = true;
    renderNotebook();
    editors.get(cell.id).focus();
  }, { name, text, place });
  await bar(p).waitFor();  // the editor is hidden in Preview view
}

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: MOCKS });
  locked = await openApp({ mocks: MOCKS });
  // Every read and write of localStorage throws from the first script on, as when WebView2's storage is
  // blocked by policy. The harness's own init script (its seed values) runs before this one is added.
  await locked.page.addInitScript(() => {
    const no = () => { throw new DOMException("The operation is insecure.", "SecurityError"); };
    Storage.prototype.getItem = no;
    Storage.prototype.setItem = no;
    Storage.prototype.removeItem = no;
  });
  locked.errors.length = 0;
  await locked.page.reload();
  await locked.page.waitForFunction(() => typeof openSqlQuery === "function");
  // With nothing remembered, the one-time KratosForge splash shows at every start (settings.js
  // showRenameSplash says so); close it as a person would, then whatever waits behind it.
  await locked.page.getByRole("button", { name: "Enter the forge" }).click();
  await locked.page.waitForTimeout(300);
  await locked.page.evaluate(() => { if (document.getElementById("dialog-root").childElementCount) closeDialog(); });
});
after(async () => { await app?.close(); await locked?.close(); });

const NAMES = [
  ["Notes", "edit"],
  ["notes", "preview"],          // differs only in case
  ["Notes ", "split"],           // differs only in a trailing space; split written, not the default
  [`Q4 "final" 'plan'`, "preview"],
  ["sales/2026\\west", "edit"],
  ["Résumé — 数据 notebook", "preview"],
  ["📊 Sales 🚀 review", "edit"],
  ["Long ".repeat(100).trim(), "preview"],  // 499 characters
  ["</script><b>x</b>", "edit"],
  ["]\"'`${}[", "preview"],
];

test("each notebook name keeps its own View choice, with odd characters, case and a trailing space", { skip }, async () => {
  const p = app.page;
  app.errors.length = 0;
  await p.evaluate((names) => names.forEach(([n]) => localStorage.removeItem(`fd.mdview.${n}`)), NAMES);
  // Every name starts on the default.
  for (const [name] of NAMES) {
    await open(p, name);
    assert.strictEqual(await pressed(p), "Split", `${JSON.stringify(name)} starts on Split`);
  }
  // Each picks its own; a later pick would overwrite an earlier one if two names shared a key.
  for (const [name, view] of NAMES) {
    await open(p, name);
    const label = view[0].toUpperCase() + view.slice(1);
    await viewBtn(p, label).click();
    assert.strictEqual(await pressed(p), label);
    assert.strictEqual(await md(p).locator(".md-rich").getAttribute("data-view"), view);
  }
  const stored = await p.evaluate((names) => names.map(([n]) => localStorage.getItem(`fd.mdview.${n}`)), NAMES);
  assert.deepStrictEqual(stored, NAMES.map(([, v]) => v), "one key per name, exactly as typed");
  // Reopened in another order, each comes back with its own.
  for (const [name, view] of [...NAMES].reverse()) {
    await open(p, name);
    assert.strictEqual(await md(p).locator(".md-rich").getAttribute("data-view"), view, `${JSON.stringify(name)} remembered`);
    // The panes follow the choice.
    assert.strictEqual(await md(p).locator(".md-edit").isVisible(), view !== "preview", `${JSON.stringify(name)} editor`);
    assert.strictEqual(await md(p).locator(".md-preview").isVisible(), view !== "edit", `${JSON.stringify(name)} preview`);
  }
  assert.deepStrictEqual(app.errors, []);
  assert.strictEqual(await p.locator("#toast").filter({ hasText: "Something didn't work" }).count(), 0);
  await p.evaluate((names) => names.forEach(([n]) => localStorage.removeItem(`fd.mdview.${n}`)), NAMES);
});

test("a stored value that isn't a view, for a name, falls back to Split", { skip }, async () => {
  const p = app.page;
  for (const junk of ["", "EDIT", "split ", "null", "{\"v\":1}"]) {
    await p.evaluate((v) => localStorage.setItem("fd.mdview.Junk 🧪", v), junk);
    await open(p, "Junk 🧪");
    assert.strictEqual(await pressed(p), "Split", JSON.stringify(junk));
  }
  await p.evaluate(() => localStorage.removeItem("fd.mdview.Junk 🧪"));
  assert.deepStrictEqual(app.errors, []);
});

test("with localStorage throwing, the app opens and the toolbar and views work, and the choice isn't kept", { skip }, async () => {
  const p = locked.page;
  assert.strictEqual(await p.evaluate(() => { try { localStorage.getItem("x"); return "read"; } catch { return "threw"; } }), "threw");
  await open(p, "Plan", "say hi");
  assert.strictEqual(await pressed(p), "Split", "the default");
  // The formatting buttons edit the source.
  await p.evaluate(() => editors.get(state.notebook.cells[0].id).dispatch({ selection: { anchor: 4, head: 6 } }));
  await bar(p).getByRole("button", { name: "Bold", exact: true }).click();
  assert.strictEqual(await p.evaluate(() => state.notebook.cells[0].source), "say **hi**");
  await md(p).locator(".md-preview strong", { hasText: "hi" }).waitFor();
  // Each view shows its panes.
  for (const [label, edit, prev] of [["Preview", false, true], ["Edit", true, false], ["Split", true, true], ["Preview", false, true]]) {
    await viewBtn(p, label).click();
    assert.strictEqual(await pressed(p), label);
    assert.strictEqual(await md(p).locator(".md-edit").isVisible(), edit, `${label}: editor`);
    assert.strictEqual(await md(p).locator(".md-preview").isVisible(), prev, `${label}: preview`);
  }
  assert.strictEqual(await bar(p).getByRole("button", { name: "Bold", exact: true }).isDisabled(), true);
  // While the notebook stays open, the choice holds through a redraw and the next edit of the cell.
  await p.evaluate(() => renderNotebook());
  assert.strictEqual(await pressed(p), "Preview");
  await viewBtn(p, "Preview").focus();  // the redraw drew a new toolbar
  await p.keyboard.press("Escape");
  await md(p).locator(".md-view strong", { hasText: "hi" }).waitFor();
  assert.strictEqual(await p.getByRole("toolbar", { name: "Markdown formatting" }).count(), 0);
  await p.evaluate(() => { state.notebook.cells[0].editing = true; renderNotebook(); });
  await bar(p).waitFor();
  assert.strictEqual(await pressed(p), "Preview", "this session's choice");
  // Ctrl+B still formats in the editor.
  await viewBtn(p, "Edit").click();
  await p.evaluate(() => { const v = editors.get(state.notebook.cells[0].id); v.focus(); v.dispatch({ selection: { anchor: 0, head: 3 } }); });
  await p.keyboard.press("Control+b");
  assert.strictEqual(await p.evaluate(() => state.notebook.cells[0].source), "**say** **hi**");
  // Reopened, the notebook starts on Split: nothing was kept.
  await open(p, "Plan");
  assert.strictEqual(await pressed(p), "Split");
  assert.deepStrictEqual(locked.errors, [], "no uncaught errors");
  assert.strictEqual(await p.locator("#toast").filter({ hasText: "Something didn't work" }).count(), 0);
});

test("two notebooks of one name in different repositories keep their own View choice", { skip }, async () => {
  const p = app.page;
  const a = { root: "C:\\repos\\sales", label: "sales" };
  const b = { root: "C:\\repos\\finance", label: "finance" };
  await p.evaluate(() => { for (let i = localStorage.length - 1; i >= 0; i -= 1) if (localStorage.key(i).startsWith("fd.mdview.")) localStorage.removeItem(localStorage.key(i)); });
  await open(p, "Plan", "x", a);
  await viewBtn(p, "Preview").click();
  await open(p, "Plan", "x", b);
  assert.strictEqual(await pressed(p), "Split", "the other repository's Plan has its own");
  await viewBtn(p, "Edit").click();
  await open(p, "Plan", "x");
  assert.strictEqual(await pressed(p), "Split", "and so does the app folder's Plan");
  await open(p, "Plan", "x", a);
  assert.strictEqual(await pressed(p), "Preview");
  await open(p, "Plan", "x", b);
  assert.strictEqual(await pressed(p), "Edit");
  // The app folder's notebook keeps the key it had before, so its choice carries over.
  await p.evaluate(() => localStorage.setItem("fd.mdview.Plan", "preview"));
  await open(p, "Plan", "x");
  assert.strictEqual(await pressed(p), "Preview");
  await p.evaluate(() => { for (let i = localStorage.length - 1; i >= 0; i -= 1) if (localStorage.key(i).startsWith("fd.mdview.")) localStorage.removeItem(localStorage.key(i)); });
});

// The controls a person sees named Edit, Split and Preview in the cell, and whether the switch shows.
const shownControls = (p) => md(p).evaluate((cell) => {
  const shown = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden";
  const names = [...cell.querySelectorAll(".md-rich button")].filter(shown).map((b) => b.textContent.trim())
    .filter((t) => ["Edit", "Split", "Preview"].includes(t));
  return { names, switch: shown(cell.querySelector(".md-switch")), tabs: shown(cell.querySelector(".md-tabs")),
    width: cell.querySelector(".md-rich").getBoundingClientRect().width };
});

for (const [w, h, explorer] of [[700, 900, true], [390, 844, false]]) {
  test(`a narrow cell at ${w}px${explorer ? " with the explorer" : ""}: Split is the Edit and Preview tabs, with no View switch beside them`, { skip }, async () => {
    const p = app.page;
    await p.evaluate(() => localStorage.removeItem("fd.mdview.Narrow"));
    await p.setViewportSize({ width: w, height: h });
    try {
      await open(p, "Narrow", "Hello **there**");
      await p.evaluate((explorer) => { state.showExplorer = explorer; renderNotebook(); }, explorer);
      await md(p).locator(".md-rich").waitFor();
      let c = await shownControls(p);
      assert.ok(c.width <= 620, `cell is ${c.width}px`);
      assert.deepStrictEqual(c, { ...c, names: ["Edit", "Preview"], switch: false, tabs: true }, "one Edit and one Preview: the tabs");
      // The tabs pick the pane, and the toolbar's one Tab stop is a button that shows.
      await md(p).getByRole("tab", { name: "Preview" }).click();
      assert.strictEqual(await md(p).locator(".md-preview").isVisible(), true);
      assert.strictEqual(await md(p).locator(".md-edit").isVisible(), false);
      const stop = await bar(p).evaluate((t) => [...t.querySelectorAll("button")].filter((b) => b.tabIndex === 0).map((b) => [b.textContent, b.getClientRects().length > 0]));
      assert.deepStrictEqual(stop, [["B", true]]);
      // Arrows along the toolbar skip the hidden switch: End lands on Table.
      await bar(p).getByRole("button", { name: "Bold", exact: true }).focus();
      await p.keyboard.press("End");
      assert.strictEqual(await p.evaluate(() => document.activeElement.title), "Table");
      // A view other than Split keeps the switch, with no tabs, so a name still shows once.
      await p.evaluate(() => setMdView(state.notebook, "edit"));
      c = await shownControls(p);
      assert.deepStrictEqual(c, { ...c, names: ["Edit", "Split", "Preview"], switch: true, tabs: false });
      // Split picked there: the switch gives way to the tabs and focus goes to the tab that shows.
      await viewBtn(p, "Split").click();
      c = await shownControls(p);
      assert.deepStrictEqual(c, { ...c, names: ["Edit", "Preview"], switch: false, tabs: true });
      assert.strictEqual(await p.evaluate(() => document.activeElement.getAttribute("role")), "tab");
      assert.deepStrictEqual(app.errors, []);
    } finally {
      await p.setViewportSize({ width: 1440, height: 940 });
      await p.evaluate(() => { state.showExplorer = true; renderNotebook(); localStorage.removeItem("fd.mdview.Narrow"); });
    }
    // Wide again: the switch, and no tabs.
    await md(p).locator(".md-rich").waitFor();
    const c = await shownControls(p);
    assert.deepStrictEqual(c, { ...c, names: ["Edit", "Split", "Preview"], switch: true, tabs: false });
  });
}
