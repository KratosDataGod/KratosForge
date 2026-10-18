// OneLake > Access > All workspaces: the access cache in the central lakehouse (access_cache_service.py).
// One row per workspace the person can open, from the last refresh; a row opens its guided fixes.

// What's worth a look in one cached workspace: [{text, fix}], fix naming the button that helps.
function accessCacheFlags(w) {
  const out = [];
  const byName = w.people.filter((p) => p.type === "User" && ["Admin", "Member", "Contributor"].includes(p.role));
  const guests = w.people.filter((p) => p.guest);
  const adminApps = w.people.filter((p) => p.type !== "User" && p.type !== "Group" && p.role === "Admin");
  if (byName.length) out.push({ kind: "byname", text: `${byName.length} ${byName.length === 1 ? "person has" : "people have"} Contributor or higher by name. A group is easier to review and to take away.`, fix: "roles" });
  if (guests.length) out.push({ kind: "guests", text: `${guests.length} ${guests.length === 1 ? "guest" : "guests"} from another organization (${guests.map((g) => `${g.name}, ${g.role}`).join("; ")}).`, fix: "roles" });
  if (adminApps.length) out.push({ kind: "apps", text: `${adminApps.length === 1 ? "An app has" : `${adminApps.length} apps have`} Admin: ${adminApps.map((a) => a.name).join(", ")}.`, fix: "roles" });
  for (const lh of w.lakehouses) {
    if (lh.known && !lh.roles.length) out.push({ kind: "nosecurity", text: `${lh.item_name} has no data access roles: OneLake security is probably off, so every Viewer reads every table.`, fix: "fabric", lakehouse: lh });
  }
  return out;
}

// The filters above the table: [id, label, keep(workspace, flags)].
const ACCESS_CACHE_FILTERS = [
  ["all", "All", () => true],
  ["flagged", "Worth a look", (w, f) => f.length > 0],
  ["guests", "Guests", (w) => w.summary.guests > 0],
  ["byname", "Write roles by name", (w, f) => f.some((x) => x.kind === "byname")],
  ["stale", "Out of date", (w) => w.stale || w.lakehouses.some((l) => l.stale)],
];

const accessCachePage = { data: null, filter: "all", query: "", open: null, busy: false };

// A call that may answer "... Confirm to continue.": ask with that text, then call again with approve.
async function withConfirm(method, params, title, okLabel) {
  try {
    return await rpc(method, params);
  } catch (err) {
    const text = String(err?.message || err);
    if (!text.includes("Confirm to continue")) throw err;
    if (!(await confirmDialog(title, text.replace(/\s*Confirm to continue\.?\s*$/, "."), okLabel))) return undefined;
    return rpc(method, { ...params, approve: true });
  }
}

