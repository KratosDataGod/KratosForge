// Best practices: checks and templates (Preview). Three places:
//   Platform > Govern > Governance > Templates   reusable policy definitions: built in, or your organization's
//   Platform > Govern > Governance > Check this project   Check now runs the open project's policies on this PC (warn only)
//   OneLake > Data products > a product  its checklist, from the contract template it follows
// The platform (central/policy.py) decides who may add templates; the checks are governance_checks.py.

const BP_CATEGORY = { naming: "Naming", schema: "Schema", quality: "Quality", classification: "Classification",
  ownership: "Ownership", contract: "Data contract", deployment: "Deployment" };
const BP_MARK = { pass: ["✓", "bp-mark pass"], fail: ["✗", "bp-mark fail"], unknown: ["!", "bp-mark unknown"] };

function bpMark(kind, label) {
  const [sym, cls] = BP_MARK[kind];
  return h("span", { class: cls, "aria-label": label || kind, title: label || "" }, sym);
}

// The switch between Policies, Templates and Check this project.
function govViewNav(body) {
  const pick = (v) => { govPage.view = v; govPage.open = null; platformGovernanceTab(body); };
  return preview("bestPractices", h("div", { class: "row tight gov-views", role: "group", "aria-label": "Governance" },
    [["policies", "Policies"], ["templates", "Templates"], ["check", "Check this project"]].map(([v, label]) =>
      h("button", { class: `chip${govPage.view === v ? " on" : ""}`, "aria-pressed": String(govPage.view === v), onclick: () => pick(v) }, label))));
}

function govBestPracticeHow() {
  return howItWorks("platform.best-practices", {
    reads: "Templates: the built-in ones in the app, and your organization's from the platform's catalog. Check now: the open project's files on this PC (entities, products, governance.toml), the platform's catalog (policies, assignments, exceptions, catalog text, product terms and endorsement) and the run logs Monitor already reads.",
    changes: "Template create, edit and delete write catalog.policy_template in the platform's catalog, and Save as template does too. Use writes a policy draft (catalog.policy and catalog.policy_version). Who can add templates is a platform setting (config.setting). Check now writes only its last result, to governance_checks.json on this PC. Label it writes the column's classification to catalog.enrichment, after you confirm. Nothing changes in Fabric.",
    results: "Template changes are in Platform > Govern > Audit. Check now is warn only: nothing is blocked, and Block policies aren't enforced yet. Checkers that run in Fabric are never started here: their last stored findings show, or not run.",
    anchor: "best-practices-checks-and-templates-preview",
  });
}

// ---------- Templates ----------

