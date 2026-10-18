// Data mesh screens: this project's domain and workspaces, its data products, links to other
// teams' products, and an entity's catalog text (About). Domains and the glossary are edited on
// Platform > Domains and Platform > Glossary (platform.js). Domains, glossary and
// published products live in the platform (SQL_Platform, or a SQLite file on this PC until a
// platform is set up in Platform > Settings); products and links are files in the project.

const MESH_ENVS = ["dev", "test", "prod"];
const ACCESS_LABEL = { public: "Everyone", domain: "My domain", private: "This project" };
const STATUS_LABEL = { draft: "Draft", published: "Published", deprecated: "Deprecated" };
const CLASS_LABEL = { "": "Not set", public: "Public", internal: "Internal", confidential: "Confidential", restricted: "Restricted" };

async function meshOverview() {
  const o = await metaCall("mesh_overview", {});
  if (o) state.meta.mesh = o;
  return o;
}

function platformNote(o) {
  const prod = o.platform?.environments?.prod || {};
  const where = prod.kind === "fabric" ? `the platform in ${prod.workspace_name || "Fabric"}` : "the platform on this PC";
  return h("p", { class: "muted-text small" }, `Domains, glossary and published products are kept in ${where}. `,
    prod.kind === "fabric" ? null : h("a", { href: "#", onclick: (e) => { e.preventDefault(); goPlatform("settings"); } }, "Set up the platform in Fabric"),
    prod.kind === "fabric" ? null : " so every team shares them.");
}

function meshProblems(o) {
  const all = [...(o.errors || []), ...(o.problems || [])];
  return all.length ? h("div", { class: "problems" }, h("b", {}, "Fix these first"), gateList(all)) : null;
}

// ---------- domains and workspaces ----------

// Which Fabric domain the project's workspaces are in. Any workspace member can read that (no
// admin rights); the domain's name comes from the catalog once a Fabric admin has imported them.
async function loadWorkspaceDomains(o, into, domain, subdomain, fillSubs) {
  const envs = o.environments || {};
  if (!state.user || !MESH_ENVS.some((e) => envs[e]?.workspace_id)) return;
  let res;
  try { res = await rpc("mesh_workspace_domains", {}); } catch { return; }
  const named = res.workspaces.filter((w) => w.domain);
  const nameOf = (id) => o.domains.find((d) => d.id === id)?.name || id;
  const where = (w) => `${ENV_LABEL[w.env]}${w.designation ? ` ${w.designation}` : ""} workspace (${w.workspace_name || w.workspace_id})`;
  const lines = [];
  if (res.suggested) {
    const pick = res.suggested.subdomain || res.suggested.domain;
    const same = domain.value === res.suggested.domain && subdomain.value === (res.suggested.subdomain || "");
    const w = [...named].reverse().find((x) => x.domain === pick) || named[0];
    lines.push(h("span", {}, `In Fabric, your ${where(w)} is in the ${nameOf(pick)} domain. `),
      same ? h("span", { class: "muted-text" }, "This project matches it.")
        : h("button", { class: "tool link small", onclick: () => {
          domain.value = res.suggested.domain; fillSubs(); subdomain.value = res.suggested.subdomain || "";
          into.replaceChildren(h("span", { class: "muted-text small" }, `Picked ${nameOf(pick)}. Save to keep it.`));
        } }, "Use it"));
  }
  const others = named.filter((w) => res.suggested && w.domain !== (res.suggested.subdomain || res.suggested.domain));
  if (others.length) lines.push(h("div", { class: "muted-text small" }, "Different domain in Fabric: ",
    others.map((w) => `${where(w)} is in ${nameOf(w.domain)}`).join("; "), "."));
  if (res.unnamed.length) lines.push(h("div", { class: "muted-text small" }, res.imported
    ? "Some of your workspaces are in a Fabric domain the platform doesn't have yet. A platform admin who is a Fabric administrator can import it again on Platform > Domains."
    : "Your workspaces are in Fabric domains, but their names aren't in the platform yet. Fabric only shows domain names to Fabric administrators: ask one to choose Import from Fabric on Platform > Domains."));
  if (lines.length) into.replaceChildren(h("div", { class: "note small" }, icon("info", 16), h("div", {}, ...lines)));
}

