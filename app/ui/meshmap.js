// Home's mesh map: every domain (with its subdomains), the projects in each, the data products
// they publish, and arrows from a product to the projects that link to it. Read from the
// platform's catalog (mesh_map), so it shows the whole organization, not only the open project.

function meshMapCard() {
  const body = loading("Loading the mesh...");
  const head = preview("mesh", h("h2", {}, "Data mesh"));
  if (!head) return null;
  const card = foldable("home.mesh", head, body);
  loadMeshMap(body);
  return card;
}

async function loadMeshMap(body) {
  let m;
  try { m = await rpc("mesh_map", {}); } catch (e) { m = { problem: String(e?.message || e), domains: [] }; }
  if (state.view !== "home" || !body.isConnected) return;
  body.replaceWith(drawMeshMap(m));
}

function drawMeshMap(m) {
  if (m.problem && !m.domains.length) {
    return h("p", { class: "muted-text" }, "The platform's catalog couldn't be read: ", m.problem);
  }
  if (!m.domains.length && !m.projects.length) {
    return emptyState({ title: "No domains yet",
      text: "Start from the domains your organization already has in Fabric: a platform admin who is a Fabric administrator imports them on Platform > Domains.",
      action: btn("Open Platform > Domains", "building", () => goPlatform("domains"), "tool outline small") });
  }
  const products = (project) => m.products.filter((p) => p.project === project);
  const readers = (pid) => m.links.filter((l) => l.product === pid).length;
  const node = (p) => h("div", { class: `mesh-node${p.name === m.me ? " me" : ""}`, "data-project": p.name,
    title: p.team ? `${p.name} · ${p.team}` : p.name,
    onclick: p.name === m.me ? () => goMeta("domains") : null },
    h("div", { class: "row tight" }, icon("metadata", 16), h("b", {}, p.name), p.name === m.me ? h("span", { class: "badge" }, "This project") : null),
    p.team ? h("div", { class: "muted-text small" }, p.team) : null,
    products(p.name).map((x) => h("div", { class: `mesh-product ${x.access}`, "data-product": x.id,
      title: `${x.id} · open to ${ACCESS_LABEL[x.access] || x.access}${x.status === "deprecated" ? " · deprecated" : ""}` },
      icon("cube", 14), h("span", { class: "grow" }, x.name),
      readers(x.id) ? h("span", { class: "muted-text small" }, `${readers(x.id)} reader${readers(x.id) === 1 ? "" : "s"}`) : null)));
  const inDomain = (id) => m.projects.filter((p) => (p.subdomain_id || p.domain_id) === id);
  const box = (d, sub) => h("div", { class: sub ? "mesh-sub" : "mesh-domain" },
    h("div", { class: "mesh-domain-head" }, h("b", {}, d.name),
      d.fabric_domain ? h("span", { class: "badge", title: "Follows this domain in Fabric" }, "Fabric") : null),
    h("div", { class: "mesh-nodes" }, inDomain(d.id).map(node)),
    sub ? null : m.domains.filter((s) => s.parent_id === d.id).map((s) => box(s, true)),
    !inDomain(d.id).length && !m.domains.some((s) => s.parent_id === d.id)
      ? h("div", { class: "muted-text small" }, "No projects yet") : null);
  const loose = m.projects.filter((p) => !p.domain_id);
  const map = h("div", { class: "mesh-map" },
    h("div", { class: "mesh-domains" },
      m.domains.filter((d) => !d.parent_id).map((d) => box(d, false)),
      loose.length ? h("div", { class: "mesh-domain loose" }, h("div", { class: "mesh-domain-head" }, h("b", {}, "No domain")),
        h("div", { class: "mesh-nodes" }, loose.map(node))) : null));
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "mesh-links");
  svg.setAttribute("aria-hidden", "true");
  map.append(svg);
  const drawLinks = () => {
    if (!map.isConnected) return;
    const base = map.getBoundingClientRect();
    svg.setAttribute("width", base.width);
    svg.setAttribute("height", base.height);
    const paths = m.links.map((l) => {
      const from = map.querySelector(`[data-product="${CSS.escape(l.product)}"]`);
      const to = map.querySelector(`[data-project="${CSS.escape(l.from)}"]`);
      if (!from || !to) return "";
      const a = from.getBoundingClientRect(), b = to.getBoundingClientRect();
      // Leave the product on the side facing the reader; a reader in the same column gets a loop on the right.
      const left = b.right <= a.left, right = b.left >= a.right;
      const x1 = (left ? a.left : a.right) - base.left, y1 = a.top + a.height / 2 - base.top;
      const x2 = (right ? b.left : b.right) - base.left, y2 = b.top + 18 - base.top;
      const bend = left || right ? Math.max(40, Math.abs(x2 - x1) / 2) : 36;
      const c1 = left ? x1 - bend : x1 + bend, c2 = right ? x2 - bend : x2 + bend;
      return `<path d="M${x1},${y1} C${c1},${y1} ${c2},${y2} ${x2},${y2}" marker-end="url(#mesh-arrow)"><title>${esc(l.from)} reads ${esc(l.product)}</title></path>`;
    });
    svg.innerHTML = `<defs><marker id="mesh-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z"/></marker></defs>${paths.join("")}`;
  };
  new ResizeObserver(drawLinks).observe(map);
  requestAnimationFrame(drawLinks);
  const top = m.domains.filter((d) => !d.parent_id).length;
  const subs = m.domains.length - top;
  const fromFabric = m.domains.some((d) => d.fabric_domain);
  return h("div", {},
    h("p", { class: "muted-text small" },
      `${top} domain${top === 1 ? "" : "s"}${subs ? ` and ${subs} subdomain${subs === 1 ? "" : "s"}` : ""}${fromFabric ? " from Fabric" : ""}, `,
      `${m.projects.length} project${m.projects.length === 1 ? "" : "s"}, ${m.products.length} data product${m.products.length === 1 ? "" : "s"}, `,
      `${m.links.length} link${m.links.length === 1 ? "" : "s"}. Arrows go from a product to the projects that read it.`),
    map,
    h("div", { class: "row tight mesh-legend" },
      h("span", { class: "mesh-product public" }, "Open to everyone"), h("span", { class: "mesh-product domain" }, "Its domain"),
      h("span", { class: "mesh-product private" }, "By request"),
      h("span", { class: "spacer" }),
      btn("Data products", "cube", () => { catalogPage.tab = "products"; go("onelake"); }, "tool link small"),
      btn("Domains", "building", () => goPlatform("domains"), "tool link small")));
}
