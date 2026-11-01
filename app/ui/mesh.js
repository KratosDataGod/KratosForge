// Data mesh screens: this project's domain and workspaces (Domains > This project), its data products,
// links to other teams' products, and an entity's catalog text (About). Domains are edited on
// Data mesh > Domains and the glossary on Platform > Set up > Glossary (platform.js). Domains, glossary and
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
    ? "Some of your workspaces are in a Fabric domain the platform doesn't have yet. A platform admin who is a Fabric administrator can import it again on Data mesh > Domains."
    : "Your workspaces are in Fabric domains, but their names aren't in the platform yet. Fabric only shows domain names to Fabric administrators: ask one to choose Import from Fabric on Data mesh > Domains."));
  if (lines.length) into.replaceChildren(h("div", { class: "note small" }, icon("info", 16), h("div", {}, ...lines)));
}

async function renderMeshDomainsTab(body) {
  const o = await meshOverview();
  if (!o || state.view !== "metadata" || state.meta.tab !== "workspaces") return;
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
  // The catalog moved the project (a domain owner approved its ask): project.toml catches up on Use it.
  const recorded = (o.projects || []).find((p) => p.name === me.name);
  const moved = me.upgraded && recorded && (recorded.domain_id || "") !== (me.domain || "")
    && (recorded.domain_id || recorded.subdomain_id) ? h("div", { class: "card pad notice mesh-moved" }, h("div", { class: "row" },
      h("span", { class: "grow" }, `The catalog has ${me.name} in ${o.domains.find((d) => d.id === recorded.domain_id)?.name || recorded.domain_id} (a domain owner approved it, or someone changed it elsewhere). project.toml says ${me.domain || "no domain"}.`),
      btn("Use it", null, async () => {
        const res = await metaCall("mesh_set_project", { fields: { domain: recorded.domain_id, subdomain: recorded.subdomain_id || "" } }, "Not saved");
        if (res) { toast("Saved to project.toml"); rerender(); }
      }, "tool primary small"))) : null;
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
      if (res?.domain_request) toast(`Saved. Joining ${res.domain_request.detail?.domain_name || domain.value} waits for its owners`);
      else if (res) toast("Saved to project.toml");
      if (res) rerender();
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

  // The organization's domains are listed, and edited by platform admins, on Domains > Domains.
  body.replaceChildren(withHowItWorks(h("div", {},
    ...[meshProblems(o), moved, projectCard, membersCard(o, rerender), workspacesCard, h("div", { class: "card pad" }, platformNote(o))].filter(Boolean)),
  howItWorks("mesh.project", PROJECT_HOW)));
}

// What Domains > This project reads and writes.
const PROJECT_HOW = {
  reads: "project.toml, the project's layers and environments, and from the platform's catalog the domains, this project's members and its waiting ask to join a domain.",
  changes: "Save writes project.toml and records the project in catalog.project. Picking a domain you don't own adds a request to catalog.request for that domain's owners; project.toml keeps the current domain until they approve, then the catalog has the new one and Use it writes it to project.toml. Add member, Make owner and Remove write catalog.project_member. Withdraw marks your ask withdrawn. Pick and the dismiss button on a designation write the environment's settings in the project.",
  results: "Only the project's members and platform admins can publish its products, make its links or change its domain. Every change is on Platform > Govern > Audit.",
  anchor: "data-mesh",
};