async function renderMeshDomainsTab(body) {
  const o = await meshOverview();
  if (!o || state.view !== "metadata" || state.meta.tab !== "domains") return;
  const rerender = () => renderMeshDomainsTab(body);
  const me = o.project;
  const top = o.domains.filter((d) => !d.parent_id);
  const domain = h("select", { "aria-label": "Domain" }, h("option", { value: "" }, "No domain yet"),
    top.map((d) => h("option", { value: d.id, selected: d.id === me.domain ? true : null }, d.name)));
  const subdomain = h("select", { "aria-label": "Subdomain" });
  const fillSubs = () => subdomain.replaceChildren(h("option", { value: "" }, "None"),
    ...o.domains.filter((d) => d.parent_id && d.parent_id === domain.value)
      .map((d) => h("option", { value: d.id, selected: d.id === me.subdomain ? true : null }, d.name)));
  domain.addEventListener("change", fillSubs);
  fillSubs();
  const team = h("input", { value: me.team || "", placeholder: "Like Sales analytics" });
  const description = h("input", { value: me.description || "", placeholder: "What this project is for" });
  const fabricHint = h("div", { class: "fabric-domain-hint" });
  loadWorkspaceDomains(o, fabricHint, domain, subdomain, fillSubs);
  const projectCard = h("div", { class: "card pad" },
    h("div", { class: "row" }, h("b", { class: "grow" }, `Project ${me.name}`),
      me.upgraded ? null : h("span", { class: "pill-s neu" }, "Not in the mesh yet")),
    h("p", { class: "muted-text small" }, me.upgraded
      ? "Saved in project.toml and recorded in the catalog, so other teams can find this project's products."
      : "Pick the domain this project belongs to. That writes project.toml (nothing else in the project changes) and lists the project in the catalog."),
    h("div", { class: "form-grid" },
      h("label", { class: "field" }, "Domain", domain), h("label", { class: "field" }, "Subdomain", subdomain),
      h("label", { class: "field" }, "Team", team), h("label", { class: "field" }, "Description", description)),
    fabricHint,
    h("div", { class: "row end" }, btn(me.upgraded ? "Save" : "Join the mesh", "save", async () => {
      const res = await metaCall("mesh_set_project", { fields: { domain: domain.value, subdomain: subdomain.value,
        team: team.value.trim(), description: description.value.trim() } }, "Not saved");
      if (res) { toast("Saved to project.toml"); rerender(); }
    }, "tool primary")));

  // Workspaces by designation: one row per designation, one column per environment.
  const envs = o.environments || {};
  const cell = (env, d) => {
    const ws = (envs[env]?.workspaces || {})[d.id];
    return h("td", {}, ws ? h("span", { class: "row tight" }, icon("workspace", 16), ws.name || ws.id,
      h("button", { class: "tool small", title: "Use the environment's own workspace", onclick: async () => {
        if (await metaCall("meta_set_designation", { env, designation: d.id }, "Not changed")) rerender();
      } }, icon("dismiss", 12)))
      : h("button", { class: "tool link small", onclick: () => pickDesignationWorkspace(env, d, rerender) },
        envs[env]?.workspace_name ? `Same as ${ENV_LABEL[env]}` : "Pick"));
  };
  const layersOf = (d) => o.layers.filter((l) => l.workspace === d.id).map((l) => l.name);
  const workspacesCard = h("div", { class: "card pad" },
    h("b", {}, "Workspaces by designation"),
    h("p", { class: "muted-text small" }, "Split one environment's lakehouses over several workspaces: Ingest for raw data, Product for what other teams read, Serve for reports, Sandbox for trying things. A layer picks its designation on the Layers tab; anything without one stays in the environment's own workspace."),
    h("table", { class: "list" },
      h("thead", {}, h("tr", {}, ["Designation", "Layers", ...MESH_ENVS.map((e) => ENV_LABEL[e])].map((c) => h("th", {}, c)))),
      h("tbody", {}, o.designations.map((d) => h("tr", {},
        h("td", {}, h("b", {}, d.label)),
        h("td", {}, layersOf(d).length ? layersOf(d).map(layerChip) : h("span", { class: "muted-text" }, "none")),
        ...MESH_ENVS.map((env) => cell(env, d)))))));

  const domainRows = o.domains.map((d) => h("tr", {},
    h("td", {}, d.parent_id ? h("span", { class: "muted-text" }, "↳ ") : null, h("b", {}, d.name),
      d.fabric_domain ? h("span", { class: "badge", title: "Follows this domain in Fabric" }, "From Fabric") : null,
      h("div", { class: "muted-text small mono" }, d.id)),
    h("td", {}, d.description || h("span", { class: "muted-text" }, "")),
    h("td", {}, d.owners || h("span", { class: "muted-text" }, "not set")),
    h("td", {}, o.projects.filter((p) => p.domain_id === d.id || p.subdomain_id === d.id).length)));
  // Domains are edited in one place, Platform > Domains (UX-009); this list is for reading.
  const domainsCard = h("div", { class: "card pad" },
    h("div", { class: "row" }, h("b", { class: "grow" }, "Your organization's domains"),
      btn("Glossary", null, () => goPlatform("glossary"), "tool link small",
        { title: "The business glossary is kept on Platform > Glossary; tag tables with its terms on their About tab" }),
      btn("Manage in Platform", "building", () => goPlatform("domains"), "tool outline small",
        { title: "Platform admins create, import and edit domains on Platform > Domains" })),
    platformNote(o),
    o.domains.length ? h("table", { class: "list" },
      h("thead", {}, h("tr", {}, ["Domain", "Description", "Owners", "Projects"].map((c) => h("th", {}, c)))),
      h("tbody", {}, domainRows))
      : h("div", { class: "empty" }, h("span", { class: "title" }, "No domains yet"),
        h("span", { class: "text" }, "A domain is an area of the business that owns its data, like Sales or Finance. Platform admins create them on Platform > Domains.")));
  body.replaceChildren(...[meshProblems(o), projectCard, workspacesCard, domainsCard].filter(Boolean));
}

