// Data mesh > Home: the mesh as each of its people sees it (Chris's "data mesh by role" review).
// My team is a team lead's products, what each needs, the requests waiting on them and the data
// they depend on. On call is incidents that reach data products and who to wake. Organization is
// the CDO's scorecard per domain. Only reads (mesh_home); each card opens the page that acts.

const MESH_VIEWS = [["team", "My team"], ["oncall", "On call"], ["org", "Organization"]];
const meshHome = { view: null, data: null };

function meshHomeSaved() {
  try { return localStorage.getItem("fd.meshHome") || null; } catch { return null; }
}

function meshHomeSave(view) {
  try { localStorage.setItem("fd.meshHome", view); } catch { /* remembered for this session only */ }
}

const mhPlural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function mhCard(title, action, ...children) {
  return h("section", { class: "card pad mh-card" },
    h("div", { class: "row tight" }, h("h2", { class: "flush grow" }, title), action), ...children);
}

function mhRequestText(r) {
  const what = REQUEST_KIND[r.kind] || r.kind;
  return [h("b", {}, `${what}: `), r.target || "", r.project ? h("span", { class: "muted-text" }, ` for ${r.project}`) : null];
}

function mhOpenRequests(show) {
  meshRequests.show = show;
  goMeta("requests");
}

function mhTeam(t) {
  const product = (p) => h("tr", { class: "click", onclick: () => openImpactProduct(p.id) },
    h("td", {}, h("b", {}, p.name), p.version ? h("span", { class: "muted-text" }, ` v${p.version}`) : null),
    h("td", {}, p.readers.length ? mhPlural(p.readers.length, "project") : h("span", { class: "muted-text" }, "None yet")),
    h("td", {}, String(p.owners.length)),
    h("td", {}, p.needs.length ? p.needs.map((n) => h("span", { class: `pill-s ${n === "Incident upstream" ? "fail" : "wrn"}` }, n))
      : h("span", { class: "pill-s pass" }, "Nothing")));
  const waiting = mhCard("Waiting on you", btn("Open Requests", null, () => mhOpenRequests("waiting"), "tool link small"),
    t.waiting.length ? h("ul", { class: "mh-list" }, t.waiting.map((r) => h("li", {},
      h("span", { class: "grow" }, mhRequestText(r)), h("span", { class: "muted-text small" }, relTime(r.requested_at)),
      btn("Decide", null, () => mhOpenRequests("waiting"), "tool outline small"))))
      : h("p", { class: "muted-text" }, "Nothing is waiting on you."),
    t.reviews_due ? h("p", {}, `${mhPlural(t.reviews_due, "grant")} to your products ${t.reviews_due === 1 ? "is" : "are"} due an access review. `,
      btn("Review", null, () => openProducts(null, { reviewDue: true }), "tool link small")) : null);
  const asks = mhCard("Your asks", previewShown() ? btn("Ask for data", null, () => newRequestDialog(null, "data"), "tool outline small") : null,
    t.asks.length ? h("ul", { class: "mh-list" }, t.asks.map((r) => h("li", {},
      h("span", { class: "grow" }, mhRequestText(r)),
      h("span", { class: `small ${r.status === "approved" ? "" : "muted-text"}` }, `${REQUEST_STATUS[r.status] || r.status} · ${relTime(r.decided_at || r.requested_at)}`))))
      : h("p", { class: "muted-text" }, "You haven't asked for anything yet."),
    btn("Everything you sent", null, () => mhOpenRequests("mine"), "tool link small"));
  const products = mhCard("Your products", btn("All products", null, () => openProducts(), "tool link small"),
    t.products.length ? h("table", { class: "list" },
      h("thead", {}, h("tr", {}, ["Product", "Readers", "Owners", "Needs"].map((c) => h("th", {}, c)))),
      h("tbody", {}, t.products.map(product)))
      : h("p", { class: "muted-text" }, t.project ? `${t.project.name} hasn't published a data product yet. Add one on Project > Products and links.`
        : "You don't own a data product yet. Open your project to see the ones it publishes."));
  const depends = mhCard("Data you depend on", null,
    t.depends.length ? h("ul", { class: "mh-list" }, t.depends.map((d) => h("li", {},
      h("button", { class: "tool link small", onclick: () => openImpactProduct(d.id) }, d.name),
      h("span", { class: "muted-text small" }, d.project),
      h("span", { class: `small grow ${d.incident ? "inc-unowned" : d.state.startsWith("Deprecated") ? "" : "muted-text"}` },
        d.incident ? "Incident upstream" : d.state))))
      : h("p", { class: "muted-text" }, "Nothing yet."),
    h("p", { class: "muted-text small" }, t.follows_known ? "Products your project links to, and products you follow." : "Products your project links to. Following isn't on this platform yet."));
  return h("div", { class: "mh-grid" }, waiting, asks, products, depends);
}

