// Entra ID groups per domain (Preview): Data mesh > Domains > Access groups. Each of a domain's roles
// (owners, creators, readers...) goes to one Entra ID security group, never to people. The dialog suggests a
// name for each from the platform's naming pattern, lets owners link a group they already have instead, and
// writes scripts (Graph PowerShell, az CLI, a CSV to build them by hand, and the Fabric role assignments) for
// an Entra admin to run. The app creates no group and grants no role itself (central/entra_service.py).

const ENTRA_SCRIPTS = [["powershell", "Graph PowerShell"], ["az", "az CLI"], ["csv", "By hand (CSV)"], ["roles", "Fabric role assignments"]];
const ENTRA_ENVS = [["dev", "Dev"], ["test", "Test"], ["prod", "Prod"]];
const ENTRA_ROLE_NAMES = { domain_admins: "Domain owners", domain_contributors: "Domain contributors", workspace_admins: "Workspace admins",
  creators: "Creators", readers: "Data readers", viewers: "Report viewers", stewards: "Data stewards" };

async function entraGroupsDialog(d, allDomains = [], was = null) {
  const view = { envs: was?.envs || ["dev", "test", "prod"], subdomains: !!was?.subdomains, kind: was?.kind || "powershell", status: null, out: null };
  const hasSubs = allDomains.some((x) => x.parent_id === d.id);
  const table = h("div", { class: "entra-table" }, loading());
  const script = h("pre", { class: "sql-block entra-script", tabindex: "0", "aria-label": "Script" });
  const steps = h("div", { class: "entra-steps" });
  const problem = h("div");
  const params = () => ({ domain_id: d.id, envs: view.envs, subdomains: view.subdomains });
  // Link and Naming open their own dialog; closing it, or saving, comes back here as it was.
  const reopen = () => entraGroupsDialog(d, allDomains, view);

  const tabs = h("div", { class: "seg", role: "group", "aria-label": "Script" }, ENTRA_SCRIPTS.map(([id, label]) =>
    h("button", { class: id === view.kind ? "on" : "", "aria-pressed": String(id === view.kind), "data-kind": id,
      onclick: () => { view.kind = id; tabs.querySelectorAll("button").forEach((b) => { b.className = b.dataset.kind === id ? "on" : ""; b.setAttribute("aria-pressed", String(b.dataset.kind === id)); }); drawScript(); } }, label)));
  const envChecks = ENTRA_ENVS.map(([id, label]) => h("label", { class: "check" },
    h("input", { type: "checkbox", checked: view.envs.includes(id) ? true : null, "data-env": id, onchange: (e) => {
      view.envs = ENTRA_ENVS.map(([x]) => x).filter((x) => (x === id ? e.target.checked : view.envs.includes(x)));
      refresh();
    } }), h("span", {}, label)));
  const subsCheck = hasSubs ? h("label", { class: "check" }, h("input", { type: "checkbox", checked: view.subdomains ? true : null, onchange: (e) => { view.subdomains = e.target.checked; refresh(); } }),
    h("span", {}, "Include subdomains (", allDomains.filter((x) => x.parent_id === d.id).map((x) => x.name).join(", "), ")")) : null;
  const namingBtn = h("span");

  const statusCell = (r) => {
    const key = `${r.domain_id}|${r.role}|${r.env}`;
    const s = view.status?.[key];
    if (r.linked) {
      const gone = s && !s.exists;
      return h("div", {}, gone ? h("div", { class: "fail-text small" }, "Linked, but not found in Entra") : h("span", { class: "pill-s pass" }, "Linked: your group"),
        h("div", { class: "muted-text small mono", title: "Object ID" }, r.linked.group_id));
    }
    if (s?.exists) return h("span", { class: "pill-s pass" }, "Already in Entra");
    return h("div", { class: "muted-text small" }, "New: in the script");
  };
  const actions = (r) => (view.out?.can_link ? h("div", { class: "row tight" },
    btn(r.linked ? "Change" : "Link existing group", null, () => entraLinkDialog(r, reopen), "tool outline small"),
    r.linked ? btn("Unlink", null, async () => {
      if (await metaCall("platform_entra_unlink", { domain_id: r.domain_id, role: r.role, env: r.env }, "Not unlinked")) refresh();
    }, "tool small") : null) : null);

  function drawTable() {
    const out = view.out;
    table.replaceChildren(h("table", { class: "list entra-roles" },
      h("thead", {}, h("tr", {}, ["Role and group", "Who goes in", "What it gets in Fabric", "Group in Entra"].map((x) => h("th", {}, x)))),
      h("tbody", {}, out.rows.map((r) => h("tr", { "data-role": r.role, "data-env": r.env, "data-domain": r.domain_id },
        h("td", {}, h("b", {}, r.label, r.domain_id !== d.id ? ` (${r.domain})` : ""), h("div", { class: "mono small" }, r.name)),
        h("td", {}, r.who), h("td", {}, r.gets), h("td", {}, statusCell(r), actions(r)))))),
    out.can_link ? null : h("p", { class: "muted-text small" }, `Only platform admins or ${d.name}'s owners can link groups they already have.`));
    namingBtn.replaceChildren(btn(`Naming: ${out.naming.pattern}`, null, () => entraNamingDialog(out.naming, out.can_name, reopen), "tool outline small",
      { title: out.can_name ? "Change how groups are named, for every domain" : "How groups are named. Platform admins change it." }));
  }

  async function drawScript() {
    script.textContent = "";
    steps.replaceChildren();
    try {
      const res = await rpc("platform_entra_script", { ...params(), kind: view.kind });
      script.textContent = res.text;
      steps.replaceChildren(...(res.steps || []).map((s) => h("p", { class: "small" }, s)));
    } catch (err) {
      script.textContent = friendlyError(err).text;
    }
  }

  async function refresh() {
    problem.replaceChildren();
    try {
      view.out = await rpc("platform_entra_groups", params());
    } catch (err) {
      table.replaceChildren(h("p", { class: "fail-text" }, friendlyError(err).text));
      return;
    }
    view.status = null;
    drawTable();
    drawScript();
  }

  async function check() {
    problem.replaceChildren(h("span", { class: "muted-text small" }, "Checking Entra..."));
    try {
      view.status = (await rpc("platform_entra_check", params())).status;
      problem.replaceChildren();
      drawTable();
    } catch (err) {
      problem.replaceChildren(h("p", { class: "fail-text small" }, friendlyError(err).text));
    }
  }

  async function save() {
    try {
      const res = await rpc("platform_entra_save", { ...params(), kind: view.kind });
      toast(`Saved to ${res.path}`);
    } catch (err) {
      problem.replaceChildren(h("p", { class: "fail-text small" }, friendlyError(err).text));
    }
  }

  const overlay = showDialog(`Access groups for ${d.name}`, h("div", { class: "entra-body" },
    withHowItWorks(h("div", { class: "entra-main" },
      h("div", { class: "row" }, h("span", { class: "grow muted-text small" },
        "One Entra ID security group per job. Give access to groups, never to people, so a joiner or leaver is one membership change."),
      previewTag(), namingBtn),
      h("div", { class: "row tight wrap" }, h("span", { class: "small" }, "Environments"), ...envChecks, subsCheck),
      table,
      h("div", { class: "card pad entra-create" },
        h("div", { class: "row tight wrap" }, h("b", { class: "grow" }, "Create them"),
          btn("Check in Entra", null, check, "tool outline small", { title: "Reads which of these groups exist. Asks to read your directory's groups the first time." }),
          btn("Copy", null, () => copyText(script.textContent, "Script"), "tool outline small"),
          btn("Save to Downloads", "save", save, "tool primary small")),
        tabs, steps, script, problem,
        h("p", { class: "muted-text small" }, "An Entra admin (Groups Administrator) runs it, or builds the groups by hand from the CSV. ",
          "It is safe to run again: a group that already exists is left alone. Fabric role assignments gives the groups their roles once they exist."))),
    howItWorks("platform.entra", {
      reads: "The platform's domains, its group naming, and the groups linked to each role. Check in Entra and Link existing group read your directory's groups by name, as you (Group.Read.All, asked the first time).",
      changes: "Link existing group and Unlink save which group a role uses in the platform's catalog (catalog.entra_group); Naming saves the pattern for every domain. Nothing here creates a group in Entra or gives a role in Fabric: the scripts do, when an admin runs them.",
      results: "Save to Downloads writes a new file there and never replaces one. Linking is kept on the Audit tab.",
      anchor: "platform",
    }))));
  overlay.querySelector(".dialog").classList.add("wide");
  refresh();
}