function domainDialog(o, d, done) {
  // A domain imported from Fabric keeps Fabric's name and parent: change those in Fabric, then import again.
  const fromFabric = !!d?.fabric_domain;
  const locked = fromFabric ? { disabled: true, title: "Comes from Fabric: rename or move it in the Fabric admin portal, then Import from Fabric" } : {};
  const name = h("input", { value: d?.name || "", ...locked });
  const parent = h("select", locked, h("option", { value: "" }, "None (a top-level domain)"),
    o.domains.filter((x) => !x.parent_id && x.id !== d?.id).map((x) => h("option", { value: x.id, selected: x.id === d?.parent_id ? true : null }, x.name)));
  const description = h("input", { value: d?.description || "" });
  const owners = h("input", { value: d?.owners || "", placeholder: "Emails, separated by commas" });
  showDialog(d ? `Edit ${d.name}` : "New domain", h("div", {},
    fromFabric ? h("p", { class: "muted-text small" }, "From Fabric. Its name and parent follow Fabric's domains; owners and description are the platform's.") : null,
    h("label", { class: "field" }, "Name", name), h("label", { class: "field" }, "Part of", parent),
    h("label", { class: "field" }, "Description", description), h("label", { class: "field" }, "Owners", owners)), [
    btn("Cancel", null, closeDialog, "tool outline"),
    btn("Save", "save", async () => {
      if (!name.value.trim()) { name.focus(); return; }
      const res = await metaCall("platform_save_domain", { domain: { id: d?.id, name: name.value.trim(), parent_id: parent.value,
        description: description.value.trim(), owners: owners.value.trim() } }, "Not saved");
      if (res) { closeDialog(); done(); }
    }, "tool primary")]);
}

// Fabric's domain list is a Fabric admin API: only a Fabric administrator can import it.
async function importFabricDomains(done) {
  if (!state.user && !(await signIn())) return;
  const out = await metaCall("platform_import_fabric_domains", {}, "Domains not imported");
  if (!out) return;
  const r = out.result;
  const parts = [r.added.length && `${r.added.length} added`, r.linked.length && `${r.linked.length} matched by name`,
    r.updated.length && `${r.updated.length} renamed or moved`].filter(Boolean);
  toast(`Imported ${out.fabric} Fabric domains${parts.length ? `: ${parts.join(", ")}` : ", nothing changed"}`);
  if (r.skipped.length) toast(`Skipped (deeper than one subdomain): ${r.skipped.join(", ")}`);
  if (r.gone.length) toast(`No longer in Fabric, kept here: ${r.gone.join(", ")}`);
  done();
}

async function pickDesignationWorkspace(env, d, done) {
  const list = h("div", { class: "scroll" }, h("p", { class: "muted-text" }, state.user ? "Loading workspaces..." : "Sign in to pick a Fabric workspace."));
  showDialog(`${d.label} workspace for ${ENV_LABEL[env]}`, list, [btn("Cancel", null, closeDialog, "tool outline")]);
  if (!state.user && !(await signIn())) return;
  const workspaces = await loadWorkspaces();
  const create = async () => {
    const caps = (await attempt(() => rpc("list_capacities"), "Could not list capacities")) || [];
    const capacity = h("select", {}, caps.map((c) => h("option", { value: c.id }, `${c.displayName}${c.sku ? ` (${c.sku})` : ""}`)),
      h("option", { value: "" }, "No capacity (assign one in Fabric later)"));
    showDialog(`New ${d.label} workspace for ${ENV_LABEL[env]}`, h("div", {},
      h("p", { class: "muted-text" }, "It's named after your domain, like Sales Ingest - Test."),
      h("label", { class: "field" }, "Capacity", capacity)), [
      btn("Cancel", null, closeDialog, "tool outline"),
      btn("Create workspace", "add", async () => {
        const res = await metaCall("meta_create_designation_workspace", { env, designation: d.id, capacity_id: capacity.value }, "Workspace not created");
        if (res) { closeDialog(); state.workspaces = null; toast(`Created the ${d.label} workspace for ${ENV_LABEL[env]}`); done(); }
      }, "tool primary")]);
  };
  list.replaceChildren(
    h("button", { class: "choice", onclick: create }, icon("add", 20),
      h("span", { class: "main" }, h("span", { class: "n" }, "Create a new workspace"), h("span", { class: "w" }, "Named after your domain and this designation"))),
    ...workspaces.map((w) => h("button", { class: "choice", onclick: async () => {
      closeDialog();
      if (await metaCall("meta_set_designation", { env, designation: d.id, workspace_id: w.id, workspace_name: w.displayName }, "Not changed")) done();
    } }, icon("workspace", 20), h("span", { class: "main" }, h("span", { class: "n" }, w.displayName)))));
}

