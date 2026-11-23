// Optimize > Assessment > Excellence (FEA-109, Preview): the Fabric Excellence Assessment scorecard,
// built from the approved FEA-108 mockup (Chris approved 2026-11-18). One workspace is assessed
// against versioned controls in ten dimensions (sidecar excellence/, excellence_assess). Reads only.
// Opportunities is the existing opportunity view (optimize-assess.js), one tab over; Gaps lists what
// couldn't be checked and why. Methodology: docs/optimize/excellence-assessment.md.

// `panel` is the Excellence panel on screen now: a run redraws that one when it ends, so switching views
// or leaving Optimize while it runs never leaves a new panel stuck on "Reading...".
const optimizeExcellence = { view: "excellence", workspaceId: "", days: 30, result: null, running: false, error: "", panel: null };

const EXC_VIEWS = [["excellence", "Excellence"], ["opportunities", "Opportunities"], ["gaps", "Gaps"]];
const EXC_DIMS = {
  architecture: "Architecture", performance: "Performance", security: "Security", governance: "Governance",
  data_engineering: "Data engineering", semantic_models: "Semantic models", capacity_finops: "Capacity",
  reliability: "Reliability", devops: "DevOps", local_development: "Local-first",
};
const EXC_SEV = { critical: ["Critical", "fail"], high: ["High", "wrn"], medium: ["Medium", "neu"], low: ["Low", "neu"], informational: ["Info", "neu"] };
const EXC_OUTCOME = { pass: ["Passed", "pass"], fail: ["Failed", "fail"], not_applicable: ["Not applicable", "neu"], not_evaluated: ["Not evaluated", "wrn"], error: ["Not evaluated (error)", "wrn"] };
const EXC_STATUS = { rated: "Rated", provisional: "Provisional", insufficient_evidence: "Insufficient evidence", not_applicable: "Not applicable", not_evaluated: "Not evaluated" };
// Every evidence kind a control can need (test_excellence_ui_names keeps this in step with the catalog).
const EXC_KIND = { "workspace.info": "Workspace settings", "workspace.inventory": "Item list", "workspace.roles": "Role assignments",
  "capacity.state": "Capacity state", "item.optimization": "Definition and run history",
  "workspace.naming": "Workspace and item names", "lakehouse.properties": "Lakehouse properties",
  "item.binding": "Default lakehouse", "item.inspection": "Notebook and pipeline definitions",
  "item.model": "Semantic model definition", "capacity.usage": "Imported Capacity Metrics exports",
  "workspace.git": "Workspace Git connection and status", "lakehouse.tables": "Lakehouse table layout" };
// What a control needed, in words: evidence kinds become their names; free text passes through.
// Only the first letter is lowered, so a product name inside ("Capacity Metrics") keeps its capitals.
const excLow = (s) => s[0].toLowerCase() + s.slice(1);
const excNeeds = (n) => String(n).split(", ").map((k) => (EXC_KIND[k] ? excLow(EXC_KIND[k]) : k)).join(", ");
const EXC_CONF = { measured: "Measured", verified_configuration: "Verified configuration", inferred: "Inferred", insufficient: "Insufficient" };

// A score or a coverage as a whole number. Rounding never shows 100 for less than 100: a score of 100
// needs full coverage (the engine caps it at 99 otherwise), and 99.6% coverage isn't everything.
function excNum(v) {
  if (v === null || v === undefined) return "—";
  const n = Math.round(v);
  return String(n === 100 && v < 100 ? 99 : n);
}

function excSev(s) {
  const [label, cls] = EXC_SEV[s] || [s, "neu"];
  return h("span", { class: `pill-s ${cls} exc-sev` }, label);
}

// The rating chip: the rating itself when rated, Provisional between the two coverage thresholds,
// nothing to rate below them.
function excRating(s) {
  if (!s || s.status === "insufficient_evidence") return h("span", { class: "exc-rating none" }, "Insufficient evidence");
  if (s.status === "provisional") return h("span", { class: "exc-rating prov", title: s.rating?.label || "" }, "Provisional");
  if (!s.rating) return h("span", { class: "exc-rating none" }, EXC_STATUS[s.status] || "Not rated");
  return h("span", { class: "exc-rating" }, s.rating.label);
}

function excDimName(r, id) {
  return EXC_DIMS[id] || r.scores.dimensions[id]?.label || id;
}