async function governanceTemplatesTab(body) {
  const [t, o] = await Promise.all([platformCall("governance_templates", {}, "Could not read the templates"),
    platformCall("governance_overview", {}, "Could not read the policies")]);
  if (!stillHere("governance") || govPage.view !== "templates") return;
  const rerender = () => governanceTemplatesTab(body);
  if (!t || !o) { body.replaceChildren(govViewNav(body)); return; }
  const admin = !!o.me?.admin;
  const save = (id) => (definition) => platformCall("governance_template_save", { template: definition, id: id || "" }, "Not saved");
  const newTemplate = () => policyDialog(o.options, null, rerender, { title: "New template", definition: null, save: save("") });
  const authors = h("select", { "aria-label": "Who can add templates", onchange: async (e) => {
    if (await platformCall("governance_template_authors", { authors: e.target.value }, "Not changed")) rerender();
  } }, [["admins", "Platform admins only"], ["domain_owners", "Platform admins and domain owners"]].map(([v, label]) =>
    h("option", { value: v, selected: t.authors === v ? true : null }, label)));
  const row = (x) => h("tr", { "data-template": x.id },
    h("td", {}, h("b", {}, x.name), " ", h("span", { class: "muted-text small" }, `(${BP_CATEGORY[x.category] || x.category})`),
      h("div", { class: "muted-text small" }, x.description || ""),
      h("div", { class: "muted-text small mono" }, x.definition?.rule ? `Checked by ${x.definition.rule}` : "")),
    h("td", {}, h("span", { class: `pill-s ${x.builtin ? "info" : "neu"} bp-source` }, x.builtin ? "Built in" : "Yours"),
      x.builtin ? null : h("div", { class: "muted-text small" }, x.created_by || "this PC")),
    h("td", {}, h("span", { class: "row tight" },
      admin ? btn("Use", null, () => useTemplateDialog(x, body), "tool primary small") : null,
      x.can_edit ? btn("Edit", null, () => policyDialog(o.options, null, rerender, { title: `Edit ${x.name}`, id: x.id, definition: x.definition, save: save(x.id) }), "tool small") : null,
      x.can_edit ? btn("Delete", "delete", async () => {
        if (!(await confirmDialog(`Delete the template ${x.name}?`, "Policies made from it stay as they are. It's removed from catalog.policy_template for everyone.", "Delete", { danger: true }))) return;
        if (await platformCall("governance_template_delete", { id: x.id }, "Not deleted")) { toast(`Deleted the template ${x.name}`, false, { log: true }); rerender(); }
      }, "tool small danger") : null)));
  body.replaceChildren(govViewNav(body), withHowItWorks(h("div", { class: "card pad bp-templates" },
    h("div", { class: "row" }, h("h2", { class: "grow" }, "Templates"),
      t.can_add ? btn("New template", "add", newTemplate, "tool primary") : null),
    h("p", { class: "muted-text small" }, "A template is a policy definition you reuse. Use makes a policy draft from it; any policy can be saved as a template from its page."),
    ...(t.problems || []).map((x) => h("p", { class: "wrn-text small" }, x)),
    h("table", { class: "list" }, h("tbody", {}, t.templates.map(row))),
    h("p", { class: "muted-text small bp-who" }, "Platform admins choose who can add templates."),
    admin ? h("label", { class: "field" }, "Who can add templates", authors) : null), govBestPracticeHow()));
}

function useTemplateDialog(x, body) {
  const id = h("input", { value: x.id, class: "mono", "aria-label": "Policy id" });
  const name = h("input", { value: x.name, "aria-label": "Policy name" });
  showDialog(`Use ${x.name}`, h("div", {},
    h("p", { class: "text" }, "Makes a new policy draft from this template in the platform's catalog. Nothing is in force until you publish and assign it."),
    h("label", { class: "field" }, "Policy id", id), h("label", { class: "field" }, "Name", name)), [
    btn("Cancel", null, closeDialog, "tool outline"),
    btn("Make the draft", null, async () => {
      const res = await platformCall("governance_template_use", { template: x.id, id: id.value.trim(), name: name.value.trim() }, "Not made");
      if (!res) return;
      closeDialog();
      toast(`Made the draft ${res.name}`, false, { log: true });
      govPage.view = "policies"; govPage.open = res.id; govPage.sub = "definition"; govPage.version = null;
      platformGovernanceTab(body);
    }, "tool primary")]);
}

function saveAsTemplateDialog(p) {
  const id = h("input", { value: `${p.id}-template`, class: "mono", "aria-label": "Template id" });
  const name = h("input", { value: p.name, "aria-label": "Template name" });
  showDialog(`Save ${p.name} as a template`, h("div", {},
    h("p", { class: "text" }, `Copies ${p.current_version ? `v${p.current_version}` : "the draft"}'s definition into a new template in catalog.policy_template. The policy doesn't change.`),
    h("label", { class: "field" }, "Template id", id), h("label", { class: "field" }, "Name", name)), [
    btn("Cancel", null, closeDialog, "tool outline"),
    btn("Save template", "save", async () => {
      const res = await platformCall("governance_template_from_policy", { policy: p.id, id: id.value.trim(), name: name.value.trim() }, "Not saved");
      if (res) { closeDialog(); toast(`Saved the template ${res.name}`, false, { log: true }); }
    }, "tool primary")]);
}

// ---------- Check this project ----------