// ---------- data products ----------

async function renderMeshProductsTab(body, p) {
  const o = await meshOverview();
  if (!o || state.view !== "metadata" || state.meta.tab !== "products") return;
  const rerender = () => renderMeshProductsTab(body, p);
  const published = Object.fromEntries(o.published.map((x) => [x.id, x]));
  const mine = o.products.map((x) => {
    const live = published[`${o.project.name}/${x.name}`];
    const envs = live ? [...new Set(live.ports.map((port) => port.env))] : [];
    return h("tr", { class: "click", onclick: () => productDialog(p, x, rerender) },
      h("td", {}, h("span", { class: "item" }, icon("cube", 18), h("span", {}, h("b", {}, x.title), h("div", { class: "muted-text small mono" }, x.name)))),
      h("td", {}, x.ports.map((port) => h("div", { class: "mono small" }, `${port.name} ← ${port.entity}`))),
      h("td", {}, ACCESS_LABEL[x.access] || x.access),
      h("td", {}, h("span", { class: `pill-s ${x.status === "published" ? "pass" : x.status === "deprecated" ? "warn" : "neu"}` }, STATUS_LABEL[x.status] || x.status)),
      h("td", {}, envs.length ? envs.map((e) => ENV_LABEL[e] || e).join(", ") : h("span", { class: "muted-text" }, "not yet")),
      h("td", {}, live ? live.consumers.length : 0),
      h("td", {}, x.owner || h("span", { class: "muted-text" }, "not set")));
  });
  const others = o.published.filter((x) => x.project !== o.project.name);
  const linked = new Set(o.links.map((l) => `${l.product}:${l.port}`));
  body.replaceChildren(...[meshProblems(o),
    h("div", { class: "card pad" },
      h("div", { class: "row" }, h("b", { class: "grow" }, "This project's data products"),
        btn(`Publish to the catalog (${ENV_LABEL[state.meta.env]})`, "publish", async () => {
          const res = await metaCall("mesh_publish", { env: state.meta.env }, "Not published");
          if (res) { toast(res.published.length ? `Published ${res.published.join(", ")} in ${ENV_LABEL[state.meta.env]}` : "Nothing to publish: every product is a draft"); rerender(); }
        }, "tool outline small"),
        btn("New data product", "add", () => productDialog(p, null, rerender), "tool primary small")),
      h("p", { class: "muted-text small" }, "A data product is the tables (ports) other teams may read from this project, with an owner and a promise. Promote publishes them to the catalog for the target environment; a product for other teams needs a description and every column described first."),
      mine.length ? h("table", { class: "list" },
        h("thead", {}, h("tr", {}, ["Product", "Ports", "Open to", "Status", "In the catalog", "Teams reading it", "Owner"].map((c) => h("th", {}, c)))),
        h("tbody", {}, mine))
        : h("div", { class: "empty" }, h("span", { class: "title" }, "No data products yet"),
          h("span", { class: "text" }, "Make one from your Gold tables so other teams can link to them instead of copying."),
          btn("New data product", "add", () => productDialog(p, null, rerender), "tool primary"))),
    h("div", { class: "card pad" },
      h("b", {}, "Other teams' products"),
      platformNote(o),
      others.length ? h("table", { class: "list" },
        h("thead", {}, h("tr", {}, ["Product", "Domain", "Description", "Ports", "Open to", ""].map((c) => h("th", {}, c)))),
        h("tbody", {}, others.filter((x) => matches(`${x.id} ${x.description}`)).map((x) => h("tr", {},
          h("td", {}, h("b", { class: "mono" }, x.id), x.status === "deprecated" ? h("span", { class: "pill-s warn" }, `Ends ${x.deprecated_on || "soon"}`) : null),
          h("td", {}, x.domain_id || ""),
          h("td", {}, x.description),
          h("td", {}, [...new Set(x.ports.map((port) => port.port))].map((port) => h("div", { class: "row tight" }, h("span", { class: "mono small" }, port),
            linked.has(`${x.id}:${port}`) ? h("span", { class: "pill-s pass" }, "linked")
              : btn("Link", "databaseLink", () => linkDialog(o, { product: x.id, port }, rerender), "tool link small")))),
          h("td", {}, ACCESS_LABEL[x.access] || x.access),
          h("td", {}, x.owner)))))
        : h("p", { class: "muted-text" }, "No other team has published a product yet.")),
  ].filter(Boolean));
}