// Members (Preview): who may publish this project's products, make its links and change its domain.
// Read on its own, so a platform that can't keep members yet still shows the rest of the tab.
function membersCard(o, rerender) {
  const head = (m) => h("div", { class: "row" }, h("b", { class: "grow" }, "Members"), previewTag(),
    m?.can_change ? btn("Add member", "add", () => addMemberDialog(rerender), "tool outline small") : null);
  const card = h("div", { class: "card pad mesh-members" }, head(null), h("p", { class: "muted-text small" }, "Reading members..."));
  if (!o.project.upgraded) {
    card.replaceChildren(head(null), h("p", { class: "muted-text small" }, "Join the mesh first: then whoever saves it is the project's first owner."));
    return card;
  }
  rpc("mesh_members", {}).then((m) => {
    if (!card.isConnected) return;
    const set = async (user, role, what) => {
      if (await metaCall("mesh_set_member", { user, role }, what)) rerender();
    };
    const me = String(state.user || "").toLowerCase();
    const owners = m.members.filter((x) => x.role === "owner").length;
    const rows = m.members.map((x) => h("tr", {},
      h("td", {}, x.user), h("td", {}, x.role === "owner" ? "Owner" : "Member"),
      h("td", {}, x.user === me ? h("span", { class: "muted-text" }, "You") : m.can_change ? h("span", { class: "row tight" },
        x.role === "member" ? btn("Make owner", null, () => set(x.user, "owner", "Not changed"), "tool link small") : null,
        x.role === "owner" && owners > 1 ? btn("Make member", null, () => set(x.user, "member", "Not changed"), "tool link small") : null,
        x.role === "owner" && owners <= 1 ? null : btn("Remove", null, async () => {
          if (await confirmDialog(`Remove ${x.user}?`, `${x.user} can no longer publish ${m.project}'s products, make its links or change its domain.`, "Remove", { danger: true })) {
            set(x.user, "", "Not removed");
          }
        }, "tool link small")) : null)));
    card.replaceChildren(head(m),
      h("p", { class: "muted-text small" }, `Members publish ${m.project}'s products, make its links and change its domain. Owners also add and remove members.`),
      m.earlier ? h("p", { class: "muted-text small" }, "From before members: the people who registered or published this project start as its owners the next time one of them changes it.") : null,
      m.members.length ? h("table", { class: "list" }, h("thead", {}, h("tr", {}, ["Person", "Role", ""].map((c) => h("th", {}, c)))), h("tbody", {}, rows))
        : h("p", { class: "muted-text" }, "No members yet: whoever saves the project next is its first owner."),
      h("p", { class: "muted-text small" }, "A project keeps at least one owner. Platform admins can change any project's members."));
    const ask = joinNotice(o, m.domain_request, rerender);
    if (ask) card.before(ask);
  }).catch((err) => {
    card.replaceChildren(head(null), h("p", { class: "muted-text small" }, `This project's members couldn't be read: ${err?.message || err}`));
  });
  return card;
}

// A waiting ask to join a domain: who decides it, and Withdraw for the person who asked.
function joinNotice(o, r, rerender) {
  if (!r) return null;
  const d = r.detail || {};
  const name = d.domain_name || o.domains.find((x) => x.id === d.domain)?.name || d.domain;
  return h("div", { class: "card pad notice mesh-join" },
    h("div", { class: "row" }, h("b", { class: "grow" }, `Waiting on ${name}'s owners`),
      r.mine ? btn("Withdraw", null, async () => {
        if (await metaCall("platform_withdraw", { id: r.id }, "Not withdrawn")) rerender();
      }, "tool outline small") : null),
    h("p", { class: "small" }, `${r.mine ? "You" : r.requested_by} asked ${relTime(r.requested_at)} for ${o.project.name} to join ${name}. `,
      d.owners ? `${name}'s owners (${d.owners}) decide it on ` : "It has no owners, so a platform admin decides it on ",
      h("a", { href: "#", onclick: (e) => { e.preventDefault(); goMeta("requests"); } }, "Data mesh > Requests"),
      `. Until then the project stays in its current domain.`));
}

