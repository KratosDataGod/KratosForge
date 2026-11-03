// The estimated plan on the Warehouse page (WH-09): Fabric's showplan XML for a query that reads, which
// Fabric works out without running the query (sql_start estimated_plan). The Plan view draws it as a tree
// of operators with estimated rows and cost, heaviest first to the eye; the XML can be copied as it came.

// { statements: [{ text, rows, cost, root }] } from showplan XML, each root an operator
// { physical, logical, rows, cost, own, object, warnings, children }. Throws if it isn't showplan XML.
function sqlParsePlan(xml) {
  const doc = new DOMParser().parseFromString(String(xml || ""), "application/xml");
  if (doc.getElementsByTagName("parsererror").length || doc.documentElement.localName !== "ShowPlanXML") {
    throw new Error("Fabric's answer isn't a showplan.");
  }
  const kids = (el, name) => [...el.children].filter((c) => c.localName === name);
  const num = (v) => (v === null || v === "" || Number.isNaN(Number(v)) ? null : Number(v));
  // The operators directly under el: RelOp elements whose nearest RelOp ancestor is el's own.
  const childOps = (el) => {
    const out = [];
    const walk = (node) => { for (const c of node.children) { if (c.localName === "RelOp") out.push(c); else walk(c); } };
    walk(el);
    return out;
  };
  const objectOf = (op) => {
    const seen = [];
    const walk = (node) => {
      for (const c of node.children) {
        if (c.localName === "RelOp") continue;
        if (c.localName === "Object") {
          const name = ["Schema", "Table", "Index"].map((a) => c.getAttribute(a)).filter(Boolean).join(".").replace(/[[\]]/g, "");
          if (name && !seen.includes(name)) seen.push(name);
        } else walk(c);
      }
    };
    walk(op);
    return seen.join(", ");
  };
  const operator = (op) => {
    const children = childOps(op).map(operator);
    const cost = num(op.getAttribute("EstimatedTotalSubtreeCost"));
    const below = children.reduce((s, c) => s + (c.cost || 0), 0);
    return { physical: op.getAttribute("PhysicalOp") || "?", logical: op.getAttribute("LogicalOp") || "",
      rows: num(op.getAttribute("EstimateRows")), cost, own: cost === null ? null : Math.max(0, cost - below),
      object: objectOf(op), warnings: kids(op, "Warnings").length > 0, children };
  };
  const statements = [...doc.getElementsByTagNameNS("*", "StmtSimple")].map((st) => {
    const plan = kids(st, "QueryPlan")[0];
    const root = plan ? kids(plan, "RelOp")[0] : null;
    return { text: st.getAttribute("StatementText") || "", rows: num(st.getAttribute("StatementEstRows")),
      cost: num(st.getAttribute("StatementSubTreeCost")), root: root ? operator(root) : null };
  });
  return { statements };
}

const sqlPlanNumber = (n, digits = 0) => (n === null ? "?" : n.toLocaleString(undefined, { maximumFractionDigits: digits }));

// The Plan view. plan: { xml, at } or { error } or null (none asked for yet).
function sqlPlanView(plan) {
  if (!plan) {
    return h("p", { class: "muted-text", style: "padding:12px 16px" },
      "Estimated plan shows how Fabric would run the query, without running it. It uses a little capacity.");
  }
  if (plan.error) return h("div", { class: "output error" }, h("pre", {}, plan.error.replace(/^Error: /, "")));
  let parsed;
  try { parsed = sqlParsePlan(plan.xml); } catch (err) {
    return h("div", { class: "sql-plan" }, h("p", { class: "muted-text" }, `${String(err).replace(/^Error: /, "")} It came back as:`),
      h("pre", { class: "sql-plan-xml" }, String(plan.xml || "").slice(0, 20000)));
  }
  const copy = btn("Copy XML", null, () => {
    navigator.clipboard?.writeText(plan.xml).then(() => toast("Copied the plan's XML."), () => toast("Could not copy the XML.", true));
  }, "tool small", { title: "Copy the showplan XML, to open in SSMS or another plan viewer" });
  const parts = [h("div", { class: "sql-plan-head" }, h("b", {}, "Estimated plan"),
    h("span", { class: "muted-text" }, `Fabric worked it out at ${timeText(plan.at)}; the query didn't run. Rows and cost are Fabric's estimates.`),
    h("span", { class: "spacer" }), copy)];
  for (const [i, st] of parsed.statements.entries()) {
    const total = st.cost || st.root?.cost || 0;
    let heaviest = null;
    const find = (op) => { if (op.own !== null && (!heaviest || op.own > heaviest.own)) heaviest = op; op.children.forEach(find); };
    if (st.root) find(st.root);
    const row = (op, depth) => {
      const share = total > 0 && op.own !== null ? Math.round((op.own / total) * 100) : null;
      return [h("div", { class: `sql-plan-op${op === heaviest && share ? " heavy" : ""}`, role: "listitem",
        style: `padding-left:${12 + depth * 18}px` },
      h("span", { class: "sql-plan-name" }, depth ? h("span", { class: "sr-only" }, `Level ${depth + 1}: `) : null, op.physical, op.logical && op.logical !== op.physical ? h("span", { class: "muted-text" }, ` (${op.logical})`) : null),
      h("span", { class: "sql-plan-object", title: op.object }, op.object),
      h("span", { class: "sql-plan-warn", title: op.warnings ? "Fabric flagged a warning on this operator; copy the XML to see it" : null }, op.warnings ? "warning" : ""),
      h("span", { class: "sql-plan-rows", title: "Estimated rows" }, `${sqlPlanNumber(op.rows)} rows`),
      h("span", { class: "sql-plan-cost", title: "This operator's share of the statement's estimated cost" }, share === null ? "" : `${share}%`)),
      ...op.children.flatMap((c) => row(c, depth + 1))];
    };
    parts.push(h("div", { class: "sql-plan-stmt" },
      h("div", { class: "sql-plan-stmt-head" }, `Statement ${i + 1}`,
        h("span", { class: "muted-text" }, ` · ${sqlPlanNumber(st.rows)} rows · cost ${sqlPlanNumber(total, 4)}`)),
      st.root ? h("div", { class: "sql-plan-tree", role: "list", "aria-label": `Plan for statement ${i + 1}` }, row(st.root, 0))
        : h("p", { class: "muted-text wh-note" }, "This statement has no operators to show.")));
  }
  if (!parsed.statements.length) parts.push(h("p", { class: "muted-text wh-note" }, "Fabric returned a plan with no statements."));
  return h("div", { class: "sql-plan" }, ...parts);
}