function optimizeExcellenceTabs(body) {
  if (!previewShown()) {
    fill(body, h("p", { class: "muted-text" }, "The assessment is a Preview feature. Turn on preview features in Settings to use it."));
    return;
  }
  const e = optimizeExcellence;
  const inner = h("div", { class: "exc-inner" });
  const tabs = h("div", { class: "subtabs exc-views", role: "tablist", "aria-label": "Assessment views" });
  const draw = () => {
    tabs.replaceChildren(...EXC_VIEWS.map(([id, label]) => h("button", {
      class: `tab${e.view === id ? " active" : ""}`, role: "tab", "aria-selected": e.view === id ? "true" : "false",
      onclick: () => { e.view = id; draw(); },
    }, label)));
    if (e.view === "opportunities") optimizeAssessPanel(inner);
    else if (e.view === "gaps") fill(inner, ...excGaps(e.result, () => { e.view = "excellence"; draw(); }));
    else excellencePanel(inner, () => { e.view = "gaps"; draw(); });
  };
  fill(body, tabs, inner);
  draw();
}

function excellencePanel(body, showGaps) {
  const e = optimizeExcellence;
  const scope = h("div", { class: "row assess-scope exc-scope" });
  const view = h("div", { class: "assess-view exc-view" });
  fill(body, scope, view);
  const draw = () => {
    if (e.running) fill(view, h("p", { class: "muted-text", role: "status" }, "Reading the workspace, its roles, definitions and run history... Nothing is written and no Fabric job starts."));
    else if (e.error) fill(view, h("p", { class: "muted-text", role: "alert" }, e.error));
    else if (!e.result) fill(view, excEmpty(() => run({ demo: true })));
    else fill(view, ...excResult(e.result, showGaps));
  };
  const run = async (params) => {
    e.running = true; e.error = ""; draw(); drawScope();
    try {
      e.result = await rpc("excellence_assess", params);
    } catch (err) {
      e.error = friendlyError(err).text;
    } finally {
      e.running = false;
    }
    const p = e.panel;
    if (p?.body.isConnected) p.redraw();
  };
  const drawScope = () => {
    const demo = !!e.result?.fixture;
    const pick = h("select", { "aria-label": "Workspace", class: "assess-pick" },
      h("option", { value: "" }, state.user ? "Listing workspaces..." : "Sign in to list workspaces"));
    const days = h("select", { "aria-label": "Run history" }, [7, 30, 90].map((d) =>
      h("option", { value: d, selected: d === e.days ? true : null }, `Last ${d} days`)));
    days.onchange = () => { e.days = Number(days.value); };
    const go_ = btn(e.result && !demo ? "Assess again" : "Assess", "search", () => {
      if (!pick.value) { toast("Pick a workspace first", true); return; }
      e.workspaceId = pick.value;
      run({ workspace_id: pick.value, days: e.days });
    }, "tool primary", { disabled: e.running ? true : null });
    const demoBtn = demo
      ? btn("Leave the demonstration", null, () => { e.result = null; drawScope(); draw(); }, "tool link")
      : btn("Try the demonstration fixture", null, () => run({ demo: true }), "tool link", { disabled: e.running ? true : null });
    const when = e.result && !demo ? h("span", { class: "muted-text small" }, `Last assessed ${dateTimeText(e.result.assessed_at)}`) : null;
    scope.replaceChildren(...[h("label", {}, "Workspace ", pick), h("label", {}, "Run history ", days), go_, when,
      h("span", { class: "spacer" }), demoBtn,
      h("p", { class: "muted-text small assess-reads" }, "Reads only, with your own access. Nothing is changed in Fabric. ",
        "Ratings are KratosForge product ratings, not a Microsoft certification.")].filter(Boolean));
    fillWorkspaces(pick);
  };
  const fillWorkspaces = async (pick) => {
    if (!state.user) return;
    const workspaces = await loadWorkspaces();
    if (!pick.isConnected) return;
    pick.replaceChildren(h("option", { value: "" }, "Pick a workspace"), ...workspaces.map((w) =>
      h("option", { value: w.id, selected: w.id === e.workspaceId ? true : null }, w.displayName)));
  };
  e.panel = { body, redraw: () => { drawScope(); draw(); } };
  drawScope();
  draw();
}

function excEmpty(tryDemo) {
  return h("div", { class: "card assess-empty exc-empty" },
    h("h3", {}, "How well is this workspace built, and how sure can you be?"),
    h("p", { class: "muted-text" }, "Pick a workspace and choose Assess. KratosForge checks it against versioned controls in ten dimensions, reading:"),
    h("ul", { class: "text" },
      h("li", {}, "the workspace's settings, items and role assignments"),
      h("li", {}, "notebook and pipeline definitions and their job history, as the opportunity view does"),
      h("li", {}, "the capacities you can see")),
    h("p", { class: "muted-text" }, "Anything that can't be read counts against coverage, never as passed."),
    btn("Try the demonstration fixture", null, tryDemo, "tool link"));
}

