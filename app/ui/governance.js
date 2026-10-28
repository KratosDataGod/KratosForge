// Platform > Govern > Governance: the enterprise policy registry. Platform admins write a policy as a draft,
// publish it as a numbered version that never changes, and apply it at enterprise, domain, project
// or entity scope; domain owners apply policies to their own domains, tightening only. The platform
// (central/policy.py) enforces all of it; this page only hides buttons a person can't use.

const GOV_STATUS = { draft: ["Draft", "neu"], published: ["Published", "pass"], deprecated: ["Deprecated", "wrn"], retired: ["Retired", "neu"] };
const GOV_ENFORCEMENT = { off: ["Off", "neu"], advisory: ["Advisory", "info"], warn: ["Warn", "wrn"], block: ["Block (not enforced yet)", "fail"] };
const GOV_STRICTER = { higher: "Higher", lower: "Lower", superset: "More items", subset: "Fewer items", true: "On", fixed: "Fixed" };
const GOV_SCOPE = { enterprise: "Enterprise", domain: "Domain", project: "Project", entity: "Entity" };
const govPage = { query: "", category: "", status: "", open: null, sub: "definition", version: null, explain: null, view: "policies" };

function govPill(map, key) {
  const [label, cls] = map[key] || [key, "neu"];
  return h("span", { class: `pill-s ${cls}` }, label);
}

function govDate(iso) {
  return dateText(iso);
}

function govValue(v) {
  return typeof v === "string" ? v : JSON.stringify(v);
}

function govParse(text) {
  const t = String(text).trim();
  try { return JSON.parse(t); } catch { return t; }
}

function govCan(o) {
  const me = o?.me || platformPage.me || {};
  return { admin: !!me.admin, domains: me.domains_owned || [] };
}

async function platformGovernanceTab(body) {
  if (!previewShown()) govPage.view = "policies";
  if (govPage.view === "templates") return governanceTemplatesTab(body);
  if (govPage.view === "check") return governanceCheckTab(body);
  if (govPage.open) return governancePolicy(body, govPage.open);
  const g = govPage;
  const o = await platformCall("governance_overview", { query: g.query, category: g.category, status: g.status }, "Could not read the policies");
  if (!o || !stillHere("governance") || govPage.open) return;
  const rerender = () => platformGovernanceTab(body);
  const search = h("input", { value: g.query, placeholder: "Name, id, owner or category", "aria-label": "Search policies",
    onkeydown: (e) => { if (e.key === "Enter") { g.query = search.value.trim(); rerender(); } } });
  const pick = (key, options, label) => h("select", { "aria-label": label, onchange: (e) => { g[key] = e.target.value; rerender(); } },
    options.map(([v, t]) => h("option", { value: v, selected: g[key] === v ? true : null }, t)));
  const c = o.counts;
  body.replaceChildren(...[
    govViewNav(body),
    h("div", { class: "tiles plat-stats" },
      platStat(c.published, "Published", "in force where assigned", () => { g.status = "published"; rerender(); }),
      platStat(c.draft, "Drafts", "waiting to publish", () => { g.status = "draft"; rerender(); }),
      platStat(c.deprecated, "Deprecated", "still in force, with a warning", () => { g.status = "deprecated"; rerender(); }),
      platStat(c.retired, "Retired", "no longer applied", () => { g.status = "retired"; rerender(); })),
    h("div", { class: "card pad" },
      h("div", { class: "row" },
        h("label", { class: "field grow" }, "Search policies", search),
        h("label", { class: "field" }, "Category", pick("category", [["", "All categories"], ...Object.entries(o.options.categories)], "Category")),
        h("label", { class: "field" }, "Status", pick("status", [["", "Any status"], ...Object.entries(GOV_STATUS).map(([k, [t]]) => [k, t])], "Status")),
        govCan(o).admin ? btn("New policy", "add", () => policyDialog(o.options, null, (p) => { govPage.open = p.id; rerender(); }), "tool primary") : null),
      h("p", { class: "muted-text small" }, "Standards your organization defines once and every project is checked against: naming, schemas, quality, classification, ownership, data contracts and deployment readiness. ",
        "A published version never changes; editing makes a new draft. Exceptions are asked for on Requests. ", learnMore("governance")),
      ...(o.problems || []).map((x) => h("p", { class: "wrn-text small" }, x)),
      o.policies.length ? h("table", { class: "list" },
        h("thead", {}, h("tr", {}, ["Policy", "Category", "Owner", "Version", "Status", "Assigned"].map((x) => h("th", {}, x)))),
        h("tbody", {}, o.policies.map((p) => h("tr", { class: "click", onclick: () => { govPage.open = p.id; govPage.sub = "definition"; govPage.version = null; rerender(); } },
          h("td", {}, h("b", {}, p.name), h("div", { class: "muted-text small mono" }, p.id),
            govExceptionCount((o.exceptions || []).filter((e) => e.policy_id === p.id))),
          h("td", {}, o.options.categories[p.category] || p.category), h("td", {}, p.owner || h("span", { class: "muted-text" }, "not set")),
          h("td", {}, p.current_version ? `v${p.current_version}` : h("span", { class: "muted-text" }, "none yet"),
            p.draft_version && p.current_version ? [" ", h("span", { class: "pill-s neu" }, `draft v${p.draft_version}`)] : null),
          h("td", {}, govPill(GOV_STATUS, p.status)),
          h("td", {}, p.assignments ? `${p.assignments} place${p.assignments === 1 ? "" : "s"}` : h("span", { class: "muted-text" }, "not assigned"))))))
        : h("p", { class: "muted-text" }, g.query || g.category || g.status ? "No policy matches." : "No policies yet."))].filter(Boolean));
}

