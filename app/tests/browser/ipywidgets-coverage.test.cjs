// ipywidgets controls (ipywidgets.js) as widgets.py describes them, with the kernel's notebook_ipywidget_event
// mocked: every kind draws as its native control with its description as the label, each change sends that
// widget's event (with the root model and the notebook), the kernel's answer is drawn in place with what the
// handlers printed, a change made while the kernel answers waits and only the latest is sent, a failed event
// is shown as an alert, and widgets whose kernel has gone say so. See harness.cjs for how to run it.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const NB = "Sales widgets";

// widgets.py _view(): {id, kind, description, disabled, ...the kind's own fields}.
// The kernel's state: what each change set (a value or an index), as widgets.py keeps it.
const vals = {};
const w = (id, kind, description, extra = {}) => ({ id, kind, description, disabled: false, ...extra,
  ...(id in vals ? { [kind === "dropdown" || kind === "radio" || kind === "multiselect" ? "index" : "value"]: vals[id] } : {}) });
function viewOf(status) {
  return w("m-root", "vbox", "", { children: [
    w("m-status", "label", "", { value: status }),
    w("m-html", "html", "", { value: "<b>Sales</b> by region" }),
    w("m-row", "hbox", "", { children: [
      w("m-refresh", "button", "Refresh", { tooltip: "Read the table again" }),
      w("m-live", "toggle", "Live", { value: false }),
      w("m-icon", "button", "", { disabled: true }),
    ] }),
    w("m-returns", "checkbox", "Include returns", { value: true }),
    w("m-ok", "valid", "Region", { value: true }),
    w("m-bad", "valid", "Size", { value: false }),
    w("m-load", "progress", "Loaded", { min: 0, max: 10, value: 3 }),
    w("m-top", "number", "Top N", { min: 1, max: 50, step: 1, value: 10 }),
    w("m-rate", "number", "", { value: 2.5 }),
    w("m-price", "range", "Price", { min: 0, max: 100, step: 5, value: [10, 90] }),
    w("m-owner", "text", "Owner", { value: "ann", placeholder: "Who owns it" }),
    w("m-secret", "password", "Secret", { value: "", filled: true }),
    w("m-notes", "textarea", "Notes", { value: "first draft", placeholder: "Anything else" }),
    w("m-region", "dropdown", "Region", { options: ["East", "West"], index: 1 }),
    w("m-size", "radio", "Size", { options: ["S", "M", "L"], index: 0 }),
    w("m-months", "multiselect", "Months", { options: ["Jan", "Feb", "Mar"], index: [0, 2] }),
    w("m-out", "output", "", { outputs: [{ type: "stream", text: "hello from the Output widget" }] }),
    w("m-date", "datepicker", "When"),
  ] });
}

// The mocked kernel: answers with the root's new view and what the handlers printed, or as `next` says.
const kernel = { next: null, hold: null };
let app;
const page = () => app.page;
const out = () => page().locator(".widget-output");
const events = () => app.calls.filter((c) => c.method === "notebook_ipywidget_event").map((c) => c.params);
const lastEvent = () => events().at(-1);

// Draws a fresh widget output in the notebook's first cell, as a run that displayed it would.
async function draw() {
  app.calls.length = 0;
  for (const k of Object.keys(vals)) delete vals[k];
  await page().evaluate((view) => {
    const cell = state.notebook.cells[0];
    cell.outputs = [{ type: "widget", model: "m-root", view }];
    updateResults(cell);
  }, viewOf("Ready"));
  await out().getByText("Ready").waitFor();
}

// Types a value into a box and leaves it: the one change event a person's edit makes.
const setValue = (box, v) => box.evaluate((el, v) => { el.value = v; el.dispatchEvent(new Event("change")); }, v);

// Waits until the kernel's answer to event n is drawn (the status label names the widget that changed).
async function answered(n, model) {
  await out().getByText(`Changed ${model} (${n})`).waitFor();
  assert.strictEqual(events().length, n, JSON.stringify(events().map((e) => [e.model, e.event])));
}