function excFixtureNote(r) {
  if (!r.fixture) return null;
  const note = String(r.fixture_note || "").replace(/^Demonstration fixture:\s*/i, "");
  return h("div", { class: "assess-fixture", role: "note" }, h("b", {}, "DEMONSTRATION FIXTURE. "), note.charAt(0).toUpperCase() + note.slice(1));
}

function excResult(r, showGaps) {
  const s = r.scores, o = s.overall, out = [excFixtureNote(r)];
  const notEvaluated = r.outcomes.filter((x) => x.outcome === "not_evaluated" || x.outcome === "error").length;
  if (s.critical_risk) {
    const f = r.findings.find((x) => x.severity === "critical" && !x.advisory);
    out.push(h("div", { class: "exc-crit", role: "alert" },
      h("span", {}, h("b", {}, "Critical Risk. "), `A critical control failed${f ? `: ${f.discovered}` : "."} This flag stands whatever the score.`),
      f ? btn("Open the finding", null, () => excFinding(r, f), "tool outline") : null));
  }
  const stat = (k, v, ...rest) => h("div", { class: "exc-stat" }, h("span", { class: "exc-k" }, k), h("span", { class: "exc-v" }, v), ...rest);
  const conf = { high: "Confidence high", medium: "Confidence medium", low: "Confidence low" }[s.confidence] || "";
  out.push(h("div", { class: "exc-hero" },
    stat("Overall score", o.status === "insufficient_evidence" ? "—" : excNum(o.score), excRating(o)),
    stat("Assured score", excNum(o.assured), h("span", { class: "exc-h" }, "Counts anything not checked as not passed. Used to rank and compare.")),
    stat("Coverage", [excNum(o.coverage), o.coverage === null || o.coverage === undefined ? "" : h("small", {}, "%")],
      h("span", { class: "exc-h" }, "of the applicable controls' weight was checked")),
    stat("Status", h("span", { class: "exc-v-s" }, EXC_STATUS[o.status] || o.status),
      h("span", { class: "exc-h" }, `${conf}. It needs most of the checked weight on measured or verified evidence, and enough coverage.`))));
  if (o.status === "provisional") {
    out.push(h("div", { class: "exc-info" }, `The rating is provisional because only ${excNum(o.coverage)}% could be checked. `,
      btn("See what wasn't", null, showGaps, "tool link")));
  } else if (o.status === "insufficient_evidence") {
    out.push(h("div", { class: "exc-info grey" }, `Too little could be checked to rate this workspace. ${notEvaluated} control${notEvaluated === 1 ? "" : "s"} couldn't decide from their evidence. `,
      btn("See Gaps for each one", null, showGaps, "tool link")));
  }
  out.push(h("h3", { class: "exc-sec" }, "Ten dimensions"),
    h("div", { class: "exc-dims" }, Object.keys(EXC_DIMS).map((id) => excDimCard(r, id))));
  out.push(h("h3", { class: "exc-sec" }, "Next actions ", r.fixture ? h("span", { class: "fx-chip" }, "Demonstration fixture") : null),
    r.next_actions.length
      ? h("div", { class: "exc-acts" }, r.next_actions.map((a) => excAction(r, a)))
      : h("p", { class: "muted-text" }, "No control failed. That covers only what could be checked, so see the coverage above and Gaps."));
  const c = s.counts;
  out.push(h("h3", { class: "exc-sec" }, "Controls"), h("div", { class: "exc-counts" },
    h("span", {}, h("b", {}, String(c.pass)), "Passed"), h("span", {}, h("b", {}, String(c.fail)), "Failed"),
    h("span", {}, h("b", {}, String(c.not_applicable)), "Not applicable"),
    h("span", {}, h("b", {}, String(c.not_evaluated + (c.errors || 0))), "Not evaluated ", btn("why", null, showGaps, "tool link"))),
    h("p", { class: "exc-foot" }, `Profile: ${r.profile.title}${r.profile.id === "baseline" ? " (default)" : ""}, version ${r.profile.version} · `,
      `${Object.keys(r.rule_versions).length} controls · Policy ${r.policy_version} · KratosForge product rating, not a certification.`));
  return out;
}