async function refreshAccessCache(workspaceIds) {
  const ac = accessCachePage;
  if (ac.busy) return;
  ac.busy = true;
  render();
  try {
    const params = workspaceIds ? { workspace_ids: workspaceIds } : {};
    const res = await attempt(() => withConfirm("access_cache_refresh", params, "Set up the access cache?", "Set up and refresh"), "Access not refreshed");
    if (res) {
      toast(`Checked ${res.checked}: ${res.changed} changed${res.problems ? `, ${res.problems} couldn't be read` : ""}`, false, { log: true });
      ac.data = null;
    }
  } finally {
    ac.busy = false;
    render();
  }
}

async function protectAccessCache(conn) {
  const add = h("input", { placeholder: "name@contoso.com, or a group's object ID" });
  const chosen = [];
  const list = h("div", { class: "check-list" });
  const draw = () => fill(list, ...(chosen.length ? chosen.map((p) => h("div", { class: "small" }, `${p.name}${p.type ? ` (${p.type})` : ""}`))
    : [h("p", { class: "muted-text small" }, "No one yet. A group of platform admins is easiest to keep up to date.")]));
  draw();
  showDialog("Who may read the access cache", h("div", { class: "scroll estate" },
    h("p", {}, "The access cache names people and their roles. Only the people and groups here will read it; everyone else who reads the central lakehouse keeps reading its other tables."),
    list,
    h("div", { class: "row" }, h("label", { class: "field grow" }, "Add someone", add), btn("Add", "add", async () => {
      const p = await attempt(() => rpc("access_find_person", { text: add.value, workspace_id: conn?.workspace_id || "" }), "Couldn't find them");
      if (!p) return;
      chosen.push(p); add.value = ""; draw();
    }, "tool outline small"))),
  [btn("Cancel", null, closeDialog, "tool outline"), btn("Continue", "shield", async () => {
    if (!chosen.length) return toast("Add at least one person or group", true);
    closeDialog();
    const res = await attempt(() => withConfirm("access_cache_protect", { members: chosen.map((p) => p.id) },
      "Change who reads the access cache?", "Change"), "Access cache not protected");
    if (res) toast(res.changed ? "Only the people you chose read the access cache now" : "Already set up that way", false, { log: true });
  }, "tool primary")]);
}

async function renderAccessAll(slot) {
  const ac = accessCachePage;
  if (!ac.data) ac.data = await attempt(() => rpc("access_cache"), "Could not read the access cache");
  if (!slot.isConnected) return;
  const d = ac.data;
  if (!d) { fill(slot, h("p", { class: "muted-text" }, "Couldn't read the access cache. Details are in Activity (the bell).")); return; }
  const refreshBtn = btn(ac.busy ? "Refreshing..." : d.ready ? "Refresh changes" : "Set up and refresh", "sync", () => refreshAccessCache(), "tool primary");
  refreshBtn.disabled = ac.busy || (d.state != null && !d.can_write) ? true : null;
  if (!d.ready) {
    fill(slot, h("div", { class: "card pad" }, h("b", {}, "The access cache isn't set up yet"),
      h("p", { class: "muted-text" }, d.message || ""),
      d.state == null ? null : d.can_write ? refreshBtn : h("p", { class: "muted-text small" }, "Setting it up needs write access to the central lakehouse.")));
    return;
  }
  const flagged = d.workspaces.map((w) => [w, accessCacheFlags(w)]);
  const last = d.workspaces.map((w) => w.checked_at).filter(Boolean).sort().pop();
  const keep = (ACCESS_CACHE_FILTERS.find((f) => f[0] === ac.filter) || ACCESS_CACHE_FILTERS[0])[2];
  const q = ac.query.toLowerCase();
  const shown = flagged.filter(([w, f]) => keep(w, f) && (!q || w.workspace_name.toLowerCase().includes(q) || w.people.some((p) => String(p.name).toLowerCase().includes(q))));
  const search = h("input", { type: "search", placeholder: "Name or group", value: ac.query });
  search.addEventListener("input", () => { ac.query = search.value; renderAccessAll(slot).then(() => slot.querySelector("input[type=search]")?.focus()); });
  const stale = flagged.filter(([w]) => w.stale).length;
  const open = flagged.find(([w]) => w.workspace_id === ac.open);
  fill(slot,
    h("div", { class: "card pad" }, h("div", { class: "row wrap" },
      h("div", { class: "grow" }, h("b", {}, last ? `From the access cache, checked ${relTime(last)}` : "From the access cache"),
        h("div", { class: "muted-text small" }, `${d.workspaces.length} ${d.workspaces.length === 1 ? "workspace" : "workspaces"} you can open${stale ? `, ${stale} out of date` : ""}. Kept in the central lakehouse.`)),
      d.can_write ? btn("Who may read it", "shield", () => protectAccessCache(null), "tool outline") : null,
      d.can_write ? refreshBtn : h("span", { class: "muted-text small" }, "Refreshing needs write access to the central lakehouse."))),
    h("div", { class: "row wrap" }, h("label", { class: "field grow" }, "Find a workspace or person", search),
      h("div", { class: "seg", role: "group", "aria-label": "Show" }, ACCESS_CACHE_FILTERS.map(([id, label]) =>
        h("button", { class: ac.filter === id ? "on" : null, onclick: () => { ac.filter = id; renderAccessAll(slot); } }, label)))),
    h("div", { class: "monitor-scroll" }, h("table", { class: "list" },
      h("thead", {}, h("tr", {}, ["Workspace", "People", "Groups", "Apps", "Guests", "Lakehouses", "Worth a look", "Changed"].map((c) => h("th", {}, c)))),
      h("tbody", {}, shown.map(([w, f]) => h("tr", { class: w.workspace_id === ac.open ? "selected" : null },
        h("td", {}, btn(w.workspace_name || w.workspace_id, null, () => { ac.open = w.workspace_id; renderAccessAll(slot); }, "tool small link"),
          w.stale ? h("div", { class: "warn-text small" }, "Out of date: couldn't read its roles at the last refresh") : null,
          !w.known ? h("div", { class: "muted-text small" }, "Roles never read: listing them needs Member or Admin") : null),
        h("td", {}, w.summary.people), h("td", {}, w.summary.groups), h("td", {}, w.summary.apps), h("td", {}, w.summary.guests),
        h("td", {}, w.lakehouses.length),
        h("td", {}, f.length ? h("span", { class: "reason small" }, `${f.length} to look at`) : h("span", { class: "muted-text small" }, "None")),
        h("td", { class: "muted-text small" }, w.changed_at ? relTime(w.changed_at) : "")))))),
    shown.length ? null : h("p", { class: "muted-text" }, d.workspaces.length ? "Nothing matches." : "Nothing cached yet. Refresh changes reads every workspace you can open."),
    open ? accessCacheDetail(open[0], open[1]) : null);
}

function accessCacheDetail(w, flags) {
  const toRoles = () => {
    // The workspace roles grid edits roles (and asks first); add this workspace to it.
    if (!accessPage.extra.some((x) => x.workspace_id === w.workspace_id)) accessPage.extra.push({ workspace_id: w.workspace_id, workspace_name: w.workspace_name });
    accessPage.data = null;
    accessPage.view = "roles";
    render();
  };
  const toTables = (lh) => { accessPage.view = "tables"; accessPage.lakehouse = lh.item_id; accessPage.lh = null; render(); };
  const fixButton = (f) => (f.fix === "fabric"
    ? btn("Open the lakehouse in Fabric", "open", () => openInFabric(fabricItemLink({ type: "Lakehouse", workspace_id: w.workspace_id, item_id: f.lakehouse.item_id })), "tool outline small")
    : btn("Review in Workspace roles", null, toRoles, "tool outline small"));
  const pathLabel = (p) => (p === "*" ? "Everything" : p.replace(/^\/Tables\//, "").replace(/\//g, ".").replace(/^\/Files/, "Files"));
  return h("div", { class: "card pad" },
    h("div", { class: "row wrap" }, h("h2", { class: "flush grow" }, w.workspace_name),
      btn("Open in Fabric", "open", () => openInFabric(fabricItemLink({ type: "", workspace_id: w.workspace_id })), "tool small link"),
      btn("Refresh this workspace", "sync", () => refreshAccessCache([w.workspace_id]), "tool small link")),
    w.problem ? h("p", { class: "warn-text small" }, w.problem) : null,
    h("h3", {}, "Guided fixes"),
    flags.length ? flags.map((f) => h("div", { class: "row wrap" }, h("span", { class: "grow" }, f.text), fixButton(f)))
      : h("p", { class: "muted-text small" }, "Nothing to fix from what the cache saw."),
    h("h3", {}, "People, groups and apps"),
    w.people.length ? h("table", { class: "list" }, h("thead", {}, h("tr", {}, ["Name", "Kind", "Role"].map((c) => h("th", {}, c)))),
      h("tbody", {}, w.people.map((p) => h("tr", {}, h("td", {}, p.name, p.guest ? h("span", { class: "pill-s warn" }, "Guest") : null),
        h("td", { class: "muted-text small" }, p.type === "ServicePrincipal" ? "App" : p.type), h("td", {}, p.role)))))
      : h("p", { class: "muted-text small" }, w.known ? "No one has a role." : "Not read yet."),
    h("h3", {}, "Lakehouses and their table roles"),
    w.lakehouses.length ? w.lakehouses.map((lh) => h("div", { class: "row wrap" },
      h("div", { class: "grow" }, h("b", {}, lh.item_name),
        lh.stale ? h("div", { class: "warn-text small" }, lh.problem || "Out of date") : null,
        lh.known && !lh.roles.length ? h("div", { class: "muted-text small" }, "No data access roles") : null,
        lh.roles.map((r) => h("div", { class: "small" }, h("b", {}, r.name), ": ", r.paths.map(pathLabel).join(", "),
          r.entra.length ? ` · ${r.entra.length} ${r.entra.length === 1 ? "member" : "members"}` : "",
          r.item_members.length ? ` · everyone with ${r.item_members.join(", ")}` : ""))),
      btn("Table access", null, () => toTables(lh), "tool outline small")))
      : h("p", { class: "muted-text small" }, "No lakehouses."),
    h("p", { class: "muted-text small" }, "Every fix opens the existing editor, which asks before it changes anything in Fabric. Role changes are recorded in the audit log."));
}
