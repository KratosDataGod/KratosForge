// The Variables pane's table (nbvars.js), the paths notebook-variables leaves out: sorting by type,
// a click on a row adding a cell that displays it, and opening a variable the kernel can't read,
// one that is gone, one hidden as a secret, a long list shown in part, and one with nothing inside.
// The table is drawn into a box with the kernel's replies mocked; what the person sees is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const ROWS = [
  { name: "orders", type: "DataFrame", size: "3 x 2", memory: 2048, value: "shape (3, 2)", expandable: true },
  { name: "ids", type: "list", size: "500", memory: 4096, value: "[1, 2, 3, ...]", expandable: true },
  { name: "gone", type: "dict", size: "2", memory: 256, value: "{...}", expandable: true },
  { name: "token", type: "str", size: "40", memory: 89, value: "***", expandable: true },
  { name: "empty", type: "set", size: "0", memory: 216, value: "set()", expandable: true },
  { name: "a", type: "int", size: "", memory: 28, value: "1", expandable: false },
];
const DETAIL = {
  orders: () => { throw new Error("The kernel is busy running a cell."); },
  ids: () => ({ name: "ids", items: [{ key: "0", type: "int", value: "1" }, { key: "1", type: "int", value: "2" }], total: 500 }),
  gone: () => ({ name: "gone", missing: true }),
  token: () => ({ name: "token", hidden: true }),
  empty: () => ({ name: "empty" }),
};

let app;
const page = () => app.page;
const box = () => page().locator("#t-box");

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: { kernel_variable_detail: (p) => DETAIL[p.name]() } });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  await page().evaluate((rows) => {
    Object.assign(nbVars, { filter: "", sort: "name", desc: false, rows: null });
    nbVars.open.clear(); nbVars.detail.clear();
    document.getElementById("t-box")?.remove();
    const b = document.createElement("div"); b.id = "t-box"; document.body.append(b);
    b.append(variableTable(rows));
  }, ROWS);
  app.errors.length = 0;
});

const names = () => box().locator("tbody tr:not(.vars-detail) td.name").allInnerTexts();

test("Type sorts by type, then by name; clicking a row adds a cell that displays it", { skip }, async () => {
  await box().getByRole("button", { name: /^Type/ }).click();
  assert.deepStrictEqual(await names(), ["orders", "gone", "a", "ids", "empty", "token"]);
  assert.strictEqual(await box().locator("th", { hasText: "Type" }).getAttribute("aria-sort"), "ascending");
  await page().evaluate(() => { window.__insertCell = window.insertCell; window.__inserted = []; window.insertCell = (c) => window.__inserted.push(c); });
  await box().locator("tbody tr", { hasText: "ids" }).locator("td.val").click();
  assert.deepStrictEqual(await page().evaluate(() => { window.insertCell = window.__insertCell; return window.__inserted; }), ["display(ids)"]);
  assert.deepStrictEqual(app.errors, []);
});

test("opening a variable says when it can't be read, is gone, is a secret, is shown in part, or has nothing inside", { skip }, async () => {
  const open = async (name, text) => {
    await box().getByRole("button", { name: `Open ${name}` }).click();
    await box().locator(".vars-detail").getByText(text).waitFor();
    await box().getByRole("button", { name: `Close ${name}` }).click();
    await box().locator(".vars-detail").waitFor({ state: "detached" });
  };
  await open("orders", "Couldn't read it: The kernel is busy running a cell.");
  await open("gone", "This variable is gone. Refresh the pane.");
  await open("token", "Hidden: it looks like a secret.");
  await open("ids", "Showing the first 2 of 500.");
  await open("empty", "Nothing more to show.");
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "kernel_variable_detail").map((c) => c.params.name),
    ["orders", "gone", "token", "ids", "empty"]);
  assert.deepStrictEqual(app.errors, []);
});