// ---------- one policy ----------

async function governancePolicy(body, id) {
  const [p, o] = await Promise.all([platformCall("governance_policy", { id, include_ended: !!govPage.endedToo }, "Could not read the policy"),
    platformCall("governance_overview", {}, "Could not read the policies")]);
  if (!stillHere("governance") || govPage.open !== id) return;
  if (!p || !o) { govPage.open = null; return platformGovernanceTab(body); }
  const rerender = () => governancePolicy(body, id);
  const can = govCan(o);
  const live = p.status !== "retired";
  const shown = govPage.version ? p.versions.find((v) => v.version === govPage.version) : (p.current || p.draft);
  const setStatus = async (status, title, text, okLabel, needNote) => {
    const note = h("input", { placeholder: needNote ? "Why (required)" : "Why (optional)" });
    const ok = await new Promise((resolve) => showDialog(title, h("div", {}, h("p", { class: "text" }, text), h("label", { class: "field" }, "Note", note)), [
      btn("Cancel", null, () => { closeDialog(); resolve(false); }, "tool outline"),
      btn(okLabel, null, () => { if (needNote && !note.value.trim()) return note.focus(); closeDialog(); resolve(true); }, "tool primary")]));
    if (ok && await platformCall("governance_set_status", { id, status, note: note.value.trim() }, "Not changed")) rerender();
  };
  const publish = async () => {
    const note = h("input", { value: p.draft?.note || "", placeholder: "What changed" });
    const ok = await new Promise((resolve) => showDialog(`Publish v${p.draft_version}?`, h("div", {},
      h("p", { class: "text" }, `Version ${p.draft_version} can't be changed after this. Projects using ${p.name} get it the next time they resolve their policies, unless an assignment pins an older version.`),
      h("label", { class: "field" }, "What changed", note)), [
      btn("Cancel", null, () => { closeDialog(); resolve(false); }, "tool outline"),
      btn("Publish", null, () => { closeDialog(); resolve(true); }, "tool primary")]));
    if (ok && await platformCall("governance_publish", { id, note: note.value.trim() }, "Not published")) { govPage.version = null; toast(`Published ${p.name} v${p.draft_version}`, false, { log: true }); rerender(); }
  };
  const tabs = [["definition", "Definition"], ["applies", "Where it applies"], ["history", "History"]];
  const sub = h("div", {});
  body.replaceChildren(...[
    h("a", { href: "#", onclick: (e) => { e.preventDefault(); govPage.open = null; platformGovernanceTab(body); } }, "All policies"),
    h("div", { class: "row" },
      h("div", { class: "grow" }, h("h2", { class: "row tight" }, p.name, " ", govPill(GOV_STATUS, p.status), p.current_version ? h("span", { class: "muted-text small" }, ` v${p.current_version}`) : null),
        h("div", { class: "muted-text small" }, h("span", { class: "mono" }, p.id), ` · ${o.options.categories[p.category] || p.category}${p.owner ? ` · Owner: ${p.owner}` : ""}`)),
      can.admin && live && p.current_version ? h("span", { class: "row tight" },
        p.status === "deprecated" ? btn("Restore", null, () => setStatus("published", `Restore ${p.name}?`, "It goes back to being a current policy, with no warning.", "Restore"), "tool outline small")
          : btn("Deprecate", null, () => setStatus("deprecated", `Deprecate ${p.name}?`, "It still applies wherever it's assigned, with a warning that it's going away.", "Deprecate"), "tool outline small"),
        btn("Retire", null, () => setStatus("retired", `Retire ${p.name}?`, "It stops applying everywhere. Its versions and history stay readable, and it can't be changed or brought back.", "Retire", true), "tool small danger"))
        : null),
    p.status === "retired" ? h("p", { class: "muted-text" }, "Retired: kept for the record and no longer applied.") : null,
    p.draft && can.admin ? h("div", { class: "card pad notice" }, h("div", { class: "row" },
      h("span", { class: "grow" }, h("b", {}, `Draft v${p.draft_version}`), ` by ${p.draft.created_by || "this PC"}, ${relTime(p.draft.created_at)}${p.draft.note ? `: ${p.draft.note}` : ""}. `,
        p.current_version ? `Projects keep v${p.current_version} until it's published.` : "Nobody sees it in force until it's published."),
      btn("Edit draft", null, () => policyDialog(o.options, p, rerender), "tool outline small"),
      btn("Discard", null, async () => {
        if (!(await confirmDialog(`Discard draft v${p.draft_version}?`, p.current_version ? `v${p.current_version} stays as it is.` : "The policy was never published, so it goes too.", "Discard", { danger: true }))) return;
        const res = await platformCall("governance_discard_draft", { id }, "Not discarded");
        if (res?.deleted) { govPage.open = null; return platformGovernanceTab(body); }
        if (res) rerender();
      }, "tool outline small"),
      btn(`Publish v${p.draft_version}`, null, publish, "tool primary small"))) : null,
    !p.draft && can.admin && live && p.current_version ? h("div", { class: "row" }, h("span", { class: "grow" }),
      btn("Edit (new draft)", null, () => policyDialog(o.options, p, rerender), "tool outline small")) : null,
    previewShown() && (can.admin || can.domains.length) ? h("div", { class: "row" }, h("span", { class: "grow" }),
      btn("Save as template", null, () => saveAsTemplateDialog(p), "tool outline small")) : null,
    h("div", { class: "subtabs" }, tabs.map(([t, label]) => h("button", { class: `tab${govPage.sub === t ? " active" : ""}`, onclick: () => { govPage.sub = t; rerender(); } }, label))),
    sub].filter(Boolean));
  if (govPage.sub === "applies") return governanceApplies(sub, p, o, can, rerender);
  if (govPage.sub === "history") {
    sub.replaceChildren(h("div", { class: "card pad" }, h("b", {}, "Every change, newest first"),
      h("p", { class: "muted-text small" }, "Recorded by the platform as the person who made the change."),
      h("table", { class: "list" }, h("tbody", {}, p.history.map((e) => h("tr", {},
        h("td", { class: "muted-text small" }, when(e.at)), h("td", {}, e.by_user || h("span", { class: "muted-text" }, "this PC")),
        h("td", {}, h("b", {}, e.action), e.version ? h("span", { class: "muted-text" }, ` v${e.version}`) : null),
        h("td", { class: "small" }, govEventDetail(e))))))));
    return;
  }
  sub.replaceChildren(h("div", { class: "gov-two" },
    h("div", { class: "card pad" }, shown ? governanceDefinition(shown, o.options) : h("p", { class: "muted-text" }, "No version yet.")),
    h("div", { class: "card gov-versions" }, h("b", { class: "pad-s" }, "Versions"),
      [...p.versions].reverse().map((v) => h("button", { class: `gov-version${shown?.version === v.version ? " on" : ""}`, onclick: () => { govPage.version = v.version; rerender(); } },
        h("span", { class: `pill-s ${v.status === "draft" ? "neu" : v.version === p.current_version ? "pass" : "neu"}` }, v.status === "draft" ? "Draft" : v.version === p.current_version ? "Current" : "Older"),
        h("span", { class: "grow" }, `v${v.version}`, v.published_at ? h("span", { class: "muted-text small" }, ` · ${govDate(v.published_at)} · ${v.published_by || "this PC"}`) : null),
        v.status === "published" ? h("span", { class: v.intact ? "muted-text small" : "fail-text small", title: v.hash }, v.intact ? "Intact" : "Changed outside the platform") : null)),
      h("p", { class: "muted-text small pad-s" }, "Published versions never change. Each is stored with its SHA-256; Intact means it still matches."))));
}

