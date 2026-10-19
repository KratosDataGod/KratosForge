// Data mesh > Overview: start at the organization's domains, open one to see each project's flow
// from its layers to the products other domains read, then open a layer of the project on this
// PC to see where each table comes from and what reads it (testing feedback item 21).
// Read only: domains are edited on Platform, a project's tables and products on its own tabs.

const meshFlow = { domain: null, sub: null, layer: null, table: null };

async function renderMeshOverviewTab(body, p) {
  body.replaceChildren(h("p", { class: "muted-text" }, "Reading the platform's catalog..."));
  let m;
  try { m = await rpc("mesh_map", {}); } catch (e) { m = { problem: String(e?.message || e), domains: [], projects: [], products: [], links: [], me: "" }; }
  if (state.view !== "metadata" || state.meta.tab !== "overview" || !body.isConnected) return;
  const draw = () => body.replaceChildren(withHowItWorks(meshFlow.layer ? mfLayer(m, p, draw) : meshFlow.domain ? mfDomain(m, p, draw) : mfDomains(m, draw),
    howItWorks("mesh.overview", {
      reads: "Domains, projects, data products and who links to them from the platform's catalog, and the tables of the project open on this PC.",
      changes: "Nothing. Domains are edited on Platform; a project's tables and products on its Design and Domains tabs.",
      results: "Everyone sees the same domains and links. A project's tables show here only for the project open on this PC.",
      anchor: "data-mesh",
    })));
  draw();
}

// "" stands for projects that haven't joined a domain yet.
function mfDomainOf(m, project) {
  return m.projects.find((x) => x.name === project)?.domain_id || "";
}

function mfDomainName(m, id) {
  return id ? m.domains.find((d) => d.id === id)?.name || id : "No domain";
}

function mfCrumbs(m, draw) {
  const step = (label, onclick) => onclick ? h("button", { class: "tool link small", onclick }, label) : h("b", {}, label);
  const parts = [step("Domains", meshFlow.domain !== null ? () => { Object.assign(meshFlow, { domain: null, sub: null, layer: null, table: null }); draw(); } : null)];
  if (meshFlow.domain !== null) parts.push(step(mfDomainName(m, meshFlow.domain), meshFlow.layer ? () => { Object.assign(meshFlow, { layer: null, table: null }); draw(); } : null));
  if (meshFlow.layer) parts.push(step(`${m.me} / ${meshFlow.layer}`, null));
  return previewMarked("mesh", h("nav", { class: "row tight mf-crumbs", "aria-label": "Where you are" }, parts.flatMap((x, i) => (i ? [h("span", { class: "muted-text", "aria-hidden": "true" }, "/"), x] : [x]))));
}

function mfDomains(m, draw) {
  if (m.problem && !m.domains.length) return h("p", { class: "muted-text" }, "The platform's catalog couldn't be read: ", m.problem);
  if (!m.domains.length && !m.projects.length) {
    return h("div", { class: "empty" }, h("span", { class: "title" }, "No domains yet"),
      h("span", { class: "text" }, "Start from the domains your organization already has in Fabric: a platform admin who is a Fabric administrator imports them on Platform > Domains."),
      btn("Open Platform > Domains", "building", () => goPlatform("domains"), "tool outline small"));
  }
  const open = (id) => { Object.assign(meshFlow, { domain: id, sub: null, layer: null, table: null }); draw(); };
  const mine = mfDomainOf(m, m.me);
  const card = (id, name, subs) => {
    const projects = m.projects.filter((x) => (x.domain_id || "") === id);
    const products = m.products.filter((x) => projects.some((pr) => pr.name === x.project));
    return h("button", { class: `card pad mf-domain${m.me && id === mine ? " me" : ""}`, onclick: () => open(id) },
      h("div", { class: "row tight" }, h("b", { class: "grow" }, name), m.me && id === mine ? h("span", { class: "badge" }, "This project's domain") : null),
      h("span", { class: "muted-text small" }, subs.length ? `Subdomains: ${subs.map((s) => s.name).join(", ")}` : "No subdomains"),
      h("span", { class: "small" }, `${projects.length} project${projects.length === 1 ? "" : "s"} · ${products.length} product${products.length === 1 ? "" : "s"}`));
  };
  const top = m.domains.filter((d) => !d.parent_id);
  const cards = top.map((d) => card(d.id, d.name, m.domains.filter((s) => s.parent_id === d.id)));
  if (m.projects.some((x) => !x.domain_id)) cards.push(card("", "No domain", []));
  // One row per pair of domains: the products one domain's projects read from another's.
  const feeds = new Map();
  for (const l of m.links) {
    const from = mfDomainOf(m, l.to), to = mfDomainOf(m, l.from);
    const key = `${from}\n${to}`;
    if (!feeds.has(key)) feeds.set(key, { from, to, products: new Set() });
    feeds.get(key).products.add(m.products.find((x) => x.id === l.product)?.name || l.product);
  }
  return h("div", { class: "mf" },
    mfCrumbs(m, draw),
    h("p", { class: "muted-text" }, "Your organization's domains. Open one to see how its projects' data flows to the products other domains use."),
    h("div", { class: "mf-domains" }, cards),
    h("div", { class: "card pad" }, h("h2", {}, "How domains feed each other"),
      feeds.size ? h("div", { class: "mf-feeds" }, [...feeds.values()].map((f) => h("div", { class: "mf-feed" },
        h("b", {}, mfDomainName(m, f.from)), h("span", { class: "muted-text", "aria-label": "feeds" }, "→"), h("b", {}, mfDomainName(m, f.to)),
        h("span", { class: "muted-text grow" }, [...f.products].join(", ")))))
        : h("p", { class: "muted-text" }, "No project reads another's products yet.")));
}