function addMemberDialog(rerender) {
  const user = h("input", { type: "email", placeholder: "name@contoso.com", "aria-label": "Their sign-in" });
  const role = h("select", { "aria-label": "Role" }, h("option", { value: "member" }, "Member"), h("option", { value: "owner" }, "Owner"));
  showDialog("Add member", h("div", {},
    h("p", { class: "muted-text small" }, "Members publish the project's products, make its links and change its domain. Owners also add and remove members."),
    h("div", { class: "form-grid" }, h("label", { class: "field" }, "Sign-in (email)", user), h("label", { class: "field" }, "Role", role)),
    howItWorks("mesh.add-member", { changes: "Adds the person to catalog.project_member, or changes their role if they're already a member.", results: "Recorded on Platform > Govern > Audit." })), [
    btn("Cancel", null, closeDialog, "tool outline"),
    btn("Add", "add", async () => {
      if (await metaCall("mesh_set_member", { user: user.value.trim(), role: role.value }, "Not added")) { closeDialog(); toast(`Added ${user.value.trim()}`); rerender(); }
    }, "tool primary")]);
  user.focus();
}

// A domain's Fabric domain admins, read during Import from Fabric: names only, never used to grant anything.
function fabricOwners(d) {
  try { return (JSON.parse(d?.fabric_owners || "[]") || []).map((p) => p.name).filter(Boolean); } catch { return []; }
}

// The owners cell: the platform's owners, else Fabric's domain admins marked "From Fabric".
function domainOwnersCell(d) {
  const fabric = fabricOwners(d);
  const tag = h("span", { class: "badge", title: "This domain's admins in Fabric (Admin portal > Domains), read by Import from Fabric" }, "From Fabric");
  if (d.owners) {
    return h("span", {}, d.owners, fabric.length ? h("div", { class: "muted-text small" }, "In Fabric: ", fabric.join(", ")) : null);
  }
  return fabric.length ? h("span", {}, fabric.join(", "), " ", tag) : h("span", { class: "muted-text" }, "not set");
}

// One line for Home's map: the first owner, and how many more.
function domainOwnerLine(d) {
  const own = String(d.owners || "").split(",").map((x) => x.trim()).filter(Boolean);
  const people = own.length ? own : fabricOwners(d);
  if (!people.length) return null;
  const more = people.length > 1 ? ` +${people.length - 1}` : "";
  return h("div", { class: "muted-text small", title: people.join(", ") }, `Owner: ${people[0]}${more}${own.length ? "" : " (from Fabric)"}`);
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
  // Only a new domain is offered to Fabric, and a subdomain only when its parent is in Fabric too.
  const inFabric = d ? null : h("input", { type: "checkbox", checked: true });
  const fabricNote = h("span", { class: "muted-text small" });
  const save = btn(d ? "Save" : "Create in Fabric", "save", () => submit(), "tool primary");
  const sync = () => {
    if (!inFabric) return;
    const p = o.domains.find((x) => x.id === parent.value);
    const blocked = p && !p.fabric_domain;
    inFabric.disabled = !!blocked;
    if (blocked) inFabric.checked = false;
    fabricNote.textContent = blocked ? `${p.name} isn't in Fabric, so its subdomains can't be either. Create ${p.name} in Fabric first, or import it.`
      : "Needs a Fabric administrator. The first time, Windows asks you to allow changing your organization's Fabric settings.";
    save.lastChild.textContent = inFabric.checked ? "Create in Fabric" : "Save";
  };
  if (inFabric) { parent.addEventListener("change", sync); inFabric.addEventListener("change", sync); }
  const values = () => ({ id: d?.id, name: name.value.trim(), parent_id: parent.value, description: description.value.trim(), owners: owners.value.trim() });
  async function submit() {
    if (!name.value.trim()) { name.focus(); return; }
    if (!inFabric?.checked) {
      if (await metaCall("platform_save_domain", { domain: values() }, "Not saved")) { closeDialog(); done(); }
      return;
    }
    if (!state.user && !(await signIn())) return;
    const domain = values();
    try {
      await rpc("platform_create_domain", { domain, in_fabric: true });
    } catch (e) {
      const f = friendlyError(e);
      if (!f.cancelled) notInFabric(domain, f.text);
      return;
    }
    closeDialog(); toast(`Created ${domain.name} in Fabric`); done();
  }
  function notInFabric(domain, why) {
    showDialog(`${domain.name} wasn't created in Fabric`, h("div", {},
      h("p", { class: "card pad warn-box" }, why),
      h("p", {}, "You can keep it in the platform only. A Fabric admin can create a domain with the same name later and choose Import from Fabric: the two are matched by name.")), [
      btn("Cancel", null, closeDialog, "tool outline"),
      btn("Save in the platform only", "save", async () => {
        if (await metaCall("platform_save_domain", { domain }, "Not saved")) { closeDialog(); done(); }
      }, "tool primary")]);
  }
  showDialog(d ? `Edit ${d.name}` : "New domain", h("div", {},
    fromFabric ? h("p", { class: "muted-text small" }, "From Fabric. Its name and parent follow Fabric's domains; owners and description are the platform's.") : null,
    h("label", { class: "field" }, "Name", name), h("label", { class: "field" }, "Part of", parent),
    h("label", { class: "field" }, "Description", description), h("label", { class: "field" }, "Owners", owners),
    inFabric ? h("label", { class: "check" }, inFabric, h("span", {}, h("strong", {}, "Also create it in Fabric (Preview)"), " ", fabricNote)) : null), [
    btn("Cancel", null, closeDialog, "tool outline"), save]);
  sync();
}

