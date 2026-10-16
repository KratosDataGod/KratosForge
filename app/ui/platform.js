// The Platform page: the central team's home. One landing zone, one key system, the domains and
// glossary every team shares, and the requests domain teams send. Platform admins change these;
// everyone else sees them and asks (Platform > Requests). The rules are enforced by the platform
// itself (central/core.py), so this page only hides buttons a person can't use.

const PLATFORM_TABS = [
  ["overview", "Overview"], ["landing", "Landing zone"], ["keys", "Business keys"], ["domains", "Domains"],
  ["glossary", "Glossary"], ["requests", "Requests"], ["audit", "Audit"], ["settings", "Settings"],
];
const REQUEST_KIND = { key_table: "Key table", source: "Source", access: "Access" };
const REQUEST_STATUS = { waiting: "Waiting", approved: "Approved", declined: "Declined", withdrawn: "Withdrawn" };
const platformPage = { tab: "overview", env: "dev", requests: "waiting", source: null, me: null };

function goPlatform(tab) {
  platformPage.tab = tab || platformPage.tab;
  go("platform");
}

function platformCall(method, params = {}, title) {
  return metaCall(method, params, title);
}

function bytes(n) {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`;
}

function adminOnly(node) {
  return platformPage.me?.admin ? node : null;
}

async function renderPlatform() {
  const p = platformPage;
  const body = h("div", { class: "meta-body" });
  const envSeg = h("div", { class: "seg", role: "group", "aria-label": "Environment" },
    MESH_ENVS.map((env) => h("button", { class: p.env === env ? "on" : null, onclick: () => { p.env = env; render(); } }, ENV_LABEL[env])));
  const who = h("p", { class: "sub" }, "Loading...");
  setView(h("div", { class: "page" },
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Platform ", previewTag()), who),
      h("div", { class: "head-actions" }, ["landing", "keys", "overview"].includes(p.tab) ? envSeg : null,
        btn("Refresh", "sync", () => render(), "tool outline"))),
    h("div", { class: "subtabs" }, PLATFORM_TABS.map(([id, label]) =>
      h("button", { class: `tab${p.tab === id ? " active" : ""}`, onclick: () => goPlatform(id) }, label))),
    body));
  p.me = await attempt(() => rpc("platform_whoami"), "Could not reach the platform");
  if (state.view !== "platform") return;
  if (!p.me) { body.replaceChildren(h("p", { class: "muted-text" }, "The platform shows once the local engine is running.")); return; }
  who.replaceChildren(...[p.me.admin ? "You're a platform admin: you create domains, key tables and sources, and decide requests. "
    : p.me.can_claim ? "This platform has no admin yet. "
      : "Domain teams see everything here and ask for what they need in Requests. ",
  p.me.can_claim ? h("a", { href: "#", onclick: (e) => { e.preventDefault(); claimAdmin(); } }, "Become its first admin") : null,
  p.me.can_claim ? ". " : null, learnMore("platform")].filter(Boolean));
  const tabs = { overview: platformOverviewTab, landing: platformLandingTab, keys: platformKeysTab, domains: platformDomainsTab,
    glossary: platformGlossaryTab, requests: platformRequestsTab, audit: platformAuditTab, settings: platformSettingsTab };
  await (tabs[p.tab] || platformOverviewTab)(body);
}

async function claimAdmin() {
  if (!state.user && !(await signIn())) return;
  if (await platformCall("platform_claim_admin", {}, "Not changed")) { toast("You're the platform's first admin"); render(); }
}

function stillHere(tab) {
  return state.view === "platform" && platformPage.tab === tab;
}

function platStat(n, label, note, onClick) {
  return h("button", { class: "tile plat-stat", onclick: onClick || null },
    h("span", { class: "plat-n" }, typeof n === "number" ? n.toLocaleString() : n), h("span", { class: "tt" }, label),
    note ? h("span", { class: "td" }, note) : null);
}

// ---------- overview ----------

async function platformOverviewTab(body) {
  const o = await platformCall("platform_overview", { env: platformPage.env }, "Could not load the platform");
  if (!o || !stillHere("overview")) return;
  const c = o.counts;
  const late = (o.landing.sources || []).filter((s) => s.late);
  const health = h("table", { class: "list" },
    h("thead", {}, h("tr", {}, ["", ...MESH_ENVS.map((e) => ENV_LABEL[e])].map((x) => h("th", {}, x)))),
    h("tbody", {}, h("tr", {}, h("td", {}, h("b", {}, "Platform")), ...MESH_ENVS.map((env) => {
      const e = o.status.environments[env];
      return h("td", {}, e.kind === "fabric" ? h("span", { class: "pill-s pass" }, e.workspace_name || "In Fabric") : h("span", { class: "pill-s neu" }, "On this PC"));
    }))));
  body.replaceChildren(...[
    o.problems.length || o.landing.problem ? h("div", { class: "problems" }, h("b", {}, "Couldn't read everything"), gateList([...o.problems, o.landing.problem].filter(Boolean))) : null,
    h("div", { class: "tiles plat-stats" },
      platStat(c.domains, "Domains", c.subdomains ? `${c.subdomains} subdomains` : "", () => goPlatform("domains")),
      platStat(c.projects, "Projects", c.teams ? `${c.teams} team${c.teams === 1 ? "" : "s"}` : ""),
      platStat(c.key_tables, "Business key tables", `${c.keys.toLocaleString()} keys in ${ENV_LABEL[o.env]}`, () => goPlatform("keys")),
      platStat(c.products, "Data products", `${c.published} published`, () => go("onelake")),
      platStat(c.requests, "Requests waiting", o.needs_you.length ? `${o.needs_you.length} for you` : "", () => goPlatform("requests"))),
    h("div", { class: "plat-two" },
      h("div", { class: "card pad" }, h("b", {}, o.me.admin ? "Needs you" : "Your requests"),
        requestTable(o.me.admin || o.needs_you.length ? o.needs_you : o.mine, () => render(), true),
        h("b", {}, "Each environment's platform"), health),
      h("div", { class: "card pad" },
        h("div", { class: "row" }, h("b", { class: "grow" }, `Landing zone in ${ENV_LABEL[o.env]}`), btn("Open", null, () => goPlatform("landing"), "tool link small")),
        late.length ? h("p", { class: "fail-text small" }, `${late.map((s) => s.name).join(", ")} ${late.length === 1 ? "is" : "are"} late.`) : null,
        (o.landing.sources || []).length ? h("table", { class: "list" },
          h("thead", {}, h("tr", {}, ["Source", "Last file", "Read by"].map((x) => h("th", {}, x)))),
          h("tbody", {}, o.landing.sources.map((s) => h("tr", {},
            h("td", {}, h("b", {}, s.name)),
            h("td", {}, s.late ? h("span", { class: "pill-s fail" }, s.newest ? `late, ${relTime(s.newest)}` : "nothing yet") : s.newest ? when(s.newest) : h("span", { class: "muted-text" }, "nothing yet")),
            h("td", {}, s.readers.length ? `${s.readers.length} project${s.readers.length === 1 ? "" : "s"}` : h("span", { class: "muted-text" }, "none"))))))
          : h("p", { class: "muted-text" }, "No sources in the landing zone yet."),
        h("b", {}, "Recent changes"),
        auditList(o.recent)))].filter(Boolean));
}

function auditList(rows) {
  return rows.length ? h("table", { class: "list" }, h("tbody", {}, rows.map((a) => h("tr", {},
    h("td", { class: "muted-text small" }, when(a.at)), h("td", {}, a.by_user || h("span", { class: "muted-text" }, "someone")),
    h("td", {}, a.action), h("td", { class: "mono small" }, a.target)))))
    : h("p", { class: "muted-text" }, "Nothing yet.");
}

// ---------- landing zone ----------

async function platformLandingTab(body) {
  const p = platformPage;
  const l = await platformCall("platform_landing", { env: p.env }, "Could not read the landing zone");
  if (!l || !stillHere("landing")) return;
  if (!l.sources.find((s) => s.name === p.source)) p.source = l.sources[0]?.name || null;
  const sel = l.sources.find((s) => s.name === p.source);
  const rerender = () => platformLandingTab(body);
  const list = h("div", { class: "card pad" },
    h("div", { class: "row" }, h("b", { class: "grow" }, "Sources landed by the central team"),
      adminOnly(btn("Add source", "add", () => sourceDialog(null, rerender), "tool primary small")),
      platformPage.me?.admin ? null : btn("Request a source", "add", () => requestDialog("source", "", rerender), "tool outline small")),
    h("p", { class: "muted-text small" }, "Each source lands once, in ", h("span", { class: "mono" }, "Files/<source>/<entity>/"), " of LH_Landing ",
      l.kind === "fabric" ? `in ${l.where}` : h("span", {}, "on this PC (", h("span", { class: "mono" }, l.where), ")"),
      ". Domain teams read it in their own Bronze; only the central team writes here."),
    l.problem ? h("p", { class: "fail-text small" }, l.problem) : null,
    l.sources.length ? h("table", { class: "list" },
      h("thead", {}, h("tr", {}, ["Source", "Entities", "Last arrival", "Expected", "Read by"].map((x) => h("th", {}, x)))),
      h("tbody", {}, l.sources.map((s) => h("tr", { class: `click${s.name === p.source ? " picked" : ""}`, onclick: () => { p.source = s.name; rerender(); } },
        h("td", {}, h("b", {}, s.name), s.registered ? null : h("span", { class: "pill-s wrn" }, "not registered")),
        h("td", {}, s.entities.length),
        h("td", {}, s.late ? h("span", { class: "pill-s fail" }, s.newest ? `late, ${relTime(s.newest)}` : "nothing yet") : s.newest ? when(s.newest) : h("span", { class: "muted-text" }, "nothing yet")),
        h("td", {}, s.expected_hours ? `every ${s.expected_hours} h` : h("span", { class: "muted-text" }, "not set")),
        h("td", {}, s.readers.length || h("span", { class: "muted-text" }, "none"))))))
      : h("div", { class: "empty" }, h("span", { class: "title" }, "The landing zone is empty"),
        h("span", { class: "text" }, "Add a source, then land its files in Files/<source>/<entity>/.")));
  body.replaceChildren(h("div", { class: "keys-grid" }, list, sel ? sourceDetail(sel, l, rerender) : null));
}

function sourceDetail(s, l, rerender) {
  const files = h("div", {}, h("p", { class: "muted-text small" }, "Loading files..."));
  platformCall("platform_landing_files", { env: l.env, source: s.name, limit: 8 }).then((fs) => {
    if (!fs) return;
    files.replaceChildren(fs.length ? h("table", { class: "mini" }, h("thead", {}, h("tr", {}, ["File", "Size", "Landed"].map((x) => h("th", {}, x)))),
      h("tbody", {}, fs.map((f) => h("tr", {}, h("td", { class: "mono" }, f.path), h("td", {}, bytes(f.size)), h("td", {}, when(f.modified))))))
      : h("p", { class: "muted-text" }, "No files yet."));
  });
  return h("div", { class: "card pad side-panel" },
    h("div", { class: "row" }, h("h2", { class: "flush" }, s.title || s.name), h("span", { class: "spacer" }),
      adminOnly(btn(s.registered ? "Edit" : "Register", null, () => sourceDialog(s, rerender), "tool outline small")),
      adminOnly(s.registered ? btn("Remove", "delete", async () => {
        if (!(await confirmDialog(`Remove source ${s.name}?`, "Only the registration goes; files in the landing zone stay. A source a project still reads can't be removed.", "Remove"))) return;
        if (await platformCall("platform_delete_source", { name: s.name }, "Not removed")) rerender();
      }, "tool outline small") : null)),
    h("p", { class: "muted-text small" }, s.description || "", s.owner ? ` Owner ${s.owner}.` : ""),
    h("b", {}, "Entities"),
    s.entities.length ? h("table", { class: "mini" }, h("thead", {}, h("tr", {}, ["Entity", "Files", "Size", "Newest"].map((x) => h("th", {}, x)))),
      h("tbody", {}, s.entities.map((e) => h("tr", {}, h("td", { class: "mono" }, e.name || "(top)"), h("td", {}, e.files), h("td", {}, bytes(e.size)), h("td", {}, when(e.newest))))))
      : h("p", { class: "muted-text" }, "Nothing landed yet."),
    h("b", {}, "Newest files"), files,
    h("b", {}, "Read by"),
    s.readers.length ? h("table", { class: "mini" }, h("tbody", {}, s.readers.map((r) => h("tr", {}, h("td", {}, h("b", {}, r.project)), h("td", { class: "mono small" }, r.entities.join(", "))))))
      : h("p", { class: "muted-text" }, "No project reads it yet."),
    h("b", {}, "Read it in a project"),
    h("pre", { class: "sql-block" }, `[source]\nlanding = "${s.name}/${s.entities[0]?.name || "<entity>"}/*.csv"`),
    h("p", { class: "muted-text small" }, "Or Metadata > New entity > From the landing zone."));
}

function sourceDialog(s, done) {
  const name = h("input", { value: s?.name || "", placeholder: "sap", disabled: s?.registered ? true : null });
  const title = h("input", { value: s?.title || "", placeholder: "SAP ERP" });
  const description = h("input", { value: s?.description || "" });
  const owner = h("input", { value: s?.owner || "", placeholder: "Who lands it" });
  const hours = h("input", { type: "number", min: "1", value: s?.expected_hours || "", placeholder: "24" });
  const secrets = MESH_ENVS.map((env) => [env, h("input", { value: s?.connections?.[env]?.secret_name || "", placeholder: "Key Vault secret name" })]);
  showDialog(s?.registered ? `Edit ${s.name}` : "Add a source", h("div", { class: "scroll" },
    h("div", { class: "form-grid" }, h("label", { class: "field" }, "Folder name", name), h("label", { class: "field" }, "Title", title),
      h("label", { class: "field" }, "Owner", owner), h("label", { class: "field" }, "Expected every (hours)", hours)),
    h("label", { class: "field" }, "Description", description),
    h("b", {}, "Connection secret per environment"),
    h("p", { class: "muted-text small" }, "The name of the Key Vault secret its pipeline uses. Secrets themselves never go here."),
    h("div", { class: "form-grid" }, secrets.map(([env, input]) => h("label", { class: "field" }, ENV_LABEL[env], input)))), [
    btn("Cancel", null, closeDialog, "tool outline"),
    btn("Save", "save", async () => {
      if (!name.value.trim()) { name.focus(); return; }
      const connections = Object.fromEntries(secrets.filter(([, i]) => i.value.trim()).map(([env, i]) => [env, { secret_name: i.value.trim() }]));
      const res = await platformCall("platform_save_source", { source: { name: name.value.trim().toLowerCase(), title: title.value.trim(),
        description: description.value.trim(), owner: owner.value.trim(), expected_hours: hours.value.trim(), connections } }, "Not saved");
      if (res) { closeDialog(); platformPage.source = name.value.trim().toLowerCase(); done(); }
    }, "tool primary")]);
}

// ---------- business keys ----------

async function platformKeysTab(body) {
  const k = await platformCall("platform_key_tables", { env: platformPage.env }, "Could not read the key tables");
  if (!k || !stillHere("keys")) return;
  const rerender = () => platformKeysTab(body);
  const admin = platformPage.me?.admin;
  body.replaceChildren(h("div", { class: "card pad" },
    h("div", { class: "row" }, h("b", { class: "grow" }, `Key tables in SQL_Platform (${ENV_LABEL[k.env]})`),
      admin ? btn("New key table", "add", () => keyTableDialog(rerender), "tool primary small")
        : btn("Request a key table", "add", () => requestDialog("key_table", "", rerender), "tool primary small")),
    h("p", { class: "muted-text small" }, "One key per business key for every team: customer C-10442 from any source always gets the same key. ",
      admin ? "A new key table is made in Dev, Test and Prod at once." : "Only platform admins add key tables; ask for one and it appears here once approved.",
      " Projects pick from this list in Metadata > Surrogate keys."),
    k.key_tables.length ? h("table", { class: "list" },
      h("thead", {}, h("tr", {}, ["Key table", "Business key", "Description", "Keys", "Next key", "Created"].map((x) => h("th", {}, x)))),
      h("tbody", {}, k.key_tables.map((t) => h("tr", {},
        h("td", {}, h("b", { class: "mono" }, `sk.${t.name}`)),
        h("td", { class: "mono" }, t.business_key || `${t.name}_bk`),
        h("td", {}, t.description || h("span", { class: "muted-text" }, "")),
        h("td", { class: "n" }, (t.keys || 0).toLocaleString()),
        h("td", { class: "n mono" }, (t.next || 1).toLocaleString()),
        h("td", { class: "muted-text small" }, t.created_at ? `${relTime(t.created_at)}${t.created_by ? `, ${t.created_by}` : ""}` : "")))))
      : h("p", { class: "muted-text" }, "No key tables yet."),
    k.requested.length ? h("div", {}, h("b", {}, "Requested"), requestTable(k.requested, rerender)) : null));
}

function keyTableDialog(done) {
  const name = h("input", { placeholder: "supplier" });
  const bk = h("input", { placeholder: "supplier_id" });
  const description = h("input", { placeholder: "A supplier, from any source" });
  showDialog("New key table", h("div", {},
    h("label", { class: "field" }, "Name", name), h("label", { class: "field" }, "Business key column", bk),
    h("label", { class: "field" }, "Description", description),
    h("p", { class: "muted-text small" }, "Creates sk.<name> in every environment's SQL_Platform with <name>_sk (INT), <name>_bk (unique) and create_date, and the Unknown member -1.")), [
    btn("Cancel", null, closeDialog, "tool outline"),
    btn("Create", "add", async () => {
      if (!name.value.trim()) { name.focus(); return; }
      const res = await platformCall("platform_create_key_table", { name: name.value.trim(), business_key: bk.value.trim(), description: description.value.trim() }, "Not created");
      if (!res) return;
      closeDialog();
      const failed = res.environments.filter((e) => !e.ok);
      toast(failed.length ? `Created sk.${res.name}, but not in ${failed.map((e) => ENV_LABEL[e.env]).join(", ")}: ${failed[0].message}` : `Created sk.${res.name} in every environment`, !!failed.length, { log: true });
      done();
    }, "tool primary")]);
}

// ---------- requests ----------

function requestDialog(kind, target, done, extra = {}) {
  const name = h("input", { value: target || "", placeholder: kind === "key_table" ? "supplier" : kind === "source" ? "shopify" : "", disabled: kind === "access" ? true : null });
  const bk = h("input", { placeholder: "supplier_id" });
  const reason = h("textarea", { rows: 2, placeholder: "What it's for" });
  const titles = { key_table: "Request a key table", source: "Request a source", access: `Request access to ${extra.title || target}` };
  showDialog(titles[kind], h("div", {},
    kind === "access" ? h("p", { class: "text" }, `Its owner${extra.owner ? `, ${extra.owner},` : ""} decides. Once approved, Link works for this project.`)
      : h("label", { class: "field" }, kind === "key_table" ? "Name" : "Folder name", name),
    kind === "key_table" ? h("label", { class: "field" }, "Business key column", bk) : null,
    h("label", { class: "field" }, "Why", reason)), [
    btn("Cancel", null, closeDialog, "tool outline"),
    btn("Send request", null, async () => {
      if (!name.value.trim()) { name.focus(); return; }
      const detail = kind === "key_table" ? { business_key: bk.value.trim(), description: reason.value.trim() } : {};
      const res = await platformCall("platform_request", { kind, target: name.value.trim(), reason: reason.value.trim(), detail }, "Not sent");
      if (res) { closeDialog(); toast("Request sent", false, { log: true }); done?.(); }
    }, "tool primary")]);
}

function requestTable(rows, rerender, compact = false) {
  if (!rows.length) return h("p", { class: "muted-text" }, "Nothing waiting.");
  const decide = async (r, approve) => {
    let note = "";
    if (!approve) {
      const input = h("input", { placeholder: "Why, so they know what to do next" });
      const ok = await new Promise((resolve) => showDialog(`Decline ${REQUEST_KIND[r.kind].toLowerCase()} ${r.target}?`, h("label", { class: "field" }, "Note", input), [
        btn("Cancel", null, () => { closeDialog(); resolve(false); }, "tool outline"),
        btn("Decline", null, () => { note = input.value.trim(); closeDialog(); resolve(true); }, "tool primary")]));
      if (!ok) return;
    }
    const res = await platformCall("platform_decide", { id: r.id, approve, note }, "Not decided");
    if (!res) return;
    toast(`${approve ? "Approved" : "Declined"} ${r.target}${res.problems?.length ? `; ${res.problems.join("; ")}` : ""}`, !!res.problems?.length, { log: true });
    rerender();
  };
  return h("table", { class: "list" },
    h("thead", {}, h("tr", {}, ["Type", "Request", "From", compact ? "" : "Status", ""].map((x) => h("th", {}, x)))),
    h("tbody", {}, rows.map((r) => h("tr", {},
      h("td", {}, h("span", { class: "pill-s info" }, REQUEST_KIND[r.kind])),
      h("td", {}, h("b", { class: "mono" }, r.target), r.project ? h("span", { class: "muted-text" }, ` for ${r.project}`) : null,
        r.reason ? h("div", { class: "muted-text small" }, r.reason) : null,
        r.note ? h("div", { class: "muted-text small" }, `Note: ${r.note}`) : null),
      h("td", {}, r.requested_by || h("span", { class: "muted-text" }, "someone"), h("div", { class: "muted-text small" }, when(r.requested_at))),
      compact ? h("td", {}) : h("td", {}, h("span", { class: `pill-s ${r.status === "approved" ? "pass" : r.status === "waiting" ? "wrn" : "neu"}` }, REQUEST_STATUS[r.status]),
        r.decided_by ? h("div", { class: "muted-text small" }, `${r.decided_by}, ${relTime(r.decided_at)}`) : null),
      h("td", {}, h("span", { class: "row tight" },
        r.can_decide ? btn("Approve", null, () => decide(r, true), "tool primary small") : null,
        r.can_decide ? btn("Decline", null, () => decide(r, false), "tool outline small") : null,
        r.mine && r.status === "waiting" ? btn("Withdraw", null, async () => {
          if (await platformCall("platform_withdraw", { id: r.id }, "Not withdrawn")) rerender();
        }, "tool outline small") : null))))));
}

async function platformRequestsTab(body) {
  const p = platformPage;
  const all = await platformCall("platform_requests", {}, "Could not read requests");
  if (!all || !stillHere("requests")) return;
  const rerender = () => platformRequestsTab(body);
  const filters = { waiting: (r) => r.status === "waiting", decided: (r) => r.status === "approved" || r.status === "declined", mine: (r) => r.mine, all: () => true };
  const labels = { waiting: "Waiting", decided: "Decided", mine: "Sent by me", all: "All" };
  const shown = all.filter(filters[p.requests] || filters.waiting);
  body.replaceChildren(h("div", { class: "card pad" },
    h("div", { class: "row" },
      h("div", { class: "seg", role: "group", "aria-label": "Show" }, Object.entries(labels).map(([id, label]) =>
        h("button", { class: p.requests === id ? "on" : null, onclick: () => { p.requests = id; rerender(); } },
          label, id === "waiting" ? ` (${all.filter(filters.waiting).length})` : ""))),
      h("span", { class: "spacer" }),
      btn("Request a key table", "add", () => requestDialog("key_table", "", rerender), "tool outline small"),
      btn("Request a source", "add", () => requestDialog("source", "", rerender), "tool outline small")),
    h("p", { class: "muted-text small" }, "Platform admins decide key tables and sources. A data product's owner decides who else may read it; ask from the OneLake catalog."),
    requestTable(shown, rerender)));
}

// ---------- domains, glossary, audit ----------

async function platformDomainsTab(body) {
  const domains = await platformCall("platform_domains");
  if (!domains || !stillHere("domains")) return;
  const rerender = () => platformDomainsTab(body);
  const o = { domains };
  body.replaceChildren(h("div", { class: "card pad" },
    h("div", { class: "row" }, h("b", { class: "grow" }, "Your organization's domains"),
      adminOnly(btn("New domain", "add", () => domainDialog(o, null, rerender), "tool primary small"))),
    h("p", { class: "muted-text small" }, "An area of the business that owns its data, like Sales or Finance; subdomains go one level deep. Projects join one in Metadata > Mesh. Domain owners keep their domain's glossary terms."),
    domains.length ? h("table", { class: "list" },
      h("thead", {}, h("tr", {}, ["Domain", "Description", "Owners", "Fabric domain", ""].map((x) => h("th", {}, x)))),
      h("tbody", {}, domains.map((d) => h("tr", {},
        h("td", {}, d.parent_id ? h("span", { class: "muted-text" }, "↳ ") : null, h("b", {}, d.name), h("div", { class: "muted-text small mono" }, d.id)),
        h("td", {}, d.description || ""), h("td", {}, d.owners || h("span", { class: "muted-text" }, "not set")),
        h("td", {}, d.fabric_domain || h("span", { class: "muted-text" }, "")),
        h("td", {}, adminOnly(h("span", { class: "row tight" },
          btn("Edit", null, () => domainDialog(o, d, rerender), "tool small"),
          btn("Delete", null, async () => {
            if (!(await confirmDialog(`Delete ${d.name}?`, "Only a domain no project or subdomain uses can be deleted.", "Delete"))) return;
            if (await platformCall("platform_delete_domain", { id: d.id }, "Not deleted")) rerender();
          }, "tool small danger"))))))))
      : h("p", { class: "muted-text" }, "No domains yet.")));
}

async function platformGlossaryTab(body) {
  const [terms, domains] = await Promise.all([platformCall("platform_glossary"), platformCall("platform_domains")]);
  if (!terms || !stillHere("glossary")) return;
  const rerender = () => platformGlossaryTab(body);
  const me = platformPage.me || {};
  const mayEdit = (t) => me.admin || (t?.domain_id && me.domains_owned.includes(t.domain_id));
  const canAdd = me.admin || me.domains_owned?.length;
  const name = (id) => (domains || []).find((d) => d.id === id)?.name || "";
  const edit = (t) => {
    const term = h("input", { value: t?.term || "", disabled: t ? true : null });
    const definition = h("textarea", { rows: 3 }, t?.definition || "");
    const owner = h("input", { value: t?.owner || "" });
    const domain = h("select", {}, me.admin ? h("option", { value: "" }, "Everyone (shared)") : null,
      (domains || []).filter((d) => me.admin || me.domains_owned.includes(d.id)).map((d) => h("option", { value: d.id, selected: d.id === t?.domain_id ? true : null }, d.name)));
    showDialog(t ? t.term : "New glossary term", h("div", {}, h("label", { class: "field" }, "Term", term),
      h("label", { class: "field" }, "Definition", definition), h("label", { class: "field" }, "Domain", domain), h("label", { class: "field" }, "Owner", owner)), [
      btn("Cancel", null, closeDialog, "tool outline"),
      btn("Save", "save", async () => {
        if (await platformCall("platform_save_term", { term: { term: term.value.trim(), definition: definition.value.trim(), owner: owner.value.trim(), domain_id: domain.value } }, "Not saved")) {
          closeDialog(); rerender();
        }
      }, "tool primary")]);
  };
  body.replaceChildren(h("div", { class: "card pad" },
    h("div", { class: "row" }, h("b", { class: "grow" }, "Business glossary"), canAdd ? btn("New term", "add", () => edit(null), "tool primary small") : null),
    h("p", { class: "muted-text small" }, "Shared words with one meaning, like Active customer. Platform admins keep shared terms; a domain's owners keep its own."),
    terms.length ? h("table", { class: "list" }, h("thead", {}, h("tr", {}, ["Term", "Definition", "Domain", "Owner"].map((x) => h("th", {}, x)))),
      h("tbody", {}, terms.filter((t) => matches(`${t.term} ${t.definition}`)).map((t) => h("tr", { class: mayEdit(t) ? "click" : null, onclick: mayEdit(t) ? () => edit(t) : null },
        h("td", {}, h("b", {}, t.term)), h("td", {}, t.definition), h("td", {}, name(t.domain_id) || h("span", { class: "muted-text" }, "shared")), h("td", {}, t.owner)))))
      : h("p", { class: "muted-text" }, "No terms yet.")));
}

async function platformAuditTab(body) {
  const rows = await platformCall("platform_audit", { limit: 200 }, "Could not read the audit");
  if (!rows || !stillHere("audit")) return;
  body.replaceChildren(h("div", { class: "card pad" }, h("b", {}, "Every change in the catalog, newest first"),
    h("p", { class: "muted-text small" }, "Recorded by the platform as the person Fabric says made the change."), auditList(rows)));
}

// ---------- settings: where the platform is, and who its admins are ----------

async function platformSettingsTab(body) {
  const [section, admins] = await Promise.all([platformSection(), platformCall("platform_admins")]);
  if (!stillHere("settings")) return;
  const rerender = () => platformSettingsTab(body);
  const me = platformPage.me || {};
  const add = h("input", { placeholder: "ana@contoso.com", "aria-label": "New admin" });
  body.replaceChildren(section, h("section", { class: "card pad" },
    h("h2", {}, "Platform admins"),
    h("p", { class: "muted-text small" }, "The central team. They create domains, key tables and sources, keep shared glossary terms, and decide requests. Admins are kept in every environment's platform."),
    me.can_claim ? h("div", { class: "row" }, h("span", { class: "grow" }, "This platform has no admin yet."), btn("Become its first admin", "shield", claimAdmin, "tool primary")) : null,
    (admins || []).length ? h("table", { class: "list" }, h("tbody", {}, admins.map((a) => h("tr", {},
      h("td", {}, h("b", {}, a.user_name), a.user_name === me.user ? h("span", { class: "muted-text" }, " (you)") : null),
      h("td", { class: "muted-text small" }, `Added ${relTime(a.added_at)}${a.added_by && a.added_by !== a.user_name ? ` by ${a.added_by}` : ""}`),
      h("td", {}, adminOnly(btn("Remove", null, async () => {
        if (!(await confirmDialog(`Remove ${a.user_name} as a platform admin?`, "They keep reading everything and can still send requests.", "Remove"))) return;
        if (await platformCall("platform_remove_admin", { user: a.user_name }, "Not removed")) rerender();
      }, "tool small danger")))))))
      : null,
    adminOnly(h("div", { class: "row" }, add, btn("Add admin", "add", async () => {
      if (!add.value.trim()) return add.focus();
      if (await platformCall("platform_add_admin", { user: add.value.trim() }, "Not added")) rerender();
    }, "tool outline small")))));
}

registerFeature({
  id: "platform", preview: true,
  rail: { label: "Platform", icon: "building", order: 55 },
  views: { platform: renderPlatform },
  commands: [
    { label: "Platform", icon: "building", keywords: "central team landing zone keys domains", preview: true, run: () => goPlatform("overview") },
    { label: "Landing zone", icon: "folderOpen", keywords: "platform sources raw files", preview: true, run: () => goPlatform("landing") },
    { label: "Business keys", icon: "database", keywords: "platform surrogate key tables", preview: true, run: () => goPlatform("keys") },
    { label: "Requests", icon: "person", keywords: "platform approve access key table", preview: true, run: () => goPlatform("requests") },
  ],
});

// ---------- OneLake > Data products: every team's products, for everyone ----------

const productsPage = { query: "", domain: "all", open: null };

async function renderDataProducts() {
  const pp = productsPage;
  const list = h("div", {}, h("p", { class: "muted-text" }, "Loading data products..."));
  const search = h("input", { class: "catalog-search", placeholder: "Search products, ports and owners", value: pp.query, "aria-label": "Search data products" });
  setView(h("div", { class: "page monitor onelake" }, onelakeTabs(),
    h("section", { class: "card pad" },
      h("div", { class: "monitor-band" }, h("h2", {}, "Data products"),
        h("span", { class: "muted-text" }, "What every team shares, from the platform's catalog"), h("span", { class: "spacer" }), search,
        btn("Refresh", "sync", () => render(), "tool outline")),
      list)));
  const out = await platformCall("platform_products", {}, "Could not read the catalog");
  if (!out || state.view !== "onelake" || catalogPage.tab !== "products") return;
  const domains = [...new Set(out.products.map((p) => p.domain_id).filter(Boolean))].sort();
  const draw = () => {
    const q = pp.query.toLowerCase();
    const shown = out.products.filter((p) => (pp.domain === "all" || p.domain_id === pp.domain)
      && (!q || `${p.name} ${p.description} ${p.owner} ${p.project} ${p.ports.map((x) => x.port).join(" ")}`.toLowerCase().includes(q)));
    const action = (p) => p.linked ? h("span", { class: "pill-s pass" }, "Linked")
      : p.can_link ? btn("Link", "databaseLink", async () => {
        const o = await meshOverview();
        if (o) linkDialog(o, { product: p.id, port: p.ports[0]?.port }, () => goMeta("links"));
      }, "tool primary small")
        : p.why === "request" ? btn("Request access", null, () => requestDialog("access", p.id, draw, { title: p.name, owner: p.owner }), "tool outline small")
          : p.why === "requested" ? h("span", { class: "pill-s wrn" }, "Requested") : h("span", { class: "muted-text small" }, p.why);
    list.replaceChildren(...[
      h("div", { class: "row", role: "group", "aria-label": "Domain" },
        ["all", ...domains].map((d) => h("button", { class: `chip${pp.domain === d ? " on" : ""}`, onclick: () => { pp.domain = d; draw(); } }, d === "all" ? "All domains" : d))),
      out.project ? null : h("p", { class: "muted-text small" }, "Open a metadata project to link to a product or ask for access."),
      shown.length ? h("table", { class: "list" },
        h("thead", {}, h("tr", {}, ["Product", "Domain", "Owner", "Open to", "Ports", "Readers", ""].map((x) => h("th", {}, x)))),
        h("tbody", {}, shown.map((p) => h("tr", {},
          h("td", {}, h("b", {}, p.name), h("span", { class: "muted-text small" }, ` v${p.version}`),
            p.status === "deprecated" ? h("span", { class: "pill-s wrn" }, `Deprecated${p.deprecated_on ? ` ${p.deprecated_on}` : ""}`) : null,
            h("div", { class: "muted-text small" }, p.description || ""), h("div", { class: "muted-text small mono" }, p.id)),
          h("td", {}, p.domain_id || h("span", { class: "muted-text" }, "")),
          h("td", {}, p.owner || h("span", { class: "muted-text" }, "not set")),
          h("td", {}, ACCESS_LABEL[p.access] || p.access),
          h("td", { class: "mono small" }, [...new Set(p.ports.map((x) => x.port))].join(", ")),
          h("td", {}, (p.consumers || []).length || h("span", { class: "muted-text" }, "none")),
          h("td", {}, action(p))))))
        : h("p", { class: "muted-text" }, out.products.length ? "Nothing matches." : "No team has published a data product yet.")].filter(Boolean));
  };
  search.addEventListener("input", () => { pp.query = search.value; draw(); });
  draw();
}