function mfDomain(m, p, draw) {
  const id = meshFlow.domain;
  const subs = m.domains.filter((s) => id && s.parent_id === id);
  const projects = m.projects.filter((x) => (x.domain_id || "") === id && (!meshFlow.sub || x.subdomain_id === meshFlow.sub));
  const chip = (sid, label) => h("button", { class: meshFlow.sub === sid ? "on" : null, onclick: () => { meshFlow.sub = sid; draw(); } }, label);
  const readers = (project) => m.links.filter((l) => l.to === project);
  const reads = (project) => m.links.filter((l) => l.from === project);
  const productName = (pid) => m.products.find((x) => x.id === pid)?.name || pid;
  const tables = (project) => {
    if (project !== m.me || !p?.layers) return h("span", { class: "muted-text small" }, "Open this project's folder on this PC to see its tables");
    return h("div", { class: "flow mf-layers" }, p.layers.map((l, i) => [i ? h("span", { class: "arrow" }, "→") : null,
      h("button", { class: "layer-chip big", style: `background:${layerColor(l.name)}`, title: `Open ${l.name}'s tables`,
        onclick: () => { Object.assign(meshFlow, { layer: l.name, table: null }); draw(); } }, l.name, h("span", { class: "count" }, l.entities))]));
  };
  const list = (items) => (items.length ? items.join("; ") : h("span", { class: "muted-text" }, "None"));
  return h("div", { class: "mf" },
    mfCrumbs(m, draw),
    h("div", { class: "row" }, h("h2", { class: "grow" }, mfDomainName(m, id)),
      btn("Manage in Platform", "building", () => goPlatform("domains"), "tool outline small"),
      btn("Glossary", null, () => goPlatform("glossary"), "tool link small")),
    subs.length ? h("div", { class: "seg small", role: "group", "aria-label": "Subdomain" }, chip(null, "All"), subs.map((s) => chip(s.id, s.name))) : null,
    projects.length ? h("table", { class: "list mf-projects" },
      h("thead", {}, h("tr", {}, ["Project", "Tables, layer by layer", "Products", "Read by", "Reads from"].map((c) => h("th", {}, c)))),
      h("tbody", {}, projects.map((x) => h("tr", {},
        h("td", {}, h("b", {}, x.name), x.name === m.me ? h("span", { class: "badge" }, "This project") : null,
          x.team ? h("div", { class: "muted-text small" }, x.team) : null),
        h("td", {}, tables(x.name)),
        h("td", {}, list(m.products.filter((pr) => pr.project === x.name).map((pr) => pr.name))),
        h("td", {}, list(readers(x.name).map((l) => `${l.from} (${productName(l.product)})`))),
        h("td", {}, list(reads(x.name).map((l) => `${productName(l.product)} from ${l.to}`)))))))
      : h("p", { class: "muted-text" }, "No projects have joined this domain yet. A project joins one on its Domains tab."));
}

function mfLayer(m, p, draw) {
  const all = p?.entities || [];
  const entities = all.filter((e) => e.layer === meshFlow.layer);
  const names = new Set(all.map((e) => e.name));
  const pick = (name) => { meshFlow.table = name; meshFlow.layer = all.find((x) => x.name === name)?.layer || meshFlow.layer; draw(); };
  const e = all.find((x) => x.name === meshFlow.table);
  const ref = (name) => (names.has(name) ? h("button", { class: "tool link small mono", onclick: () => pick(name) }, name) : h("span", { class: "mono small" }, name));
  const column = (items, none) => (items.length ? h("div", { class: "mf-col" }, items) : h("span", { class: "muted-text" }, none));
  let detail = h("p", { class: "muted-text" }, "Pick a table to see where it comes from and what reads it.");
  if (e) {
    const up = (e.reads || []).filter((n) => names.has(n));
    const down = all.filter((x) => (x.reads || []).includes(e.name));
    const products = (p?.products || []).filter((pr) => (pr.ports || []).some((port) => port.entity === e.name));
    detail = h("div", { class: "card pad mf-table" },
      h("div", { class: "row" }, h("h2", { class: "grow mono" }, e.name), layerChip(e.layer),
        btn("Open in Design", "table", () => openEntity(e.name), "tool outline small")),
      h("div", { class: "mf-lineage" },
        h("div", {}, h("b", {}, "Comes from"), column(up.length ? up.map(ref) : e.source ? [h("span", { class: "mono small" }, e.source)] : [], "Not set")),
        h("span", { class: "arrow", "aria-hidden": "true" }, "→"),
        h("div", {}, h("b", {}, "Read by"), column([...down.map((x) => ref(x.name)),
          ...products.map((pr) => h("span", { class: "small" }, icon("cube", 14), ` Product ${pr.title || pr.name}`))], "Nothing in this project reads it"))),
      h("p", { class: "muted-text small" }, `${e.checks} check${e.checks === 1 ? "" : "s"}${e.owner ? ` · owner ${e.owner}` : ""}. Runs, tests and promotion are on the project's Runs, Quality and Deploy tabs.`));
  }
  return h("div", { class: "mf" },
    mfCrumbs(m, draw),
    h("div", { class: "mf-layer" },
      h("div", { class: "card pad" }, h("h2", {}, `${meshFlow.layer} tables`),
        entities.length ? h("div", { class: "mf-col" }, entities.map((x) => h("button", { class: `tool ${x.name === meshFlow.table ? "primary" : "outline"} small mono`, onclick: () => pick(x.name) }, x.name)))
          : h("p", { class: "muted-text" }, "No tables in this layer yet.")),
      detail));
}
