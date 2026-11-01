// The notebook Clean view (Preview): Data Wrangler on a cell's table output, like Fabric's Data
// Wrangler and Databricks' no-code transforms. Each step becomes a line of Polars code; the engine
// runs that code on the whole result for the preview (wrangler.py, over notebook_frame_wrangle).
// Nothing changes until the code is added as a new cell and run, so the original data stays as it is.

const WRANGLE_OPS = [
  ["drop", "Drop columns"], ["keep", "Keep only columns"], ["rename", "Rename column"], ["cast", "Change column type"],
  ["fill", "Fill missing values"], ["drop_missing", "Drop rows with missing values"], ["filter", "Filter rows"],
  ["dedupe", "Remove duplicate rows"], ["trim", "Trim spaces"], ["case", "Change text case"],
  ["replace", "Find and replace"], ["split", "Split text"], ["sort", "Sort rows"]];
const WRANGLE_TYPES = ["integer", "double", "string", "boolean", "date", "timestamp"];
const WRANGLE_TESTS = [["eq", "equals"], ["ne", "doesn't equal"], ["gt", "is greater than"], ["ge", "is at least"],
  ["lt", "is less than"], ["le", "is at most"], ["contains", "contains"], ["starts", "starts with"], ["ends", "ends with"],
  ["missing", "is missing"], ["present", "isn't missing"]];
// Which fields each step asks for: columns (several), column (one), and its own settings.
const WRANGLE_FIELDS = {
  drop: ["columns"], keep: ["columns"], rename: ["column", "to"], cast: ["columns", "type"], fill: ["columns", "value"],
  drop_missing: ["columns?"], filter: ["column", "test", "value?"], dedupe: ["columns?"], trim: ["columns"],
  case: ["columns", "case"], replace: ["columns", "find", "value"], split: ["column", "value", "parts"], sort: ["column", "desc"],
};

// The variable a cell displayed, so the generated code reads the same data: display(x), or x alone on the last line.
function wrangleName(cell) {
  const lines = String(cell?.source || "").trim().split("\n");
  const last = lines[lines.length - 1] || "";
  const m = last.match(/^\s*display\(\s*([A-Za-z_]\w*)\s*[,)]/) || last.match(/^\s*([A-Za-z_]\w*)\s*$/);
  return m ? m[1] : "df";
}

function renderWrangler(o, cell) {
  const w = o.wrangle ||= { steps: [], name: wrangleName(cell), data: null, adding: "" };
  const box = h("div", { class: "wrangle" });
  const redraw = () => box.replaceWith(renderWrangler(o, cell));
  const refresh = () => { w.data = null; redraw(); };
  const removeStep = (i) => { w.steps.splice(i, 1); refresh(); };
  const name = h("input", { type: "text", class: "wrangle-name", value: w.name, "aria-label": "Data frame variable", spellcheck: "false" });
  name.addEventListener("change", () => { w.name = name.value.trim(); refresh(); });
  box.append(h("div", { class: "wrangle-head" },
    h("label", { class: "chart-opt" }, "Data frame", name),
    h("span", { class: "muted-text" }, "Steps run on the whole result for the preview; the notebook changes only when you run the new cell.")));
  const steps = h("ol", { class: "wrangle-steps", "aria-label": "Steps" });
  const preview = h("div", { class: "wrangle-preview", role: "status" }, "Previewing every row...");
  box.append(h("div", { class: "wrangle-body" }, h("div", { class: "wrangle-side" }, h("div", { class: "wrangle-label" }, "Steps"), steps,
    renderWrangleAdd(o, w, refresh)), preview));
  const draw = (d) => {
    if (d.gone) { preview.textContent = "Run the cell again to clean this result: the kernel no longer holds it."; return; }
    steps.replaceChildren(...w.steps.map((s, i) => h("li", { class: d.error?.step === i ? "wrangle-step bad" : "wrangle-step" },
      h("span", {}, d.steps[i] || s.op),
      d.error?.step === i ? h("span", { class: "wrangle-error", role: "alert" }, d.error.message) : null,
      btn("", "dismiss", () => removeStep(i), "tool small", { "aria-label": `Remove step ${i + 1}`, title: "Remove" }))));
    if (!w.steps.length) steps.append(h("li", { class: "muted-text" }, "No steps yet."));
    const numeric = d.types.map((t) => ["integer", "double"].includes(t) || t.startsWith("decimal"));
    const table = resultTable(d.columns, d.types, d.rows, numeric, null, () => {});
    const changed = new Set(d.changed.map((c) => d.columns.indexOf(c)));
    table.querySelectorAll("tr").forEach((tr) => [...tr.children].forEach((td, i) => { if (changed.has(i)) td.classList.add("changed"); }));
    const rows = (n) => `${n.toLocaleString()} row${n === 1 ? "" : "s"}`;
    preview.replaceChildren(
      h("div", { class: "more" }, `${rows(d.rows_before)} before, ${rows(d.rows_after)} after.`,
        d.changed.length ? " Changed columns are marked." : "", d.sampled ? ` Previewing the first ${rows(d.rows_before)} of the result.` : "",
        d.rows_after > d.rows.length ? ` Showing the first ${d.rows.length}.` : ""),
      h("div", { class: "out-table-scroll" }, table),
      h("div", { class: "wrangle-label" }, "Code (Polars)"),
      h("pre", { class: "wrangle-code" }, d.code),
      h("div", { class: "wrangle-actions" },
        btn("Add code to notebook", "add", () => addWrangleCell(cell, d.code), "tool primary small", { disabled: Boolean(d.error) || !w.steps.length }),
        btn("Copy code", null, () => attempt(() => navigator.clipboard.writeText(d.code).then(() => toast("Copied the code.")), "Could not copy"), "tool outline small")));
  };
  if (w.data) draw(w.data);
  else {
    rpc("notebook_frame_wrangle", { frame: o.frame, steps: w.steps, name: w.name || "df" })
      .then((d) => { if (!d.gone) w.data = d; draw(d); }, (err) => { preview.textContent = `Could not preview: ${friendlyError(err).text}`; });
  }
  return box;
}

