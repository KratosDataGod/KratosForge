// The new notebook wizard (#1317), against the real engine (wizard_templates, wizard_preview and
// wizard_create in wizard.py and prove_service.py). Checks:
// - the templates list shows, and the group buttons and the search narrow it
// - on a template's questions, an empty required answer or an invalid one keeps the person on that
//   step with the engine's sentence
// - the cells the review shows are the cells of the notebook Create writes and opens, with the same
//   proof contract, and nothing is written to the notebooks folder before Create
// - a name already taken is refused and the first notebook's file is left as it was
// - Back keeps the answers, and Cancel writes nothing
// - axe finds nothing serious on each step
// It found three problems, fixed with it in wizard.js: the engine's exception name showed on the step,
// choosing the same template again after Back emptied the answers, and the review's scrolling cell
// list couldn't be reached by keyboard.
//
// The "Clean a table" template needs no lakehouse: with none attached the notebook says to add one
// once it opens, and nothing is read until a cell runs, so nothing here reaches Fabric.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings: harnessAxe } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const ENGINE = ["wizard_templates", "wizard_preview", "wizard_create"];
const CLEAN = "Clean a table";
const ANSWERS = { "Table to read (schema.table)": "dbo.orders", "Columns to keep (all when empty)": "order_id, amount",
  "Columns that identify a row": "order_id", "Table to write (schema.table)": "silver.orders",
  "Columns to ignore when a proof compares runs": "loaded_at" };

let app;
const page = () => app.page;
const dialog = () => page().getByRole("dialog", { name: "New notebook from a template" });
const button = (name) => dialog().getByRole("button", { name, exact: true });
const notebooksDir = () => path.join(app.engineHome, "FabricDesktop", "notebooks");
const calls = (method) => app.calls.filter((c) => c.method === method);

// Every file under the notebooks folder, with its bytes, so a change anywhere shows.
function snapshot(dir = notebooksDir(), out = {}) {
  if (!fs.existsSync(dir)) return out;
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) snapshot(p, out);
    else out[path.relative(notebooksDir(), p)] = fs.readFileSync(p, "utf8");
  }
  return out;
}

// The cells of a notebook file as {type, source}, as the engine wrote them.
function fileCells(name) {
  const nb = JSON.parse(fs.readFileSync(path.join(notebooksDir(), `${name}.ipynb`), "utf8"));
  return { cells: nb.cells.map((c) => ({ type: c.cell_type, source: [].concat(c.source).join("") })),
    proof: nb.metadata.fabric_desktop.proof };
}

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ENGINE, mocks: { readiness_check: () => [] } });
});

after(async () => { await app?.close(); });

// Opens the wizard fresh, as the Home tile and the command do.
async function open() {
  await page().evaluate(() => closeDialog());  // one left open by a test that failed
  await page().locator("#dialog-root .dialog").waitFor({ state: "detached" });
  await page().evaluate(() => openWizard());
  await dialog().waitFor();
  return dialog();
}

// The step the wizard shows, read from the steps list.
const step = () => dialog().locator('.wizard-steps [aria-current="step"]').innerText();

async function pick(title) {
  await dialog().locator(".wizard-template", { hasText: title }).click();
  await dialog().locator('.wizard-steps [aria-current="step"]', { hasText: "2. Inputs and output" }).waitFor();
}

async function answer(values) {
  for (const [label, value] of Object.entries(values)) await dialog().getByLabel(label, { exact: true }).fill(value);
}

// Next: review, then waits for the review step or for the problem the engine gave.
async function toReview() {
  const n = calls("wizard_preview").length;
  // A problem still showing from the last try is marked, so only one drawn after this preview counts.
  await page().evaluate(() => document.querySelector("#dialog-root .dialog .field-error")?.setAttribute("data-before", ""));
  await button("Next: review").click();
  const h = await page().waitForFunction(() => {
    const d = document.querySelector("#dialog-root .dialog");
    const now = d?.querySelector('.wizard-steps [aria-current="step"]')?.textContent;
    const problem = d?.querySelector(".field-error:not([data-before])")?.textContent;
    return now === "3. Review" ? { step: now } : problem ? { step: now, problem } : null;
  }, null, { timeout: 15000 }).catch(() => null);
  assert.ok(h, "the review or a problem showed");
  assert.strictEqual(calls("wizard_preview").length, n + 1, "one preview was asked for");
  return h.jsonValue();
}