function govEventDetail(e) {
  const d = e.detail || {};
  if (d.scope) return `${GOV_SCOPE[d.scope] || d.scope} ${d.target === "*" ? "" : d.target}${d.settings && Object.keys(d.settings).length ? `: ${JSON.stringify(d.settings)}` : ""}${d.reason ? `: ${d.reason}` : ""}`;
  return d.note || "";
}

function governanceDefinition(v, options) {
  const d = v.definition;
  const at = d.applies_to || {};
  const applies = ["layers", "environments", "asset_types"].filter((k) => at[k]?.length).map((k) => `${k.replace("_", " ")}: ${at[k].join(", ")}`);
  const row = (k, val) => [h("dt", {}, k), h("dd", {}, val)];
  return h("div", {},
    v.status === "draft" ? h("p", { class: "muted-text small" }, `Draft v${v.version}: not in force yet.`) : h("p", { class: "muted-text small" }, `Version ${v.version}, published ${govDate(v.published_at)} by ${v.published_by || "this PC"}${v.note ? `: ${v.note}` : ""}.`),
    h("dl", { class: "gov-kv" },
      row("Description", d.description || h("span", { class: "muted-text" }, "none")),
      row("Severity", d.severity), row("Enforcement", govPill(GOV_ENFORCEMENT, d.enforcement)),
      row("Mandatory", d.mandatory ? "Yes: nothing below where it's assigned can make it weaker" : "No, unless an assignment makes it mandatory"),
      row("Can be assigned at", d.scopes.map((s) => GOV_SCOPE[s]).join(", ")),
      row("Applies to", applies.length ? applies.join("; ") : "Everything"),
      row("In force", d.effective_from || d.expires_on ? `${d.effective_from || "now"} to ${d.expires_on || "no end date"}` : "Always"),
      row("Exceptions", d.exception_eligible ? "Allowed, with a reason, an approver and an expiry" : "Not allowed"),
      row("Checked by", d.rule ? h("span", { class: "mono" }, d.rule) : h("span", { class: "muted-text" }, "not set")),
      row("How to fix", d.remediation || h("span", { class: "muted-text" }, "not written"))),
    Object.keys(d.params || {}).length ? h("table", { class: "list" },
      h("thead", {}, h("tr", {}, ["Parameter", "Value", "Stricter is", ""].map((x) => h("th", {}, x)))),
      h("tbody", {}, Object.entries(d.params).map(([k, s]) => h("tr", {}, h("td", { class: "mono" }, k), h("td", { class: "mono" }, govValue(s.value)),
        h("td", {}, GOV_STRICTER[s.stricter] || s.stricter), h("td", { class: "muted-text small" }, s.description || "")))))
      : null);
}