// Fabric's domain list is a Fabric admin API: only a Fabric administrator can import it.
async function importFabricDomains(done) {
  if (!state.user && !(await signIn())) return;
  const out = await metaCall("platform_import_fabric_domains", {}, "Domains not imported");
  if (!out) return;
  const r = out.result;
  const parts = [r.added.length && `${r.added.length} added`, r.linked.length && `${r.linked.length} matched by name`,
    r.updated.length && `${r.updated.length} renamed, moved or with new admins`].filter(Boolean);
  toast(`Imported ${out.fabric} Fabric domains${parts.length ? `: ${parts.join(", ")}` : ", nothing changed"}`);
  if (r.skipped.length) toast(`Skipped (deeper than one subdomain): ${r.skipped.join(", ")}`);
  if (r.gone.length) toast(`No longer in Fabric, kept here: ${r.gone.join(", ")}`);
  if (out.owners_unread?.length) toast(`Domain admins couldn't be read for: ${out.owners_unread.join(", ")}. Their owners from Fabric stay as they were.`);
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
        : emptyState({ title: "No data products yet",
          text: "Make one from your Gold tables so other teams can link to them instead of copying.",
          action: btn("New data product", "add", () => productDialog(p, null, rerender), "tool primary") })),
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
  const steward = h("input", { value: x?.steward || "", placeholder: "Who answers for its meaning and quality" });
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
      h("label", { class: "field" }, "Owner", owner), h("label", { class: "field" }, "Steward", steward), h("label", { class: "field" }, "Open to", access),
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
        description: description.value.trim(), owner: owner.value.trim(), steward: steward.value.trim(), access: access.value, status: status.value,
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
        h("tbody", {}, rows.map((r) => r.row))) : emptyState({ title: "No links yet",
        text: "Find a product on the Data products tab and choose Link, or add one here." }),
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
  const section = h("section", { class: "card pad" }, h("h2", {}, "Platform"), loading());
  const s = await attempt(() => rpc("platform_status"));
  if (!s) { section.replaceChildren(h("h2", {}, "Platform"), h("p", { class: "muted-text" }, "The platform shows once the local engine is running.")); return section; }
  const policy = s.source === "policy";
  const row = (env) => {
    const c = s.environments[env];
    const fabric = c.kind === "fabric";
    return settingRow(`${ENV_LABEL[env]}: ${fabric ? c.workspace_name || c.workspace_id : "on this PC"}`,
      fabric ? "Catalog and run history for every team, in SQL_Platform and LH_Meta; keys too, until they move to the Keys workspace."
        : env === "prod" ? "Domains, products and catalog text are kept in a file on this PC until the Prod platform exists."
          : "Keys stay in each project's LH_Keys until this environment has a platform or they move to the Keys workspace.",
      policy ? null : h("div", { class: "row" },
        btn(fabric ? "Finish or repair" : "Create", fabric ? "sync" : "add", () => platformSetupDialog(env, s), fabric ? "tool outline" : "tool primary"),
        fabric ? btn("Forget", null, async () => {
          if (!(await confirmDialog(`Stop using ${c.workspace_name} for ${ENV_LABEL[env]}?`, "Nothing in Fabric is deleted; this PC just stops using it.", "Forget"))) return;
          if (await attempt(() => rpc("platform_forget", { env }), "Not changed")) render();
        }, "tool outline") : btn("Join existing", "workspace", () => platformJoin(env), "tool outline")),
      policy ? "policy" : null);
  };
  section.replaceChildren(h("h2", {}, "Platform ", previewTag()),
    h("p", { class: "muted-text small" }, "The central team's workspace in each environment: one landing zone for every source (LH_Landing), the catalog (SQL_Platform, which also holds each environment's keys until they move to the Keys workspace below), the functions every app and notebook calls (UDF_Platform), run history (LH_Meta) and their ids (VL_Platform). Create it once; every other PC joins it. ", learnMore("platform")),
    ...MESH_ENVS.map(row), keysRow(s, policy));
  return section;
}