before(async () => {
  if (skip) return;
  app = await openApp({
    viewport: { width: 1280, height: 1400 },
    mocks: {
      save_notebook: () => ({ saved: true }),
      notebook_dependencies: () => ({ cells: [] }),
      notebook_ipywidget_event: async ({ model, event }) => {
        if (kernel.hold) await kernel.hold.promise;
        const next = kernel.next;
        kernel.next = null;
        if (next === "fail") throw new Error("TimeoutError: the widget's handler ran for more than 30 seconds");
        if (next === "gone") return { gone: true, message: "The kernel restarted, so these widgets are gone. Run the cell again." };
        if ("value" in event) vals[model] = event.value;
        if ("index" in event) vals[model] = event.index;
        const n = app.calls.filter((c) => c.method === "notebook_ipywidget_event").length;
        return { view: viewOf(`Changed ${model} (${n})`), outputs: [{ type: "stream", text: `handler ran for ${model}` }] };
      },
    },
  });
  // An HTML widget goes to the notebook's sandboxed output frame, which the shell serves.
  await page().route("http://nbout.test/**", (route) => route.fulfill({ contentType: "text/html", body: "<!doctype html><title>frame</title>" }));
  await page().evaluate((name) => {
    window.__TAURI__.core.convertFileSrc = () => "http://nbout.test/frame";
    loadIntoState({ name, cells: [{ type: "code", cell_id: "c1", meta: {}, source: "display(dashboard)" }] });
    go("notebook");
  }, NB);
  await page().locator(".cell").first().waitFor();
  app.errors.length = 0;
});

after(async () => { if (app) await app.close(); });

test("each kind draws as its own control, labelled by its description", { skip }, async () => {
  await draw();
  const o = out();
  assert.match(await o.locator(".out-bar").innerText(), /Widgets/);
  assert.strictEqual(await o.locator(".widget-html iframe.out-html").getAttribute("sandbox"), "allow-scripts");
  assert.strictEqual(await o.locator(".widget-box.hbox .widget-button").count(), 3);
  assert.strictEqual(await o.getByRole("button", { name: "Refresh" }).getAttribute("title"), "Read the table again");
  assert.strictEqual(await o.getByRole("button", { name: "Live" }).getAttribute("aria-pressed"), "false");
  assert.ok(await o.getByRole("button", { name: "Button", exact: true }).isDisabled(), "an undescribed button is named Button");
  assert.ok(await o.getByRole("checkbox", { name: "Include returns" }).isChecked());
  assert.deepStrictEqual(await o.locator(".widget-label").allInnerTexts(),
    ["Ready", "Region ✓ Valid", "Size ✗ Not valid", "datepicker widget"]);
  assert.strictEqual(await o.locator("progress").getAttribute("value"), "3");
  assert.strictEqual(await o.getByRole("spinbutton", { name: "Top N" }).inputValue(), "10");
  assert.strictEqual(await o.getByRole("spinbutton", { name: "number" }).getAttribute("step"), "any", "a number with no step takes any");
  assert.strictEqual(await o.getByRole("spinbutton", { name: "Price from" }).inputValue(), "10");
  assert.strictEqual(await o.getByRole("spinbutton", { name: "Price to" }).inputValue(), "90");
  assert.strictEqual(await o.getByRole("textbox", { name: "Owner" }).inputValue(), "ann");
  assert.strictEqual(await o.locator("input[type=password]").getAttribute("placeholder"), "(set)", "a set password shows (set), not its value");
  assert.strictEqual(await o.getByRole("textbox", { name: "Notes" }).inputValue(), "first draft");
  assert.strictEqual(await o.getByRole("combobox", { name: "Region" }).inputValue(), "1");
  assert.ok(await o.getByRole("radio", { name: "S" }).isChecked());
  assert.deepStrictEqual(await o.getByRole("listbox", { name: "Months" }).evaluate((s) => [...s.selectedOptions].map((x) => x.text)), ["Jan", "Mar"]);
  await o.locator(".widget-out").getByText("hello from the Output widget").waitFor();
  assert.deepStrictEqual(events(), [], "drawing sends nothing");
});