function productDialog(p, x, done) {
  const name = h("input", { value: x?.name || "", placeholder: "Like customer_360" });
  const title = h("input", { value: x?.title || "" });
  const description = h("textarea", { rows: 3 }, x?.description || "");
  const owner = h("input", { value: x?.owner || state.user || "" });
  const access = h("select", {}, Object.entries(ACCESS_LABEL).map(([id, label]) => h("option", { value: id, selected: (x?.access || "domain") === id ? true : null }, label)));
  const status = h("select", {}, Object.entries(STATUS_LABEL).map(([id, label]) => h("option", { value: id, selected: (x?.status || "draft") === id ? true : null }, label)));
  const deprecated = h("input", { type: "date", value: x?.deprecated_on || "" });
  const chosen = Object.fromEntries((x?.ports || []).map((port) => [port.entity, port.name]));
  const portRows = p.entities.map((e) => {
    const box = h("input", { type: "checkbox", checked: chosen[e.name] ? true : null });
    const port = h("input", { value: chosen[e.name] || e.name.split(".").pop(), class: "mono", "aria-label": "Port name" });
    return { e, box, port, row: h("label", { class: "check port-row" }, box, layerChip(e.layer), h("span", { class: "mono grow" }, e.name), port) };
  });
  showDialog(x ? `Edit ${x.title}` : "New data product", h("div", { class: "scroll" },
    h("div", { class: "form-grid" }, h("label", { class: "field" }, "Name", name), h("label", { class: "field" }, "Title", title),
      h("label", { class: "field" }, "Owner", owner), h("label", { class: "field" }, "Open to", access),
      h("label", { class: "field" }, "Status", status), h("label", { class: "field" }, "Ends on (deprecated only)", deprecated)),
    h("label", { class: "field" }, "Description", description),
    h("div", { class: "field" }, "Ports: the tables other teams may read, and the name they use", h("div", { class: "port-list" }, portRows.map((r) => r.row)))), [
    x ? btn("Delete", "delete", async () => {
      if (!(await confirmDialog(`Delete ${x.title}?`, "The product file is removed. Teams that link to it keep their links until they remove them; their runs fail once it's gone from the catalog.", "Delete", { danger: true }))) return;
      if (await metaCall("mesh_delete_product", { name: x.name }, "Not deleted")) done();
    }, "tool outline") : null,
    btn("Cancel", null, closeDialog, "tool outline"),
    btn("Save", "save", async () => {
      const ports = portRows.filter((r) => r.box.checked).map((r) => ({ name: r.port.value.trim(), entity: r.e.name }));
      const res = await metaCall("mesh_save_product", { product: { name: name.value.trim(), title: title.value.trim(),
        description: description.value.trim(), owner: owner.value.trim(), access: access.value, status: status.value,
        deprecated_on: deprecated.value, version: x?.version || 1, ports }, original: x?.name || "" }, "Not saved");
      if (res) { closeDialog(); toast("Saved"); done(); }
    }, "tool primary")].filter(Boolean));
}

// ---------- links ----------

