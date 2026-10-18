// The risk check before a run (FINOPS-004): what could stop or slow it, with the evidence, shown
// in the run dialogs. The engine only reads to work it out. Blocked means a real restriction and
// has no "run anyway"; going ahead past warnings is recorded with risk_acknowledge.

const RISK_PILL = { pass: "pass", warning: "wrn", blocked: "fail", unknown: "neu" };
const RISK_ICON = { pass: "ok", warning: "warning", blocked: "error", unknown: "info" };

function riskFinding(f) {
  return h("div", { class: `risk-finding ${f.outcome}` }, icon(RISK_ICON[f.outcome] || "info", 18),
    h("div", {},
      h("b", {}, f.title), f.measurement ? [" ", measureBadge(f.measurement)] : null,
      f.detail ? h("div", {}, f.detail) : null,
      f.evidence?.length ? h("div", { class: "muted-text small" }, f.evidence.join(" · ")) : null,
      f.remedy ? h("div", { class: "risk-remedy" }, f.remedy) : null));
}

// Notebooks that don't need Fabric can be offered "Run on this PC" when Fabric is blocked.
function riskLocalAlternative(risk) {
  return (risk?.findings || []).some((f) => f.check === "local_alternative" && f.title !== "Needs Fabric");
}

// The findings box. A pass folds away; otherwise what matters is shown and the rest folds away.
// opts.onRecheck reads the capacity again; opts.box gets the acknowledgement checkbox, if asked for.
function riskPanel(risk, opts = {}) {
  if (!risk) return null;
  const findings = risk.findings || [];
  // Always shown, tagged Preview: a warning shouldn't vanish because preview features are hidden.
  const pill = previewMarked("risk", h("span", { class: "risk-pills" },
    h("span", { class: `pill-s ${RISK_PILL[risk.result] || "neu"}` }, risk.label || risk.result)));
  const line = (f) => h("li", {}, f.title, f.measurement ? [" ", measureBadge(f.measurement)] : null);
  if (risk.result === "pass") {
    return h("details", { class: "risk-panel pass" },
      h("summary", {}, pill, h("span", {}, risk.summary), h("span", { class: "grow" }), h("span", { class: "link-text small" }, "Show checks")),
      findings.length ? h("ul", {}, findings.map(line)) : null);
  }
  const main = (f) => f.outcome === "blocked" || f.outcome === "warning" || (risk.result === "unknown" && f.outcome === "unknown")
    || (risk.result === "blocked" && f.check === "local_alternative");
  const shown = findings.filter(main);
  const rest = findings.filter((f) => !main(f));
  return h("div", { class: `risk-panel ${risk.result}` },
    h("div", { class: "risk-head" }, pill, h("b", {}, risk.summary), h("span", { class: "grow" }),
      opts.onRecheck ? h("a", { href: "#", class: "small", onclick: (e) => { e.preventDefault(); opts.onRecheck(); } }, "Check again") : null),
    shown.map(riskFinding),
    rest.length ? h("details", { class: "risk-rest" },
      h("summary", {}, `${rest.length} other check${rest.length === 1 ? "" : "s"} passed or ${rest.length === 1 ? "isn't" : "aren't"} known`),
      h("ul", {}, rest.map(line))) : null,
    opts.box || null);
}

// Ticked when the settings ask for it; the run button checks it.
function riskAckBox(risk) {
  if (!risk?.needs_acknowledgement) return null;
  return h("label", { class: "risk-ack" }, h("input", { type: "checkbox" }), "I've looked at these and want to run anyway");
}

// Going ahead past warnings: say whether the box was needed and not ticked, then record it.
// A changed or blocked result is refused by the engine and the dialog says so.
async function riskProceed(target, risk, box) {
  if (!risk || risk.result !== "warning") return true;
  if (box && !box.querySelector("input").checked) {
    toast("Tick \"I've looked at these\" first, or cancel.", true);
    return false;
  }
  return !!(await attempt(() => rpc("risk_acknowledge", { id: target.id, action: target.action, env: target.env || null,
    risk_id: risk.id }), "Not started"));
}

// Before a run that has no dialog of its own (notebook Run in Fabric, Data mesh Run): nothing to
// show when it passes or can't be checked; otherwise a dialog. Resolves true to go ahead.
// target: { id, action, env, title, runLabel, alt: { label, run } }.
async function riskGate(target, refresh = false) {
  const risk = await rpc("risk_check", { id: target.id, action: target.action, env: target.env || null, refresh }).catch(() => null);
  if (!risk || risk.result === "pass" || risk.result === "unknown") return true;
  return new Promise((resolve) => {
    const box = riskAckBox(risk);
    const again = async () => { closeDialog(); resolve(await riskGate(target, true)); };
    const actions = risk.result === "blocked"
      ? [btn("Check again", null, again, "tool outline"),
        target.alt && riskLocalAlternative(risk) ? btn(target.alt.label, null, () => { closeDialog(); resolve(false); target.alt.run(); }, "tool outline") : null,
        btn("Close", null, () => { closeDialog(); resolve(false); }, "tool primary")]
      : [btn("Cancel", null, () => { closeDialog(); resolve(false); }, "tool outline"),
        btn(target.runLabel || "Run anyway", null, async () => {
          if (!(await riskProceed(target, risk, box))) return;
          closeDialog();
          resolve(true);
        }, "tool primary")];
    showDialog(target.title, h("div", { class: "wl-confirm" },
      riskPanel(risk, { onRecheck: again, box }),
      h("p", { class: "muted-text small" }, risk.note)), actions.filter(Boolean));
  });
}

// The "Before a run" fields of the Prices dialog; values() is what set_risk_settings takes.
function riskSettingsFields(cfg) {
  const num = (label, value, attrs) => {
    const input = h("input", { type: "number", value: value ?? "", ...attrs });
    return [h("label", { class: "field" }, label, input), input];
  };
  const [cu, cuIn] = num("Warn above this many CU hours per run", cfg.warn_run_cu_hours, { min: "0", step: "0.1", placeholder: "Off" });
  const [failed, failedIn] = num("Warn when this many of the last 5 runs failed", cfg.warn_failed_runs, { min: "0", max: "5", step: "1" });
  const [window_, windowIn] = num("Warn when a schedule on this PC runs it within (minutes)", cfg.schedule_window_minutes, { min: "0", step: "5" });
  const ack = h("input", { type: "checkbox", checked: !!cfg.require_acknowledgement });
  return {
    el: [h("h3", {}, "Before a run"),
      h("div", { class: "form-grid" }, cu, failed, window_),
      h("label", { class: "risk-ack" }, ack, "Ask me to tick \"I've looked at these\" before running with warnings"),
      h("p", { class: "muted-text small" }, "The CU figure is measured when an import matched the run, otherwise the app's estimate, ",
        "and the warning says which. A cost never blocks a run; only a real restriction does.")],
    values: () => ({ warn_run_cu_hours: cuIn.value.trim() === "" ? "" : Number(cuIn.value), warn_failed_runs: Number(failedIn.value || 0),
      schedule_window_minutes: Number(windowIn.value || 0), require_acknowledgement: ack.checked }),
  };
}
