// Data mesh > Requests: everyone's inbox. Waiting on you is what you can decide (a product's owner its
// access and change requests, a domain's owners new products, admins the rest); Sent by you is what
// you asked for and what happened to it. It was Platform > Requests; older links open it here.

const meshRequests = { show: null };

async function renderMeshRequestsTab(body) {
  const rerender = () => renderMeshRequestsTab(body);
  // New request is the page's button; its dialog asks for data too. Who decides is in How this works.
  meshChrome({ actions: btn("New request", "add", () => newRequestDialog(rerender), "tool primary") });
  body.replaceChildren(loading("Reading requests..."));
  const all = await platformCall("platform_requests", {}, "Could not read requests");
  if (!all || state.view !== "metadata" || state.meta.tab !== "requests" || !body.isConnected) return;
  const filters = {
    waiting: [(r) => r.status === "waiting" && r.can_decide, "Waiting on you"],
    mine: [(r) => r.mine, "Sent by you"],
    all: [() => true, "Everything"],
  };
  const waiting = all.filter(filters.waiting[0]).length;
  const show = filters[meshRequests.show] ? meshRequests.show : waiting || !all.some(filters.mine[0]) ? "waiting" : "mine";
  const shown = all.filter(filters[show][0]);
  const empty = { waiting: "Nothing is waiting on you.", mine: "You haven't asked for anything yet.", all: "No one has asked for anything yet." }[show];
  meshChrome({
    views: h("div", { class: "seg", role: "group", "aria-label": "Show" }, Object.entries(filters).map(([id, [, label]]) =>
      h("button", { class: show === id ? "on" : null, "aria-pressed": String(show === id), onclick: () => { meshRequests.show = id; rerender(); } },
        label, id === "waiting" ? ` (${waiting})` : ""))),
  });
  body.replaceChildren(withHowItWorks(h("div", { class: "card pad mesh-requests" },
    shown.length ? requestTable(shown, rerender) : h("p", { class: "muted-text" }, empty)),
  howItWorks("mesh.requests", REQUESTS_HOW)));
}