// Use a group someone already has for one role: find it by name, or paste its object ID.
function entraLinkDialog(r, back) {
  const search = h("input", { placeholder: "Part of the group's name, or its object ID" });
  const results = h("div", { class: "entra-results", role: "radiogroup", "aria-label": "Groups" });
  const pasteId = h("input", { class: "mono", placeholder: "00000000-0000-0000-0000-000000000000" });
  const pasteName = h("input", { placeholder: "Its name, so people recognise it" });
  const allEnvs = r.env ? h("input", { type: "checkbox" }) : null;
  const note = h("div");
  let picked = null;
  let timer = null;
  const find = async () => {
    const text = search.value.trim();
    if (text.length < 2) { results.replaceChildren(); return; }
    try {
      const found = await rpc("platform_entra_find", { query: text });
      note.replaceChildren();
      results.replaceChildren(...(found.length ? found.map((g) => h("label", { class: `check entra-result${g.usable ? "" : " muted-text"}` },
        h("input", { type: "radio", name: "entra-group", disabled: g.usable ? null : true, onchange: () => { picked = g; } }),
        h("span", {}, h("b", {}, g.name), h("div", { class: "small muted-text" }, g.kind,
          g.usable ? "" : ": can't hold Fabric roles", " · ", h("span", { class: "mono" }, g.id)))))
        : [h("p", { class: "muted-text small" }, "No group has that in its name.")]));
    } catch (err) {
      results.replaceChildren();
      note.replaceChildren(h("p", { class: "warn-box card pad small" }, friendlyError(err).text));
    }
  };
  search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(find, 350); });
  const submit = async () => {
    const id = picked?.id || pasteId.value.trim();
    if (!id) { search.focus(); return; }
    const name = picked?.name || pasteName.value.trim();
    const envs = allEnvs?.checked ? ["dev", "test", "prod"] : null;
    if (await metaCall("platform_entra_link", { domain_id: r.domain_id, role: r.role, env: r.env, group_id: id, group_name: name, envs }, "Not linked")) {
      toast(`${r.label} now uses ${name || id}`);
      closeDialog();  // back to the groups, which read the link again
    }
  };
  showDialog(`Link a group to ${r.label}`, h("div", {},
    h("p", { class: "muted-text small" }, `${r.domain}. Use a group you already have instead of ${r.suggested}. Nothing changes in Entra.`),
    h("label", { class: "field" }, "Find a group", search), note, results,
    h("details", {}, h("summary", { class: "small" }, "Can't search your directory? Paste the group's object ID"),
      h("label", { class: "field" }, "Object ID", pasteId), h("label", { class: "field" }, "Name", pasteName)),
    allEnvs ? h("label", { class: "check" }, allEnvs, h("span", {}, `Also use it for ${ENTRA_ROLE_NAMES[r.role] || r.role} in the other environments`)) : null,
    h("p", { class: "muted-text small" }, "The link is saved with the domain. The create scripts skip this role; Fabric role assignments gives the role to your group.")), [
    btn("Cancel", null, closeDialog, "tool outline"), btn("Link group", null, submit, "tool primary")], back);
}