// ---------- one keys database for Dev, Test and Prod (Preview) ----------

// Creating a workspace assigns it the capacity picked, so the setup dialogs ask first, as the landing
// workspace does (platform.js). Only a new workspace asks: Finish or repair keeps the one there.
// Cancel goes back to the setup dialog; reopen() shows it again with what was typed.
async function confirmNewWorkspace(name, capacity, what, reopen) {
  const cap = capacity.value ? capacity.selectedOptions[0]?.textContent : "";
  const ok = await confirmDialog(`Create ${name}?`, `This creates the workspace ${name} in Fabric ${cap ? `on ${cap}` : "with no capacity"} (or uses the one with that name), and ${what} in it.`, "Create");
  reopen();
  return ok;
}

function keysRow(s, policy) {
  const k = s.keys || {};
  const where = k.kind === "fabric" ? k.workspace_name || k.workspace_id : "on this PC";
  const title = `Keys: ${k.workspace_id ? where : "one keys database for every environment"}`;
  if (k.shared) {
    return settingRow(h("span", {}, title, " ", h("span", { class: "pill-s pass" }, "Dev, Test and Prod use it")),
      `One key per business key in every environment${k.kind === "fabric" ? ", in SQL_Keys and UDF_Keys" : ", in a file on this PC"}${k.moved_at ? `. Moved ${when(k.moved_at)}` : ""}.`,
      null, policy ? "policy" : null);
  }
  const fabric = k.kind === "fabric";
  return settingRow(h("span", {}, title, " ", previewTag()),
    "A dedicated Keys workspace (SQL_Keys and UDF_Keys) so customer ABC has the same key in Dev, Test and Prod. Move keys in shows what would move first; Prod's keys keep their numbers.",
    policy ? null : h("div", { class: "row" },
      btn("Move keys in", "databaseArrowRight", moveKeysIn, "tool primary"),
      btn(fabric ? "Finish or repair" : "Create", fabric ? "sync" : "add", () => keysSetupDialog(s), "tool outline"),
      btn("Join existing", "workspace", keysJoin, "tool outline")),
    policy ? "policy" : null);
}

async function keysJoin() {
  if (!state.user && !(await signIn())) return;
  const ws = await pickWorkspace(await loadWorkspaces(), { title: "Join the Keys workspace", okLabel: "Join",
    note: "Pick the Keys workspace your central team made. SQL_Keys and UDF_Keys are found by name." });
  if (!ws) return;
  const res = await attempt(() => rpc("platform_keys_join", { workspace_id: ws.id }), "Could not join");
  if (res) { toast(res.keys?.shared ? `Every environment now uses ${ws.displayName}` : `Joined ${ws.displayName}; keys haven't moved in yet`); render(); }
}

