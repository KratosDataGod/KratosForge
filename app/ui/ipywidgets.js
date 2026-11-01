// ipywidgets in a notebook output (Preview): sliders, dropdowns, text boxes, buttons and interact.
// The kernel keeps each widget's state (widgets.py); this file draws it with native controls.
// A change goes to the kernel (notebook_ipywidget_event), which runs the widget's handlers and
// answers with the widget's new state, which is drawn in place. Text is always text; an HTML
// widget goes to the same sandboxed frame as any HTML output.

function renderWidgetOutput(o, cell) {
  const wrap = h("div", { class: "output widget-output" });
  const redraw = () => wrap.replaceWith(renderWidgetOutput(o, cell));
  const notebook = state.notebook?.name;  // its session's kernel holds these widgets (kernel_sessions.py)
  // One change at a time: one made while the kernel answers waits, and only the latest is sent.
  const send = (model, event) => {
    if (o.busy) { o.pending = { model, event }; return; }
    o.busy = true;
    const next = () => {
      o.busy = false;
      const p = o.pending;
      delete o.pending;
      if (p && !o.gone) send(p.model, p.event);
      else redraw();
    };
    rpc("notebook_ipywidget_event", { model, event, root: o.model, notebook }).then((r) => {
      if (r.gone) o.gone = r.message;
      else {
        if (r.view) o.view = r.view;
        o.events = r.outputs || [];
      }
      next();
    }, (err) => { o.problem = friendlyError(err).text; next(); });
  };
  wrap.append(h("div", { class: "out-bar" }, previewMarked("ipywidgets", h("span", { class: "muted-text" }, "Widgets"))));
  if (o.gone) {
    wrap.append(h("div", { class: "more", role: "status" }, o.gone));
    return wrap;
  }
  wrap.append(drawWidget(o.view, send, cell, 0));
  if (o.problem) wrap.append(h("div", { class: "more widget-problem", role: "alert" }, o.problem));
  delete o.problem;
  if (o.events?.length) wrap.append(h("div", { class: "widget-events" }, o.events.map((e) => renderOutput(e, cell))));
  return wrap;
}

function drawWidget(v, send, cell, depth) {
  if (!v || depth > 20) return null;
  const name = v.description || "";
  const off = v.disabled || null;
  const field = (control, wide) => h("label", { class: `widget-field${wide ? " wide" : ""}`, title: v.tooltip || null },
    name ? h("span", { class: "widget-name" }, name) : null, control);
  const label = name || v.kind;
  if (v.kind === "vbox" || v.kind === "hbox") {
    return h("div", { class: `widget-box ${v.kind}` }, (v.children || []).map((c) => drawWidget(c, send, cell, depth + 1)));
  }
  if (v.kind === "output") return h("div", { class: "widget-out" }, (v.outputs || []).map((x) => renderOutput(x, cell)));
  if (v.kind === "label") return h("div", { class: "widget-label" }, String(v.value ?? ""));
  if (v.kind === "html") return h("div", { class: "widget-html" }, htmlFrame(String(v.value ?? "")));
  if (v.kind === "button") {
    return h("button", { class: "tool outline small widget-button", disabled: off, title: v.tooltip || null, "aria-label": name ? null : "Button",
      onclick: () => send(v.id, { click: true }) }, name || "Button");
  }
  if (v.kind === "toggle") {
    return h("button", { class: `tool outline small widget-button${v.value ? " toggled" : ""}`, "aria-pressed": String(Boolean(v.value)),
      disabled: off, onclick: () => send(v.id, { value: !v.value }) }, name || "Toggle");
  }
  if (v.kind === "checkbox") {
    return field(h("input", { type: "checkbox", checked: v.value || null, disabled: off, "aria-label": name ? null : label,
      onchange: (e) => send(v.id, { value: e.target.checked }) }));
  }
  if (v.kind === "valid") return h("div", { class: "widget-label" }, name, v.value ? " ✓ Valid" : " ✗ Not valid");
  if (v.kind === "slider") {
    const readout = h("span", { class: "widget-readout" }, String(v.value));
    const range = h("input", { type: "range", min: v.min, max: v.max, step: v.step, value: v.value, disabled: off,
      "aria-label": name ? null : label, oninput: (e) => { readout.textContent = e.target.value; },
      onchange: (e) => send(v.id, { value: Number(e.target.value) }) });
    return field(h("span", { class: "widget-slider" }, range, readout), true);
  }
  if (v.kind === "progress") {
    return field(h("progress", { min: v.min, max: v.max, value: v.value, "aria-label": name ? null : label }, String(v.value)), true);
  }
  if (v.kind === "number") {
    return field(h("input", { type: "number", min: v.min ?? null, max: v.max ?? null, step: v.step ?? "any", value: v.value, disabled: off,
      "aria-label": name ? null : label, onchange: (e) => e.target.value !== "" && send(v.id, { value: Number(e.target.value) }) }));
  }
  if (v.kind === "range") {
    const [a, b] = v.value;
    const box = (val, which) => h("input", { type: "number", min: v.min, max: v.max, step: v.step, value: val, disabled: off,
      "aria-label": `${label} ${which}`, onchange: () => send(v.id, { value: [Number(lo.value), Number(hi.value)] }) });
    const lo = box(a, "from");
    const hi = box(b, "to");
    return field(h("span", { class: "widget-slider" }, lo, " to ", hi), true);
  }
  if (v.kind === "text" || v.kind === "password" || v.kind === "textarea") {
    const input = v.kind === "textarea"
      ? h("textarea", { rows: "3", disabled: off, placeholder: v.placeholder || null, "aria-label": name ? null : label }, v.value)
      : h("input", { type: v.kind === "password" ? "password" : "text", value: v.value, disabled: off,
        placeholder: v.kind === "password" && v.filled ? "(set)" : v.placeholder || null, "aria-label": name ? null : label });
    input.addEventListener("change", () => send(v.id, { value: input.value }));
    if (v.kind !== "textarea") {
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); send(v.id, { value: input.value, submit: true }); } });
    }
    return field(input, true);
  }
  if (v.kind === "dropdown") {
    return field(h("select", { disabled: off, "aria-label": name ? null : label, onchange: (e) => send(v.id, { index: Number(e.target.value) }) },
      (v.options || []).map((opt, i) => h("option", { value: String(i), selected: v.index === i || null }, opt))));
  }
  if (v.kind === "radio") {
    const group = `w-${v.id}`;
    return h("fieldset", { class: "widget-radio", disabled: off }, name ? h("legend", {}, name) : null,
      (v.options || []).map((opt, i) => h("label", {}, h("input", { type: "radio", name: group, value: String(i), checked: v.index === i || null,
        onchange: () => send(v.id, { index: i }) }), opt)));
  }
  if (v.kind === "multiselect") {
    return field(h("select", { multiple: true, size: String(Math.min(6, (v.options || []).length || 1)), disabled: off,
      "aria-label": name ? null : label, onchange: (e) => send(v.id, { index: [...e.target.selectedOptions].map((x) => Number(x.value)) }) },
    (v.options || []).map((opt, i) => h("option", { value: String(i), selected: (v.index || []).includes(i) || null }, opt))));
  }
  return h("div", { class: "widget-label muted-text" }, `${v.kind} widget`);
}