// The naming pattern for every domain's suggested groups. Platform admins change it.
function entraNamingDialog(naming, canEdit, back) {
  const lock = canEdit ? {} : { disabled: true };
  const pattern = h("input", { class: "mono", value: naming.pattern, ...lock });
  const prefix = h("input", { value: naming.prefix || "", ...lock });
  const caseSel = h("select", lock, [["upper", "UPPER"], ["lower", "lower"], ["as_typed", "As typed"]].map(([v, l]) =>
    h("option", { value: v, selected: v === naming.case ? true : null }, l)));
  const part = h("select", lock, [["id", "Domain id (sales)"], ["name", "Domain name (Sales)"]].map(([v, l]) =>
    h("option", { value: v, selected: v === naming.domain_part ? true : null }, l)));
  const words = Object.fromEntries(Object.keys(ENTRA_ROLE_NAMES).map((k) => [k, h("input", { value: naming.words?.[k] || "", ...lock, "aria-label": ENTRA_ROLE_NAMES[k] })]));
  const off = Object.fromEntries(Object.keys(ENTRA_ROLE_NAMES).map((k) => [k, h("input", { type: "checkbox", checked: !(naming.roles_off || []).includes(k) ? true : null, ...lock,
    "aria-label": `Suggest a group for ${ENTRA_ROLE_NAMES[k]}` })]));
  const preview = h("div", { class: "mono small entra-preview" });
  const values = () => ({ pattern: pattern.value.trim(), prefix: prefix.value.trim(), case: caseSel.value, domain_part: part.value,
    words: Object.fromEntries(Object.entries(words).map(([k, i]) => [k, i.value.trim()])),
    roles_off: Object.keys(off).filter((k) => !off[k].checked) });
  const draw = () => {
    const v = values();
    preview.replaceChildren(...[["domain_admins", ""], ["creators", "Dev"], ["readers", "Prod"]].map(([role, env]) =>
      h("div", {}, entraName(v, role, env))));
  };
  [pattern, prefix, caseSel, part, ...Object.values(words)].forEach((i) => i.addEventListener("input", draw));
  showDialog("Group naming", h("div", {},
    h("p", { class: "muted-text small" }, "One pattern for every domain, kept in the platform. Parts: {PREFIX} {DOMAIN} {SUBDOMAIN} {ROLE} {ENV}. A part left empty takes its separator with it, so a top-level domain or a role for every environment still reads cleanly."),
    h("label", { class: "field" }, "Pattern", pattern),
    h("div", { class: "entra-grid" }, h("label", { class: "field" }, "Prefix", prefix), h("label", { class: "field" }, "Case", caseSel),
      h("label", { class: "field" }, "Domain part", part)),
    h("b", { class: "small" }, "Roles and their words"),
    h("div", { class: "entra-grid entra-words" }, Object.keys(ENTRA_ROLE_NAMES).map((k) => h("label", { class: "check" }, off[k],
      h("span", { class: "grow" }, ENTRA_ROLE_NAMES[k]), words[k]))),
    h("div", { class: "card pad" }, h("div", { class: "muted-text small" }, "For a domain called Finance:"), preview),
    canEdit ? null : h("p", { class: "muted-text small" }, "Only platform admins can change it.")),
  canEdit ? [btn("Cancel", null, closeDialog, "tool outline"), btn("Save", "save", async () => {
    if (await metaCall("platform_entra_naming_save", { naming: values() }, "Not saved")) closeDialog();
  }, "tool primary")] : [btn("Close", null, closeDialog, "tool outline")], back);
  draw();
}