test("the templates list shows, and the group filter and the search narrow it", { skip }, async () => {
  const d = await open();
  assert.strictEqual(await step(), "1. Template");
  const { templates, groups } = await page().evaluate(() => wizardState.templates);
  const titles = () => d.locator(".wizard-template b").allInnerTexts();
  assert.deepStrictEqual(await titles(), templates.map((t) => t.title), "every template shows");
  const all = d.getByRole("group", { name: "Template groups" }).getByRole("button", { name: /^All templates/ });
  assert.strictEqual(await all.getAttribute("aria-pressed"), "true");
  assert.match(await all.innerText(), new RegExp(`All templates\\s*${templates.length}`));

  // A group shows only its templates, and its button says how many.
  const model = d.getByRole("group", { name: "Template groups" }).getByRole("button", { name: new RegExp(`^${groups.model}`) });
  await model.click();
  const inModel = templates.filter((t) => t.group === "model").map((t) => t.title);
  assert.ok(inModel.length > 1 && inModel.length < templates.length);
  assert.deepStrictEqual(await titles(), inModel);
  assert.strictEqual(await model.getAttribute("aria-pressed"), "true");
  assert.strictEqual(await all.getAttribute("aria-pressed"), "false");
  assert.match(await model.innerText(), new RegExp(`\\s${inModel.length}$`));

  // The search narrows the group further; each word must be in the title or description.
  const search = d.getByLabel("Search templates");
  await search.fill("dimension history");
  assert.deepStrictEqual(await titles(), ["Keep a dimension's history (SCD type 2)"]);
  // Back to all groups: the search stays and finds matches in every group.
  await all.click();
  assert.strictEqual(await d.getByLabel("Search templates").inputValue(), "dimension history");
  await d.getByLabel("Search templates").fill("watermark");
  assert.deepStrictEqual(await titles(), ["Incremental load with a watermark"]);
  await d.getByLabel("Search templates").fill("no such template anywhere");
  assert.deepStrictEqual(await titles(), []);
  await d.getByText("No template matches.").waitFor();
  await d.getByLabel("Search templates").fill("");
  assert.strictEqual((await titles()).length, templates.length);
  assert.deepStrictEqual(app.errors, []);
});

test("an empty required answer or an invalid one keeps the person on the questions with the engine's sentence", { skip }, async () => {
  const before = snapshot();
  await open();
  await pick(CLEAN);
  // The key columns are required for this template.
  await answer({ ...ANSWERS, "Columns that identify a row": "" });
  let shown = await toReview();
  assert.strictEqual(shown.step, "2. Inputs and output");
  assert.match(shown.problem, /Name the columns that identify a row, so duplicates can be dropped and the proof can match rows\.$/);
  // A table name that isn't schema.table.
  await answer({ ...ANSWERS, "Table to read (schema.table)": "orders; drop" });
  shown = await toReview();
  assert.strictEqual(shown.step, "2. Inputs and output");
  assert.match(shown.problem, /The table to read must be a table name like dbo\.orders\.$/);
  // The answers given stay in their boxes.
  assert.strictEqual(await dialog().getByLabel("Table to write (schema.table)").inputValue(), "silver.orders");
  // Fixed: the review shows and the message is gone.
  await answer(ANSWERS);
  shown = await toReview();
  assert.strictEqual(shown.step, "3. Review");
  assert.strictEqual(await dialog().locator(".field-error").count(), 0);
  assert.deepStrictEqual(snapshot(), before, "nothing was written");
  assert.strictEqual(calls("wizard_create").length, 0);
  assert.deepStrictEqual(app.errors, []);
});

// The engine's sentence, without its exception name in front ("ValueError: Name the columns...").
test("the message on a step doesn't start with the engine's exception name", { skip }, async () => {
  await open();
  await pick(CLEAN);
  await answer({ ...ANSWERS, "Columns that identify a row": "" });
  const shown = await toReview();
  assert.doesNotMatch(shown.problem, /^[A-Za-z]+Error: /);
});