// ---------- writing a draft ----------

// ``template`` (Governance > Templates): the same form for a template, {title, id, definition, save(def) -> result}.
function policyDialog(options, p, done, template = null) {
  const base = template?.definition || (p ? (p.draft || p.current).definition : { severity: "medium", enforcement: "warn", scopes: ["enterprise", "domain", "project", "entity"], exception_eligible: true, params: {} });
  const input = (value, attrs = {}) => h("input", { value: value ?? "", ...attrs });
  const select = (value, pairs) => h("select", {}, pairs.map(([v, t]) => h("option", { value: v, selected: v === value ? true : null }, t)));
  const check = (on) => h("input", { type: "checkbox", checked: on ? true : null });
  const f = {
    name: input(base.name), id: input(template ? template.id || "" : p?.id || "", { disabled: (template ? template.id : p) ? true : null, placeholder: "made from the name" }),
    description: h("textarea", { rows: 2 }, base.description || ""),
    category: select(base.category || "naming", Object.entries(options.categories)), owner: input(base.owner, { placeholder: "Data office" }),
    rule: input(base.rule, { placeholder: "table_name_pattern", class: "mono" }),
    severity: select(base.severity, options.severities.map((s) => [s, s[0].toUpperCase() + s.slice(1)])),
    enforcement: select(base.enforcement, options.enforcements.map((e) => [e, GOV_ENFORCEMENT[e][0]])),
    effective: input(base.effective_from, { type: "date" }), expires: input(base.expires_on, { type: "date" }),
    mandatory: check(base.mandatory), exceptions: check(base.exception_eligible),
    remediation: h("textarea", { rows: 2 }, base.remediation || ""), note: input("", { placeholder: "What changed in this version" }),
    layers: input((base.applies_to?.layers || []).join(", "), { placeholder: "all" }),
    environments: input((base.applies_to?.environments || []).join(", "), { placeholder: "all" }),
  };
  const scopes = Object.fromEntries(options.scopes.map((s) => [s, check(base.scopes.includes(s))]));
  const params = h("tbody", {});
  const addParam = (name = "", spec = { value: "", stricter: "fixed", description: "" }) => {
    const tr = h("tr", {},
      h("td", {}, input(name, { class: "mono", "aria-label": "Parameter name" })),
      h("td", {}, input(spec.value === "" ? "" : govValue(spec.value), { class: "mono", "aria-label": "Value" })),
      h("td", {}, select(spec.stricter, options.stricter.map((s) => [s, GOV_STRICTER[s]]))),
      h("td", {}, input(spec.description, { "aria-label": "What it means" })),
      h("td", {}, btn("Remove", null, () => tr.remove(), "tool small")));
    params.append(tr);
  };
  Object.entries(base.params || {}).forEach(([k, s]) => addParam(k, s));
  const list = (el) => el.value.split(",").map((x) => x.trim()).filter(Boolean);
  const collect = () => ({
    id: f.id.value.trim() || undefined, name: f.name.value.trim(), description: f.description.value.trim(), category: f.category.value,
    owner: f.owner.value.trim(), rule: f.rule.value.trim(), severity: f.severity.value, enforcement: f.enforcement.value,
    mandatory: f.mandatory.checked, exception_eligible: f.exceptions.checked, remediation: f.remediation.value.trim(),
    effective_from: f.effective.value, expires_on: f.expires.value,
    scopes: options.scopes.filter((s) => scopes[s].checked),
    applies_to: { layers: list(f.layers), environments: list(f.environments) },
    params: Object.fromEntries([...params.children].map((tr) => {
      const [name, value, stricter, description] = tr.querySelectorAll("input, select");
      return [name.value.trim(), { value: govParse(value.value), stricter: stricter.value, description: description.value.trim() }];
    }).filter(([k]) => k)),
  });
  const save = async (andPublish) => {
    if (!f.name.value.trim()) return f.name.focus();
    const policy = collect();
    const res = p ? await platformCall("governance_save_draft", { id: p.id, policy, note: f.note.value.trim() }, "Not saved")
      : await platformCall("governance_create", { policy: { ...policy, note: f.note.value.trim() } }, "Not created");
    if (!res) return;
    if (andPublish && !(await platformCall("governance_publish", { id: res.id, note: f.note.value.trim() }, "Saved, but not published"))) { closeDialog(); return done(res); }
    closeDialog();
    toast(andPublish ? `Published ${res.name}` : `Saved the draft of ${res.name}`, false, { log: true });
    govPage.version = null;
    done(res);
  };
  const saveTemplate = async () => {
    if (!f.name.value.trim()) return f.name.focus();
    const res = await template.save(collect());
    if (!res) return;
    closeDialog();
    toast(`Saved the template ${res.name}`, false, { log: true });
    done(res);
  };
  const draftNo = p ? (p.draft_version || Math.max(...p.versions.map((v) => v.version)) + 1) : 1;
  showDialog(template ? template.title : p ? `${p.draft ? "Edit" : "New"} draft v${draftNo}` : "New policy", h("div", { class: "gov-form" },
    h("div", { class: "row" }, h("label", { class: "field grow" }, "Name", f.name), h("label", { class: "field" }, "Id", f.id)),
    h("label", { class: "field" }, "Description", f.description),
    h("div", { class: "row" }, h("label", { class: "field grow" }, "Category", f.category), h("label", { class: "field grow" }, "Owner", f.owner),
      h("label", { class: "field grow" }, "Checked by (rule)", f.rule)),
    h("div", { class: "row" }, h("label", { class: "field grow" }, "Severity", f.severity), h("label", { class: "field grow" }, "Enforcement", f.enforcement),
      h("label", { class: "field" }, "In force from", f.effective), h("label", { class: "field" }, "Until", f.expires)),
    h("div", { class: "row wrap" }, h("label", { class: "check" }, f.mandatory, "Mandatory wherever it's assigned"),
      h("label", { class: "check" }, f.exceptions, "People can ask for an exception")),
    h("div", { class: "row wrap" }, h("span", { class: "muted-text small" }, "Can be assigned at:"),
      options.scopes.map((s) => h("label", { class: "check" }, scopes[s], GOV_SCOPE[s]))),
    h("div", { class: "row" }, h("label", { class: "field grow" }, "Only for layers", f.layers), h("label", { class: "field grow" }, "Only in environments", f.environments)),
    h("div", { class: "row" }, h("b", { class: "grow" }, "Parameters"), btn("Add parameter", "add", () => addParam(), "tool small")),
    h("table", { class: "list" }, h("thead", {}, h("tr", {}, ["Name", "Value", "Stricter is", "What it means", ""].map((x) => h("th", {}, x)))), params),
    h("label", { class: "field" }, "How to fix (shown to developers)", f.remediation),
    template ? null : h("label", { class: "field" }, "What changed", f.note)), template ? [
    btn("Cancel", null, closeDialog, "tool outline"),
    btn("Save template", "save", saveTemplate, "tool primary")] : [
    btn("Cancel", null, closeDialog, "tool outline"),
    btn("Save draft", "save", () => save(false), "tool outline"),
    btn(`Save and publish v${draftNo}`, null, () => save(true), "tool primary")]);
}

