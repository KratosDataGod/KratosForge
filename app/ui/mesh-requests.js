// Data mesh > Requests: everyone's inbox. Waiting on you is what you can decide (a product's owner its
// access and change requests, a domain's owners new products, admins the rest); Sent by you is what
// you asked for and what happened to it. It was Platform > Requests; older links open it here.

const meshRequests = { show: null };

async function renderMeshRequestsTab(body) {
  body.replaceChildren(loading("Reading requests..."));
  const all = await platformCall("platform_requests", {}, "Could not read requests");
  if (!all || state.view !== "metadata" || state.meta.tab !== "requests" || !body.isConnected) return;
  const rerender = () => renderMeshRequestsTab(body);
  const filters = {
    waiting: [(r) => r.status === "waiting" && r.can_decide, "Waiting on you"],
    mine: [(r) => r.mine, "Sent by you"],
    all: [() => true, "Everything"],
  };
  const waiting = all.filter(filters.waiting[0]).length;
  const show = filters[meshRequests.show] ? meshRequests.show : waiting || !all.some(filters.mine[0]) ? "waiting" : "mine";
  const shown = all.filter(filters[show][0]);
  const empty = { waiting: "Nothing is waiting on you.", mine: "You haven't asked for anything yet.", all: "No one has asked for anything yet." }[show];
  body.replaceChildren(withHowItWorks(h("div", { class: "card pad mesh-requests" },
    h("div", { class: "row" },
      h("div", { class: "seg", role: "group", "aria-label": "Show" }, Object.entries(filters).map(([id, [, label]]) =>
        h("button", { class: show === id ? "on" : null, "aria-pressed": String(show === id), onclick: () => { meshRequests.show = id; rerender(); } },
          label, id === "waiting" ? ` (${waiting})` : ""))),
      h("span", { class: "spacer" }),
      previewShown() ? btn("Ask for data", null, () => newRequestDialog(rerender, "data"), "tool outline small") : null,
      btn("New request", "add", () => newRequestDialog(rerender), "tool primary small")),
    h("p", { class: "muted-text small" }, "A data product's owner decides who else may read it and changes to it. A domain's owners answer requests for a new data product. Platform admins decide key tables, sources, workspaces, workspace access, domains and policy exceptions, and can decide any request."),
    shown.length ? requestTable(shown, rerender) : h("p", { class: "muted-text" }, empty)),
  howItWorks("mesh.requests", REQUESTS_HOW)));
}