test("Back from the review keeps every answer, and Back to the templates keeps the filter", { skip }, async () => {
  const d = await open();
  await d.getByLabel("Search templates").fill("clean");
  await pick(CLEAN);
  await answer(ANSWERS);
  await d.getByLabel("Write mode").selectOption("append");
  await d.getByLabel("Notebook name").fill("Kept answers");
  assert.strictEqual((await toReview()).step, "3. Review");
  await button("Back").click();
  await d.locator('.wizard-steps [aria-current="step"]', { hasText: "2. Inputs and output" }).waitFor();
  for (const [label, value] of Object.entries(ANSWERS)) {
    assert.strictEqual(await d.getByLabel(label, { exact: true }).inputValue(), value, label);
  }
  assert.strictEqual(await d.getByLabel("Write mode").inputValue(), "append");
  assert.strictEqual(await d.getByLabel("Notebook name").inputValue(), "Kept answers");
  // The review again holds what was answered: append mode, the kept columns.
  assert.strictEqual((await toReview()).step, "3. Review");
  const review = await d.locator(".wizard-cells").innerText();
  assert.match(review, /df = df\.select\(\['order_id', 'amount'\]\)/);
  assert.match(review, /mode='append'/);
  // Back twice: the templates, with the search as it was.
  await button("Back").click();
  await d.locator('.wizard-steps [aria-current="step"]', { hasText: "2. Inputs and output" }).waitFor();
  await button("Back").click();
  await d.locator('.wizard-steps [aria-current="step"]', { hasText: "1. Template" }).waitFor();
  assert.strictEqual(await d.getByLabel("Search templates").inputValue(), "clean");
  await button("Cancel").click();
  await d.waitFor({ state: "detached" });
});

// Back to the templates and the same one again: what was typed is still there.
test("choosing the same template again after Back keeps the answers", { skip }, async () => {
  const d = await open();
  await pick(CLEAN);
  await answer(ANSWERS);
  await button("Back").click();
  await pick(CLEAN);
  assert.strictEqual(await d.getByLabel("Table to read (schema.table)").inputValue(), "dbo.orders");
});

test("Cancel writes nothing, on the questions and on the review", { skip }, async () => {
  const before = snapshot();
  const created = calls("wizard_create").length;
  let d = await open();
  await pick(CLEAN);
  await answer(ANSWERS);
  await d.getByLabel("Notebook name").fill("Cancelled one");
  await button("Cancel").click();
  await d.waitFor({ state: "detached" });
  d = await open();
  await pick(CLEAN);
  await answer(ANSWERS);
  await d.getByLabel("Notebook name").fill("Cancelled two");
  assert.strictEqual((await toReview()).step, "3. Review");
  await button("Cancel").click();
  await d.waitFor({ state: "detached" });
  await sleep(300);  // grace: nothing should arrive
  assert.strictEqual(calls("wizard_create").length, created);
  assert.deepStrictEqual(snapshot(), before);
  assert.notStrictEqual(await page().evaluate(() => state.view), "notebook");
});