// The same naming as central/entra.py group_name, for the preview only (the engine names the real groups).
function entraName(v, role, env, domain = { id: "finance", name: "Finance" }) {
  const part = (x) => String(x || "").replace(/[^A-Za-z0-9_.-]+/g, "-").replace(/^[-_.]+|[-_.]+$/g, "");
  const values = { PREFIX: v.prefix || "", DOMAIN: part(v.domain_part === "name" ? domain.name : domain.id), SUBDOMAIN: "",
    ROLE: v.words?.[role] || role, ENV: env };
  const name = v.pattern.replace(/\{(PREFIX|DOMAIN|SUBDOMAIN|ROLE|ENV)\}/g, (_, k) => values[k])
    .replace(/([-_.])[-_.]+/g, "$1").replace(/^[-_.]+|[-_.]+$/g, "");
  return v.case === "upper" ? name.toUpperCase() : v.case === "lower" ? name.toLowerCase() : name;
}

// On a data product's page: which groups give access to its domain, so a reader knows what to ask for.
function productDomainGroupsCard(p) {
  if (!p.domain_id) return null;
  const card = h("div", { class: "card pad product-groups" }, h("b", {}, "Access to the domain"), loading());
  (async () => {
    let out;
    try {
      out = await rpc("platform_entra_groups", { domain_id: p.domain_id, envs: ["prod"] });
    } catch {
      card.remove();  // no platform, or the domain isn't in it: nothing to say
      return;
    }
    const pick = (role) => out.rows.find((r) => r.role === role && r.domain_id === p.domain_id);
    const line = (label, r) => (r ? h("div", { class: "row tight" }, h("span", { class: "muted-text entra-ask" }, label),
      h("code", {}, r.name), btn("Copy", null, () => copyText(r.name, "Group name"), "tool small", { "aria-label": `Copy ${r.name}` })) : null);
    card.replaceChildren(h("div", { class: "row tight" }, h("b", {}, `Access to ${out.domain.name}`), previewTag()),
      h("p", { class: "muted-text small" }, `${out.domain.name} gives access through these groups. Ask for data and its owners add you to the right one.`),
      line("Read the data", pick("readers")), line("See the reports", pick("viewers")), line("Owners", pick("domain_admins")));
  })();
  return card;
}