async function renderMeshLinksTab(body) {
  const o = await meshOverview();
  if (!o || state.view !== "metadata" || state.meta.tab !== "links") return;
  const rerender = () => renderMeshLinksTab(body);
  const env = state.meta.env;
  const status = h("span", { class: "muted-text small" }, "Checking where each link points...");
  const rows = o.links.map((l) => {
    const where = h("td", { class: "mono small" }, "...");
    return { l, where, row: h("tr", {},
      h("td", {}, h("span", { class: "item" }, icon("databaseLink", 18), h("b", { class: "mono" }, l.alias))),
      h("td", { class: "mono" }, `${l.product} · ${l.port}`),
      h("td", {}, MESH_ENVS.map((e) => `${ENV_LABEL[e]} reads ${ENV_LABEL[l.reads[e]] || l.reads[e]}`).join(", ")),
      h("td", {}, p_usedBy(l.alias)),
      where,
      h("td", {}, btn("Remove", null, async () => {
        if (await metaCall("mesh_delete_link", { alias: l.alias }, "Not removed")) rerender();
      }, "tool small danger"))) };
  });
  body.replaceChildren(...[meshProblems(o),
    h("div", { class: "card pad" },
      h("div", { class: "row" }, h("b", { class: "grow" }, "Links to other teams' products"),
        btn("New link", "add", () => linkDialog(o, null, rerender), "tool primary small")),
      h("p", { class: "muted-text small" }, "A link reads another team's table where it lives, with no copy. An entity uses it with [source] link = \"<name>\". Each of your environments says which of theirs it reads, so Dev can read their Prod. ",
        "The catalog checks the product is open to you."),
      rows.length ? h("table", { class: "list" },
        h("thead", {}, h("tr", {}, ["Link", "Product · port", "Reads", "Used by", `Table in ${ENV_LABEL[env]}`, ""].map((c) => h("th", {}, c)))),
        h("tbody", {}, rows.map((r) => r.row))) : h("div", { class: "empty" }, h("span", { class: "title" }, "No links yet"),
        h("span", { class: "text" }, "Find a product on the Data products tab and choose Link, or add one here.")),
      rows.length ? status : null)].filter(Boolean));
  if (!rows.length) return;
  const resolved = await attempt(() => rpc("mesh_resolve_links", { env })) || [];
  for (const r of rows) {
    const got = resolved.find((x) => x.alias === r.l.alias);
    r.where.replaceChildren(got?.ok ? h("span", { title: got.table }, got.table.replace(/^abfss:\/\/([^@]+)@[^/]+\//, "$1/"))
      : h("span", { class: "fail-text" }, got?.message || "not resolved"));
  }
  status.textContent = `Resolved for ${ENV_LABEL[env]}.`;
}

function p_usedBy(alias) {
  const users = (state.meta.project?.entities || []).filter((e) => e.link === alias).map((e) => e.name);
  return users.length ? users.map((u) => h("button", { class: "tool link small mono", onclick: () => openEntity(u) }, u))
    : h("span", { class: "muted-text" }, "nothing yet");
}

function linkDialog(o, preset, done) {
  const products = o.published.filter((x) => x.project !== o.project.name && x.status !== "draft");
  const product = h("select", {}, products.map((x) => h("option", { value: x.id, selected: x.id === preset?.product ? true : null }, x.id)));
  const port = h("select", {});
  const fillPorts = () => {
    const x = products.find((y) => y.id === product.value);
    port.replaceChildren(...[...new Set((x?.ports || []).map((pp) => pp.port))].map((pp) => h("option", { value: pp, selected: pp === preset?.port ? true : null }, pp)));
  };
  product.addEventListener("change", fillPorts);
  fillPorts();
  const alias = h("input", { value: preset ? `${preset.product.split("/").pop()}_${preset.port}`.toLowerCase() : "", class: "mono" });
  const reads = Object.fromEntries(MESH_ENVS.map((e) => [e, h("select", { "aria-label": `${ENV_LABEL[e]} reads` },
    MESH_ENVS.map((t) => h("option", { value: t, selected: (e === "dev" ? "prod" : e) === t ? true : null }, ENV_LABEL[t])))]));
  if (!products.length) {
    showDialog("New link", h("p", { class: "text" }, "No other team has published a product you can link to yet."), [btn("Close", null, closeDialog, "tool primary")]);
    return;
  }
  showDialog("New link", h("div", {},
    h("div", { class: "form-grid" }, h("label", { class: "field" }, "Product", product), h("label", { class: "field" }, "Port", port)),
    h("label", { class: "field" }, "Name entities use", alias),
    h("div", { class: "field" }, "Which of their environments each of yours reads",
      h("div", { class: "row" }, MESH_ENVS.map((e) => h("label", { class: "row tight" }, ENV_LABEL[e], reads[e]))))), [
    btn("Cancel", null, closeDialog, "tool outline"),
    btn("Add link", "databaseLink", async () => {
      const res = await metaCall("mesh_save_link", { link: { alias: alias.value.trim(), product: product.value, port: port.value,
        reads: Object.fromEntries(MESH_ENVS.map((e) => [e, reads[e].value])) } }, "Link not added");
      if (res) { closeDialog(); toast(`Linked. Use [source] link = "${alias.value.trim()}" in an entity.`); done(); }
    }, "tool primary")]);
}

// ---------- an entity's About card ----------

function aboutCard(e) {
  if (!previewShown()) return null;
  const card = h("div", { class: "card pad" }, h("b", {}, "About"), h("p", { class: "muted-text small" }, "Loading the catalog..."));
  rpc("mesh_about", { name: e.name }).then((a) => fillAbout(card, e, a)).catch((err) => {
    card.replaceChildren(h("b", {}, "About"), h("p", { class: "muted-text small" }, friendlyError(String(err)).text));
  });
  return card;
}

function fillAbout(card, e, a) {
  const description = h("textarea", { rows: 2, placeholder: "What one row is, and when to use this table" }, a.description || "");
  const tags = h("input", { value: (a.tags || []).join(", "), placeholder: "Like pii, finance" });
  const classification = h("select", {}, Object.entries(CLASS_LABEL).map(([id, label]) => h("option", { value: id, selected: (a.classification || "") === id ? true : null }, label)));
  const steward = h("input", { value: a.steward || "", placeholder: "Who answers questions about it" });
  const terms = h("input", { value: (a.terms || []).join(", "), placeholder: a.glossary.length ? `Like ${a.glossary.slice(0, 2).join(", ")}` : "Glossary terms", list: `terms-${e.name}` });
  const colInputs = a.columns.map((c) => ({ c, input: h("input", { value: c.description || "", placeholder: "Describe this column" }),
    cls: h("select", { "aria-label": "Classification" }, Object.entries(CLASS_LABEL).map(([id, label]) => h("option", { value: id, selected: (c.classification || "") === id ? true : null }, label))) }));
  const save = async () => {
    let ok = await metaCall("mesh_save_about", { name: e.name, fields: { description: description.value.trim(), tags: tags.value,
      classification: classification.value, steward: steward.value.trim(), terms: terms.value } }, "Not saved");
    for (const { c, input, cls } of colInputs) {
      if (!ok) break;
      if (input.value.trim() !== (c.description || "") || cls.value !== (c.classification || "")) {
        ok = await metaCall("mesh_save_about", { name: e.name, column: c.name, fields: { description: input.value.trim(), classification: cls.value } }, "Not saved");
      }
    }
    if (ok) { toast("Saved to the catalog"); fillAbout(card, e, ok); }
  };
  card.replaceChildren(...[
    h("div", { class: "row" }, h("b", { class: "grow" }, "About ", previewTag()),
      a.products.length ? a.products.map((x) => h("span", { class: "pill-s info" }, `${x.product} · ${x.port}`)) : null,
      btn("Save", "save", save, "tool primary small")),
    h("p", { class: "muted-text small" }, "Anyone can describe this table and its columns here; it's saved in the catalog, not in the entity file, so no Git change is needed. Text in the entity's [catalog] block shows until someone writes their own."),
    a.problem ? h("p", { class: "fail-text small" }, a.problem) : null,
    a.publish_problems?.length ? h("div", { class: "problems" }, h("b", {}, "Needed before this port can be published"), gateList(a.publish_problems)) : null,
    h("label", { class: "field" }, "Description", description),
    h("div", { class: "form-grid" }, h("label", { class: "field" }, "Tags", tags), h("label", { class: "field" }, "Classification", classification),
      h("label", { class: "field" }, "Steward", steward), h("label", { class: "field" }, "Glossary terms", terms)),
    h("datalist", { id: `terms-${e.name}` }, a.glossary.map((t) => h("option", { value: t }))),
    h("table", { class: "list" }, h("thead", {}, h("tr", {}, ["Column", "Type", "Description", "Classification"].map((c) => h("th", {}, c)))),
      h("tbody", {}, colInputs.map(({ c, input, cls }) => h("tr", {},
        h("td", { class: "mono" }, c.name), h("td", { class: "mono muted-text" }, c.type), h("td", { class: "grow" }, input), h("td", {}, cls)))))].filter(Boolean));
}

// ---------- Platform > Settings ----------

async function platformSection() {
  const section = h("section", { class: "card pad" }, h("h2", {}, "Platform"), h("p", { class: "muted-text" }, "Loading..."));
  const s = await attempt(() => rpc("platform_status"));
  if (!s) { section.replaceChildren(h("h2", {}, "Platform"), h("p", { class: "muted-text" }, "The platform shows once the local engine is running.")); return section; }
  const policy = s.source === "policy";
  const row = (env) => {
    const c = s.environments[env];
    const fabric = c.kind === "fabric";
    return settingRow(`${ENV_LABEL[env]}: ${fabric ? c.workspace_name || c.workspace_id : "on this PC"}`,
      fabric ? "Surrogate keys, catalog and run history for every team, in SQL_Platform and LH_Meta."
        : env === "prod" ? "Domains, products and catalog text are kept in a file on this PC until the Prod platform exists."
          : "Keys stay in each project's LH_Keys until this environment has a platform.",
      policy ? null : h("div", { class: "row" },
        btn(fabric ? "Finish or repair" : "Create", fabric ? "sync" : "add", () => platformSetupDialog(env, s), fabric ? "tool outline" : "tool primary"),
        fabric ? btn("Forget", null, async () => {
          if (!(await confirmDialog(`Stop using ${c.workspace_name} for ${ENV_LABEL[env]}?`, "Nothing in Fabric is deleted; this PC just stops using it.", "Forget"))) return;
          if (await attempt(() => rpc("platform_forget", { env }), "Not changed")) render();
        }, "tool outline") : btn("Join existing", "workspace", () => platformJoin(env), "tool outline")),
      policy ? "policy" : null);
  };
  section.replaceChildren(h("h2", {}, "Platform ", previewTag()),
    h("p", { class: "muted-text small" }, "The central team's workspace in each environment: one landing zone for every source (LH_Landing), one key system for every team (SQL_Platform, which also holds the catalog), the functions every app and notebook calls (UDF_Platform), run history (LH_Meta) and their ids (VL_Platform). Create it once; every other PC joins it. ", learnMore("platform")),
    ...MESH_ENVS.map(row));
  return section;
}

async function platformJoin(env) {
  if (!state.user && !(await signIn())) return;
  const ws = await pickWorkspace(await loadWorkspaces(), { title: `Join the ${ENV_LABEL[env]} platform`, okLabel: "Join",
    note: "Pick the Platform workspace your central team made. Its items are found by name." });
  if (!ws) return;
  if (await attempt(() => rpc("platform_join", { env, workspace_id: ws.id }), "Could not join")) { toast(`${ENV_LABEL[env]} now uses ${ws.displayName}`); render(); }
}

async function platformSetupDialog(env, s) {
  if (!state.user && !(await signIn())) return;
  const c = s.environments[env];
  const name = h("input", { value: c.workspace_name || s.default_names[env], disabled: c.workspace_id ? true : null });
  const capacity = h("select", {}, h("option", { value: "" }, "Loading capacities..."));
  const steps = h("div", {});
  const run = async () => {
    steps.replaceChildren(h("p", { class: "muted-text" }, "Creating the platform. This takes a minute..."));
    const res = await attempt(() => rpc("platform_setup", { env, name: name.value.trim(), capacity_id: capacity.value }), "Platform not created");
    if (!res) { steps.replaceChildren(); return; }
    steps.replaceChildren(h("ul", { class: "steps-list" }, res.steps.map((st) => h("li", {},
      icon(st.status === "done" ? "ok" : "error", 14), h("b", {}, ` ${st.step}: `), st.detail))),
    h("p", { class: res.ready ? "muted-text" : "fail-text" }, res.ready ? "The platform is ready." : "Some steps didn't finish. Fix what they say, then choose Finish or repair."));
    if (res.ready) toast(`The ${ENV_LABEL[env]} platform is ready`, false, { log: true });
  };
  showDialog(`${c.workspace_id ? "Finish" : "Create"} the ${ENV_LABEL[env]} platform`, h("div", {},
    h("p", { class: "text" }, "Creates (or finishes) the Platform workspace with LH_Meta, SQL_Platform, UDF_Platform and VL_Platform. Steps that are already done are kept."),
    h("label", { class: "field" }, "Workspace", name), h("label", { class: "field" }, "Capacity", capacity), steps), [
    btn("Close", null, () => { closeDialog(); render(); }, "tool outline"),
    btn(c.workspace_id ? "Finish setup" : "Create platform", "add", run, "tool primary")]);
  const caps = (await attempt(() => rpc("list_capacities"), "Could not list capacities")) || [];
  capacity.replaceChildren(...caps.map((x) => h("option", { value: x.id }, `${x.displayName}${x.sku ? ` (${x.sku})` : ""}`)),
    h("option", { value: "" }, "No capacity (SQL database and functions need one)"));
}

// ---------- surrogate keys in the platform ----------

function keysPlatformCard(k, rerender) {
  if (!k.platform && !k.platform_available) return null;
  if (k.platform) {
    return h("div", { class: "card pad" }, h("div", { class: "row" }, icon("database", 18),
      h("span", { class: "grow" }, h("b", {}, "Keys are in the platform's SQL database"),
        h("div", { class: "muted-text small" }, `Moved ${k.moved_at ? when(k.moved_at) : ""}. LH_Keys is kept as it was. Every run asks SQL_Platform for keys.`)),
      btn("Back to LH_Keys", null, async () => {
        if (!(await confirmDialog("Use LH_Keys again?", "Only allowed while the platform hasn't issued keys LH_Keys doesn't have.", "Use LH_Keys"))) return;
        if (await metaCall("meta_keys_to_lakehouse", { env: k.env }, "Not changed")) rerender();
      }, "tool outline small")));
  }
  return preview("platform", h("div", { class: "card pad" }, h("div", { class: "row" }, icon("database", 18),
    h("span", { class: "grow" }, h("b", {}, "Move these keys to the platform"),
      h("div", { class: "muted-text small" }, "Copies every key into SQL_Platform with the same number, checks each row, then switches this environment over. One key service for every team, and no MERGE on LH_Keys during runs.")),
    btn("Move keys", "databaseArrowRight", async () => {
      if (!(await confirmDialog(`Move ${ENV_LABEL[k.env]}'s keys to the platform?`, "LH_Keys is only read. The switch happens only if every key matches.", "Move keys"))) return;
      const res = await metaCall("meta_keys_to_platform", { env: k.env }, "Keys not moved");
      if (!res) return;
      if (res.ok) toast(`Moved ${res.tables.reduce((n, t) => n + t.checked, 0).toLocaleString()} keys; every one matches`, false, { log: true });
      else showDialog("Keys not switched", h("div", {}, h("p", { class: "text" }, "Some keys didn't match, so this environment still uses LH_Keys."),
        gateList(res.tables.filter((t) => !t.match).map((t) => `${t.table}: keys ${t.differ.join(", ")} differ`))), [btn("Close", null, closeDialog, "tool primary")]);
      rerender();
    }, "tool outline small"))));
}

registerFeature({
  id: "mesh", preview: true,
  commands: [
    { label: "New data product", icon: "cube", keywords: "mesh publish share", preview: true, run: () => goMeta("products"), when: () => !!state.meta?.project?.path },
    { label: "Link another team's product", icon: "databaseLink", keywords: "mesh shortcut reference", preview: true, run: () => goMeta("links"), when: () => !!state.meta?.project?.path },
    { label: "Set up the platform", icon: "database", keywords: "central sql keys catalog", preview: true, run: () => goPlatform("settings") },
  ],
});