// ---------- where it applies, and why ----------

// Exceptions in force for a policy, granted by approving a request on Data mesh > Requests.
function govExceptionCount(list) {
  const ending = list.filter((e) => e.ending).length;
  return list.length ? h("div", { class: "small" }, h("span", { class: "pill-s wrn" }, `${list.length} exception${list.length === 1 ? "" : "s"} in force`),
    ending ? [" ", h("span", { class: "pill-s fail" }, `${ending} ending soon`)] : null) : null;
}

// Where an exception stands: in force, ending within the warning window, ended on its date, or ended early.
function govExceptionStatus(e) {
  if (e.ended_early) return h("span", { class: "pill-s neu" }, "Ended early");
  if (!e.active) return h("span", { class: "pill-s neu" }, "Ended");
  if (e.ending) return h("span", { class: "pill-s wrn" }, e.days_left <= 0 ? "Ends today" : `Ends in ${e.days_left} day${e.days_left === 1 ? "" : "s"}`);
  return h("span", { class: "pill-s pass" }, "In force");
}

// End early: platform admins only (the platform checks that too), with a reason the policy's history keeps.
async function govEndException(p, e, rerender) {
  const reason = await confirmDialog(`End the exception for ${e.target} now?`,
    `${p.name} applies to ${e.target} again from today instead of ${govDate(e.until)}. The exception isn't deleted: its row in catalog.policy_exception records who ended it, when and why, and the platform's audit and the policy's history get an entry. The reason is visible to anyone who can read the policy's history, which is everyone.`,
    "End early", { danger: true, reason: { label: "Why it ends early (required)", placeholder: "For example: the masked copy is ready", max: 500 } });
  if (!reason) return;
  const out = await platformCall("governance_end_exception", { id: e.id, reason }, "Not ended");
  if (out) { toast(`The exception for ${e.target} has ended`, false, { log: true }); rerender(); }
}

