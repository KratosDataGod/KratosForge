// The notebook's Clean view (wrangler.js) beyond output-views.test.cjs: the forms for changing a type,
// text case, find and replace, splitting text and dropping missing rows send exactly what was chosen; a
// step the engine can't run is marked with its reason and the code can't be added; a preview of part of a
// large result says so; and Copy code copies the code shown. notebook_frame_wrangle is mocked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

const COLUMNS = ["region", "amount", "name"];
const TYPES = ["string", "integer", "string"];
const ROWS = [["East", 120, " ann "], ["West", 340, "bo,b"], ["East", null, "cy"]];
const output = () => ({ type: "table", columns: COLUMNS, types: TYPES, rows: ROWS, total_rows: 3, frame: "f1" });

// wrangler.run's answer: a step that casts text to an integer fails, as Polars would.
function wrangle({ frame, steps, name }) {
  const bad = steps.findIndex((s) => s.op === "cast" && s.to === "integer");
  return { frame, columns: COLUMNS, types: TYPES, rows: ROWS.slice(0, 2), rows_before: 50000, rows_after: 48000, sampled: true,
    changed: bad < 0 && steps.length ? ["name"] : [], steps: steps.map((s) => `${s.op} step`),
    error: bad < 0 ? null : { step: bad, message: "Can't make 'region' an integer: 'East' isn't a number." },
    code: `${name}_clean = clean_data(${name})  # ${steps.length} steps\n` };
}

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    notebook_frame_wrangle: wrangle,
    notebook_dependencies: () => ({ cells: [] }),
    save_notebook: () => ({ saved: true }),
    kernel_variables: () => ({ variables: [] }),
  } });
  await page().evaluate(() => {
    loadIntoState({ name: "Clean views", cells: [{ type: "code", cell_id: "c1", meta: {}, source: "people = load()\npeople" }] });
    go("notebook");
    window.__copied = [];
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (t) => { window.__copied.push(t); } } });
  });
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  await page().evaluate((out) => {
    const c = state.notebook.cells[0];
    c.outputs = [out]; c.status = "ok"; c.count = 1;
    renderNotebook();
  }, output());
  await result().getByRole("button", { name: "Clean", exact: true }).click();
  await result().locator(".wrangle-preview .wrangle-code").waitFor();
});

const result = () => page().locator(".table-output").first();
const form = () => result().locator(".wrangle-add");
const lastSteps = () => app.calls.filter((c) => c.method === "notebook_frame_wrangle").at(-1).params.steps;
const addStep = async (op, fill) => {
  await form().getByRole("combobox", { name: "Add a step" }).selectOption(op);
  await fill();
  const n = app.calls.length;
  await form().getByRole("button", { name: "Add step" }).click();
  for (const end = Date.now() + 5000; !app.calls.slice(n).some((c) => c.method === "notebook_frame_wrangle"); await new Promise((r) => setTimeout(r, 20))) {
    if (Date.now() > end) throw new Error("the step was never previewed");
  }
  await result().locator(".wrangle-preview .wrangle-code").waitFor();
};

test("each step's form sends exactly what was chosen, and a sampled preview says how much it shows", { skip }, async () => {
  assert.match(await result().locator(".wrangle-preview .more").innerText(),
    /^50,000 rows before, 48,000 rows after\. Previewing the first 50,000 rows of the result\. Showing the first 2\.$/);
  await addStep("case", async () => {
    await form().getByRole("listbox", { name: "Columns" }).selectOption(["name"]);
    await form().getByRole("combobox", { name: "Case" }).selectOption("title");
  });
  await addStep("replace", async () => {
    await form().getByRole("listbox", { name: "Columns" }).selectOption(["name", "region"]);
    await form().getByRole("textbox", { name: "Find" }).fill(" ");
    await form().getByRole("textbox", { name: "Replace with" }).fill("_");
  });
  await addStep("split", async () => {
    await form().getByRole("combobox", { name: "Column" }).selectOption("name");
    assert.strictEqual(await form().getByRole("textbox", { name: "Split on" }).inputValue(), ",");
    await form().getByRole("spinbutton", { name: "Parts" }).fill("3");
  });
  await addStep("drop_missing", async () => {
    await form().getByRole("listbox", { name: "Columns (none means all)" }).waitFor();
  });
  assert.deepStrictEqual(lastSteps(), [
    { op: "case", columns: ["name"], to: "title" },
    { op: "replace", columns: ["region", "name"], find: " ", value: "_" },
    { op: "split", column: "name", value: ",", parts: 3 },
    { op: "drop_missing", columns: [] },
  ]);
  assert.match(await result().locator(".wrangle-preview .more").innerText(), /Changed columns are marked\./);
  assert.ok(await result().getByRole("button", { name: "Add code to notebook" }).isEnabled());
});

test("a step the engine can't run is marked with why, and its code can't be added", { skip }, async () => {
  await addStep("cast", async () => {
    await form().getByRole("listbox", { name: "Columns" }).selectOption(["region"]);
    await form().getByRole("combobox", { name: "New type" }).selectOption("integer");
  });
  assert.deepStrictEqual(lastSteps(), [{ op: "cast", columns: ["region"], to: "integer" }]);
  const bad = result().locator(".wrangle-step.bad");
  assert.strictEqual(await bad.getByRole("alert").innerText(), "Can't make 'region' an integer: 'East' isn't a number.");
  assert.ok(await result().getByRole("button", { name: "Add code to notebook" }).isDisabled());
  await bad.getByRole("button", { name: "Remove step 1" }).click();
  await result().locator(".wrangle-steps").getByText("No steps yet.").waitFor();
  assert.deepStrictEqual(lastSteps(), []);
});

test("Copy code copies the code shown", { skip }, async () => {
  await result().getByRole("button", { name: "Copy code" }).click();
  await page().waitForFunction(() => document.getElementById("toast").textContent === "Copied the code.");
  assert.deepStrictEqual(await page().evaluate(() => window.__copied), [await result().locator(".wrangle-code").innerText()]);
});