test("the review shows the cells Create writes, nothing is written before Create, and Create opens it", { skip }, async () => {
  const d = await open();
  await pick(CLEAN);
  await answer(ANSWERS);
  await d.getByLabel("Notebook name").fill("Clean orders");
  assert.strictEqual((await toReview()).step, "3. Review");
  assert.deepStrictEqual(snapshot(), {}, "the notebooks folder is still empty");

  // What the review shows: each markdown cell's heading, each code cell's code, in order.
  const preview = await page().evaluate(() => wizardState.preview);
  assert.ok(preview.cells.length > 3);
  const shown = await d.locator(".wizard-cells > *").evaluateAll((els) => els.map((e) => e.textContent));
  assert.deepStrictEqual(shown, preview.cells.map((c) => (c.type === "markdown" ? c.source.split("\n")[0].replace(/^#\s*/, "") : c.source)));
  assert.match(await d.innerText(), /Proof contract saved in the notebook: rows of silver\.orders are matched by order_id, ignoring loaded_at\./);

  await button("Create notebook").click();
  const opened = await page().waitForFunction(() => !document.querySelector("#dialog-root .dialog") && state.view === "notebook"
    && state.notebook?.name === "Clean orders" && { cells: state.notebook.cells.map((c) => ({ type: c.type, source: c.source })),
    drawn: document.querySelectorAll("[data-cell]").length }, null, { timeout: 15000 }).then((h) => h.jsonValue());
  assert.deepStrictEqual(opened.cells, preview.cells, "the opened notebook holds the previewed cells");
  assert.strictEqual(opened.drawn, preview.cells.length);
  await page().locator(".toast", { hasText: /^Created Clean orders\. Run it here/ }).waitFor();

  // The file the engine wrote: the same cells and the proof contract the review described.
  assert.deepStrictEqual(Object.keys(snapshot()), ["Clean orders.ipynb"]);
  const file = fileCells("Clean orders");
  assert.deepStrictEqual(file.cells, preview.cells);
  assert.deepStrictEqual(file.proof, preview.proof);
  assert.deepStrictEqual(file.proof, { tables: { "silver.orders": { keys: ["order_id"], ignore: ["loaded_at"] } } });
  // Create sent the answers the preview was made from.
  assert.deepStrictEqual(calls("wizard_create").at(-1).params.values, calls("wizard_preview").at(-1).params.values);
  assert.strictEqual(calls("wizard_create").at(-1).params.name, "Clean orders");
  assert.deepStrictEqual(app.errors, []);
});

test("a name already taken is refused, and the first notebook is unchanged", { skip }, async () => {
  const first = snapshot()["Clean orders.ipynb"];
  assert.ok(first, "the notebook from the test before is there");
  const created = calls("wizard_create").length;
  const d = await open();
  await pick(CLEAN);
  // Different answers, so an overwrite would change the file.
  await answer({ ...ANSWERS, "Table to read (schema.table)": "dbo.returns", "Table to write (schema.table)": "silver.returns" });
  await d.getByLabel("Notebook name").fill("Clean orders");
  assert.strictEqual((await toReview()).step, "3. Review");
  await button("Create notebook").click();
  // The engine answers with a problem on the review, or the dialog closes on a notebook it opened.
  const { problem } = await page().waitForFunction(() => {
    const d = document.querySelector("#dialog-root .dialog");
    return d ? (d.querySelector(".field-error") && { problem: d.querySelector(".field-error").textContent }) : { problem: null };
  }, null, { timeout: 15000 }).then((h) => h.jsonValue());
  assert.strictEqual(calls("wizard_create").length, created + 1);
  assert.strictEqual(snapshot()["Clean orders.ipynb"], first, "the first file is byte for byte as it was");
  assert.match(String(problem), /A notebook named 'Clean orders' already exists\.$/);
  assert.strictEqual(await step(), "3. Review", "the person stays on the review");
  assert.deepStrictEqual(Object.keys(snapshot()), ["Clean orders.ipynb"], "nothing else was written instead");

  // Back, a new name, and Create makes the second notebook beside the first.
  await button("Back").click();
  await d.getByLabel("Notebook name").fill("Clean returns");
  assert.strictEqual((await toReview()).step, "3. Review");
  await button("Create notebook").click();
  await page().waitForFunction(() => !document.querySelector("#dialog-root .dialog") && state.notebook?.name === "Clean returns",
    null, { timeout: 15000 });
  assert.deepStrictEqual(Object.keys(snapshot()).sort(), ["Clean orders.ipynb", "Clean returns.ipynb"]);
  assert.strictEqual(snapshot()["Clean orders.ipynb"], first);
  assert.match(fileCells("Clean returns").cells.map((c) => c.source).join("\n"), /lakehouse\.read_table\('dbo\.returns'\)/);
  assert.deepStrictEqual(app.errors, []);
});

const axeSkip = skip || (!axeSource && "axe-core is not installed");

// Each step redraws the dialog, which fades in; the harness waits for it before axe looks.
const axeFindings = () => harnessAxe(page(), "#dialog-root .dialog");

test("the templates and questions steps have no serious axe findings", { skip: axeSkip }, async () => {
  const d = await open();
  assert.deepStrictEqual(await axeFindings(), [], "templates");
  await d.getByLabel("Search templates").fill("nothing matches this");
  assert.deepStrictEqual(await axeFindings(), [], "templates, none matching");
  await d.getByLabel("Search templates").fill("");
  await pick(CLEAN);
  assert.deepStrictEqual(await axeFindings(), [], "questions");
  await answer({ ...ANSWERS, "Columns that identify a row": "" });
  assert.strictEqual((await toReview()).step, "2. Inputs and output");
  assert.deepStrictEqual(await axeFindings(), [], "questions with a problem");
  await button("Cancel").click();
  await d.waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});

// The review's cell list scrolls (.wizard-cells, max-height 340px), so it takes focus for the
// keyboard to scroll it (axe's scrollable-region-focusable).
test("the review step has no serious axe findings", { skip: axeSkip }, async () => {
  const d = await open();
  await pick(CLEAN);
  await answer(ANSWERS);
  await d.getByLabel("Notebook name").fill("Clean orders");
  assert.strictEqual((await toReview()).step, "3. Review");
  const review = await axeFindings();
  // The name is taken by now: the review with its problem.
  await button("Create notebook").click();
  await d.locator(".field-error").waitFor();
  const withProblem = await axeFindings();
  await button("Cancel").click();
  await d.waitFor({ state: "detached" });
  assert.deepStrictEqual({ review, withProblem }, { review: [], withProblem: [] });
});