// The form for the next step, using the columns as the steps so far leave them.
function renderWrangleAdd(o, w, refresh) {
  const columns = w.data?.columns || o.columns;
  const op = h("select", { "aria-label": "Add a step" }, h("option", { value: "" }, "Choose an operation..."),
    WRANGLE_OPS.map(([v, label]) => h("option", { value: v, selected: w.adding === v || null }, label)));
  const form = h("div", { class: "wrangle-add" }, h("label", { class: "chart-opt" }, "Add a step", op));
  op.addEventListener("change", () => { w.adding = op.value; refresh(); });
  if (!w.adding) return form;
  const values = {};
  const field = (label, input, key) => { values[key] = input; form.append(h("label", { class: "chart-opt" }, label, input)); };
  const choose = (opts, multiple) => h("select", { multiple: multiple || null, size: multiple ? Math.min(6, opts.length) : null },
    opts.map(([v, text]) => h("option", { value: v }, text)));
  const cols = columns.map((c) => [c, c]);
  for (const f of WRANGLE_FIELDS[w.adding]) {
    const key = f.replace("?", "");
    if (key === "columns") field(f.endsWith("?") ? "Columns (none means all)" : "Columns", choose(cols, true), "columns");
    else if (key === "column") field("Column", choose(cols), "column");
    else if (key === "type") field("New type", choose(WRANGLE_TYPES.map((t) => [t, t])), "to");
    else if (key === "case") field("Case", choose([["upper", "UPPER"], ["lower", "lower"], ["title", "Title"]]), "to");
    else if (key === "test") field("Keep rows where it", choose(WRANGLE_TESTS), "test");
    else if (key === "desc") field("Order", choose([["", "Smallest first"], ["1", "Largest first"]]), "desc");
    else if (key === "parts") field("Parts", h("input", { type: "number", min: "2", max: "20", value: "2" }), "parts");
    else if (key === "to") field("New name", h("input", { type: "text" }), "to");
    else if (key === "find") field("Find", h("input", { type: "text" }), "find");
    else field({ fill: "Fill with", split: "Split on", replace: "Replace with" }[w.adding] || "Value", h("input", { type: "text", value: w.adding === "split" ? "," : "" }), "value");
  }
  form.append(btn("Add step", "add", () => {
    const step = { op: w.adding };
    for (const [key, input] of Object.entries(values)) {
      if (key === "columns") step.columns = [...input.selectedOptions].map((x) => x.value);
      else if (key === "desc") step.desc = input.value === "1";
      else if (key === "parts") step.parts = Number(input.value);
      else step[key] = input.value;
    }
    w.steps.push(step);
    w.adding = "";
    refresh();
  }, "tool outline small"));
  return form;
}

// The generated code goes in a new cell under the one that showed the result.
function addWrangleCell(cell, code) {
  if (cell) state.selected = cell.id;
  insertCell(code);
  toast("Added the code as a new cell. Run it to make the cleaned data frame.");
}