function excDimCard(r, id) {
  const d = r.scores.dimensions[id];
  const name = excDimName(r, id);
  if (!d || d.status === "not_applicable") {
    return h("button", { type: "button", class: "exc-dim ne", onclick: () => excDimension(r, id) },
      h("span", { class: "exc-dn" }, name), h("span", { class: "exc-ds" }, "—"), h("span", { class: "exc-dr none" }, "Not applicable"),
      h("span", { class: "exc-dm" }, "No control applies here yet."));
  }
  if (d.status === "not_evaluated" || d.coverage === 0) {
    const need = r.outcomes.find((x) => x.dimension === id && x.needs)?.needs;
    return h("button", { type: "button", class: "exc-dim ne", onclick: () => excDimension(r, id) },
      h("span", { class: "exc-dn" }, name), h("span", { class: "exc-ds" }, "—"), h("span", { class: "exc-dr none" }, "Not evaluated"),
      h("span", { class: "exc-dm" }, need ? `Needs ${excNeeds(need)}. ` : "Nothing here could be checked. ", "It counts against coverage, never as passed."));
  }
  const label = d.status === "provisional" ? "Provisional" : d.status === "insufficient_evidence" ? "Insufficient evidence" : d.rating?.label || "";
  return h("button", { type: "button", class: "exc-dim", onclick: () => excDimension(r, id), "aria-label": `${name}: open its controls` },
    h("span", { class: "exc-dn" }, name), h("span", { class: "exc-ds" }, d.status === "insufficient_evidence" ? "—" : excNum(d.score)),
    h("span", { class: `exc-dr${d.status === "rated" ? "" : d.status === "provisional" ? " prov" : " none"}` }, label),
    h("span", { class: "exc-dm" }, `Coverage ${excNum(d.coverage)}% · Assured ${excNum(d.assured)}`));
}

function excAction(r, a) {
  const f = r.findings.find((x) => x.id === a.finding_id);
  const dim = f ? excDimName(r, f.dimension) : "";
  return h("div", { class: "exc-act" }, excSev(a.severity),
    h("span", {}, h("span", { class: "exc-t" }, f?.discovered || a.title), h("br"),
      h("span", { class: "exc-w" }, [dim, a.solution].filter(Boolean).join(" · "), ` Effort: ${a.effort}.`, f?.rollback ? ` Undo: ${f.rollback}` : "")),
    f ? btn("Open", null, () => excFinding(r, f), "tool outline", { "aria-label": `Open the finding: ${a.title}` }) : h("span"));
}

function excEvidenceList(r, ids) {
  const byId = new Map(r.evidence.map((x) => [x.id, x]));
  const got = (ids || []).map((id) => byId.get(id)).filter(Boolean);
  if (!got.length) return h("p", { class: "muted-text small" }, "No evidence is cited.");
  return h("ul", { class: "assess-ev" }, got.map((x) => h("li", {},
    h("span", {}, `${x.subject?.name || x.subject?.id || ""}: ${EXC_KIND[x.kind] || x.kind}`),
    h("span", { class: "muted-text small" }, `${EXC_CONF[x.confidence] || x.confidence} · ${x.source} · collected ${dateTimeText(x.collected_at)}`))));
}

function excDimension(r, id) {
  const d = r.scores.dimensions[id], name = excDimName(r, id);
  const outs = r.outcomes.filter((x) => x.dimension === id);
  const body = h("div", { class: "scroll assess-evidence" },
    r.fixture ? h("div", { class: "row" }, h("span", { class: "fx-chip" }, "Demonstration fixture")) : null,
    h("p", { class: "text" }, d && d.status !== "not_applicable"
      ? `Score ${excNum(d.score)}, assured ${excNum(d.assured)}, coverage ${excNum(d.coverage)}%. Weight ${d.weight} of 100 in the ${r.profile.title} profile.`
      : "No control in this version applies to this dimension yet, so it's left out of the overall score. It is never shown as passing."),
    outs.length ? h("table", { class: "list compact exc-outcomes" },
      h("thead", {}, h("tr", {}, ["Control", "Resource", "Outcome", "What it found"].map((x) => h("th", {}, x)))),
      h("tbody", {}, outs.map((x) => {
        const [label, cls] = EXC_OUTCOME[x.outcome] || [x.outcome, "neu"];
        return h("tr", {}, h("td", {}, h("div", {}, x.control_id), excSev(x.severity)), h("td", {}, x.subject?.name || x.subject?.id),
          h("td", {}, h("span", { class: `pill-s ${cls}` }, label)),
          h("td", {}, h("div", {}, x.text), x.needs ? h("div", { class: "muted-text small" }, `Needs: ${excNeeds(x.needs)}`) : null,
            x.evidence_ids?.length ? h("details", {}, h("summary", {}, "Evidence"), excEvidenceList(r, x.evidence_ids)) : null));
      }))) : null);
  const overlay = showDialog(name, body, [btn("Close", null, closeDialog, "tool outline")]);
  overlay.querySelector(".dialog")?.classList.add("assess-sheet", "exc-sheet");
}

