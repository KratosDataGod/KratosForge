// UX-012: menus, clickable rows and tabs work from the keyboard alone (see harness.cjs for how to run it).
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

let app;
const skip = !playwright && "Playwright is not installed";
const page = () => app.page;
const focused = () => page().evaluate(() => document.activeElement?.id || document.activeElement?.textContent || "");

before(async () => {
  if (skip) return;
  app = await openApp();
  await page().waitForSelector("#rail-main .rail-item");
});

after(async () => { await app?.close(); });

test("a menu opens on its first item, arrows move, Escape closes it and returns to its button", { skip }, async () => {
  await page().evaluate(() => {
    const anchor = document.getElementById("account");
    anchor.focus();
    showMenu(anchor, [{ label: "First", action: () => { window.picked = "First"; } }, { info: "A note" },
      { label: "Second", action: () => { window.picked = "Second"; } }]);
  });
  assert.strictEqual(await page().getAttribute("#menu", "role"), "menu");
  assert.strictEqual(await focused(), "First");
  await page().keyboard.press("ArrowDown");
  assert.strictEqual(await focused(), "Second");  // the note is skipped
  await page().keyboard.press("ArrowDown");
  assert.strictEqual(await focused(), "First");  // wraps
  await page().keyboard.press("End");
  await page().keyboard.press("Escape");
  assert.strictEqual(await page().locator("#menu").count(), 0);
  assert.strictEqual(await focused(), "account");
  assert.strictEqual(await page().getAttribute("#account", "aria-expanded"), "false");

  await page().evaluate(() => showMenu(document.getElementById("account"), [{ label: "Only", action: () => { window.picked = "Only"; } }]));
  await page().keyboard.press("Enter");
  assert.strictEqual(await page().evaluate(() => window.picked), "Only");
  assert.strictEqual(await page().locator("#menu").count(), 0);
});

test("a clickable row is reached with Tab and opened with Enter or Space", { skip }, async () => {
  await page().evaluate(() => {
    window.opened = [];
    const row = (name) => h("tr", { class: "click", onclick: () => window.opened.push(name) }, h("td", {}, name));
    document.getElementById("view").replaceChildren(h("table", { class: "list" }, h("tbody", {}, row("orders"), row("customers"))),
      h("table", {}, h("tbody", {}, h("tr", {}, h("td", {}, "not clickable")))));
  });
  assert.deepStrictEqual(await page().$$eval("#view tr", (rows) => rows.map((r) => r.getAttribute("tabindex"))), ["0", "0", null]);
  await page().focus("#view tr.click");
  await page().keyboard.press("Enter");
  await page().keyboard.press("Tab");
  await page().keyboard.press(" ");
  assert.deepStrictEqual(await page().evaluate(() => window.opened), ["orders", "customers"]);
});

test("arrow keys move between tabs and select them, even when selecting re-renders the list", { skip }, async () => {
  await page().evaluate(() => {
    let on = 0;
    const draw = () => document.getElementById("view").replaceChildren(h("div", { role: "tablist" },
      ["Runs", "Schedules", "Cost"].map((t, i) => h("button", { role: "tab", "aria-selected": String(i === on),
        onclick: () => { on = i; draw(); } }, t))));
    draw();
    document.querySelector("[role=tab]").focus();
  });
  const selected = () => page().$eval("[role=tab][aria-selected=true]", (t) => t.textContent);
  await page().keyboard.press("ArrowRight");
  assert.strictEqual(await selected(), "Schedules");
  assert.strictEqual(await focused(), "Schedules");
  await page().keyboard.press("ArrowLeft");
  await page().keyboard.press("ArrowLeft");
  assert.strictEqual(await selected(), "Cost");  // wraps
  await page().keyboard.press("Home");
  assert.strictEqual(await focused(), "Runs");
});

test("the rail marks the page you're on", { skip }, async () => {
  const current = await page().$$eval("#rail-main [aria-current=page]", (els) => els.map((e) => e.textContent));
  assert.deepStrictEqual(current, ["Home"]);
  assert.deepStrictEqual(app.errors, []);
});