async function keysSetupDialog(s) {
  if (!state.user && !(await signIn())) return;
  const k = s.keys || {};
  const name = h("input", { value: k.workspace_name || k.default_name, disabled: k.workspace_id ? true : null });
  const capacity = h("select", {}, h("option", { value: "" }, "Loading capacities..."));
  const steps = h("div", {});
  const run = async () => {
    if (!k.workspace_id && !(await confirmNewWorkspace(name.value.trim(), capacity, "SQL_Keys and UDF_Keys", open))) return;
    steps.replaceChildren(h("p", { class: "muted-text" }, "Creating the Keys workspace. This takes a minute..."));
    const res = await attempt(() => rpc("platform_keys_setup", { name: name.value.trim(), capacity_id: capacity.value }), "Keys workspace not created");
    if (!res) { steps.replaceChildren(); return; }
    steps.replaceChildren(h("ul", { class: "steps-list" }, res.steps.map((st) => h("li", {},
      icon(st.status === "done" ? "ok" : "error", 14), h("b", {}, ` ${st.step}: `), st.detail))),
    h("p", { class: res.ready ? "muted-text" : "fail-text" }, res.ready ? "The Keys workspace is ready. Nothing has moved yet: choose Move keys in." : "Some steps didn't finish. Fix what they say, then choose Finish or repair."));
  };
  const body = h("div", {},
    h("p", { class: "text" }, "Creates (or finishes) a workspace with SQL_Keys, a SQL database for every environment's keys, and UDF_Keys, the functions that issue them. Nothing moves until you choose Move keys in."),
    h("label", { class: "field" }, "Workspace", name), h("label", { class: "field" }, "Capacity", capacity), steps);
  const actions = [btn("Close", null, () => { closeDialog(); render(); }, "tool outline"),
    btn(k.workspace_id ? "Finish setup" : "Create workspace", "add", run, "tool primary")];
  const open = () => showDialog(`${k.workspace_id ? "Finish" : "Create"} the Keys workspace`, body, actions);
  open();
  const caps = (await attempt(() => rpc("list_capacities"), "Could not list capacities")) || [];
  capacity.replaceChildren(...caps.map((x) => h("option", { value: x.id }, `${x.displayName}${x.sku ? ` (${x.sku})` : ""}`)),
    h("option", { value: "" }, "No capacity (SQL database and functions need one)"));
}

function keysMoveTable(tables) {
  return tables.length ? h("table", { class: "list" },
    h("thead", {}, h("tr", {}, ["From", "Key table", "Keys", "New numbers"].map((x) => h("th", {}, x)))),
    h("tbody", {}, tables.map((t) => h("tr", {}, h("td", {}, ENV_LABEL[t.env] || t.env), h("td", { class: "mono" }, `sk.${t.table}`),
      h("td", { class: "n" }, (t.keys ?? t.copied ?? 0).toLocaleString()),
      h("td", { class: "n" }, t.env === "prod" ? "0 (kept)" : (t.renumbered || 0).toLocaleString())))))
    : h("p", { class: "muted-text" }, "No key tables yet, so nothing is copied.");
}