function govExceptions(p, can = {}, rerender = () => {}) {
  const list = p.exceptions || [];
  const endedToo = !!govPage.endedToo;
  const showEnded = h("input", { type: "checkbox", checked: endedToo ? true : null,
    onchange: (ev) => { govPage.endedToo = ev.target.checked; rerender(); } });
  return h("div", { class: "card pad gov-exceptions" },
    h("div", { class: "row" }, h("b", { class: "grow" }, endedToo ? "Exceptions" : "Exceptions in force"),
      h("label", { class: "check small" }, showEnded, "Include ended"),
      btn("Ask for one", null, () => { closeDialog(); newRequestDialog(() => goMeta("requests"), "exception"); }, "tool outline small")),
    h("p", { class: "muted-text small" }, "Where an exception covers a place, the policy shows there as excepted until its date instead of applying. Each ends on its own on its date; one that ends within 14 days warns whoever asked and the project. A platform admin grants one by approving a request, and can end it early."),
    ...(p.problems || []).map((x) => h("p", { class: "wrn-text small" }, x)),
    list.length ? h("table", { class: "list" },
      h("thead", {}, h("tr", {}, ["For", "Until", "Status", "Why", "Granted by", ""].map((x) => h("th", {}, x)))),
      h("tbody", {}, list.map((e) => h("tr", { "data-exception": e.id },
        h("td", {}, h("b", {}, e.target), h("span", { class: "muted-text" }, ` (${e.scope})`)),
        h("td", {}, govDate(e.ended_early ? e.granted_until : e.until)),
        h("td", {}, govExceptionStatus(e),
          e.ended_early ? h("div", { class: "muted-text small" }, `${govDate(e.ended_at)} by ${e.ended_by || "a platform admin"}: ${e.end_reason || ""}`) : null),
        h("td", { class: "small" }, e.reason),
        h("td", { class: "muted-text small" }, `${e.approved_by || "this PC"}, asked by ${e.requested_by || "someone"}`),
        h("td", {}, can.admin && e.active ? btn("End early", null, () => govEndException(p, e, rerender), "tool small danger") : null)))))
      : h("p", { class: "muted-text" }, endedToo ? "None, in force or ended." : "None in force."),
    howItWorks("governance.exceptions", {
      reads: "The policy's exceptions from the platform's catalog (catalog.policy_exception): those in force, or with Include ended those that ended on their date or early too. With the platform in Fabric that's one call to UDF_Platform, which uses a little capacity.",
      changes: "End early (platform admins only, with a reason) updates the exception's row in catalog.policy_exception: its end moves to yesterday and ended_at, ended_by, end_reason and granted_until are set. The row is never deleted. It also writes one entry to the platform's audit (catalog.audit) and one to the policy's history (catalog.policy_event, which audits too). Opening Data mesh while an exception ends within 14 days, on a PC with a Teams route that takes the notice, sets its warned_until in catalog.policy_exception, so one PC in the organization posts it. Nothing changes in Fabric or OneLake.",
      results: "Check now and Explain stop treating the place as excepted at once. The ended exception shows here with Include ended, and in Platform > Govern > Audit.",
      anchor: "policy-exceptions",
    }));
}

async function governanceApplies(sub, p, o, can, rerender) {
  const reach = await platformCall("governance_applicability", { id: p.id }, "Could not read where it applies");
  if (!reach || govPage.sub !== "applies") return;
  const domainName = (id) => o.domains.find((d) => d.id === id)?.name || id;
  const mayAssign = (scope, target) => can.admin || (scope === "domain" && (can.domains.includes(target) ||
    can.domains.includes(o.domains.find((d) => d.id === target)?.parent_id)));
  const live = p.status !== "retired" && p.current_version;
  const settingsText = (s) => [s.mandatory ? "Mandatory" : null, s.enforcement ? GOV_ENFORCEMENT[s.enforcement][0] : null,
    s.severity ? `severity ${s.severity}` : null, s.version ? `pinned to v${s.version}` : null,
    ...Object.entries(s.params || {}).map(([k, v]) => `${k} ${govValue(v)}`)].filter(Boolean).join(", ") || "As published";
  const explain = h("div", {});
  const showWhy = async (project) => {
    govPage.explain = project;
    const r = await platformCall("governance_resolve", { project }, "Could not work it out");
    if (!r || govPage.explain !== project) return;
    const got = r.policies.find((x) => x.id === p.id);
    const gone = r.inactive.find((x) => x.id === p.id);
    const steps = (got || gone || {}).steps || [];
    explain.replaceChildren(h("div", { class: "card pad gov-why" },
      h("b", {}, `Why ${project} gets ${got ? `${GOV_ENFORCEMENT[got.enforcement][0]}, v${got.version}` : "nothing"}`),
      h("p", { class: "muted-text small" }, "Read from the top. Worked out on this PC from the platform's copy. With the platform in Fabric, reading that copy is one call to UDF_Platform, which uses a little capacity; no OneLake data is read and no Fabric job runs."),
      h("ol", { class: "gov-steps" }, steps.map((s) => h("li", { class: s.outcome === "ignored" ? "fail-text" : null },
        h("b", {}, `${GOV_SCOPE[s.scope]} ${s.target === "*" ? "" : s.scope === "domain" ? domainName(s.target) : s.target}`),
        s.origin !== "platform" ? h("span", { class: "muted-text" }, ` (${s.origin})`) : null, ": ",
        s.change === "introduced" ? `introduces v${s.version}` : s.change === "mandatory" ? (s.to ? "makes it mandatory" : "makes it optional")
          : `${s.change} ${govValue(s.from)} to ${govValue(s.to)}`,
        s.outcome === "ignored" ? `. Ignored: ${s.reason}` : ""))),
      got?.exception ? h("p", { class: "small wrn-text" }, got.exception.note) : null,
      got ? h("p", { class: "small" }, "Result: ", govPill(GOV_ENFORCEMENT, got.enforcement), ` severity ${got.severity}`,
        Object.keys(got.params).length ? `, ${Object.entries(got.params).map(([k, v]) => `${k} ${govValue(v)}`).join(", ")}` : "",
        got.deprecated ? h("span", { class: "wrn-text" }, ". Deprecated: it's going away.") : null)
        : h("p", { class: "small" }, gone ? `Not in force: it ${gone.reason}.` : "Not in force here."),
      r.conflicts.filter((c) => c.policy === p.id).length ? h("p", { class: "fail-text small" }, `${r.conflicts.filter((c) => c.policy === p.id).length} conflict: a lower scope tries to loosen a mandatory policy.`) : null));
  };
  sub.replaceChildren(h("div", { class: "gov-two" },
    h("div", { class: "gov-main" },
      h("div", { class: "card pad" },
        h("div", { class: "row" }, h("b", { class: "grow" }, "Assigned at"),
          live && (can.admin || can.domains.length) ? btn("Assign", "add", () => assignDialog(p, o, can, null, rerender), "tool primary small") : null),
        reach.assignments.length ? h("table", { class: "list" },
          h("thead", {}, h("tr", {}, ["Scope", "Settings here", "By", ""].map((x) => h("th", {}, x)))),
          h("tbody", {}, reach.assignments.map((a) => h("tr", {},
            h("td", {}, h("b", {}, GOV_SCOPE[a.scope]), " ", a.scope === "domain" ? domainName(a.target) : a.target === "*" ? "" : a.target),
            h("td", {}, settingsText(a.settings), a.note ? h("div", { class: "muted-text small" }, a.note) : null),
            h("td", { class: "muted-text small" }, `${a.updated_by || "this PC"}, ${relTime(a.updated_at)}`),
            h("td", {}, live && mayAssign(a.scope, a.target) ? h("span", { class: "row tight" },
              btn("Change", null, () => assignDialog(p, o, can, a, rerender), "tool small"),
              btn("Remove", null, async () => {
                if (!(await confirmDialog(`Stop applying ${p.name} at ${GOV_SCOPE[a.scope].toLowerCase()} ${a.target === "*" ? "" : a.target}?`, "Projects there stop getting it from this assignment.", "Remove"))) return;
                if (await platformCall("governance_unassign", { policy: p.id, scope: a.scope, target: a.target }, "Not removed")) rerender();
              }, "tool small danger")) : null)))))
          : h("p", { class: "muted-text" }, live ? "Not assigned anywhere yet, so it isn't in force." : "Not assigned.")),
      h("div", { class: "card pad" }, h("b", {}, "Projects it reaches"),
        h("p", { class: "muted-text small" }, "Every project registered in the platform's catalog that gets this policy. A project's own governance.toml can add to or tighten it; Explain shows each step."),
        reach.projects.length ? h("table", { class: "list" },
          h("thead", {}, h("tr", {}, ["Project", "Domain", "Through", "Here it is", ""].map((x) => h("th", {}, x)))),
          h("tbody", {}, reach.projects.map((r) => h("tr", {},
            h("td", {}, h("b", {}, r.project)), h("td", {}, [r.domain, r.subdomain].filter(Boolean).map(domainName).join(" / ")),
            h("td", { class: "small" }, r.via.join(", ")),
            h("td", {}, r.inactive ? h("span", { class: "muted-text small" }, `Not in force: ${r.inactive}`) : [govPill(GOV_ENFORCEMENT, r.enforcement), ` v${r.version}`]),
            h("td", {}, btn("Explain", null, () => showWhy(r.project), "tool small"))))))
          : h("p", { class: "muted-text" }, "No registered project gets it yet.")),
      govExceptions(p, can, rerender)),
    h("div", { class: "gov-side" }, explain)));
  if (govPage.explain && reach.projects.some((r) => r.project === govPage.explain)) showWhy(govPage.explain);
}