function mhOncall(o, out) {
  const incidents = mhCard("Incidents that reach data products", btn("Open Incidents", null, backToIncidents, "tool link small"),
    !out.incidents_read ? h("p", { class: "muted-text" }, "Open a metadata project to see its incidents here; Monitor > Incidents lists them all.")
      : o.incidents.length ? h("table", { class: "list" },
        h("thead", {}, h("tr", {}, ["Incident", "Where", "Products", "Readers", "Owner"].map((c) => h("th", {}, c)))),
        h("tbody", {}, o.incidents.map((i) => h("tr", { class: "click", onclick: () => openIncident(i) },
          h("td", {}, i.priority ? h("span", { class: "pill-s fail" }, i.priority) : null, " ", h("b", {}, i.title)),
          h("td", {}, envName(i.env)),
          h("td", {}, i.products.map((p) => p.name).join(", ")),
          h("td", {}, mhPlural(i.readers.length, "project")),
          h("td", {}, i.owner || h("span", { class: "inc-unowned" }, "No owner"))))))
        : h("p", { class: "muted-text" }, "No active incident reaches a data product."));
  const wake = mhCard("Who to wake", null,
    o.wake.length ? h("ul", { class: "mh-list" }, o.wake.map((w) => h("li", {}, h("b", { class: "grow" }, w.product),
      h("span", {}, w.owners.length ? w.owners.join(", ") : h("span", { class: "inc-unowned" }, "No owner")))))
      : h("p", { class: "muted-text" }, "No one: nothing upstream of a product is broken."),
    h("p", { class: "muted-text small" }, "The owners of each product an open incident reaches. Tell them from the incident's Who's affected card."));
  const unknown = (what) => h("p", { class: "muted-text" }, `${what} couldn't be read.`);
  const landing = mhCard("Late landing sources in Prod", btn("Landing zone", null, () => goPlatform("landing"), "tool link small"),
    o.late === null ? unknown("The landing zone") : o.late.length ? h("ul", { class: "mh-list" }, o.late.map((s) => h("li", {},
      h("b", { class: "grow" }, s.title || s.name), h("span", { class: "muted-text small" }, s.newest ? `last file ${relTime(s.newest)}` : "No files yet"))))
      : h("p", { class: "muted-text" }, "Every source arrived on time."),
    o.landing_problem ? h("p", { class: "muted-text small" }, o.landing_problem) : null);
  const shortcuts = mhCard("Broken shortcuts", btn("Shortcuts", null, () => { catalogPage.tab = "shortcuts"; scPage.view = "health"; go("onelake"); }, "tool link small"),
    o.broken === null ? unknown("Shortcuts") : o.broken.length ? h("ul", { class: "mh-list" }, o.broken.map((s) => h("li", {},
      h("b", { class: "grow" }, s.name), h("span", { class: "muted-text small" }, `${s.workspace_name} / ${s.lakehouse_name}`))))
      : h("p", { class: "muted-text" }, "No shortcut was broken at its last check."));
  const owners = o.owner_gaps ? mhCard("Owner gaps", btn("Owners", null, () => goPlatform("overview"), "tool link small"),
    h("p", {}, o.owner_gaps.total ? `${mhPlural(o.owner_gaps.total, "thing")} without cover, ${o.owner_gaps.no_owner} with no owner at all.`
      : "Every domain, product and source has two owners.")) : null;
  return h("div", { class: "mh-grid" }, incidents, wake, landing, shortcuts, owners);
}

