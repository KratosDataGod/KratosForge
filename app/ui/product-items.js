// Product groups (Preview): a data product's tables, semantic models and reports, on Data mesh > Products.
// Tables are the product's ports (its project's definition). Its owner adds semantic models and reports
// from Fabric: the platform keeps only each item's id, type, workspace id and display name
// (catalog.product_item), and nothing in Fabric changes (central/trust_service.py). Each model and report
// shows the endorsement the last tenant scan read; a product's badge is its semantic model's.

const PRODUCT_GROUPS = [
  { type: "SemanticModel", title: "Semantic models", one: "model", many: "models", add: "Add a model", dialog: "Add a semantic model", word: "semantic model" },
  { type: "Report", title: "Reports", one: "report", many: "reports", add: "Add a report", dialog: "Add a report", word: "report" },
];
const PRODUCT_OFFER_FILTERS = [["all", "All"], ["tables", "Has tables"], ["models", "Has models"], ["reports", "Has reports"]];

// The product's tables: its ports in the highest environment it's published to (Prod, else Test, else Dev).
function productTables(p) {
  const ports = p.ports || [];
  const env = ["prod", "test", "dev"].find((e) => ports.some((x) => x.env === e));
  const seen = new Map();
  for (const x of ports.filter((y) => y.env === env)) {
    const name = x.table_name || x.entity || x.port;
    if (name && !seen.has(name)) seen.set(name, { name, lakehouse: x.lakehouse_name || "", env });
  }
  return [...seen.values()].sort((a, b) => a.name.localeCompare(b.name));
}

const productModels = (p) => (p.items || []).filter((i) => i.item_type === "SemanticModel");
const productReports = (p) => (p.items || []).filter((i) => i.item_type === "Report");

// "3 tables, 1 model, 2 reports", leaving out what it has none of.
function productOfferSummary(p) {
  const n = (count, one, many) => (count ? `${count} ${count === 1 ? one : many}` : null);
  return [n(productTables(p).length, "table", "tables"), n(productModels(p).length, "model", "models"),
    n(productReports(p).length, "report", "reports")].filter(Boolean).join(", ");
}

function productOfferMatches(p, filter) {
  if (filter === "tables") return productTables(p).length > 0;
  if (filter === "models") return productModels(p).length > 0;
  if (filter === "reports") return productReports(p).length > 0;
  return true;
}

// A product's badge is its semantic model's endorsement: unknown with no model or no scan, varies when its models differ.
function productModelBadge(p) {
  const seen = [...new Set(productModels(p).map((m) => m.endorsement || ""))];
  if (seen.length > 1) return h("span", { class: "pill-s neu endorse-badge varies", title: "Its semantic models' endorsements differ" }, "Endorsement varies");
  return endorsementBadge(seen[0] || null);
}

// On a product's page: what it offers, in three groups. onItems hears its models and reports when they load.
function productOfferCard(p, onItems = () => {}) {
  const pp = productsPage;
  const card = h("div", { class: "card pad product-offer" }, h("b", {}, "What it offers"), loading());
  const view = { items: null, canEdit: false, problem: "" };
  const own = pp.mine && p.project === pp.mine;

  const head = (title, count, action) => h("div", { class: "row tight product-offer-head" },
    h("b", { class: "grow" }, title, " ", h("span", { class: "muted-text product-offer-count" }, String(count))), action);
  const line = (main, where, badge, extra) => h("div", { class: "row tight product-offer-item" },
    h("span", { class: "grow" }, main, " ", h("span", { class: "muted-text small" }, where)), badge, extra);

  const tablesGroup = () => {
    const tables = productTables(p);
    return h("div", { class: "product-offer-group", "data-group": "tables" },
      head("Tables", tables.length, own ? btn("Add a table", null, () => goMeta("products"), "tool outline small") : null),
      ...tables.map((t) => line(h("b", {}, t.name), [t.lakehouse, ENV_LABEL[t.env] || t.env].filter(Boolean).join(", "), null, null)),
      tables.length ? null : h("p", { class: "muted-text small" }, "No tables yet."),
      own ? null : h("p", { class: "muted-text small" }, "Tables are the product's ports: its owner adds one in the product's definition, in its metadata project."));
  };

  const itemGroup = (g) => {
    const items = (view.items || []).filter((i) => i.item_type === g.type);
    const open = (i) => h("a", { href: "#", class: "product-offer-open", title: "Open in Fabric", onclick: (e) => {
      e.preventDefault();
      openLink(fabricItemLink({ type: i.item_type, workspace_id: i.workspace_id, item_id: i.item_id }));
    } }, h("b", {}, i.item_name || i.item_id));
    return h("div", { class: "product-offer-group", "data-group": g.type },
      head(g.title, items.length, view.canEdit ? btn(g.add, "add", () => productItemDialog(p, g, done), "tool outline small") : null),
      ...items.map((i) => line(open(i), i.workspace_name || "", endorsementBadge(i.endorsement),
        view.canEdit ? btn("Remove", "delete", () => remove(i, g), "tool link small", { "aria-label": `Remove ${i.item_name}` }) : null)),
      items.length ? null : h("p", { class: "muted-text small" }, view.canEdit ? `No ${g.many} yet. Add one from Fabric.`
        : `No ${g.many} yet. The product's owner or a platform admin adds them.`));
  };

  const draw = () => {
    card.replaceChildren(h("div", { class: "row tight" }, h("b", { class: "grow" }, "What it offers"), previewTag()),
      tablesGroup(),
      ...(view.problem ? [h("p", { class: "fail-text small" }, view.problem)] : PRODUCT_GROUPS.map(itemGroup)));
  };
  const done = (out) => {
    view.items = out.items || [];
    view.problem = "";
    draw();
    onItems(view.items);
  };
  const remove = async (i, g) => {
    if (!(await confirmDialog(`Remove ${i.item_name} from ${p.name}?`,
      `The ${g.word} comes off this product. It stays in Fabric, as it is.`, "Remove", { danger: true }))) return;
    try {
      done(await rpc("platform_remove_product_item", { product_id: p.id, item_id: i.item_id }));
      toast(`Removed ${i.item_name}`);
    } catch (err) {
      toast(trustError(err), true);
    }
  };
  (async () => {
    // The server's rule (an owner or a platform admin) decides; adding and removing check the same one.
    try { view.canEdit = !!(await rpc("platform_product_trust", { product_id: p.id })).can_edit_terms; } catch { view.canEdit = false; }
    try {
      done(await rpc("platform_product_items", { product_id: p.id }));
    } catch (err) {
      view.items = [];
      view.problem = trustError(err);
      draw();
    }
  })();
  return card;
}