function assignDialog(p, o, can, current, done) {
  const def = p.current.definition;
  const scopes = def.scopes.filter((s) => can.admin || s === "domain");
  const scope = h("select", { disabled: current ? true : null }, scopes.map((s) => h("option", { value: s, selected: current?.scope === s ? true : null }, GOV_SCOPE[s])));
  const domains = o.domains.filter((d) => can.admin || can.domains.includes(d.id) || can.domains.includes(d.parent_id));
  const target = h("span", {});
  const pickTarget = () => {
    const s = scope.value;
    const value = current?.target || "";
    target.replaceChildren(s === "enterprise" ? h("span", { class: "muted-text" }, "Every project")
      : s === "domain" ? h("select", { disabled: current ? true : null }, domains.map((d) => h("option", { value: d.id, selected: d.id === value ? true : null }, `${d.parent_id ? "↳ " : ""}${d.name}`)))
        : s === "project" ? h("select", { disabled: current ? true : null }, o.projects.map((n) => h("option", { value: n, selected: n === value ? true : null }, n)))
          : h("input", { value, placeholder: "crm:silver.customer", class: "mono", disabled: current ? true : null }));
  };
  scope.onchange = pickTarget;
  pickTarget();
  const s = current?.settings || {};
  const keep = [["", "As published"]];
  const enforcement = h("select", {}, [...keep, ...["off", ...o.options.enforcements].map((e) => [e, GOV_ENFORCEMENT[e][0]])].map(([v, t]) => h("option", { value: v, selected: (s.enforcement || "") === v ? true : null }, t)));
  const severity = h("select", {}, [...keep, ...o.options.severities.map((x) => [x, x])].map(([v, t]) => h("option", { value: v, selected: (s.severity || "") === v ? true : null }, t)));
  const mandatory = h("input", { type: "checkbox", checked: s.mandatory ?? def.mandatory ? true : null });
  const version = h("select", {}, [["", `Latest (now v${p.current_version})`], ...p.versions.filter((v) => v.status === "published").map((v) => [String(v.version), `v${v.version} only`])]
    .map(([v, t]) => h("option", { value: v, selected: String(s.version || "") === v ? true : null }, t)));
  const params = Object.entries(def.params || {}).map(([k, spec]) => [k, h("input", { class: "mono", value: k in (s.params || {}) ? govValue(s.params[k]) : "", placeholder: `${govValue(spec.value)} (stricter: ${GOV_STRICTER[spec.stricter]})` })]);
  const note = h("input", { value: current?.note || "", placeholder: "Why here" });
  showDialog(`${current ? "Change" : "Assign"} ${p.name}`, h("div", { class: "gov-form" },
    h("div", { class: "row" }, h("label", { class: "field" }, "Scope", scope), h("label", { class: "field grow" }, "Where", target)),
    h("p", { class: "muted-text small" }, "Leave a setting as published to inherit it. Under a mandatory assignment above, only stricter settings are accepted."),
    h("div", { class: "row" }, h("label", { class: "field grow" }, "Enforcement", enforcement), h("label", { class: "field grow" }, "Severity", severity), h("label", { class: "field grow" }, "Version", version)),
    h("label", { class: "check" }, mandatory, "Mandatory here and below: lower scopes can only make it stricter"),
    params.length ? h("div", {}, h("b", {}, "Parameters"), params.map(([k, el]) => h("label", { class: "field" }, h("span", { class: "mono" }, k), el))) : null,
    h("label", { class: "field" }, "Note", note)), [
    btn("Cancel", null, closeDialog, "tool outline"),
    btn(current ? "Save" : "Assign", null, async () => {
      const where = target.querySelector("select, input");
      const settings = {};
      if (mandatory.checked) settings.mandatory = true;
      else if (s.mandatory) settings.mandatory = false;
      if (enforcement.value) settings.enforcement = enforcement.value;
      if (severity.value) settings.severity = severity.value;
      if (version.value) settings.version = Number(version.value);
      const ps = Object.fromEntries(params.filter(([, el]) => el.value.trim()).map(([k, el]) => [k, govParse(el.value)]));
      if (Object.keys(ps).length) settings.params = ps;
      if (await platformCall("governance_assign", { policy: p.id, scope: scope.value, target: where ? where.value.trim() : "", settings, note: note.value.trim() }, "Not assigned")) {
        closeDialog(); done();
      }
    }, "tool primary")]);
}