async function governanceCheckTab(body, fresh = null) {
  const last = fresh ? { project: fresh.project, result: fresh } : await platformCall("governance_last_check", {}, "Could not read the last check");
  if (!stillHere("governance") || govPage.view !== "check") return;
  const rerender = (r) => governanceCheckTab(body, r);
  const r = last?.result;
  const run = btn("Check now", "shield", async () => {
    run.disabled = true;
    run.textContent = "Checking...";
    const res = await platformCall("governance_check_now", {}, "Could not check the project");
    if (res) rerender(res);
    else { run.disabled = false; run.textContent = "Check now"; }
  }, "tool primary");
  if (!last?.project) {
    body.replaceChildren(govViewNav(body), withHowItWorks(h("div", { class: "card pad" }, h("h2", {}, "Check a project"),
      h("p", { class: "muted-text" }, "Open a metadata project to check it against its policies.")), govBestPracticeHow()));
    return;
  }
  const n = r?.counts?.to_look_at || 0;
  const head = h("div", { class: "card pad bp-check-head" },
    h("div", { class: "row" }, h("h2", { class: "grow bp-summary" }, r ? `${last.project}: ${n ? `${n} to look at` : "nothing to look at"}` : `${last.project}: not checked yet`), run),
    r ? h("p", { class: "muted-text small bp-when" }, `Checked on this PC ${relTime(r.checked_at)}. ${r.enforcement}`)
      : h("p", { class: "muted-text small" }, "Check now runs the policies in force for this project on this PC. Warn only: nothing is blocked."),
    r?.counts?.unknown ? h("p", { class: "wrn-text small" }, `${r.counts.unknown} couldn't be checked: what they need isn't recorded. Each says why.`) : null,
    ...(r?.problems || []).map((x) => h("p", { class: "wrn-text small" }, x)));
  const list = r ? r.policies.map((p) => bpPolicyCard(p, last.project, () => run.click())) : [];
  body.replaceChildren(govViewNav(body), withHowItWorks(h("div", {}, head,
    r && !r.policies.length ? h("p", { class: "muted-text card pad" }, "No policy is in force for this project. Assign one on Policies, or add it in governance.toml.") : null,
    ...list), govBestPracticeHow()));
}

function bpPolicyCard(p, project, recheck) {
  const enforcement = govPill(GOV_ENFORCEMENT, p.enforcement);
  const head = (mark, extra) => h("div", { class: "row tight" }, mark, h("b", {}, p.name), " ", enforcement, extra ? h("span", { class: "muted-text small" }, extra) : null);
  if (p.status === "pass") {
    return h("div", { class: "card pad bp-policy pass", "data-policy": p.id },
      head(bpMark("pass", "Passes"), ` ${p.passed} of ${p.checked} pass${p.checked ? "" : ": nothing here to check"}`),
      ...bpNotRun(p));
  }
  if (p.status === "not_checked" || p.status === "not_run") {
    return h("div", { class: "card pad bp-policy muted", "data-policy": p.id }, head(bpMark("unknown", "Not checked"), ""),
      p.note ? h("p", { class: "muted-text small" }, p.note) : null, ...bpNotRun(p));
  }
  const ask = btn("Ask for an exception", null, () => newRequestDialog(() => {}, "exception",
    { policy: p.id, scope: "project", scope_target: project }), "tool outline small");
  return h("div", { class: `card pad bp-policy ${p.status}`, "data-policy": p.id },
    h("div", { class: "row" }, h("span", { class: "grow" }, head(bpMark(p.findings.length ? "fail" : "unknown"),
      ` ${p.findings.length} to look at${p.unknown.length ? `, ${p.unknown.length} unknown` : ""}`)), p.status === "excepted" ? null : ask),
    h("ul", { class: "bp-findings" }, [...p.findings, ...p.unknown].map((f) => h("li", { "data-rule": f.rule_id },
      bpMark(f.outcome === "unknown" ? "unknown" : "fail"), " ",
      h("span", { class: "mono small" }, f.object), " ", f.description, " ",
      h("span", { class: "muted-text small" }, `${f.rule} · ${f.severity}`),
      f.excepted ? h("div", { class: "wrn-text small bp-excepted" }, f.excepted) : null,
      !f.excepted && f.fix ? h("div", {}, bpFix(f.fix, recheck)) : null))),
    ...bpNotRun(p));
}