// Pick a workspace, then a semantic model or report in it. Only reads Fabric; the product records the item's id.
async function productItemDialog(p, g, done) {
  const problem = h("p", { class: "fail-text small", role: "alert" });
  const list = h("div", { class: "product-item-pick", role: "radiogroup", "aria-label": g.title });
  const ws = h("select", { "aria-label": "Workspace", disabled: true }, h("option", {}, "Loading workspaces..."));
  let items = [];
  const listItems = async () => {
    problem.textContent = "";
    list.replaceChildren(loading());
    const id = ws.value;
    let found = [], why = "";
    try {
      found = (await rpc("list_items", { workspace_id: id })).filter((i) => i.type === g.type)
        .sort((a, b) => String(a.displayName).localeCompare(String(b.displayName)));
    } catch (err) {
      why = trustError(err);
    }
    if (ws.value !== id) return; // an earlier workspace's list answered late: keep the one picked since
    items = found;
    problem.textContent = why;
    list.replaceChildren(...(items.length ? items.map((i, n) => h("label", { class: "check" },
      h("input", { type: "radio", name: "product-item", value: i.id, checked: n === 0 ? true : null }), h("span", {}, i.displayName)))
      : [h("p", { class: "muted-text small" }, `No ${g.word} in this workspace that you can see.`)]));
  };
  ws.addEventListener("change", listItems);
  showDialog(g.dialog, h("div", {},
    h("label", { class: "field" }, "Workspace", ws), list,
    h("p", { class: "muted-text small" }, "Only items you can see in Fabric are listed. The product records the item's id; nothing in Fabric changes."),
    problem), [
    btn("Cancel", null, closeDialog, "tool outline"),
    btn("Add", "add", async () => {
      const id = list.querySelector("input:checked")?.value;
      const it = items.find((i) => i.id === id);
      if (!it) { problem.textContent = `Pick a ${g.word}.`; return; }
      const space = (state.workspaces || []).find((w) => w.id === ws.value);
      try {
        const out = await rpc("platform_add_product_item", { product_id: p.id, item_type: g.type, item_id: it.id,
          workspace_id: ws.value, workspace_name: space?.displayName || "", item_name: it.displayName });
        closeDialog();
        toast(`Added ${it.displayName} to ${p.name}`);
        done(out);
      } catch (err) {
        problem.textContent = trustError(err);
      }
    }, "tool primary")]);
  try {
    if (!state.workspaces) state.workspaces = await rpc("list_workspaces");
  } catch (err) {
    ws.replaceChildren(h("option", {}, "No workspaces"));
    problem.textContent = trustError(err);
    return;
  }
  const spaces = [...(state.workspaces || [])].sort((a, b) => String(a.displayName).localeCompare(String(b.displayName)));
  ws.replaceChildren(...spaces.map((w) => h("option", { value: w.id }, w.displayName)));
  ws.disabled = false;
  if (spaces.length) await listItems();
  else list.replaceChildren(h("p", { class: "muted-text small" }, "You can't see any workspace in Fabric."));
}