function mhOrg(g) {
  const t = g.totals;
  const tile = (value, label) => h("div", { class: "card pad mh-tile" }, h("b", {}, value === null ? "—" : String(value)), h("span", { class: "muted-text small" }, label));
  const domains = mhCard("Domains", btn("Open Overview", null, () => goMeta("overview"), "tool link small"),
    g.domains.length ? h("table", { class: "list" },
      h("thead", {}, h("tr", {}, ["Domain", "Products", "Reader projects", "Two owners", "Needs"].map((c) => h("th", {}, c)))),
      h("tbody", {}, g.domains.map((d) => h("tr", {},
        h("td", {}, h("b", {}, d.name)), h("td", {}, String(d.products)), h("td", {}, String(d.readers)),
        h("td", {}, d.products ? `${d.two_owners} of ${d.products}` : "—"),
        h("td", {}, d.needs.length ? d.needs.join(" · ") : h("span", { class: "muted-text" }, "Nothing"))))))
      : emptyState({ title: "No domains yet", text: "A platform admin imports the organization's Fabric domains on Data mesh > Domains.",
        action: btn("Open Domains", "building", () => goMeta("domains"), "tool outline small") }));
  const stale = mhCard(`Waiting over ${g.stale_days} days`, btn("Open Requests", null, () => mhOpenRequests("all"), "tool link small"),
    g.stale.length ? h("ul", { class: "mh-list" }, g.stale.map((r) => h("li", {},
      h("span", { class: "grow" }, mhRequestText(r)), h("span", { class: "muted-text small" }, `${r.days} days`))))
      : h("p", { class: "muted-text" }, "No request has waited that long."));
  return h("div", {},
    h("div", { class: "mh-tiles" },
      tile(t.domains, "domains"), tile(t.products, "data products"),
      tile(t.products ? `${t.two_owners} of ${t.products}` : 0, "with two owners"),
      tile(t.reviews_due, "access reviews due"), tile(t.waiting, "requests waiting")),
    h("div", { class: "mh-grid" }, domains, stale));
}

async function mhLoad(view) {
  try { return await rpc("mesh_home", { view }); } catch (e) { return { view, problem: String(e?.message || e) }; }
}

async function renderMeshHomeTab(body, p) {
  body.replaceChildren(h("p", { class: "muted-text" }, "Reading the platform's catalog..."));
  const here = () => state.view === "metadata" && state.meta.tab === "home" && body.isConnected;
  let view = meshHome.view || meshHomeSaved();
  let out = await mhLoad(view || "team");
  // No choice yet: My team when you own a product or have a project, else On call for an admin.
  if (!view && !out.problem) {
    view = out.team?.products.length || out.project ? "team" : out.me?.admin ? "oncall" : "org";
    if (view !== "team") out = await mhLoad(view);
  }
  if (!here()) return;
  meshHome.view = view || "team";
  const pick = (v) => { meshHome.view = v; meshHomeSave(v); renderMeshHomeTab(body, p); };
  const seg = h("div", { class: "seg", role: "group", "aria-label": "View" }, MESH_VIEWS.map(([id, label]) =>
    h("button", { class: meshHome.view === id ? "on" : null, "aria-pressed": String(meshHome.view === id), onclick: () => pick(id) }, label)));
  const who = out.project ? `${out.project.name}${out.project.domain ? ` · domain ${out.project.domain}` : ""}${out.project.team ? ` · ${out.project.team}` : ""}`
    : "No metadata project open on this PC.";
  const main = out.problem ? h("p", { class: "muted-text" }, "The platform's catalog couldn't be read: ", out.problem)
    : { team: () => mhTeam(out.team), oncall: () => mhOncall(out.oncall, out), org: () => mhOrg(out.org) }[out.view]();
  body.replaceChildren(withHowItWorks(h("div", { class: "mh" },
    previewMarked("mesh", h("div", { class: "row tight" }, h("p", { class: "muted-text grow" }, who), seg)),
    (out.problems || []).length ? h("div", { class: "problems" }, h("b", {}, "Some of this couldn't be read"), gateList(out.problems)) : null,
    main),
  howItWorks("mesh.home", {
    reads: "Data products, requests, follows, access reviews and domains from the platform's catalog. On call also reads the landing zone, the shortcuts the catalog has seen and owner coverage. Incidents come from the open project's incident lists in each environment.",
    changes: "Nothing. Each card opens the page that acts: Data mesh > Requests to decide, a product to review access, Monitor to work an incident.",
    results: "My team, On call and Organization show the same catalog to everyone; your view choice is remembered on this PC.",
    anchor: "data-mesh",
  })));
}