test("each change sends its widget's event, and the kernel's answer is drawn in place", { skip }, async () => {
  await draw();
  const o = out();
  const base = { root: "m-root", notebook: NB };
  await o.getByRole("button", { name: "Refresh" }).click();
  await answered(1, "m-refresh");
  assert.deepStrictEqual(lastEvent(), { model: "m-refresh", event: { click: true }, ...base });
  await o.locator(".widget-events").getByText("handler ran for m-refresh").waitFor();

  await o.getByRole("button", { name: "Live" }).click();
  await answered(2, "m-live");
  assert.deepStrictEqual(lastEvent().event, { value: true });

  await o.getByRole("checkbox", { name: "Include returns" }).uncheck();
  await answered(3, "m-returns");
  assert.deepStrictEqual(lastEvent().event, { value: false });

  // The slider's readout follows as it's dragged; the value goes when it's let go.
  const range = o.locator("input[type=range]");
  await page().evaluate(() => {
    const view = state.notebook.cells[0].outputs[0].view;
    view.children.push({ id: "m-n", kind: "slider", description: "", disabled: false, min: 1, max: 10, step: 1, value: 5 });
  });
  await page().evaluate(() => updateResults(state.notebook.cells[0]));
  await range.evaluate((el) => { el.value = "8"; el.dispatchEvent(new Event("input")); });
  assert.strictEqual(await o.locator(".widget-readout").innerText(), "8");
  assert.strictEqual(events().length, 3, "dragging sends nothing");
  await range.evaluate((el) => el.dispatchEvent(new Event("change")));
  await answered(4, "m-n");
  assert.deepStrictEqual(lastEvent().event, { value: 8 });

  // An emptied number box sends nothing; a number sends the number.
  await setValue(o.getByRole("spinbutton", { name: "Top N" }), "");
  assert.strictEqual(events().length, 4);
  await setValue(o.getByRole("spinbutton", { name: "Top N" }), "12");
  await answered(5, "m-top");
  assert.deepStrictEqual(lastEvent().event, { value: 12 });

  await setValue(o.getByRole("spinbutton", { name: "Price from" }), "15");
  await answered(6, "m-price");
  assert.deepStrictEqual(lastEvent().event, { value: [15, 90] });

  // Enter in a text box submits it (typed, so no change event comes first).
  await o.getByRole("textbox", { name: "Owner" }).evaluate((el) => { el.value = "bo"; });
  await o.getByRole("textbox", { name: "Owner" }).press("Enter");
  await answered(7, "m-owner");
  assert.deepStrictEqual(lastEvent().event, { value: "bo", submit: true });

  await setValue(o.getByRole("textbox", { name: "Notes" }), "second draft");
  await answered(8, "m-notes");
  assert.deepStrictEqual(lastEvent().event, { value: "second draft" });

  await o.getByRole("combobox", { name: "Region" }).selectOption({ label: "East" });
  await answered(9, "m-region");
  assert.deepStrictEqual(lastEvent().event, { index: 0 });

  await o.getByRole("radio", { name: "L" }).check();
  await answered(10, "m-size");
  assert.deepStrictEqual(lastEvent().event, { index: 2 });

  await o.getByRole("listbox", { name: "Months" }).selectOption([{ label: "Feb" }]);
  await answered(11, "m-months");
  assert.deepStrictEqual(lastEvent().event, { index: [1] });
});

test("a change made while the kernel answers waits, and only the latest is sent", { skip }, async () => {
  await draw();
  const o = out();
  let release;
  kernel.hold = { promise: new Promise((r) => { release = r; }) };
  await o.getByRole("button", { name: "Refresh" }).click();
  await o.getByRole("button", { name: "Live" }).click();
  await o.getByRole("checkbox", { name: "Include returns" }).uncheck();
  assert.deepStrictEqual(events().map((e) => e.model), ["m-refresh"], "one at a time");
  kernel.hold = null;
  release();
  await answered(2, "m-returns");
  assert.deepStrictEqual(events().map((e) => [e.model, e.event]), [["m-refresh", { click: true }], ["m-returns", { value: false }]]);
});

test("a failed event is shown as an alert and the controls stay", { skip }, async () => {
  await draw();
  kernel.next = "fail";
  await out().getByRole("button", { name: "Refresh" }).click();
  const alert = out().getByRole("alert");
  await alert.waitFor();
  assert.match(await alert.innerText(), /30 seconds/);
  assert.ok(await out().getByRole("button", { name: "Refresh" }).isVisible());
  // The alert is for that change only: the next answer clears it.
  await out().getByRole("button", { name: "Live" }).click();
  await answered(2, "m-live");
  assert.strictEqual(await out().getByRole("alert").count(), 0);
});

test("widgets whose kernel has gone say so, and their controls go", { skip }, async () => {
  await draw();
  kernel.next = "gone";
  await out().getByRole("button", { name: "Refresh" }).click();
  await out().getByRole("status").getByText("The kernel restarted, so these widgets are gone. Run the cell again.").waitFor();
  assert.strictEqual(await out().locator("button, input, select, textarea").count(), 0);
});

test("no page errors", { skip }, () => {
  // The harness's start-up script runs in every frame, and the sandboxed HTML frame has no localStorage.
  assert.deepStrictEqual(app.errors.filter((e) => !/localStorage.*sandboxed|sandboxed.*localStorage/.test(e)), []);
});