// ---------- policy exceptions about to end ----------

// Opens a policy on Where it applies, where its exceptions are listed (a bell or Teams notice lands here).
function openPolicyExceptions(id) {
  Object.assign(govPage, { view: "policies", open: id || null, sub: "applies", version: null });
  goPlatform("governance");
}

// Policy exceptions that end within 14 days and concern this person or the open project, asked once a day
// per project. governance_exceptions_ending also sends each one Teams notice (once per exception and date).
const govEnding = { key: "", rows: [], loading: null };

function loadEndingPolicyExceptions() {
  if (!previewShown()) return Promise.resolve([]);
  const key = `${new Date().toISOString().slice(0, 10)}|${state.meta?.project?.path || ""}`;
  if (govEnding.key === key) return Promise.resolve(govEnding.rows);
  if (!govEnding.loading) {
    govEnding.loading = rpc("governance_exceptions_ending", {})
      .then((out) => (out?.ending || []).filter((e) => e.for_you)).catch(() => [])
      .then((rows) => { Object.assign(govEnding, { key, rows, loading: null }); return rows; });
  }
  return govEnding.loading;
}

// One bell item per exception and day, like an exceptions.toml one (exceptionNotice in meta.js).
function policyExceptionNotice(e, day) {
  if (!e || !e.ending) return null;
  const when = e.days_left <= 0 ? "today" : e.days_left === 1 ? "tomorrow" : `in ${e.days_left} days`;
  return { id: `policy-exception:${e.id}:${e.until}:${day}`, status: "info", notice: true,
    title: "A policy exception is about to end",
    text: `The exception to ${e.policy_id} for ${e.target} ends ${when} (${e.until}). After that the policy applies again: meet it, or ask for a new exception on Data mesh > Requests.` };
}

function policyExceptionBanner(rows) {
  if (!rows.length) return null;
  const one = new Set(rows.map((e) => e.policy_id)).size === 1;
  return h("div", { class: "problems gov-ending" },
    h("div", { class: "row" }, icon("warning", 18),
      h("b", {}, `${rows.length} policy exception${rows.length === 1 ? " is" : "s are"} about to end`),
      h("span", { class: "spacer" }),
      btn("Review", null, () => openPolicyExceptions(one ? rows[0].policy_id : null), "tool outline")),
    h("ul", { class: "gate-list" }, rows.map((e) => h("li", {}, policyExceptionNotice(e, "").text))));
}

// Fills a Data mesh banner slot (or only rings the bell when there's none) once the platform answers.
async function raisePolicyExceptionNotices(slot) {
  const rows = await loadEndingPolicyExceptions();
  const day = new Date().toISOString().slice(0, 10);
  for (const e of rows) {
    const item = policyExceptionNotice(e, day);
    if (item) logActivity(item);
  }
  if (slot) slot.replaceChildren(...[policyExceptionBanner(rows)].filter(Boolean));
}
