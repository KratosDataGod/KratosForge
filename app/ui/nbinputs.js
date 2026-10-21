// Parameters and Widgets bar above a notebook's cells (Preview). It shows the parameter cell's
// values and the Databricks widgets the notebook creates, and keeps values typed here with the
// notebook (notebook_inputs_set). The code never changes: when a cell runs on this PC, runCell adds
// the lines the engine gives for that cell (nbInputSuffix), as Fabric does with run parameters.

state.nbInputs = null;           // notebook_inputs result for the open notebook
state.nbInputsCollapsed = false;

function nbInputCells(nb) {
  return nb.cells.map((c) => ({ type: c.type, source: c.source, meta: c.meta, cell_id: c.cell_id }));
}

// The code to run after this cell's own, or "" (runCell).
function nbInputSuffix(cell) {
  const extra = state.nbInputs?.notebook === state.notebook?.name ? state.nbInputs.suffix?.[cell.cell_id] : null;
  return extra ? `\n${extra}` : "";
}

function inputsBar(nb) {
  const slot = h("div", { class: "nb-inputs-slot" });
  refreshInputs(nb, slot);
  return slot;
}

async function refreshInputs(nb, slot = document.querySelector(".nb-inputs-slot")) {
  let got;
  try {
    got = await rpc("notebook_inputs", { name: nb.name, cells: nbInputCells(nb) });
  } catch {
    return;  // the bar is a convenience: the cells still run with their own values
  }
  if (state.notebook !== nb) return;
  state.nbInputs = { ...got, notebook: nb.name };
  slot?.replaceChildren(...renderInputs(nb));
}

function currentInputValues() {
  const v = { parameters: {}, widgets: {} };
  for (const p of state.nbInputs?.parameters || []) if (p.value !== p.default) v.parameters[p.name] = p.value;
  for (const w of state.nbInputs?.widgets || []) if (w.value !== w.default) v.widgets[w.name] = w.value;
  return v;
}

// Saves go one after another, and each change is worked out from the values the save before it
// returned: two quick changes (one box, then another) can't drop the first. A run waits for them
// (nbInputsSaved, runCell) so it uses the values the bar shows. A save that throws is reported and
// the queue goes on: one failure can't stop later saves or every run after it.
let inputsSaving = Promise.resolve();
const nbInputsSaved = () => inputsSaving;

function queueInputs(nb, valuesFrom) {
  inputsSaving = inputsSaving.then(() => (state.notebook === nb ? saveInputValues(nb, valuesFrom()) : null))
    .catch((e) => { console.error(e); toast("Could not keep the value. Change it again to retry.", true); });
  return inputsSaving;
}

async function saveInputValues(nb, values) {
  const got = await attempt(() => rpc("notebook_inputs_set", { name: nb.name, cells: nbInputCells(nb), values }),
    "Could not keep the value");
  if (!got || state.notebook !== nb) return;
  state.nbInputs = { ...got, notebook: nb.name };
  document.querySelector(".nb-inputs-slot")?.replaceChildren(...renderInputs(nb));
}

function setInput(nb, group, name, value) {
  return queueInputs(nb, () => {
    const values = currentInputValues();
    values[group][name] = value;
    return values;
  });
}

function inputField(nb, group, row) {
  const id = `nbin-${group}-${row.name}`;
  const set = (value) => setInput(nb, group, row.name, value);
  let control;
  if (group === "parameters" && row.type === "bool") {
    control = h("select", { id, onchange: (e) => set(e.target.value) },
      ["False", "True"].map((v) => h("option", { value: v, selected: row.value === v }, v)));
  } else if (row.kind === "dropdown") {
    control = h("select", { id, onchange: (e) => set(e.target.value) },
      row.choices.map((v) => h("option", { value: v, selected: row.value === v }, v)));
  } else if (row.kind === "multiselect") {
    const picked = new Set(row.value ? row.value.split(",") : []);
    control = h("div", { class: "nb-input-choices", role: "group", "aria-labelledby": `${id}-label` },
      row.choices.map((v) => h("label", { class: "nb-input-choice" },
        h("input", { type: "checkbox", checked: picked.has(v), onchange: (e) => {
          if (e.target.checked) picked.add(v); else picked.delete(v);
          set(row.choices.filter((c) => picked.has(c)).join(","));
        } }), v)));
  } else {
    const list = row.kind === "combobox" ? `${id}-list` : null;
    control = h("input", { id, value: row.value, list, spellcheck: "false", onchange: (e) => set(e.target.value) });
    if (list) control = h("span", {}, control, h("datalist", { id: list }, row.choices.map((v) => h("option", { value: v }))));
  }
  const kind = group === "parameters" ? row.type : row.kind;
  return h("div", { class: `nb-input${row.error ? " invalid" : ""}${row.value !== row.default ? " changed" : ""}` },
    h(row.kind === "multiselect" ? "span" : "label", { id: `${id}-label`, for: row.kind === "multiselect" ? null : id },
      group === "widgets" ? row.label : row.name, h("span", { class: "nb-input-kind" }, kind)),
    control,
    row.error ? h("span", { class: "nb-input-error", role: "alert" }, row.error) : null);
}

function renderInputs(nb) {
  const got = state.nbInputs;
  if (!got || (!got.parameters.length && !got.widgets.length)) return [];
  const changed = [...got.parameters, ...got.widgets].filter((r) => r.value !== r.default && !r.error);
  const where = got.parameters.length
    ? `From the parameter cell (cell ${nb.cells.findIndex((c) => c.cell_id === got.parameter_cell) + 1})`
    : "Created by dbutils.widgets";
  const head = h("div", { class: "row nb-inputs-head" },
    previewMarked("inputs", h("b", {}, got.parameters.length && got.widgets.length ? "Parameters and widgets"
      : got.parameters.length ? "Parameters" : "Widgets")),
    h("span", { class: "muted-text grow" }, `${where}. Runs on this PC use these values; the notebook's code doesn't change.`),
    btn("Reset to code", "restart", () => queueInputs(nb, () => ({ parameters: {}, widgets: {} })), "tool small",
      { disabled: !changed.length && !got.parameters.concat(got.widgets).some((r) => r.error) }),
    h("button", { class: "tool small", "aria-expanded": String(!state.nbInputsCollapsed),
      "aria-label": state.nbInputsCollapsed ? "Show parameters" : "Hide parameters",
      onclick: () => { state.nbInputsCollapsed = !state.nbInputsCollapsed; document.querySelector(".nb-inputs-slot")?.replaceChildren(...renderInputs(nb)); } },
    icon(state.nbInputsCollapsed ? "chevronDown" : "up", 14)));
  if (state.nbInputsCollapsed) return [h("section", { class: "nb-inputs", "aria-label": "Parameters" }, head)];
  return [h("section", { class: "nb-inputs", "aria-label": "Parameters" }, head,
    h("div", { class: "nb-inputs-fields" },
      got.parameters.map((p) => inputField(nb, "parameters", p)),
      got.widgets.map((w) => inputField(nb, "widgets", w))),
    changed.length ? h("div", { class: "muted-text small" },
      `Set here: ${changed.map((r) => `${r.name} = ${r.value}`).join(", ")}. Run the notebook to use them.`) : null)];
}