async function moveKeysIn() {
  const plan = await attempt(() => rpc("platform_share_keys", {}), "Could not read the keys");
  if (!plan) return;
  if (plan.already) { toast("Every environment already uses the one keys database"); render(); return; }
  // Moving closes every environment's keys and renumbers Dev and Test keys, and once it switches it can't be
  // cancelled (central/service.py platform_cancel_share_keys), so its button is danger-styled and Cancel has focus.
  const overlay = showDialog("Move keys in", h("div", {},
    h("p", { class: "text" }, plan.message.replace(/ Confirm to continue\.$/, "")),
    keysMoveTable(plan.tables),
    plan.renumbered ? h("p", { class: "warn-text small" }, `${plan.renumbered.toLocaleString()} Dev and Test keys change number. Re-run Dev and Test afterwards.`) : null), [
    btn("Cancel", null, closeDialog, "tool outline"),
    btn("Move keys in", "databaseArrowRight", async () => {
      closeDialog();
      const res = await attempt(() => rpc("platform_share_keys", { approve: true }), "Keys not moved");
      if (!res) return;
      if (res.ok) {
        showDialog("Keys moved", h("div", {}, keysMoveTable(res.tables),
          h("p", { class: "warn-text small" }, res.renumbered
            ? "Every environment now uses the one keys database. Re-run Dev and Test: their tables still hold the old numbers for the keys that changed. Prod's tables don't change."
            : "Every environment now uses the one keys database. No key changed number.")),
        [btn("Close", null, () => { closeDialog(); render(); }, "tool primary")]);
        toast("Every environment now uses the one keys database", false, { log: true });
        return;
      }
      showDialog("Keys didn't switch", h("div", {}, h("p", { class: "text" }, res.message),
        res.changed_during_move ? gateList(res.changed_during_move.map((x) => `${x} changed during the move`)) : null,
        res.tables?.some((t) => !t.match) ? gateList(res.tables.filter((t) => !t.match).map((t) => `${ENV_LABEL[t.env] || t.env} ${t.table}: didn't check out`)) : null), [
        btn("Cancel the move", null, () => { closeDialog(); cancelKeysMove(); }, "tool outline"),
        btn("Move again", "sync", () => { closeDialog(); moveKeysIn(); }, "tool primary")]);
    }, "tool danger-solid")]);
  overlay.querySelector(".dialog-actions button")?.focus();
}

async function cancelKeysMove() {
  const plan = await attempt(() => rpc("platform_cancel_share_keys", {}), "Could not cancel");
  if (!plan) return;
  if (!(await confirmDialog("Cancel the move?", plan.message.replace(/ Confirm to continue\.$/, ""), "Cancel the move", { danger: true }))) return;
  const res = await attempt(() => rpc("platform_cancel_share_keys", { approve: true }), "Not cancelled");
  if (res) { toast("Move cancelled; every environment issues its own keys again", false, { log: true }); render(); }
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
    if (!c.workspace_id && !(await confirmNewWorkspace(name.value.trim(), capacity, "LH_Meta, LH_Landing, SQL_Platform, UDF_Platform and VL_Platform", open))) return;
    steps.replaceChildren(h("p", { class: "muted-text" }, "Creating the platform. This takes a minute..."));
    const res = await attempt(() => rpc("platform_setup", { env, name: name.value.trim(), capacity_id: capacity.value }), "Platform not created");
    if (!res) { steps.replaceChildren(); return; }
    steps.replaceChildren(h("ul", { class: "steps-list" }, res.steps.map((st) => h("li", {},
      icon(st.status === "done" ? "ok" : "error", 14), h("b", {}, ` ${st.step}: `), st.detail))),
    h("p", { class: res.ready ? "muted-text" : "fail-text" }, res.ready ? "The platform is ready." : "Some steps didn't finish. Fix what they say, then choose Finish or repair."));
    if (res.ready) toast(`The ${ENV_LABEL[env]} platform is ready`, false, { log: true });
  };
  const body = h("div", {},
    h("p", { class: "text" }, "Creates (or finishes) the Platform workspace with LH_Meta, SQL_Platform, UDF_Platform and VL_Platform. Steps that are already done are kept."),
    h("label", { class: "field" }, "Workspace", name), h("label", { class: "field" }, "Capacity", capacity), steps);
  const actions = [btn("Close", null, () => { closeDialog(); render(); }, "tool outline"),
    btn(c.workspace_id ? "Finish setup" : "Create platform", "add", run, "tool primary")];
  const open = () => showDialog(`${c.workspace_id ? "Finish" : "Create"} the ${ENV_LABEL[env]} platform`, body, actions);
  open();
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
        h("div", { class: "muted-text small" }, `${k.moved_at ? `Moved ${when(k.moved_at)}. ` : ""}LH_Keys is kept as it was. Every run asks SQL_Platform for keys.`)),
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