function excFinding(r, f) {
  const sec = (title, ...kids) => h("section", {}, h("h4", {}, title), ...kids);
  const body = h("div", { class: "scroll assess-evidence" },
    h("div", { class: "row" }, r.fixture ? h("span", { class: "fx-chip" }, "Demonstration fixture") : null, excSev(f.severity),
      h("span", { class: "pill-s neu" }, excDimName(r, f.dimension)), h("span", { class: "pill-s neu" }, `Effort ${f.effort}`),
      h("span", { class: "pill-s neu" }, `Risk of change ${f.risk}`), h("span", { class: "pill-s info" }, f.confidence_label)),
    sec("What was found", h("p", { class: "text" }, f.discovered), h("p", { class: "muted-text small" }, `${f.resource?.name || f.resource?.id} · ${f.requirement_label}`)),
    sec("Why it matters", h("p", { class: "text" }, f.why)),
    sec("Recommended", h("p", { class: "text" }, f.solution), f.steps?.length ? h("ol", { class: "text" }, f.steps.map((x) => h("li", {}, x))) : null),
    f.benefit ? sec("Expected benefit", h("p", { class: "text" }, f.benefit)) : null,
    sec("How to check it worked", h("p", { class: "text" }, f.validation)),
    sec("How to undo it", h("p", { class: "text" }, f.rollback)),
    sec("Evidence", excEvidenceList(r, f.evidence_ids)),
    f.references?.length ? sec("References", h("div", { class: "row exc-refs" }, f.references.map((x) =>
      btn(x.title, "open", () => openLink(x.url), "tool link")))) : null,
    h("p", { class: "muted-text small" }, `Rule ${f.rule}. Nothing is changed for you, because KratosForge only reads.`));
  const overlay = showDialog(f.title, body, [btn("Close", null, closeDialog, "tool outline")]);
  overlay.querySelector(".dialog")?.classList.add("assess-sheet");
}

// Gaps: what couldn't be checked, and why. Each of these counts against coverage and the assured score.
function excGaps(r, back) {
  if (!r) {
    return [h("div", { class: "card assess-empty" }, h("p", { class: "muted-text" }, "Gaps lists what an assessment couldn't check. Assess a workspace first."),
      btn("Go to Excellence", null, back, "tool link"))];
  }
  const failed = r.reads.filter((x) => !x.ok);
  const notEval = r.outcomes.filter((x) => x.outcome === "not_evaluated" || x.outcome === "error");
  const items = r.item_coverage.filter((x) => x.state !== "inspected");
  const out = [excFixtureNote(r)];
  if (!failed.length && !notEval.length && !items.length) {
    out.push(h("p", { class: "muted-text" }, "Everything that applies was read and checked."));
    return out;
  }
  if (failed.length) {
    out.push(h("section", { class: "card" }, h("h4", {}, "Reads that failed"),
      h("ul", { class: "text" }, failed.map((x) => h("li", {}, h("b", {}, cap1(x.read)), `: ${x.problem || "Couldn't be read."}`)))));
  }
  if (notEval.length) {
    out.push(h("section", { class: "card" }, h("h4", {}, `Controls not evaluated (${notEval.length})`),
      h("table", { class: "list compact" },
        h("thead", {}, h("tr", {}, ["Control", "Dimension", "Resource", "Why"].map((x) => h("th", {}, x)))),
        h("tbody", {}, notEval.map((x) => h("tr", {}, h("td", {}, x.control_id), h("td", {}, excDimName(r, x.dimension)),
          h("td", {}, x.subject?.name || x.subject?.id), h("td", {}, x.text, x.needs ? h("div", { class: "muted-text small" }, `Needs: ${excNeeds(x.needs)}`) : null)))))));
  }
  if (items.length) {
    out.push(h("section", { class: "card" }, h("h4", {}, `Items not inspected (${items.length})`),
      h("ul", { class: "text" }, items.map((x) => h("li", {}, h("b", {}, x.item?.name || x.item?.id),
        ` (${ASSESS_TYPE[x.item?.item_type] || x.item?.item_type || "item"}): `, x.state === "unsupported" ? "not supported yet. " : "not inspected. ", x.reason || "")))));
  }
  return out;
}