function bpNotRun(p) {
  return (p.not_run || []).map((x) => h("p", { class: "muted-text small" },
    `${x.name} runs in Fabric, so Check now doesn't start it: `, x.last ? `last findings ${relTime(x.last.at)} (${x.last.findings.length}).` : "not run."));
}

// A finding's fix: an action here when one is safe, else where to fix it.
function bpFix(fix, recheck) {
  if (fix.action === "label") return btn(fix.label, null, () => labelDialog(fix, recheck), "tool outline small");
  if (fix.action === "open_entity") return btn(fix.label, null, () => openEntity(fix.entity), "tool link small");
  if (fix.action === "edit_product") return btn(fix.label, null, () => goMeta("products"), "tool link small");
  if (fix.action === "open_product_page") {
    return btn(fix.label, null, async () => {
      const out = await platformCall("platform_products", {}, "Could not read the catalog");
      const prod = out?.products.find((x) => x.id === fix.product_id);
      if (!prod) return;
      productsPage.open = prod; catalogPage.tab = "products"; go("onelake");
    }, "tool link small");
  }
  return null;
}

// Label it: asks first and says what changes (the column's classification in the platform's catalog).
function labelDialog(fix, recheck) {
  const labels = fix.labels?.length ? fix.labels : ["confidential", "restricted"];
  const pick = h("select", { "aria-label": "Label" }, labels.map((l) => h("option", { value: l }, l[0].toUpperCase() + l.slice(1))));
  showDialog(`Label ${fix.column}?`, h("div", {},
    h("p", { class: "text bp-label-what" }, `Sets the classification of ${fix.column} in ${fix.entity} in the platform's catalog (catalog.enrichment). The project's files don't change.`),
    h("label", { class: "field" }, "Label", pick)), [
    btn("Cancel", null, closeDialog, "tool outline"),
    btn("Label it", null, async () => {
      const ok = await metaCall("mesh_save_about", { name: fix.entity, column: fix.column, fields: { classification: pick.value } }, "Not labelled");
      if (!ok) return;
      closeDialog();
      toast(`Labelled ${fix.column} ${pick.value}`, false, { log: true });
      recheck();
    }, "tool primary")]);
}

// ---------- a data product's checklist (OneLake > Data products > a product) ----------

function productChecklistCard(p) {
  const card = h("div", { class: "card pad bp-checklist" }, h("b", {}, "Checklist"), loading());
  rpc("governance_product_checklist", { product_id: p.id }).then((c) => {
    const from = c.source.builtin ? `From the built-in ${c.source.name} template: no contract policy is in force for ${c.project}.`
      : `From the policy ${c.source.name}, in force for ${c.project}.`;
    card.replaceChildren(h("div", { class: "row tight" }, h("b", {}, "Checklist"), previewTag()),
      h("p", { class: "muted-text small bp-from" }, from),
      h("ul", { class: "bp-rows" }, c.rows.map((r) => h("li", { "data-rule": r.rule_id },
        bpMark(r.mark, { pass: "Passes", fail: "Doesn't pass", unknown: "Unknown" }[r.mark]), " ", h("b", {}, r.label), " ",
        h("span", { class: r.mark === "fail" ? "small" : "muted-text small" }, r.detail)))),
      ...(c.problems || []).map((x) => h("p", { class: "wrn-text small" }, x)),
      h("p", { class: "muted-text small" }, "Read from the platform's catalog and, for your open project, its files and run log. Nothing is written."));
  }).catch((err) => card.replaceChildren(h("b", {}, "Checklist"), h("p", { class: "fail-text small" }, trustError(err))));
  return card;
}

registerFeature({
  id: "bestPractices", preview: true,
  commands: [
    { label: "Check this project against its policies", icon: "shield", keywords: "governance best practice check policies findings", preview: true,
      run: () => { govPage.view = "check"; govPage.open = null; goPlatform("governance"); } },
    { label: "Policy templates", icon: "shield", keywords: "governance templates policy reuse", preview: true,
      run: () => { govPage.view = "templates"; govPage.open = null; goPlatform("governance"); } },
  ],
});
