// The Platform page: the central team's setup. One landing zone, one key system, the glossary every
// team shares, governance, audit and the platform's settings. Domains (below) and requests are on
// Data mesh, where every team works: Data mesh > Domains and Data mesh > Requests (mesh-requests.js).
// Platform admins change these; everyone else sees them and asks. The rules are enforced by the
// platform itself (central/core.py), so this page only hides buttons a person can't use.

const PLATFORM_TABS = [
  ["overview", "Overview"], ["landing", "Landing zone"], ["keys", "Business keys"],
  ["glossary", "Glossary"], ["governance", "Governance"], ["audit", "Audit"], ["settings", "Settings"],
];
// The tab bar: four tabs, the way Data mesh groups its pages. Set up and Govern each hold several pages,
// picked on the view row's switcher. Every tab above is still a page of its own, so "Platform > Business keys" still works.
const PLATFORM_TAB_BAR = [
  ["overview", "Overview"],
  ["setup", "Set up", ["landing", "keys", "glossary"]],
  ["govern", "Govern", ["governance", "audit"]],
  ["settings", "Settings"],
];
const REQUEST_KIND = { key_table: "Key table", source: "Source", access: "Access", shortcut: "Shortcut",
  workspace: "Workspace", workspace_access: "Workspace access", domain: "Domain", exception: "Exception", data: "Data",
  join_domain: "Join domain" };
const REQUEST_STATUS = { waiting: "Waiting", approved: "Approved", declined: "Declined", withdrawn: "Withdrawn" };
const platformPage = { tab: "overview", env: "dev", source: null, me: null };

function goPlatform(tab) {
  // Domains and Requests moved to Data mesh; older links still land there.
  if (tab === "domains" || tab === "requests") return goMeta(tab);
  platformPage.tab = tab || platformPage.tab;
  go("platform");
}

function platformCall(method, params = {}, title) {
  return metaCall(method, params, title);
}

function adminOnly(node) {
  return platformPage.me?.admin ? node : null;
}

async function renderPlatform() {
  const p = platformPage;
  if (p.tab === "domains" || p.tab === "requests") { const tab = p.tab; p.tab = "overview"; return goMeta(tab); }
  const body = h("div", { class: "meta-body" });
  // Labelled, so it doesn't read as more of Set up's page switcher beside it.
  const envSeg = h("div", { class: "row tight platform-env" }, h("span", { class: "muted-text small" }, "Environment"),
    h("div", { class: "seg", role: "group", "aria-label": "Environment" },
      MESH_ENVS.map((env) => h("button", { class: p.env === env ? "on" : null, onclick: () => { p.env = env; render(); } }, ENV_LABEL[env]))));
  const who = h("p", { class: "sub" }, "Loading...");
  // The page layout every tabbed page shares (docs/ui-guidelines.md): title, tabs, then the view row with
  // the environment on the left and Refresh on the right. Only Overview, the landing zone and business
  // keys are kept per environment; the glossary, governance and audit are organization-wide, so they
  // show no environment picker.
  const perEnv = p.tab === "overview" || (["landing", "keys"].includes(p.tab) && !(p.tab === "landing" && p.landingCentral) && !(p.tab === "keys" && p.keysShared));
  setView(h("div", { class: "page" },
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Platform ", previewTag()), who)),
    platformTabBar(),
    h("div", { class: "row mesh-view-row" }, platformGroupSeg(), perEnv ? envSeg : null, h("span", { class: "spacer" }), btn("Refresh", "sync", () => render(), "tool outline")),
    body));
  p.me = await attempt(() => rpc("platform_whoami"), "Could not reach the platform");
  // Owner coverage's last-used date, only where policy turns on platform_presence; the engine notes it in the background.
  if (p.me && !platformPage.seenNoted) { platformPage.seenNoted = true; rpc("platform_note_seen", {}).catch(() => {}); }
  if (state.view !== "platform") return;
  if (!p.me) { body.replaceChildren(h("p", { class: "muted-text" }, "The platform shows once the local engine is running.")); return; }
  // What the page is for folds into the info drawer; the claim link stays on the page.
  who.classList.toggle("page-intro", !p.me.can_claim);
  who.replaceChildren(...[p.me.admin ? "You're a platform admin: you create domains, key tables and sources, write governance policies, and decide requests. "
    : p.me.can_claim ? "This platform has no admin yet. "
      : "Domain teams see everything here and ask for what they need on Data mesh > Requests. ",
  p.me.can_claim ? h("a", { href: "#", onclick: (e) => { e.preventDefault(); claimAdmin(); } }, "Become its first admin") : null,
  p.me.can_claim ? ". " : null, learnMore("platform")].filter(Boolean));
  const tabs = { overview: platformOverviewTab, landing: platformLandingTab, keys: platformKeysTab,
    glossary: platformGlossaryTab, governance: platformGovernanceTab, audit: platformAuditTab, settings: platformSettingsTab };
  await (tabs[p.tab] || platformOverviewTab)(body);
}

function platformOpen(id) {
  if (id === "governance") govPage.open = null;
  goPlatform(id);
}

function platformGroup() {
  return PLATFORM_TAB_BAR.find(([id, , items]) => items ? items.includes(platformPage.tab) : id === platformPage.tab) || PLATFORM_TAB_BAR[0];
}

function platformTabBar() {
  const [here] = platformGroup();
  return h("div", { class: "subtabs", role: "tablist", "aria-label": "Platform" }, PLATFORM_TAB_BAR.map(([id, text, items]) =>
    h("button", { class: `tab${here === id ? " active" : ""}`, role: "tab", "aria-selected": String(here === id),
      onclick: () => platformOpen(items ? items[0] : id) }, text)));
}

// A tab with several pages picks them on the view row, as Data mesh does.
function platformGroupSeg() {
  const [, text, items] = platformGroup();
  if (!items) return null;
  const label = (id) => PLATFORM_TABS.find(([t]) => t === id)[1];
  return h("div", { class: "seg platform-pages", role: "group", "aria-label": text }, items.map((id) =>
    h("button", { class: platformPage.tab === id ? "on" : null, "aria-pressed": String(platformPage.tab === id), onclick: () => platformOpen(id) }, label(id))));
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
  // One row per environment, so the card stays narrow enough to sit beside the landing zone.
  const health = h("table", { class: "list" }, h("tbody", {}, MESH_ENVS.map((env) => {
    const e = o.status.environments[env];
    return h("tr", {}, h("td", {}, h("b", {}, ENV_LABEL[env])),
      h("td", { class: "grow" }, e.kind === "fabric" ? h("span", { class: "pill-s pass" }, e.workspace_name || "In Fabric") : h("span", { class: "pill-s neu" }, "On this PC")),
      h("td", {}, e.kind === "fabric" ? null : o.status.source === "policy"
        ? h("span", { class: "muted-text small" }, "Set by your organization's policy")
        : (adminOnly(btn("Set up in Fabric", null, () => platformSetupDialog(env, o.status), "tool link small"))
          || h("span", { class: "muted-text small" }, "A platform admin sets it up"))));
  })));
  body.replaceChildren(withHowItWorks([
    o.problems.length || o.landing.problem ? h("div", { class: "problems" }, h("b", {}, "Couldn't read everything"), gateList([...o.problems, o.landing.problem].filter(Boolean))) : null,
    h("div", { class: "tiles plat-stats" },
      platStat(c.domains, "Domains", c.subdomains ? `${c.subdomains} subdomain${c.subdomains === 1 ? "" : "s"}` : "", () => goMeta("domains")),
      platStat(c.projects, "Projects joined", c.teams ? `${c.teams} team${c.teams === 1 ? "" : "s"}, joined to a domain` : "joined to a domain in Data mesh",
        () => goMeta("domains")),
      platStat(c.key_tables, "Business key tables", `${c.keys.toLocaleString()} keys in ${ENV_LABEL[o.env]}`, () => goPlatform("keys")),
      platStat(c.products, "Data products", `${c.published} published`, () => openProducts()),
      platStat(c.requests, "Requests waiting", o.needs_you.length ? `${o.needs_you.length} for you` : "", () => goMeta("requests"))),
    h("div", { class: "plat-two" },
      h("div", { class: "card pad" }, h("b", {}, o.me.admin ? "Needs you" : "Your requests"),
        requestTable(o.me.admin || o.needs_you.length ? o.needs_you : o.mine, () => render(), true),
        h("b", {}, "Each environment's platform"), health),
      h("div", { class: "card pad" },
        h("div", { class: "row" }, h("b", { class: "grow" }, "Landing zone"),
          // The environment picker above the page chooses which one; one landing workspace serves them all.
          h("span", { class: "muted-text small" }, o.landing.central ? "Read by Dev, Test and Prod" : ENV_LABEL[o.env]),
          btn("Open", null, () => goPlatform("landing"), "tool link small")),
        late.length ? h("p", { class: "fail-text small" }, `${late.map((s) => s.name).join(", ")} ${late.length === 1 ? "is" : "are"} late.`) : null,
        (o.landing.sources || []).length ? h("table", { class: "list" },
          h("thead", {}, h("tr", {}, ["Source", "Last file", "Read by"].map((x) => h("th", {}, x)))),
          h("tbody", {}, o.landing.sources.map((s) => h("tr", {},
            h("td", {}, h("b", {}, s.name)),
            h("td", {}, s.late ? h("span", { class: "pill-s fail" }, s.newest ? `late, ${relTime(s.newest)}` : "nothing yet") : s.newest ? when(s.newest) : h("span", { class: "muted-text" }, "nothing yet")),
            h("td", {}, s.readers.length ? `${s.readers.length} project${s.readers.length === 1 ? "" : "s"}` : h("span", { class: "muted-text" }, "none"))))))
          : h("p", { class: "muted-text" }, "No sources in the landing zone yet."),
        h("b", {}, "Recent changes"),
        auditList(o.recent, true))),
    previewShown() ? ownerCoverageCard() : null].filter(Boolean),
  howItWorks("platform.overview", {
    reads: "Each environment's platform: its catalog in SQL_Platform in Fabric, or a file on this PC until that environment has a platform. It reads when the page opens and when you press Refresh.",
    changes: "Requests waiting for you can be approved or declined here; approving one makes what it asks for first, as you: a shortcut in OneLake, a workspace or a workspace role in Fabric, a domain or a policy exception in the platform. Key tables and sources change on their own tabs and domains on Data mesh > Domains, and only platform admins can change them. Every request is on Data mesh > Requests.",
    results: "Everyone who uses this platform sees the same counts, requests and recent changes. Each change is kept on the Audit tab. Owners reads every domain's, data product's and source's owners, and when each owner last used the platform; opening the platform from the app records that you used it today (catalog.person), once a day.",
    anchor: "platform",
  })));
}

// Platform > Overview > Owners (Preview): what has no owner, no second owner, or an owner who has gone quiet.
const COVERAGE_KIND = { domain: "Domain", product: "Data product", source: "Source" };
function ownerCoverageCard() {
  const card = h("div", { class: "card pad owner-coverage" }, h("b", {}, "Owners"), loading());
  const open = (g) => (g.opens === "products" ? () => openProducts() : () => goPlatform(g.opens));
  rpc("platform_owner_coverage", {}).then((c) => {
    if (c.available === false) {
      card.replaceChildren(h("b", {}, "Owners"), h("p", { class: "muted-text small" }, c.problem));
      return;
    }
    const head = h("div", { class: "row tight" }, h("b", { class: "grow" }, "Owners"), previewTag(),
      h("span", { class: "muted-text small" }, `${c.covered} of ${c.checked} covered`));
    if (!c.gaps.length) {
      card.replaceChildren(head, h("p", { class: "muted-text small" }, c.checked
        ? (c.presence === false ? "Every domain, data product and source has two owners." : "Every domain, data product and source has two owners who have used the platform lately.")
        : "No domains, data products or sources yet."));
      return;
    }
    card.replaceChildren(head,
      h("p", { class: "muted-text small" }, c.presence === false
        ? "Each should have an owner and a second one to cover. Whether owners have gone quiet isn't checked: your organization hasn't turned on platform_presence."
        : `Each should have an owner and a second one to cover, who have used the platform in the last ${c.quiet_days} days.`),
      h("table", { class: "list" },
        h("thead", {}, h("tr", {}, ["What", "Owners", "Why it's listed", ""].map((x) => h("th", {}, x)))),
        h("tbody", {}, c.gaps.map((g) => h("tr", { "data-owned": `${g.kind}:${g.id}` },
          h("td", {}, h("span", { class: "pill-s info" }, COVERAGE_KIND[g.kind] || g.kind), " ", h("b", {}, g.name)),
          h("td", {}, g.owners.length ? g.owners.join(", ") : h("span", { class: "muted-text" }, "none"),
            g.unchecked.length ? h("div", { class: "muted-text small" }, "Names, not sign-ins, can't be checked for being quiet") : null),
          h("td", {}, g.problems.map((p) => h("div", { class: p === "No owner" ? "fail-text small" : "small" }, p))),
          h("td", {}, btn("Open", null, open(g), "tool link small")))))));
  }).catch((err) => card.replaceChildren(h("b", {}, "Owners"), h("p", { class: "fail-text small" }, trustError(err))));
  return card;
}

// compact: two lines per change, for a narrow card (Overview); the Audit tab keeps the table.
function auditList(rows, compact = false) {
  if (compact) {
    return rows.length ? h("div", { class: "audit-compact" }, rows.map((a) => h("div", { class: "audit-row" },
      h("div", { class: "audit-what" }, a.action, " ", h("span", { class: "mono small", title: a.target }, a.target)),
      h("div", { class: "muted-text small" }, a.by_user || "someone", " · ", when(a.at)))))
      : h("p", { class: "muted-text" }, "Nothing yet.");
  }
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
  if (!!l.central !== !!p.landingCentral) { p.landingCentral = !!l.central; render(); return; }  // one landing workspace: no environment picker
  if (!l.sources.find((s) => s.name === p.source)) p.source = l.sources[0]?.name || null;
  const sel = l.sources.find((s) => s.name === p.source);
  const rerender = () => platformLandingTab(body);
  const late = (s) => s.late ? h("span", { class: "pill-s fail" }, s.newest ? `late, ${relTime(s.newest)}` : "nothing yet")
    : s.newest ? when(s.newest) : h("span", { class: "muted-text" }, "nothing yet");
  const pick = (s) => ({ class: `click${s.name === p.source ? " picked" : ""}`, onclick: () => { p.source = s.name; rerender(); } });
  const addSource = [adminOnly(btn("Add source", "add", () => sourceDialog(null, rerender, l.system_types, l.central ? l.zones : []), "tool outline small")),
    platformPage.me?.admin ? null : btn("Request a source", "add", () => requestDialog("source", "", rerender), "tool outline small")];
  let main;
  if (l.central) {
    main = h("div", { class: "card pad" },
      h("div", { class: "row" }, h("b", { class: "grow" }, "Landing zones ", previewTag()), ...addSource,
        adminOnly(btn("New landing zone", "add", () => landingZoneDialog(l, rerender), "tool primary small"))),
      h("p", { class: "muted-text small" }, "One landing workspace, ", h("b", {}, l.where), ", read by Dev, Test and Prod. Raw source data isn't per environment, so there's no environment switch here. ",
        "Each zone is its own lakehouse; only the central team writes to a landing zone. This is the one list of source systems for every project."),
      l.problem ? h("p", { class: "fail-text small" }, l.problem) : null,
      switchReadsLine(l, rerender),
      h("div", { class: "zone-cards" }, l.zones.map((z) => zoneCard(z, l, rerender))),
      l.sources.length ? h("table", { class: "list" },
        h("thead", {}, h("tr", {}, ["Source", "Zone", "Lands in", "Expected", "Last arrival", "Read by"].map((x) => h("th", {}, x)))),
        h("tbody", {}, l.sources.map((s) => h("tr", pick(s),
          h("td", {}, h("b", {}, s.registered ? s.code : s.name), s.registered ? null : h("span", { class: "pill-s wrn" }, "not registered")),
          h("td", {}, s.zone_name || "Shared"),
          h("td", { class: "mono small" }, `Files/${s.name}/<entity>/`),
          h("td", {}, s.expected_hours ? `every ${s.expected_hours} h` : h("span", { class: "muted-text" }, "not set")),
          h("td", {}, late(s)),
          h("td", {}, s.readers.length || h("span", { class: "muted-text" }, "none"))))))
        : emptyState({ title: "No sources yet", text: "Add a source, pick its zone, then land its files in Files/<source>/<entity>/ of that zone's lakehouse." }),
      h("p", { class: "muted-text small" }, "Entities keep reading ", h("span", { class: "mono" }, 'landing = "<source>/<entity>/*.csv"'),
        ": the platform knows which zone each source is in, so nothing in a project changes when a source moves zone. Dev on this PC still reads the local copy in the platform's landing folder."));
  } else {
    main = h("div", { class: "card pad" },
      h("div", { class: "row" }, h("b", { class: "grow" }, "Source systems"), ...addSource),
      h("p", { class: "muted-text small" }, "Each source lands once, in ", h("span", { class: "mono" }, "Files/<source>/<entity>/"), " of LH_Landing ",
        l.kind === "fabric" ? `in ${l.where}` : h("span", {}, "on this PC (", h("span", { class: "mono" }, l.where), ")"),
        ". Domain teams read it in their own Bronze; only the central team writes here. ",
        "This is the one list of source systems for every project: each keeps its id here, and projects pick theirs in Data mesh > Source systems."),
      l.problem ? h("p", { class: "fail-text small" }, l.problem) : null,
      l.sources.length ? h("table", { class: "list" },
        h("thead", {}, h("tr", {}, ["Source", "Id", "Entities", "Last arrival", "Expected", "Read by"].map((x) => h("th", {}, x)))),
        h("tbody", {}, l.sources.map((s) => h("tr", pick(s),
          h("td", {}, h("b", {}, s.registered ? s.code : s.name), s.registered ? null : h("span", { class: "pill-s wrn" }, "not registered")),
          h("td", { class: "mono" }, s.source_id ?? ""),
          h("td", {}, s.entities.length),
          h("td", {}, late(s)),
          h("td", {}, s.expected_hours ? `every ${s.expected_hours} h` : h("span", { class: "muted-text" }, "not set")),
          h("td", {}, s.readers.length || h("span", { class: "muted-text" }, "none"))))))
        : emptyState({ title: "The landing zone is empty",
          text: "Add a source, then land its files in Files/<source>/<entity>/." }),
      moveLandingCard(l, rerender));
  }
  body.replaceChildren(withHowItWorks(h("div", { class: "keys-grid" }, main, sel ? sourceDetail(sel, l, rerender) : null),
    howItWorks("platform.landing", {
      reads: "The source systems and landing zones in the platform's catalog, and the files in each zone's lakehouse in the landing workspace, or, before that's set up, in this environment's LH_Landing (or this PC's landing folder). It reads when the tab opens; listing files in OneLake uses capacity.",
      changes: "Add source, Edit and Remove change a source system in the catalog, and Edit sets its zone; its files stay. " +
        "Set up the landing workspace (Move to one landing workspace) creates the workspace and its LH_Landing in Fabric, unless they're there, and records the Shared zone in the catalog. Files in each environment's LH_Landing aren't copied, and nothing is deleted. Switch reads to the landing workspace records in the catalog that every environment now reads Shared sources from it; until then they read their own LH_Landing. " +
        "New landing zone creates LH_Landing_<Name> in that workspace and records the zone; a secure zone's OneLake data access role and sensitivity label aren't made here: set them in Fabric. " +
        "Remove zone takes a zone with no sources out of the catalog; its lakehouse stays. Request a source adds a request. Only platform admins make these changes.",
      results: "Entities keep their landing paths. With one landing workspace, Dev, Test and Prod read each source in a new zone from that zone's lakehouse there, and Shared sources too once reads are switched; and a zone Dev and Test don't read stops their runs with a reason. Dev on this PC reads the local landing folder for every zone. Each change is kept on the Audit tab.",
      anchor: "platform",
    })));
}

// Until an admin switches reads, Shared sources are read from each environment's own LH_Landing, so
// the move can't break a Prod run before its files are copied. Other zones are new and read at once.
function switchReadsLine(l, rerender) {
  const shared = l.zones.find((z) => z.id === "shared");
  if (!shared || shared.switched_at) return null;
  return h("div", { class: "callout git-warn small" },
    h("span", { class: "grow" }, h("b", {}, "The Shared zone isn't read yet. "),
      "Dev, Test and Prod still read Shared sources from their own LH_Landing, so runs keep working while files are copied to ", l.where,
      ". Other zones are read from the landing workspace already."),
    adminOnly(btn("Switch reads to the landing workspace", null, async () => {
      if (!(await confirmDialog("Switch reads to the landing workspace?", `Every environment then reads the Shared zone in ${l.where}. Copy the files runs still need there first: Prod's LH_Landing isn't copied for you.`, "Switch reads", { danger: true }))) return;
      if (await platformCall("platform_switch_landing_reads", {}, "Reads not switched")) { toast(`Every environment now reads the Shared zone in ${l.where}`, false, { log: true }); rerender(); }
    }, "tool outline small")));
}

function zoneDevTest(z) {
  return z.devtest === "read" ? "Dev, Test and Prod read it." : "Prod reads it. Dev and Test don't.";
}

function zoneCard(z, l, rerender) {
  const n = l.sources.filter((s) => (s.zone || "shared") === z.id);
  const lateN = n.filter((s) => s.late).length;
  return h("div", { class: `card pad zone-card${z.secure ? " secure" : ""}` },
    h("div", { class: "row" }, h("b", { class: "grow" }, z.name), z.secure ? h("span", { class: "pill-s warn" }, "Secure") : h("span", { class: "mono small muted-text" }, z.lakehouse_name)),
    h("div", { class: "muted-text small" }, [z.secure ? z.lakehouse_name : null, z.domain_id ? `Domain ${z.domain_id}` : z.id === "shared" ? "Every domain" : null,
      z.readers.length ? `Readers: ${z.readers.join(", ")}` : z.id === "shared" ? "Readers: everyone with Viewer on the workspace" : null,
      z.label ? `Label: ${z.label}` : null].filter(Boolean).join(". ")),
    h("div", { class: "small" }, `${n.length} source${n.length === 1 ? "" : "s"}${lateN ? `, ${lateN} late` : ""}. ${zoneDevTest(z)}`),
    z.secure ? h("p", { class: "warn-text small" }, `Set in Fabric for now: a OneLake data access role on ${z.lakehouse_name} for ${z.readers.length ? z.readers.join(", ") : "its readers"} only`,
      z.label ? `, and the sensitivity label ${z.label} on it.` : ".") : null,
    z.id !== "shared" && !n.length ? adminOnly(btn("Remove zone", "delete", async () => {
      if (!(await confirmDialog(`Remove the zone ${z.name}?`, `Only the catalog's record goes. ${z.lakehouse_name} and its files stay in ${l.where}; delete them in Fabric if you don't need them.`, "Remove zone", { danger: true }))) return;
      if (await platformCall("platform_delete_landing_zone", { id: z.id }, "Not removed")) rerender();
    }, "tool link small")) : null);
}

function zoneLakehouse(name) {
  return `LH_Landing_${String(name || "").trim().replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "")}`;
}

async function landingZoneDialog(l, done) {
  const domains = (await platformCall("platform_domains", {}, "Could not read the domains")) || [];
  const name = h("input", { placeholder: "HR-Secure" });
  const domain = h("select", {}, h("option", { value: "" }, "None"), domains.map((d) => h("option", { value: d.id }, d.parent_id ? `  ${d.name}` : d.name)));
  const readers = h("input", { placeholder: "HR Secure (group)" });
  const secure = h("input", { type: "checkbox" });
  const label = h("input", { placeholder: "Highly Confidential" });
  const prodOnly = h("input", { type: "radio", name: "devtest", checked: true });
  const likeProd = h("input", { type: "radio", name: "devtest" });
  const note = h("p", { class: "muted-text small" });
  const explain = () => {
    const lh = name.value.trim() ? zoneLakehouse(name.value) : "LH_Landing_<Name>";
    note.replaceChildren(`Creates ${lh} in ${l.where}. Only the central team writes to a landing zone. `,
      secure.checked ? `Its OneLake data access role (the readers above only) and its sensitivity label aren't made here yet: set them on ${lh} in Fabric once it's created.` : "");
  };
  name.oninput = explain;
  secure.onchange = explain;
  explain();
  showDialog("New landing zone", h("div", {},
    h("label", { class: "field" }, "Name", name), h("label", { class: "field" }, "Domain", domain),
    h("label", { class: "field" }, "Who reads it", readers),
    h("label", { class: "check" }, secure, h("span", {}, h("b", {}, "Secure: PII, PHI or other sensitive data"),
      h("div", { class: "muted-text small" }, "Its own lakehouse with a OneLake data access role for the readers above only. Nobody gets it through workspace Viewer."))),
    h("label", { class: "field" }, "Sensitivity label", label),
    h("div", { class: "field" }, "Dev and Test", h("div", { class: "row wrap" },
      h("label", { class: "check" }, prodOnly, "Don't read it (Prod only)"), h("label", { class: "check" }, likeProd, "Read it like Prod"))),
    note), [
    btn("Cancel", null, closeDialog, "tool outline"),
    btn("Create zone", "add", async () => {
      if (!name.value.trim()) { name.focus(); return; }
      const lh = zoneLakehouse(name.value);
      const zone = { name: name.value.trim(), domain_id: domain.value, readers: readers.value, secure: secure.checked,
        label: label.value.trim(), devtest: likeProd.checked ? "read" : "none" };
      if (!(await confirmDialog(`Create the landing zone ${zone.name}?`, `This creates the lakehouse ${lh} in ${l.where} in Fabric (or keeps it if it's there) and records the zone in the catalog.${zone.secure ? ` Set its OneLake data access role and sensitivity label on ${lh} in Fabric afterwards.` : ""}`, "Create zone"))) return;
      const res = await platformCall("platform_create_landing_zone", { zone }, "Zone not created");
      if (res) { toast(`${res.created ? "Created" : "Kept"} ${res.lakehouse}; the zone ${zone.name} is in the catalog`, false, { log: true }); done(); }
    }, "tool primary")]);
  name.focus();
}

// The one-time move from each environment's LH_Landing to one landing workspace. In this release it
// creates the workspace and the Shared zone only: files aren't copied and nothing is deleted.
function moveLandingCard(l, rerender) {
  if (l.policy) return h("p", { class: "muted-text small" }, "Your organization's policy sets the platform, so one landing workspace is added to that policy.");
  return h("div", { class: "callout" }, icon("databaseArrowRight", 18),
    h("span", { class: "grow" }, h("b", {}, "One landing workspace for Dev, Test and Prod ", previewTag()),
      h("div", { class: "small" }, "Raw source data isn't per environment: one workspace with a Shared zone, plus zones per domain and secure zones for PII or PHI with their own readers.")),
    platformPage.me?.admin ? btn("Move to one landing workspace", null, () => landingSetupDialog(l, rerender), "tool primary small")
      : h("span", { class: "muted-text small" }, "A platform admin sets it up"));
}

async function landingSetupDialog(l, done) {
  if (!state.user && !(await signIn())) return;
  const name = h("input", { value: l.landing?.workspace_name || l.default_landing_name });
  const capacity = h("select", {}, h("option", { value: "" }, "Loading capacities..."));
  showDialog("Move to one landing workspace", h("div", {},
    h("p", { class: "text" }, "Today each environment has its own LH_Landing. This creates the workspace below with one Shared zone, LH_Landing, and records it in the catalog. Shared sources keep reading each environment's own LH_Landing until you choose Switch reads to the landing workspace, so runs keep working while you copy files over."),
    h("label", { class: "field" }, "Workspace", name), h("label", { class: "field" }, "Capacity", capacity),
    h("div", { class: "callout git-warn small" }, h("span", {}, "Files already in each environment's LH_Landing aren't copied yet: they stay where they are. Copy what runs still need into the new workspace before you switch reads. Nothing is deleted: the old LH_Landing lakehouses stay until you remove them.")),
    h("p", { class: "muted-text small" }, "Entities keep the same landing paths. Reading and writing OneLake uses capacity.")), [
    btn("Cancel", null, closeDialog, "tool outline"),
    btn("Move", null, async () => {
      const ws = name.value.trim() || l.default_landing_name;
      const cap = capacity.value;
      if (!(await confirmDialog(`Create ${ws}?`, `This creates the workspace ${ws} in Fabric${cap ? "" : " with no capacity"} (or uses the one with that name), and its lakehouse LH_Landing, then records the Shared zone in the catalog. Files aren't copied and nothing is deleted.`, "Create"))) return;
      const steps = h("div", {}, h("p", { class: "muted-text" }, "Setting up the landing workspace..."));
      showDialog("Move to one landing workspace", steps, [btn("Close", null, () => { closeDialog(); done(); }, "tool primary")]);
      const res = await attempt(() => rpc("platform_landing_setup", { workspace_name: ws, capacity_id: cap }), "Landing workspace not set up");
      if (!res) { steps.replaceChildren(h("p", { class: "fail-text" }, "Nothing was set up.")); return; }
      steps.replaceChildren(h("ul", { class: "steps-list" }, res.steps.map((st) => h("li", {},
        icon(st.status === "done" ? "ok" : "error", 14), h("b", {}, ` ${st.step}: `), st.detail))),
      h("p", { class: res.ready ? "muted-text" : "fail-text" }, res.ready
        ? "Done. Shared sources still read each environment's LH_Landing until you switch reads; new zones are read from the landing workspace."
        : "Some steps didn't finish, so runs still read each environment's LH_Landing. Fix what they say, then move again."));
      if (res.ready) toast("One landing workspace is set up", false, { log: true });
    }, "tool primary")]);
  const caps = (await attempt(() => rpc("list_capacities"), "Could not list capacities")) || [];
  capacity.replaceChildren(...caps.map((x) => h("option", { value: x.id }, `${x.displayName}${x.sku ? ` (${x.sku})` : ""}`)),
    h("option", { value: "" }, "No capacity (lakehouses need one)"));
}

// The Copy job a request made for this source (scale plan 4.1, Preview).
function copyJobLine(s) {
  const j = s.copy_job;
  const run = btn("Run now...", null, async () => {
    if (!(await confirmDialog(`Run ${j.name} now?`, `It copies ${j.tables} table${j.tables === 1 ? "" : "s"} into the landing zone in Fabric. It uses capacity, and the gateway reads the source.`, "Run now"))) return;
    if (!(await riskGate({ id: `fabric:${j.workspace_id}:${j.id}`, action: "run_item", title: `Run ${j.name} now?`, runLabel: "Run now" }))) return;
    if (await platformCall("run_fabric_item_now", { workspace_id: j.workspace_id, item_id: j.id, job_type: "Execute", name: j.name, item_type: "CopyJob" }, "Not started"))
      toast(`${j.name} started; it shows in Monitor > Runs`, false, { log: true });
  }, "tool outline small");
  return h("div", { class: "copy-job-line" },
    h("div", {}, h("b", {}, "Lands with "), h("span", { class: "mono" }, j.name), " ", previewTag(),
      h("div", { class: "muted-text small" }, `A Copy job made from a request: ${j.tables} table${j.tables === 1 ? "" : "s"}${j.times?.length ? `, daily at ${j.times.join(", ")} UTC` : ", no schedule"}. Changing its tables is a new request; edit the job itself in Fabric.`)),
    adminOnly(run));
}

function sourceDetail(s, l, rerender) {
  const files = h("div", {}, h("p", { class: "muted-text small" }, "Loading files..."));
  platformCall("platform_landing_files", { env: l.env, source: s.name, limit: 8 }).then((fs) => {
    if (!fs) return;
    files.replaceChildren(fs.length ? h("table", { class: "mini" }, h("thead", {}, h("tr", {}, ["File", "Size", "Landed"].map((x) => h("th", {}, x)))),
      h("tbody", {}, fs.map((f) => h("tr", {}, h("td", { class: "mono" }, f.path), h("td", {}, sizeText(f.size)), h("td", {}, when(f.modified))))))
      : h("p", { class: "muted-text" }, "No files yet."));
  });
  const zone = l.central ? l.zones.find((z) => z.id === (s.zone || "shared")) : null;
  return h("div", { class: "card pad side-panel" },
    h("div", { class: "row" }, h("h2", { class: "flush" }, s.title || s.name), h("span", { class: "spacer" }),
      adminOnly(btn(s.registered ? "Edit" : "Register", null, () => sourceDialog(s, rerender, l.system_types, l.central ? l.zones : []), "tool outline small")),
      adminOnly(s.registered ? btn("Remove", "delete", async () => {
        if (!(await confirmDialog(`Remove source ${s.name}?`, "Only the registration goes; files in the landing zone stay. A source a project still reads can't be removed.", "Remove", { danger: true }))) return;
        if (await platformCall("platform_delete_source", { name: s.name }, "Not removed")) rerender();
      }, "tool outline small") : null)),
    s.registered ? h("p", { class: "muted-text small" }, `${s.code} · id ${s.source_id ?? "not given yet"}${s.system_type ? ` · ${s.system_type}` : ""}`) : null,
    zone ? h("p", { class: "muted-text small" }, `In the ${zone.secure ? "secure " : ""}zone ${zone.name}, ${zone.lakehouse_name}. ${zoneDevTest(zone)}`) : null,
    h("p", { class: "muted-text small" }, s.description || "", s.owner ? ` Owner ${s.owner}.` : ""),
    s.copy_job ? copyJobLine(s) : null,
    h("b", {}, "Entities"),
    s.entities.length ? h("table", { class: "mini" }, h("thead", {}, h("tr", {}, ["Entity", "Files", "Size", "Newest"].map((x) => h("th", {}, x)))),
      h("tbody", {}, s.entities.map((e) => h("tr", {}, h("td", { class: "mono" }, e.name || "(top)"), h("td", {}, e.files), h("td", {}, sizeText(e.size)), h("td", {}, when(e.newest))))))
      : h("p", { class: "muted-text" }, "Nothing landed yet."),
    h("b", {}, "Newest files"), files,
    h("b", {}, "Read by"),
    s.readers.length ? h("table", { class: "mini" }, h("tbody", {}, s.readers.map((r) => h("tr", {}, h("td", {}, h("b", {}, r.project)), h("td", { class: "mono small" }, r.entities.join(", "))))))
      : h("p", { class: "muted-text" }, "No project reads it yet."),
    h("b", {}, "Read it in a project"),
    h("pre", { class: "sql-block" }, `[source]\nlanding = "${s.name}/${s.entities[0]?.name || "<entity>"}/*.csv"`),
    h("p", { class: "muted-text small" }, "Or Data mesh > New entity > From the landing zone."));
}

function sourceDialog(s, done, systemTypes = [], zones = []) {
  const name = h("input", { value: s?.name || "", placeholder: "sap", disabled: s?.registered ? true : null });
  const title = h("input", { value: s?.title || "", placeholder: "SAP ERP" });
  const description = h("input", { value: s?.description || "" });
  const owner = h("input", { value: s?.owner || "", placeholder: "Who lands it" });
  const hours = h("input", { type: "number", min: "1", value: s?.expected_hours || "", placeholder: "24" });
  const type = h("select", {}, ["", ...systemTypes, ...(s?.system_type && !systemTypes.includes(s.system_type) ? [s.system_type] : [])]
    .map((t) => h("option", { value: t, selected: t === (s?.system_type || "") ? true : null }, t || "Not set")));
  // With one landing workspace, each source is in a zone; Shared is stored as no zone.
  const zone = zones.length ? h("select", {}, zones.map((z) => h("option", { value: z.id === "shared" ? "" : z.id,
    selected: (z.id === "shared" ? "" : z.id) === (s?.zone || "") ? true : null }, `${z.name}${z.secure ? " (secure)" : ""}`))) : null;
  const secrets = MESH_ENVS.map((env) => [env, h("input", { value: s?.connections?.[env]?.secret_name || "", placeholder: "Key Vault secret name" })]);
  showDialog(s?.registered ? `Edit ${s.name}` : "Add a source", h("div", { class: "scroll" },
    h("div", { class: "form-grid" }, h("label", { class: "field" }, "Folder name", name), h("label", { class: "field" }, "Title", title),
      h("label", { class: "field" }, "Owner", owner), h("label", { class: "field" }, "Expected every (hours)", hours),
      h("label", { class: "field" }, "System type", type), zone ? h("label", { class: "field" }, "Zone", zone) : null),
    h("label", { class: "field" }, "Description", description),
    h("b", {}, "Connection secret per environment"),
    h("p", { class: "muted-text small" }, "The name of the Key Vault secret its pipeline uses. Secrets themselves never go here."),
    h("div", { class: "form-grid" }, secrets.map(([env, input]) => h("label", { class: "field" }, ENV_LABEL[env], input)))), [
    btn("Cancel", null, closeDialog, "tool outline"),
    btn("Save", "save", async () => {
      if (!name.value.trim()) { name.focus(); return; }
      const connections = Object.fromEntries(secrets.filter(([, i]) => i.value.trim()).map(([env, i]) => [env, { secret_name: i.value.trim() }]));
      const res = await platformCall("platform_save_source", { source: { name: name.value.trim().toLowerCase(), title: title.value.trim(),
        description: description.value.trim(), owner: owner.value.trim(), expected_hours: hours.value.trim(), connections,
        system_type: type.value, ...(zone ? { zone: zone.value } : {}), ...(s?.registered ? { code: s.code, active: s.active !== false } : {}) } }, "Not saved");
      if (res) { closeDialog(); platformPage.source = name.value.trim().toLowerCase(); done(); }
    }, "tool primary")]);
}

// ---------- business keys ----------

async function platformKeysTab(body) {
  const k = await platformCall("platform_key_tables", { env: platformPage.env }, "Could not read the key tables");
  if (!k || !stillHere("keys")) return;
  const rerender = () => platformKeysTab(body);
  const admin = platformPage.me?.admin;
  if (!!k.shared !== !!platformPage.keysShared) { platformPage.keysShared = !!k.shared; render(); return; }  // one list: no environment picker
  body.replaceChildren(withHowItWorks(h("div", { class: "card pad" },
    h("div", { class: "row" }, h("b", { class: "grow" }, k.shared ? "Key tables in SQL_Keys" : `Key tables in SQL_Platform (${ENV_LABEL[k.env]})`),
      k.shared ? h("span", { class: "pill-s pass" }, "Shared by Dev, Test and Prod") : null,
      admin ? btn("New key table", "add", () => keyTableDialog(rerender, k.shared), "tool primary small")
        : btn("Request a key table", "add", () => requestDialog("key_table", "", rerender), "tool primary small")),
    h("p", { class: "muted-text small" }, k.shared
      ? "One key per business key, everywhere: customer C-10442 has the same key in Dev, Test and Prod. Test runs that meet new customers use up key numbers here too. "
      : "One key per business key for every team: customer C-10442 from any source always gets the same key. ",
      admin ? (k.shared ? "A new key table is made once." : "A new key table is made in Dev, Test and Prod at once.") : "Only platform admins add key tables; ask for one and it appears here once approved.",
      " Projects pick from this list in Data mesh > Surrogate keys."),
    !k.shared && admin ? h("p", { class: "muted-text small" }, "To give every environment the same keys, use Move keys in, in Settings under Platform.") : null,
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
    k.requested.length ? h("div", {}, h("b", {}, "Requested"), requestTable(k.requested, rerender)) : null),
  howItWorks("platform.keys", {
    reads: k.shared
      ? "The key tables in SQL_Keys, the one keys database Dev, Test and Prod share. It reads when the tab opens."
      : "The key tables in this environment's SQL_Platform, or a file on this PC until the environment has a platform. It reads when the tab opens.",
    changes: `New key table adds sk.<name> ${k.shared ? "once, in SQL_Keys" : "in every environment's SQL_Platform"}; only platform admins can. ` +
      "From Settings under Platform: Create or Finish makes a Keys workspace with SQL_Keys and UDF_Keys, which uses Fabric capacity. " +
      "Move keys in shows what would move first, then copies every key table into it, keeps Prod's numbers, gives Dev and Test keys new numbers where Prod differs, closes each environment's old key tables and switches every environment to it. " +
      "Cancel the move removes the copied tables and reopens the old ones; it's refused once the move has finished.",
    results: "Runs in every environment issue keys from the list on this tab. After a move, re-run Dev and Test so their tables pick up the new numbers. Each change is kept on the Audit tab.",
    anchor: "platform",
  })));
}

function keyTableDialog(done, shared = false) {
  const name = h("input", { placeholder: "supplier" });
  const bk = h("input", { placeholder: "supplier_id" });
  const description = h("input", { placeholder: "A supplier, from any source" });
  showDialog("New key table", h("div", {},
    h("label", { class: "field" }, "Name", name), h("label", { class: "field" }, "Business key column", bk),
    h("label", { class: "field" }, "Description", description),
    h("p", { class: "muted-text small" }, `Creates sk.<name> ${shared ? "once, in SQL_Keys," : "in every environment's SQL_Platform"} with <name>_sk (INT), <name>_bk (unique) and create_date, and the Unknown member -1.`)), [
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
  if (kind === "source") return sourceRequestDialog(target, done);
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

// A source in the landing zone (scale plan 4.1, Preview): optionally with the Copy job that lands it.
// A platform admin approves it, and approving makes the job as them (central/landing_copy_service.py).
async function sourceRequestDialog(target, done) {
  const name = h("input", { value: target || "", placeholder: "erp", "aria-label": "Folder name" });
  const owner = h("input", { placeholder: "sales-data@contoso.com", "aria-label": "Owner" });
  const hours = h("input", { value: "24", inputmode: "decimal", "aria-label": "Expected every (hours)" });
  const why = h("textarea", { rows: 2, placeholder: "What it's for", "aria-label": "Why" });
  const withJob = h("input", { type: "checkbox", checked: true });
  const conn = h("select", { "aria-label": "Fabric connection" }, h("option", { value: "" }, "Reading your connections..."));
  const time = h("input", { value: "02:00", placeholder: "02:00", "aria-label": "Time of day (UTC)" });
  const tables = h("div", { class: "src-tables" });
  const addRow = (t = {}) => {
    const table = h("input", { value: t.table || "", placeholder: "dbo.Customer", class: "mono", "aria-label": "Table" });
    const mode = h("select", { "aria-label": "Each run copies" }, reqOptions([["full", "The whole table"], ["incremental", "New and changed rows"]], t.mode || "full"));
    const column = h("input", { value: t.column || "", placeholder: "ModifiedAt", class: "mono", "aria-label": "Column" });
    const show = () => { column.disabled = mode.value !== "incremental"; };
    mode.onchange = show;
    show();
    const row = h("div", { class: "src-table-row" }, table, mode, column,
      btn("", "dismiss", () => row.remove(), "tool small", { "aria-label": "Remove this table", title: "Remove" }));
    row.read = () => ({ table: table.value.trim(), mode: mode.value, column: column.value.trim() });
    row.focusTable = () => table.focus();
    tables.append(row);
  };
  addRow();
  const jobBox = h("div", {},
    reqField("Fabric connection", conn, "From Data Factory > Connections. Its password stays in Fabric; KratosForge never reads it."),
    h("b", { class: "small" }, "Tables"),
    h("div", { class: "src-table-row muted-text small" }, h("span", {}, "Table"), h("span", {}, "Each run copies"), h("span", {}, "Column"), h("span", {})),
    tables, btn("Add a table", "add", () => addRow(), "tool outline small"),
    reqField("Every day at (UTC)", time, "Leave blank for no schedule: the central team runs it when it's needed."));
  withJob.onchange = () => { jobBox.hidden = !withJob.checked; };
  let connections = [];
  (async () => {
    for (let i = 0; i < 20 && document.body.contains(conn); i++) {
      const out = await reqQuietly("fabric_connections");
      if (out && !out.pending) {
        connections = (out.connections || []).filter((c) => /sql/i.test(c.type || ""));
        conn.replaceChildren(...reqOptions(connections.length ? [["", "Pick one"], ...connections.map((c) => [c.id, `${c.name}${c.type ? ` (${c.type})` : ""}`])]
          : [["", out?.error ? "Couldn't read your connections" : "No SQL Server connection you can use: add one in Fabric"]]));
        return;
      }
      await new Promise((r) => setTimeout(r, 2500));
    }
  })();
  showDialog("Request a source", h("div", {},
    h("p", { class: "text" }, "A platform admin approves it. ", previewTag()),
    h("div", { class: "req-two" }, reqField("Folder name", name, "Files land in LH_Landing/Files/<folder>/<table>/"), reqField("Owner", owner)),
    reqField("Expected every (hours)", hours),
    h("label", { class: "check" }, withJob, " Land it with a Copy job: approving makes it in the landing workspace"),
    jobBox, reqField("Why", why)), [
    btn("Cancel", null, closeDialog, "tool outline"),
    btn("Send request", null, async () => {
      if (!name.value.trim()) { name.focus(); return; }
      const detail = { owner: owner.value.trim(), expected_hours: hours.value.trim() };
      if (withJob.checked) {
        if (!conn.value) { conn.focus(); return; }
        const rows = [...tables.children].map((r) => r.read()).filter((t) => t.table);
        if (!rows.length) { tables.firstElementChild?.focusTable(); return; }
        const c = connections.find((x) => x.id === conn.value);
        detail.copy = { connection_id: conn.value, connection_name: c ? `${c.name}${c.path ? `: ${c.path}` : ""}` : "",
          tables: rows, times: time.value.trim() ? [time.value.trim()] : [] };
      }
      const res = await platformCall("platform_request", { kind: "source", target: name.value.trim(), reason: why.value.trim(), detail }, "Not sent");
      if (res) { closeDialog(); toast("Request sent", false, { log: true }); done?.(); }
    }, "tool primary")]);
}

// The kinds the New request dialog offers first (Preview, "requestsCoreFour"); the older four follow under "Also here".
const NEW_REQUEST_KINDS = [
  ["workspace", "A new workspace", "Named, in a domain, on a capacity"],
  ["workspace_access", "Access to a workspace", "A role for you or a group"],
  ["domain", "A domain", "Or a subdomain"],
  ["exception", "An exception to a policy", "For a while, with a reason"],
  ["data", "Data from another team", "A new data product, or a change to one"],
];
const WORKSPACE_ROLES = ["Viewer", "Contributor", "Member", "Admin"];

const reqQuietly = (method, params = {}) => rpc(method, params).catch(() => null);
const reqDay = (d) => d.toISOString().slice(0, 10);
const reqField = (label, input, hint) => h("label", { class: "field" }, label, input, hint ? h("span", { class: "muted-text small" }, hint) : null);
const reqOptions = (rows, picked) => rows.map(([v, t]) => h("option", { value: v, selected: v === picked ? true : null }, t));

// Each form draws its fields into `box` and returns a function that gives the request to send, or null
// (after focusing what's missing). The platform checks every field again when it's sent.
const REQUEST_FORMS = {
  async workspace(box) {
    const [domains, capacities] = await Promise.all([reqQuietly("platform_domains"), reqQuietly("list_capacities")]);
    const name = h("input", { placeholder: "Sales Forecasting - Dev", "aria-label": "Workspace name" });
    const domain = h("select", { "aria-label": "Domain" }, reqOptions([["", "No domain"], ...(domains || []).map((d) => [d.id, d.parent_id ? `${(domains.find((x) => x.id === d.parent_id) || {}).name || d.parent_id} / ${d.name}` : d.name])]));
    const caps = capacities || [];
    const capacity = h("select", { "aria-label": "Capacity" }, reqOptions(caps.length ? caps.map((c) => [c.id, c.displayName || c.id]) : [["", "None I can see: the approver picks one"]]));
    const admins = h("input", { placeholder: "lee@contoso.com, or a group's object ID", "aria-label": "Admins" });
    const why = h("textarea", { rows: 2, placeholder: "What it's for", "aria-label": "Why" });
    box.replaceChildren(reqField("Workspace name", name),
      h("div", { class: "req-two" }, reqField("Domain", domain), reqField("Capacity", capacity)),
      reqField("Admins", admins, "People by email or UPN, groups by object ID, separated by commas."), reqField("Why", why));
    return () => {
      if (!name.value.trim()) return name.focus();
      if (!admins.value.trim()) return admins.focus();
      if (!why.value.trim()) return why.focus();
      const cap = caps.find((c) => c.id === capacity.value);
      return { kind: "workspace", target: name.value.trim(), reason: why.value.trim(),
        detail: { name: name.value.trim(), domain_id: domain.value, capacity_id: capacity.value, capacity_name: cap?.displayName || "",
          admins: admins.value.split(/[,;]/).map((x) => x.trim()).filter(Boolean) } };
    };
  },
  async workspace_access(box) {
    const spaces = (state.workspaces || await reqQuietly("list_workspaces") || []).filter((w) => w.id);
    const ws = h("select", { "aria-label": "Workspace" }, reqOptions([...spaces.map((w) => [w.id, w.displayName || w.id]), ["other", "Another workspace (paste its ID)"]]));
    const otherId = h("input", { placeholder: "Its ID, from its address in Fabric", "aria-label": "Workspace ID" });
    const otherName = h("input", { placeholder: "Its name", "aria-label": "Workspace name" });
    const other = h("div", { class: "req-two", hidden: spaces.length ? true : null }, reqField("Workspace ID", otherId), reqField("Name", otherName));
    if (!spaces.length) ws.value = "other";
    ws.onchange = () => { other.hidden = ws.value !== "other"; };
    const role = h("select", { "aria-label": "Role" }, reqOptions(WORKSPACE_ROLES.map((r) => [r, r]), "Viewer"));
    let who = "me";
    const groupId = h("input", { placeholder: "The group's object ID", "aria-label": "Group object ID" });
    const groupName = h("input", { placeholder: "Sales Engineers", "aria-label": "Group name" });
    const group = h("div", { class: "req-two", hidden: true }, reqField("Group object ID", groupId), reqField("Group name", groupName));
    const seg = h("div", { class: "seg", role: "group", "aria-label": "Who" }, [["me", "Me"], ["group", "A group"]].map(([k, t]) =>
      h("button", { class: k === who ? "on" : null, onclick: (e) => { who = k; seg.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b === e.currentTarget)); group.hidden = k !== "group"; } }, t)));
    const why = h("textarea", { rows: 2, placeholder: "What you need it for", "aria-label": "Why" });
    box.replaceChildren(reqField("Workspace", ws), other, reqField("Role", role), h("div", { class: "field" }, "Who", seg), group, reqField("Why", why));
    return () => {
      const picked = spaces.find((w) => w.id === ws.value);
      if (ws.value === "other" && !otherId.value.trim()) return otherId.focus();
      if (who === "group" && !groupId.value.trim()) return groupId.focus();
      const id = picked ? picked.id : otherId.value.trim();
      const wsName = picked ? picked.displayName : (otherName.value.trim() || id);
      return { kind: "workspace_access", target: wsName, reason: why.value.trim(),
        detail: { workspace_id: id, workspace_name: wsName, role: role.value, who,
          ...(who === "group" ? { group_id: groupId.value.trim(), group_name: groupName.value.trim() } : {}) } };
    };
  },
  async domain(box) {
    const domains = (await reqQuietly("platform_domains")) || [];
    const name = h("input", { placeholder: "Marketing", "aria-label": "Domain name" });
    const parent = h("select", { "aria-label": "Parent" }, reqOptions([["", "None: a top-level domain"], ...domains.filter((d) => !d.parent_id).map((d) => [d.id, d.name])]));
    const description = h("input", { placeholder: "What it covers", "aria-label": "Description" });
    const owners = h("input", { placeholder: "ana@contoso.com, Marketing data team", "aria-label": "Owners" });
    const why = h("textarea", { rows: 2, placeholder: "Who needs it, and for what", "aria-label": "Why" });
    box.replaceChildren(reqField("Name", name), reqField("Parent", parent, "Pick one to ask for a subdomain."),
      reqField("Description", description), reqField("Owners", owners), reqField("Why", why));
    return () => {
      if (!name.value.trim()) return name.focus();
      return { kind: "domain", target: name.value.trim(), reason: why.value.trim(),
        detail: { name: name.value.trim(), parent_id: parent.value, description: description.value.trim(), owners: owners.value.trim() } };
    };
  },
  async exception(box, prefill = {}) {
    const o = (await reqQuietly("governance_overview")) || { policies: [], domains: [], projects: [] };
    const live = o.policies.filter((p) => p.current_version && p.status !== "retired");
    if (!live.length) { box.replaceChildren(h("p", { class: "muted-text" }, "No published policy to ask an exception to.")); return () => null; }
    const pol = h("select", { "aria-label": "Policy" }, reqOptions(live.map((p) => [p.id, p.name])));
    const scope = h("select", { "aria-label": "Scope" }, reqOptions([["project", "A project"], ["domain", "A domain"], ["workspace", "A workspace"]]));
    const targets = {
      project: h("select", { "aria-label": "Project" }, reqOptions((o.projects || []).map((p) => [p, p]))),
      domain: h("select", { "aria-label": "Domain" }, reqOptions((o.domains || []).map((d) => [d.id, d.name]))),
      workspace: h("input", { placeholder: "The workspace's name", "aria-label": "Workspace" }),
    };
    const where = h("div", {}, reqField("Which", targets.project));
    scope.onchange = () => where.replaceChildren(reqField("Which", targets[scope.value]));
    // From a Check now finding (Governance > Check this project): the policy and project are filled in.
    if (prefill.policy) pol.value = prefill.policy;
    if (prefill.scope) { scope.value = prefill.scope; scope.onchange(); }
    const t = targets[scope.value];
    if (prefill.scope_target && t.tagName === "SELECT" && ![...t.options].some((x) => x.value === prefill.scope_target)) {
      t.append(h("option", { value: prefill.scope_target }, prefill.scope_target));
    }
    if (prefill.scope_target) t.value = prefill.scope_target;
    const tomorrow = new Date(Date.now() + 86400000);
    const yearOn = new Date(); yearOn.setFullYear(yearOn.getFullYear() + 1);
    const month = new Date(Date.now() + 30 * 86400000);
    const until = h("input", { type: "date", min: reqDay(tomorrow), max: reqDay(yearOn), value: reqDay(month), "aria-label": "Until" });
    const why = h("textarea", { rows: 2, placeholder: "Why the policy can't be met for now", "aria-label": "Why" });
    box.replaceChildren(reqField("Policy", pol), h("div", { class: "req-two" }, reqField("For", scope), where),
      reqField("Until", until, "A year at most. It ends on its own on that date."), reqField("Why", why));
    return () => {
      const target = targets[scope.value];
      if (!target.value.trim()) return target.focus();
      if (!until.value) return until.focus();
      if (!why.value.trim()) return why.focus();
      return { kind: "exception", target: pol.value, reason: why.value.trim(),
        detail: { policy: pol.value, scope: scope.value, scope_target: target.value.trim(), until: until.value } };
    };
  },
};

// Asking another team for data (Preview): a new product from a domain, or a change to a product.
// prefill.product asks for a change to that product (from its page).
REQUEST_FORMS.data = async function data(box, prefill = {}) {
  const [domains, listed] = await Promise.all([reqQuietly("platform_domains"), reqQuietly("platform_products")]);
  const products = (listed?.products || []).filter((p) => p.status !== "draft");
  let what = prefill.product ? "change" : "new";
  const domain = h("select", { "aria-label": "Domain" }, reqOptions((domains || []).map((d) => [d.id, d.parent_id ? `${(domains.find((x) => x.id === d.parent_id) || {}).name || d.parent_id} / ${d.name}` : d.name])));
  const product = h("select", { "aria-label": "Data product" }, reqOptions(products.map((p) => [p.id, `${p.name} (${p.id})`]), prefill.product));
  const needs = h("textarea", { rows: 3, placeholder: "What you need and what it's for, e.g. daily churn by region for the retention dashboard", "aria-label": "What the data is for" });
  const tomorrow = new Date(Date.now() + 86400000);
  const by = h("input", { type: "date", min: reqDay(tomorrow), "aria-label": "Needed by" });
  const which = h("div", {});
  const draw = () => which.replaceChildren(what === "new"
    ? reqField("From the domain", domain, "Its owners decide. A platform admin can too.")
    : reqField("Data product", product, "Its owner decides. A platform admin can too."));
  const seg = h("div", { class: "seg", role: "group", "aria-label": "What kind" }, [["new", "A new data product"], ["change", "A change to one"]].map(([k, t]) =>
    h("button", { class: k === what ? "on" : null, "aria-pressed": String(k === what), onclick: (e) => {
      what = k;
      seg.querySelectorAll("button").forEach((b) => { b.classList.toggle("on", b === e.currentTarget); b.setAttribute("aria-pressed", String(b === e.currentTarget)); });
      draw();
    } }, t)));
  draw();
  box.replaceChildren(h("div", { class: "field" }, "What kind", seg), which, reqField("What you need", needs),
    reqField("Needed by", by, "Optional. The team answers with how and when."));
  return () => {
    const target = what === "new" ? domain.value : product.value;
    if (!target) return (what === "new" ? domain : product).focus();
    if (!needs.value.trim()) return needs.focus();
    return { kind: "data", target, reason: needs.value.trim(), detail: { what, needed_by: by.value } };
  };
};

// Data mesh > Requests > New request: the kind picker, then that kind's fields.
function newRequestDialog(done, start = "workspace", prefill = {}) {
  const shown = previewShown();
  const box = h("div", { class: "req-form" });
  let read = null;
  const pick = async (kind) => {
    grid.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.kind === kind)));
    box.replaceChildren(loading("Loading..."));
    read = await REQUEST_FORMS[kind](box, kind === start ? prefill : {});
  };
  const grid = h("div", { class: "req-kinds", role: "group", "aria-label": "What you need" }, NEW_REQUEST_KINDS.map(([k, title, sub]) =>
    h("button", { class: "req-kind", "data-kind": k, "aria-pressed": "false", onclick: () => pick(k) },
      previewMarked("requestsCoreFour", h("b", {}, title, " ")), h("span", { class: "muted-text small" }, sub))));
  const elsewhere = (label, run) => btn(label, null, () => { closeDialog(); run(); }, "tool link small");
  const also = h("div", { class: "row tight muted-text small" }, "Also here: ",
    elsewhere("Key table", () => requestDialog("key_table", "", done)),
    elsewhere("Source", () => requestDialog("source", "", done)),
    elsewhere("Product access", () => openProducts()),
    elsewhere("Shortcut", () => { catalogPage.tab = "shortcuts"; scPage.view = "requests"; go("onelake"); shortcutDialog("request"); }));
  showDialog("New request", h("div", { class: "req-dialog" }, shown ? grid : null, also, shown ? box : null), [
    btn("Cancel", null, closeDialog, "tool outline"),
    shown ? btn("Send request", null, async () => {
      const req = read?.();
      if (!req) return;
      const res = await platformCall("platform_request", req, "Not sent");
      if (res) { closeDialog(); toast(`Request sent: ${REQUEST_KIND[req.kind].toLowerCase()} ${res.target}`, false, { log: true }); done?.(); }
    }, "tool primary") : null].filter(Boolean));
  if (shown) pick(start);
}

// The role a workspace access request's person or group has today, read as the approver and never
// stored (#1108): null when it wasn't read (not theirs to decide) or Fabric didn't answer.
function accessToday(r) {
  const t = r.today;
  if (!t || t.checked === false) return null;
  const d = r.detail || {};
  const p = d.principal || {};
  const role = t.role || "";
  const keeps = !!role && WORKSPACE_ROLES.indexOf(role) >= WORKSPACE_ROLES.indexOf(d.role);
  return { who: p.type === "Group" ? p.name : p.email || p.name, role, keeps };
}

// The muted line under a workspace access request that says what approving changes.
function accessTodayLine(r) {
  if (!r.today) return null;
  const d = r.detail || {};
  const t = accessToday(r);
  const text = !t ? "Couldn't check today's role: Fabric didn't answer. Approving never lowers a role either way."
    : t.keeps ? `${t.who} has ${t.role} in ${r.target} today, so approving keeps ${t.role}.`
    : t.role ? `${t.who} has ${t.role} in ${r.target} today; approving raises it to ${d.role}.`
    : `${t.who} has no role of its own in ${r.target} today. A role through another group isn't counted.`;
  return h("div", { class: "muted-text small" }, text);
}

// One line on what a request is for, beyond its target.
function requestDetail(r) {
  const d = r.detail || {};
  const muted = (text) => (text ? h("span", { class: "muted-text" }, text) : null);
  const names = (list) => (list || []).map((a) => a.name || a.email || a.id).join(", ");
  if (r.kind === "shortcut") return [h("b", {}, d.name), muted(` in ${d.workspace_name || ""} / ${d.lakehouse_name || ""}`)];
  if (r.kind === "source" && d.copy) {
    const t = d.copy.tables || [];
    return [h("b", { class: "mono" }, r.target), h("div", { class: "muted-text small" },
      `Copy job: ${t.length} table${t.length === 1 ? "" : "s"} from ${d.copy.connection_name || "a Fabric connection"}${d.copy.times?.length ? `, daily at ${d.copy.times.join(", ")} UTC` : ""}`)];
  }
  if (r.kind === "workspace") {
    return [h("b", {}, r.target), h("div", { class: "muted-text small" },
      [d.domain_name ? `${d.domain_name} domain` : null, d.capacity_name || (d.capacity_id ? "a capacity" : "capacity: the approver picks"),
        `admins ${names(d.admins)}`].filter(Boolean).join(", "))];
  }
  if (r.kind === "workspace_access") {
    const p = d.principal || {};
    return [h("b", {}, r.target), muted(` ${d.role} for ${p.type === "Group" ? `group ${p.name}` : p.email || p.name}`)];
  }
  if (r.kind === "domain") return [h("b", {}, d.name || r.target), muted(d.parent_id ? ` under ${d.parent_id}` : " (top level)"), d.owners ? h("div", { class: "muted-text small" }, `Owners: ${d.owners}`) : null];
  if (r.kind === "exception") {
    return [h("b", {}, d.policy_name || r.target), muted(` for ${d.scope} ${d.scope_target}`), h("div", { class: "muted-text small" }, `Until ${d.until}`)];
  }
  if (r.kind === "join_domain") {
    return [h("b", {}, `${r.target} into ${d.domain_name || d.domain}`), d.subdomain ? muted(` / ${d.subdomain}`) : null,
      h("div", { class: "muted-text small" }, `Domain owners: ${d.owners || "none yet, so a platform admin decides"}`)];
  }
  if (r.kind === "data") {
    return [h("b", {}, d.what === "change" ? `Change to ${d.product_name || r.target}` : `New data product from ${d.domain_name || r.target}`),
      d.needed_by ? h("div", { class: "muted-text small" }, `Needed by ${d.needed_by}`) : null];
  }
  return h("b", { class: "mono" }, r.target);
}

// What approving makes, said before it's made. A request that makes something in Fabric is confirmed
// first; null means the person cancelled. Returns extra params for platform_decide.
async function confirmApproval(r) {
  const d = r.detail || {};
  const names = (list) => (list || []).map((a) => a.name || a.email || a.id).join(", ");
  const after = "Then the request is approved. If any step fails, it stays waiting and says which.";
  if (r.kind === "workspace") {
    let capacity = d.capacity_id;
    let capName = d.capacity_name || "its capacity";
    if (!capacity) {
      const caps = (await reqQuietly("list_capacities")) || [];
      if (!caps.length) { toast("No capacity you can put a workspace on. Ask for Contributor on one, then approve.", true); return null; }
      const sel = h("select", { "aria-label": "Capacity" }, reqOptions(caps.map((c) => [c.id, c.displayName || c.id])));
      const ok = await new Promise((resolve) => showDialog(`Pick a capacity for ${r.target}`, reqField("Capacity", sel, "The request didn't name one."), [
        btn("Cancel", null, () => { closeDialog(); resolve(false); }, "tool outline"),
        btn("Next", null, () => { closeDialog(); resolve(true); }, "tool primary")]));
      if (!ok) return null;
      capacity = sel.value;
      capName = caps.find((c) => c.id === capacity)?.displayName || "that capacity";
    }
    // Putting a workspace in a Fabric domain needs Fabric's admin API, which KratosForge doesn't ask
    // for, so that step is named as the approver's to do in Fabric rather than promised.
    const domain = d.domain_id ? ` Then put it in the ${d.domain_name || d.domain_id} domain in Fabric's admin portal.` : "";
    const ok = await confirmDialog(`Approve and create ${r.target}?`,
      `As you, KratosForge creates the workspace on ${capName} and makes ${names(d.admins)} its admins. ${after}${domain}`,
      "Create and approve");
    return ok ? { capacity_id: d.capacity_id ? "" : capacity } : null;
  }
  if (r.kind === "workspace_access") {
    const p = d.principal || {};
    const group = p.type === "Group" ? `the group ${p.name}` : p.email;
    const t = accessToday(r);
    const text = t?.keeps
      ? `${t.who} already has ${t.role} in ${r.target}, which is ${t.role === d.role ? "the role asked for" : `higher than ${d.role}`}. Approving keeps ${t.role} and marks the request approved. Nothing changes in Fabric.`
      : t?.role ? `As you, KratosForge raises ${group} from ${t.role} to ${d.role} in ${r.target}.`
      : `As you, KratosForge gives ${group} the ${d.role} role in ${r.target}.${t ? "" : " If they already have a higher role, they keep it."}`;
    const ok = await confirmDialog(`Approve ${d.role} on ${r.target}?`, `${text} ${after}`, t?.keeps ? "Approve" : "Add and approve");
    return ok ? {} : null;
  }
  if (r.kind === "domain") {
    // Created in Fabric unless its parent isn't there: a subdomain can't be in Fabric without its parent.
    const kind = d.parent_id ? "subdomain" : "domain";
    const name = d.name || r.target;
    const parent = d.parent_id ? ((await reqQuietly("platform_domains")) || []).find((x) => x.id === d.parent_id) : null;
    const inFabric = !parent || !!parent.fabric_domain;
    const ok = await confirmDialog(`Approve the domain ${name}?`, inFabric
      ? `As you, KratosForge creates the ${kind} ${name} in Fabric, for the whole organization, and keeps it in the platform linked to it, where every team sees it. That needs a Fabric administrator: if Fabric refuses you, nothing is made and a Fabric administrator can approve it instead, or you can create it in the platform only. ${after}`
      : `${parent.name} isn't in Fabric, so its subdomains can't be either. As you, KratosForge saves the subdomain ${name} in the platform only, where every team sees it. ${after}`,
      inFabric ? "Create and approve" : "Save and approve");
    return ok ? {} : null;
  }
  if (r.kind === "source" && d.copy) return confirmCopyJob(r);
  if (r.kind === "exception") {
    const ok = await confirmDialog(`Grant an exception to ${d.policy_name || r.target}?`,
      `The platform records the exception for ${d.scope} ${d.scope_target} until ${d.until}, with its reason, and adds it to the policy's history. Where the policy is resolved there, it shows as "Excepted until ${d.until}" instead of applying. It ends on its own on that date.`,
      "Approve");
    return ok ? {} : null;
  }
  return {};
}

// Where the Copy job writes, with one landing workspace: the zone comes from the request, so the
// approver sees it (a secure zone, or Shared, which every domain and Dev and Test read).
function landsIn(where, central) {
  if (!where) return h("p", { class: "wrn-text small" }, `In ${central.workspace_name || "the landing workspace"}; its zone couldn't be read, so check the request before approving.`);
  if (where.problem) return h("p", { class: "fail-text small" }, where.problem);
  return h("p", { class: "text" }, "It writes to ", h("b", {}, `${where.lakehouse_name || "the zone's lakehouse"}`),
    `, the ${where.secure ? "secure " : ""}zone ${where.zone_name}, in ${where.workspace_name || "the landing workspace"}. `,
    where.zone === "shared" ? "Every domain, and Dev and Test, read the Shared zone." : where.secure ? "Only the readers its data access role allows see it." : "");
}

// Approving a source with a Copy job (scale plan 4.1): what it makes, where, and that runs use capacity.
async function confirmCopyJob(r) {
  const c = r.detail.copy;
  const st = await reqQuietly("platform_status");
  const central = st?.landing?.central ? st.landing : null;
  const env = h("select", { "aria-label": "Environment" }, reqOptions(MESH_ENVS.map((e) => [e, ENV_LABEL[e]]), "prod"));
  const ok = await new Promise((resolve) => showDialog(`Approve source ${r.target}?`, h("div", {},
    h("p", { class: "text" }, "Approving makes, as you:"),
    h("ol", { class: "text" },
      h("li", {}, `The source ${r.target} in the landing zone${r.detail.owner ? `, owned by ${r.detail.owner}` : ""}.`),
      h("li", {}, "A Copy job ", h("b", { class: "mono" }, `CJ_Landing_${r.target}`), ` reading ${c.connection_name || "the Fabric connection"}, writing Parquet to:`,
        h("table", { class: "mini" }, h("tbody", {}, c.tables.map((t) => h("tr", {}, h("td", { class: "mono" }, t.table),
          h("td", { class: "mono" }, `Files/${r.target}/${t.folder}/`),
          h("td", {}, t.mode === "incremental" ? `rows changed since the last run, by ${t.column}` : "the whole table")))))),
      c.times?.length ? h("li", {}, `Its schedule: every day at ${c.times.join(", ")} UTC.`) : null),
    central ? landsIn(r.lands_in, central)
      : reqField("Into the LH_Landing of", env, "There's no one landing workspace yet, so pick the environment whose platform workspace it lands in."),
    h("p", { class: "wrn-text small" }, "Creating it starts no run. Each run uses capacity, and the gateway reads the source. Copy jobs made here are Preview: not yet run against a real tenant.")), [
    btn("Cancel", null, () => { closeDialog(); resolve(false); }, "tool outline"),
    btn("Approve and make it", null, () => { closeDialog(); resolve(true); }, "tool primary")]));
  return ok ? (central ? {} : { env: env.value }) : null;
}

// What approving a request made in Fabric before a later step failed (detail.made), still there after
// declining it: the same sentences as left_in_fabric in central/requests_service.py.
function leftInFabric(r) {
  const d = r.detail || {}, made = d.made || {};
  if (r.kind === "workspace" && made.workspace_id) {
    return [`The workspace ${d.name || r.target || made.workspace_id}, made by this request, is still in Fabric on its capacity: delete it there if it isn't wanted`];
  }
  if (r.kind === "workspace_access" && made.assigned && made.change !== "kept") {
    const who = d.principal || {};
    const label = who.name || who.email || who.id || "They";
    const whom = who.type === "Group" ? `The group ${label}` : label;
    const role = d.role || "role", ws = d.workspace_name || d.workspace_id || "the workspace";
    if (made.change === "gave") return [`${whom} keeps the ${role} role in ${ws} that approving this request gave: remove it in the workspace's Manage access in Fabric if it isn't wanted`];
    if (made.change === "raised" && made.from) return [`${whom} keeps the ${role} role in ${ws} that approving this request raised from ${made.from}: set it back in the workspace's Manage access in Fabric if it isn't wanted`];
    // The same role was there, or a request recorded before #1207: say both, so no one removes access they had before.
    return [`${whom} has the ${role} role${made.change === "same" ? "" : " or a higher one"} in ${ws}: they had it before this request, or approving it gave it. Check which before removing it in the workspace's Manage access in Fabric`];
  }
  if (r.kind === "source" && made.copy_job_id) {
    return [`The Copy job ${made.copy_job_name || made.copy_job_id}, made by this request, is still in the landing workspace${made.schedule_id ? " and its daily schedule still runs it" : ""}: delete it there if it isn't wanted`];
  }
  if (r.kind === "shortcut" && made.shortcut_id) {
    const sc = `The shortcut ${d.name || r.target} in ${d.lakehouse_name || "its lakehouse"}`;
    return [made.found ? `${sc} is in OneLake: it was there before this request, or approving it made it. Check which before removing it on OneLake > Shortcuts`
      : `${sc}, made by this request, is still in OneLake: remove it on OneLake > Shortcuts if it isn't wanted`];
  }
  if (r.kind === "domain" && made.domain_id) {
    const name = d.name || r.target || made.domain_id;
    if (made.found) return [`The domain ${name} is in the platform${made.fabric_domain_id ? " and Fabric" : ""}: it was there before this request, or approving it made it. Check which before deleting it`];
    return [made.fabric_domain_id
      ? `The domain ${name}, made by this request, is still in Fabric and the platform: delete it in Fabric's admin portal, then on Data mesh > Domains, if it isn't wanted`
      : `The domain ${name}, made by this request, is still in the platform: delete it on Data mesh > Domains if it isn't wanted`];
  }
  return [];
}

function requestTable(rows, rerender, compact = false) {
  if (!rows.length) return h("p", { class: "muted-text" }, "Nothing waiting.");
  const decide = async (r, approve) => {
    let note = "";
    let extra = {};
    if (!approve) {
      const input = h("input", { placeholder: "Why, so they know what to do next" });
      const left = leftInFabric(r);
      const body = left.length ? h("div", {}, h("p", { class: "wrn-text" }, `Declining deletes nothing in Fabric. ${left.join(". ")}.`),
        h("label", { class: "field" }, "Note", input)) : h("label", { class: "field" }, "Note", input);
      const ok = await new Promise((resolve) => showDialog(`Decline ${(REQUEST_KIND[r.kind] || "request").toLowerCase()} ${r.target}?`, body, [
        btn("Cancel", null, () => { closeDialog(); resolve(false); }, "tool outline"),
        btn("Decline", null, () => { note = input.value.trim(); closeDialog(); resolve(true); }, "tool primary")]));
      if (!ok) return;
    } else if (r.kind === "data") {
      const input = h("textarea", { rows: 2, placeholder: "How and when you'll do it, e.g. in Prod by 30 November", "aria-label": "Reply" });
      const ok = await new Promise((resolve) => showDialog(`Accept the data request from ${r.requested_by || "them"}?`,
        h("div", {}, h("p", { class: "text" }, "Accepting tells them your team will do it. Nothing is built or changed now."),
          h("label", { class: "field" }, "Reply", input)), [
        btn("Cancel", null, () => { closeDialog(); resolve(false); }, "tool outline"),
        btn("Accept", null, () => { if (!input.value.trim()) { input.focus(); return; } note = input.value.trim(); closeDialog(); resolve(true); }, "tool primary")]));
      if (!ok) return;
    } else {
      extra = await confirmApproval(r);
      if (!extra) return;
    }
    const res = await platformCall("platform_decide", { id: r.id, approve, note, ...extra }, "Not decided");
    if (!res) return;
    const tail = [...(res.problems || []), ...(res.next || [])];
    toast(`${approve ? "Approved" : "Declined"} ${r.kind === "shortcut" ? `shortcut ${r.detail?.name}` : r.target}${tail.length ? `; ${tail.join("; ")}` : ""}`, !!res.problems?.length, { log: true });
    rerender();
  };
  return h("table", { class: "list" },
    h("thead", {}, h("tr", {}, ["Type", "Request", "From", compact ? "" : "Status", ""].map((x) => h("th", {}, x)))),
    h("tbody", {}, rows.map((r) => h("tr", { "data-request": r.id },
      h("td", {}, h("span", { class: "pill-s info" }, REQUEST_KIND[r.kind] || r.kind)),
      h("td", {}, requestDetail(r), r.project ? h("span", { class: "muted-text" }, ` for ${r.project}`) : null,
        r.reason ? h("div", { class: "muted-text small" }, r.reason) : null,
        r.kind === "workspace_access" ? accessTodayLine(r) : null,
        r.note ? h("div", { class: "muted-text small" }, `Note: ${r.note}`) : null),
      h("td", {}, r.requested_by || h("span", { class: "muted-text" }, "someone"), h("div", { class: "muted-text small" }, when(r.requested_at))),
      compact ? h("td", {}) : h("td", {}, h("span", { class: `pill-s ${r.status === "approved" ? "pass" : r.status === "waiting" ? "wrn" : "neu"}` }, REQUEST_STATUS[r.status]),
        r.decided_by ? h("div", { class: "muted-text small" }, `${r.decided_by}, ${relTime(r.decided_at)}`) : null),
      h("td", {}, h("span", { class: "row tight" },
        r.can_decide ? btn(r.kind === "workspace" ? "Approve and create" : r.kind === "source" && r.detail?.copy ? "Approve and make it" : r.kind === "data" ? "Accept" : "Approve", null, () => decide(r, true), "tool primary small") : null,
        r.can_decide ? btn("Decline", null, () => decide(r, false), "tool outline small") : null,
        r.mine && r.status === "waiting" ? btn("Withdraw", null, async () => {
          const res = await platformCall("platform_withdraw", { id: r.id }, "Not withdrawn");
          if (!res) return;
          if (res.next?.length) toast(`Withdrew ${r.target}; ${res.next.join("; ")}`, false, { log: true });
          rerender();
        }, "tool outline small") : null))))));
}

// What Requests reads and writes, on Data mesh > Requests (mesh-requests.js).
const REQUESTS_HOW = {
  reads: "The requests in the platform's catalog (SQL_Platform in Fabric, or a file on this PC), when the tab opens. For each waiting workspace access request you can decide, the role that person or group has in the workspace today (its role assignments in Fabric, read as you once per workspace, never stored). New request also lists the domains, capacities, workspaces and policies you can see, to pick from, and Request a source lists the Fabric connections you can use (never their credentials).",
  changes: "Sending adds a request to the catalog. Approving makes what was asked for first, as you: a key table or source in the platform (for a source asking for a Copy job, first the Copy job CJ_Landing_<source> in the landing workspace, writing to LH_Landing/Files/<source>/<table>/, and its daily schedule, Preview); a shortcut in OneLake; a workspace in Fabric on its capacity, with its admins added; a role in a workspace; a domain created in Fabric and kept in the platform (in the platform only when its parent isn't in Fabric; creating it in Fabric needs a Fabric administrator); an exception recorded in the platform for its policy; access to a data product given in the catalog; a project's domain set in the catalog when it asked to join one. Then the request is marked approved. Accepting a data request records your reply and builds nothing. Declining records your note; Withdraw marks your own request withdrawn. If a step fails, the request stays waiting and the error names the step. Declining or withdrawing a request whose approval already made something (a workspace, a role, a Copy job, a domain, a shortcut) deletes nothing in Fabric: the confirm and the message after say what is still there, to delete in Fabric if it isn't wanted.",
  results: "Everyone sees requests here and in the bell, and the platform's Teams routes get a notice (no email yet). A workspace's domain in Fabric is set by a Fabric administrator in Fabric's admin portal. Every change is on Platform > Govern > Audit.",
  more: [["Who decides", "A data product's owner decides who else may read it and changes to it. A domain's owners answer requests for a new data product and decide which projects join their domain. Platform admins decide key tables, sources, workspaces, workspace access, domains and policy exceptions, and can decide any request."]],
  anchor: "data-mesh",
};

// ---------- Data mesh > Domains: the organization's domains (it was Platform > Set up > Domains) ----------

async function renderMeshOrgDomainsTab(body) {
  const here = () => state.view === "metadata" && state.meta.tab === "domains" && body.isConnected;
  body.replaceChildren(loading("Reading the platform's domains..."));
  // Admin buttons need to know who you are; Platform reads it too, so it's kept for both.
  if (!platformPage.me) platformPage.me = await rpc("platform_whoami").catch(() => null);
  const [domains, m] = await Promise.all([platformCall("platform_domains"), rpc("mesh_map", {}).catch(() => null)]);
  if (!here()) return;
  if (!domains) { body.replaceChildren(h("p", { class: "muted-text" }, "The platform's domains couldn't be read. Refresh to try again.")); return; }
  const rerender = () => renderMeshOrgDomainsTab(body);
  const o = { domains };
  // Projects and published products per domain, from the same catalog read as the Map.
  const projectsIn = (d) => (m?.projects || []).filter((p) => p.domain_id === d.id || p.subdomain_id === d.id);
  const productsIn = (d) => (m?.products || []).filter((x) => projectsIn(d).some((p) => p.name === x.project));
  const count = (n) => (m ? n : h("span", { class: "muted-text", title: "The catalog's projects couldn't be read" }, "—"));
  // The tab's buttons sit top right like every Data mesh tab's; what a domain is goes in How this works.
  meshChrome({ actions: [
    btn("Glossary", null, () => goPlatform("glossary"), "tool outline",
      { title: "The business glossary is kept on Platform > Set up > Glossary; tag tables with its terms on their About tab" }),
    adminOnly(btn("New domain", "add", () => domainDialog(o, null, rerender), "tool outline")),
    adminOnly(btn("Import from Fabric", "sync", () => importFabricDomains(rerender), "tool primary",
      { title: "Bring in Fabric's domains and subdomains. Needs a Fabric administrator." }))] });
  body.replaceChildren(withHowItWorks(h("div", { class: "card pad mesh-org-domains" },
    domains.length ? h("table", { class: "list" },
      h("thead", {}, h("tr", {}, ["Domain", "Description", "Owners", "Projects", "Products", "Source", ""].map((x) => h("th", {}, x)))),
      h("tbody", {}, domains.map((d) => h("tr", { "data-domain": d.id },
        h("td", { class: d.parent_id ? "plat-subdomain" : null }, h("b", {}, d.name), h("div", { class: "muted-text small mono" }, d.parent_id ? `${d.id} · subdomain` : d.id)),
        h("td", {}, d.description || ""), h("td", {}, domainOwnersCell(d)),
        h("td", {}, count(projectsIn(d).length)), h("td", {}, count(productsIn(d).length)),
        h("td", {}, d.fabric_domain ? h("span", { class: "badge", title: d.fabric_domain }, "From Fabric") : h("span", { class: "muted-text" }, "Only here")),
        h("td", {}, h("span", { class: "row tight" },
          btn("Access groups", null, () => entraGroupsDialog(d, domains), "tool small",
            { title: "Entra ID groups for this domain's roles, with scripts to create them" }),
          adminOnly(h("span", { class: "row tight" },
          btn(d.fabric_domain ? "Edit owners" : "Edit", null, () => domainDialog(o, d, rerender), "tool small"),
          btn("Delete", null, async () => {
            const why = "Only a domain no project or subdomain uses can be deleted." + (d.fabric_domain
              ? " It follows a domain in Fabric: delete that one in Fabric's admin portal first, or the next Import from Fabric brings it back." : "");
            if (!(await confirmDialog(`Delete ${d.name}?`, why, "Delete", { danger: true }))) return;
            if (await platformCall("platform_delete_domain", { id: d.id }, "Not deleted")) rerender();
          }, "tool small danger")))))))))
      : h("p", { class: "muted-text" }, platformPage.me?.admin ? "No domains yet. Import them from Fabric, or add one." : "No domains yet. A platform admin imports them from Fabric.")),
  howItWorks("platform.domains", {
    reads: "The platform's domains, and the projects and published products in each, from the platform's catalog (SQL_Platform in Fabric, or the file on this PC). Import from Fabric reads Fabric's domains and each one's admins, as a Fabric administrator.",
    changes: "New, Edit and Delete change the domain in the platform's catalog; only platform admins see them. New domain with Also create it in Fabric ticked also creates the domain (or subdomain) in Fabric's admin portal, for the whole organization, before saving it here; that needs a Fabric administrator. Nothing here renames, moves or deletes a domain in Fabric. Access groups saves which Entra ID group each of the domain's roles uses (catalog.entra_group) and its naming pattern; it creates nothing in Entra or Fabric, only scripts an admin runs.",
    results: "Everyone using this platform sees the same domains. Each change is kept on Platform > Govern > Audit.",
    more: [["What a domain is", "An area of the business that owns its data, like Sales or Finance; subdomains go one level deep. A project joins one on Domains > This project. Domain owners keep their domain's glossary terms. Platform admins create, import and edit domains here; everyone else reads them."],
      ["Domains from Fabric", `${domains.some((d) => d.fabric_domain)
        ? "These follow the domains in Fabric's admin portal. Import again after a domain is added, renamed or moved there. "
        : "Start from the domains already in Fabric: Import from Fabric copies their names and subdomains here, so everyone can see them. "}Reading Fabric's domain list needs a Fabric administrator; after that nobody else needs admin rights. Each domain's admins in Fabric show as owners marked From Fabric until you set the platform's own.`]],
    anchor: "data-mesh",
  })));
}

// ---------- glossary, audit ----------

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
        if (!(await confirmDialog(`Remove ${a.user_name} as a platform admin?`, "They keep reading everything and can still send requests.", "Remove", { danger: true }))) return;
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
    { label: "Requests", icon: "person", keywords: "platform data mesh approve access key table", preview: true, run: () => goMeta("requests") },
    { label: "Governance policies", icon: "shield", keywords: "platform governance policy policies standards naming quality classification", preview: true, run: () => { govPage.open = null; goPlatform("governance"); } },
  ],
});

// ---------- Data mesh > Products: every team's products, for everyone ----------
// It was OneLake > Data products; OneLake's tab and every link now open it on Data mesh.

const productsPage = { query: "", domain: "all", open: null, following: false, reviewDue: false, mine: "", offer: "all" };
const productsHost = { body: null };

// Open the products list, or one product's page, on Data mesh > Products.
function openProducts(prod = null, opts = {}) {
  Object.assign(productsPage, { open: prod }, opts);
  goMeta("catalog");
}

function productsHere() {
  return state.view === "metadata" && state.meta.tab === "catalog" && !!productsHost.body?.isConnected;
}

function productsView(node) {
  productsHost.body?.replaceChildren(node);
}

function renderMeshCatalogTab(body) {
  productsHost.body = body;
  return renderDataProducts();
}

async function renderDataProducts() {
  const pp = productsPage;
  // Ask for data is the page's button on Home and Products, where you look for data.
  meshChrome({ actions: previewShown() ? btn("Ask for data", null, () => newRequestDialog(null, "data"), "tool primary") : null, views: null, tools: null });
  if (pp.open && previewShown()) return renderProductTrust(pp.open);
  pp.open = null;
  const list = h("div", {}, loading("Loading data products..."));
  const search = h("input", { class: "catalog-search", placeholder: "Search products, ports and owners", value: pp.query, "aria-label": "Search data products" });
  productsView(h("div", { class: "monitor onelake mesh-products" }, withHowItWorks(
    h("section", { class: "card pad" }, list),
    howItWorks("onelake.products", {
      reads: "Every team's published data products, their ports and readers, and the semantic models and reports grouped under each (catalog.product_item, with the endorsement the last tenant scan read in catalog.endorsement), from the platform's catalog (SQL_Platform in Fabric, or the file on this PC), and, for the products you own, which grants are due for their access review. It reads when the tab opens and when you press Refresh.",
      changes: "Link writes a link file in your open metadata project on this PC and records your project as a reader in the platform's catalog. Request access adds a request to the platform's catalog for the product's owner, and Ask for data adds one for the domain's or product's owners. None of them touches the product's tables in OneLake, and nothing else here writes.",
      results: "The product's owner sees your request on Data mesh > Requests and decides it, and sees your project among the product's readers. A link shows on Project > Products and links > Links.",
      anchor: "data-products-preview",
    }))));
  const [out, reviews] = await Promise.all([platformCall("platform_products", {}, "Could not read the catalog"),
    previewShown() ? rpc("platform_access_reviews", {}).catch(() => null) : null]);
  if (!out || !productsHere()) return;
  pp.mine = out.project?.name || "";
  const dueFor = new Set((reviews?.grants || []).filter((g) => g.due).map((g) => g.product_id));
  const domains = [...new Set(out.products.map((p) => p.domain_id).filter(Boolean))].sort();
  const draw = () => {
    const q = pp.query.toLowerCase();
    const grouped = !!out.can_group && previewShown();
    const shown = out.products.filter((p) => (pp.domain === "all" || p.domain_id === pp.domain) && (!pp.following || p.following) && (!pp.reviewDue || dueFor.has(p.id))
      && (!grouped || productOfferMatches(p, pp.offer))
      && (!q || `${p.name} ${p.description} ${p.owner} ${p.project} ${p.ports.map((x) => x.port).join(" ")}`.toLowerCase().includes(q)));
    const action = (p) => p.linked ? h("span", { class: "pill-s pass" }, "Linked")
      : p.can_link ? btn("Link", "databaseLink", async () => {
        const o = await meshOverview();
        if (o) linkDialog(o, { product: p.id, port: p.ports[0]?.port }, () => goMeta("links"));
      }, "tool primary small")
        : p.why === "request" ? btn("Request access", null, () => requestDialog("access", p.id, draw, { title: p.name, owner: p.owner }), "tool outline small")
          : p.why === "requested" ? h("span", { class: "pill-s wrn" }, "Requested") : h("span", { class: "muted-text small" }, p.why);
    list.replaceChildren(...[
      reviews?.due ? h("div", { class: "row tight access-review-band" }, icon("shield", 16),
        h("span", { class: "grow" }, `Access review: ${reviews.due} grant${reviews.due === 1 ? "" : "s"} on your products ${reviews.due === 1 ? "hasn't" : "haven't"} been confirmed for ${reviews.every_days} days. Open each product and confirm who still needs it, or take access away.`),
        h("button", { class: `chip review-chip${pp.reviewDue ? " on" : ""}`, "aria-pressed": String(pp.reviewDue),
          onclick: () => { pp.reviewDue = !pp.reviewDue; draw(); } }, `Review due (${dueFor.size})`)) : null,
      out.project ? null : h("p", { class: "muted-text small" }, "Open a metadata project to link to a product or ask for access."),
      // Find products by what they offer (Preview): tables, semantic models, reports.
      grouped ? h("div", { class: "row tight product-offer-filters" },
        h("div", { class: "seg", role: "group", "aria-label": "Offers" }, PRODUCT_OFFER_FILTERS.map(([id, label]) =>
          h("button", { class: pp.offer === id ? "on" : null, "aria-pressed": String(pp.offer === id), "data-offer": id,
            onclick: () => { pp.offer = id; draw(); } }, label))), previewTag()) : null,
      shown.length ? h("table", { class: "list" },
        h("thead", {}, h("tr", {}, ["Product", "Domain", "Owner", "Open to", "Ports", "Readers", ""].map((x) => h("th", {}, x)))),
        h("tbody", {}, shown.map((p) => h("tr", {},
          h("td", {}, previewShown() ? h("a", { href: "#", class: "product-open", title: "Endorsement and business terms",
            onclick: (e) => { e.preventDefault(); pp.open = p; render(); } }, h("b", {}, p.name)) : h("b", {}, p.name),
            h("span", { class: "muted-text small" }, ` v${p.version}`),
            grouped ? h("span", { class: "product-offer-badge" }, " ", productModelBadge(p)) : null,
            p.status === "deprecated" ? h("span", { class: "pill-s wrn" }, `Deprecated${p.deprecated_on ? ` ${p.deprecated_on}` : ""}`) : null,
            h("div", { class: "muted-text small" }, p.description || ""),
            grouped ? h("div", { class: "muted-text small product-offer-summary" }, productOfferSummary(p)) : null,
            h("div", { class: "muted-text small mono" }, p.id)),
          h("td", {}, p.domain_id || h("span", { class: "muted-text" }, "")),
          h("td", {}, p.owner || h("span", { class: "muted-text" }, "not set")),
          h("td", {}, ACCESS_LABEL[p.access] || p.access),
          h("td", { class: "mono small" }, [...new Set(p.ports.map((x) => x.port))].join(", ")),
          h("td", {}, (p.consumers || []).length || h("span", { class: "muted-text" }, "none")),
          h("td", {}, action(p))))))
        : h("p", { class: "muted-text" }, out.products.length ? "Nothing matches." : "No team has published a data product yet.")].filter(Boolean));
  };
  // All products or the ones you follow on the left; the domain, search and Refresh on the right.
  const following = out.can_follow && previewShown();
  const views = h("div", { class: "seg", role: "group", "aria-label": "Show" });
  const drawViews = () => views.replaceChildren(...[[false, "All products"], [true, `Following (${out.products.filter((p) => p.following).length})`]]
    .map(([on, label]) => h("button", { class: `${pp.following === on ? "on" : ""}${on ? " follow-chip" : ""}`.trim() || null,
      "aria-pressed": String(pp.following === on), onclick: () => { pp.following = on; drawViews(); draw(); } }, label)));
  if (following) drawViews();
  else pp.following = false;
  const domain = h("select", { class: "group-select", "aria-label": "Domain", onchange: (e) => { pp.domain = e.target.value; draw(); } },
    ["all", ...domains].map((d) => h("option", { value: d, selected: pp.domain === d ? true : null }, d === "all" ? "All domains" : d)));
  meshChrome({ views: following ? views : null, tools: [domains.length ? domain : null, search] });
  search.addEventListener("input", () => { pp.query = search.value; draw(); });
  draw();
}

// ---------- Data mesh > Products > one product: endorsement and business terms (Preview) ----------
// Endorsement is read only by Fabric admins (Power BI's admin tenant scan); the platform keeps the last
// scan's reading so everyone sees it. Terms are typed with a link to Purview; nothing reads Purview.

const ENDORSE_CLASS = { Certified: "pass", Promoted: "info", "Master data": "info", None: "neu" };
const PURVIEW_HOSTS = ["purview.microsoft.com", "web.purview.azure.com"];
const ITEM_WORD = { Lakehouse: "lakehouse", SemanticModel: "semantic model", Report: "report", Warehouse: "warehouse" };

// A badge for what the last scan read; no reading is "Endorsement unknown", never a guess.
function endorsementBadge(e) {
  if (!e) return h("span", { class: "pill-s neu endorse-badge unknown" }, "Endorsement unknown");
  return h("span", { class: `pill-s ${ENDORSE_CLASS[e] || "info"} endorse-badge` }, e === "None" ? "Not endorsed" : e);
}

// Why a pasted term address isn't a Purview link, or "" (the platform checks it the same way: core.purview_url).
function purviewUrlProblem(text) {
  const bad = "Paste the term's address from Purview: an https link on purview.microsoft.com or web.purview.azure.com";
  const t = String(text || "").trim();
  if (!t || /[\s\\]/.test(t)) return bad;
  let u;
  try { u = new URL(t); } catch { return bad; }
  const host = u.hostname.toLowerCase();
  if (u.protocol !== "https:" || u.username || u.password || (u.port && u.port !== "443")) return bad;
  return PURVIEW_HOSTS.some((x) => host === x || host.endsWith(`.${x}`)) ? "" : bad;
}

// Follow a product to hear in Teams when it's deprecated, gets a new version or its columns change in Prod.
// Whoever links a project to it hears too. A platform older than following shows no button.
function followButton(p) {
  if (p.following === undefined) return null;
  const b = btn("", null, async () => {
    b.disabled = true;
    try {
      const res = await rpc("platform_follow", { product_id: p.id, follow: !p.following });
      p.following = res.following.includes(p.id);
      toast(p.following ? `Following ${p.name}: you'll hear when it changes in Prod.` : `Stopped following ${p.name}.`);
    } catch (err) {
      toast(trustError(err), true);
    }
    b.disabled = false;
    paint();
  }, "tool outline small follow-button");
  const paint = () => {
    b.textContent = p.following ? "Following" : "Follow";
    b.setAttribute("aria-pressed", String(!!p.following));
    b.title = p.following ? "Stop following" : "Hear in Teams when it's deprecated, gets a new version or its columns change";
  };
  paint();
  return b;
}

function trustError(err) {
  const raw = String((err && typeof err === "object" ? err.message : err) || "");
  return raw.replace(/^[A-Za-z]*Error: /, "") || "The platform didn't answer.";
}

async function renderProductTrust(p) {
  const pp = productsPage;
  const badges = h("span", { class: "row tight endorse-head" });
  const lastScan = h("div", { class: "muted-text small trust-last-scan" });
  const endorsement = h("div", { class: "card pad trust-endorsement" }, h("b", {}, "Endorsement"), loading());
  const terms = h("div", { class: "card pad trust-terms" }, h("b", {}, "Business terms"), loading());
  productsView(h("div", { class: "monitor onelake mesh-products" }, withHowItWorks(
    h("div", {},
      h("section", { class: "card pad" },
        h("div", { class: "monitor-band" }, btn("All data products", null, () => { pp.open = null; render(); }, "tool link small"),
          h("span", { class: "spacer" }), followButton(p),
          // The open project's own product: its definition is a file there, edited on Project.
          pp.mine && p.project === pp.mine ? btn("Edit definition", null, () => goMeta("products"), "tool outline small") : null, btn("Ask for a change", null, () => newRequestDialog(null, "data", { product: p.id }), "tool outline small")),
        h("div", { class: "row" }, h("h2", { class: "trust-name" }, p.name), badges, previewTag()),
        h("div", { class: "muted-text small" }, p.description || ""), h("div", { class: "muted-text small mono" }, p.id), lastScan),
      productOfferCard(p, (items) => { models = items.filter((i) => i.item_type === "SemanticModel"); paintBadge(); }),
      endorsement, terms, productChecklistCard(p), productAccessCard(p), productDomainGroupsCard(p)),
    howItWorks("onelake.product-trust", {
      reads: "The product's endorsement as the last tenant scan read it, its business terms, and its semantic models and reports (catalog.product_item), from the platform's catalog; its tables are its ports. Add a model and Add a report list your workspaces and their items from Fabric, read only. Scan now reads Fabric's admin tenant scan (Power BI) of the workspaces the platform's published products live in, as you: only a Fabric administrator can. Nothing reads Purview.",
      changes: "Scan now writes what the scan read to catalog.endorsement in the platform's catalog. Adding or removing a term writes catalog.product_term. Take access away deletes that project's row in catalog.grant; links it already made stay. Ask for a change adds a request to catalog.request for the product's owner. Still needed records your confirmation in catalog.grant_review. Follow adds you to catalog.follow (Following takes you off). Add a model or Add a report writes one row to catalog.product_item (the item's id, type, workspace id and display name) and an add product item entry to catalog.audit; Remove deletes that row and logs remove product item there. Nothing changes in Fabric or Purview: KratosForge can't set endorsement, and terms stay in Purview.",
      results: "Everyone who opens this product sees the last scan's badge and when it was read, and each term with a link to it in Purview. Each scan, term change, model or report added or removed, and access taken away is in the platform's audit log. The product's badge here and on the products list is its semantic model's endorsement when it has one. When a promote to Prod deprecates the product, changes its version or changes a port's columns, its followers and the people who linked a project to it are named in a Teams notice that says what changed.",
      anchor: "endorsement-and-business-terms-preview",
    }))));
  const here = () => productsHere() && pp.open === p;
  // The product's badge is its semantic model's endorsement when it has one; otherwise its lakehouses'.
  let models = [];
  let shown = null;
  const paintBadge = () => {
    if (!here() || (!shown && !models.length)) return;
    if (models.length) { badges.replaceChildren(productModelBadge({ items: models })); return; }
    // Items that differ get one neutral badge here; the Endorsement card names each item's own.
    const seen = [...new Map(shown.items.map((i) => [i.endorsement || "", i.endorsement])).values()];
    badges.replaceChildren(...(seen.length > 1
      ? [h("span", { class: "pill-s neu endorse-badge varies", title: "Its items' endorsements differ: each one is under Endorsement" }, "Endorsement varies")]
      : (seen.length ? seen : [null]).map(endorsementBadge)));
  };
  const draw = (t) => {
    if (!here()) return;
    shown = t;
    paintBadge();
    const latest = t.items.map((i) => i.scanned_at).filter((x) => x && !Number.isNaN(Date.parse(x)))
      .sort((a, b) => Date.parse(b) - Date.parse(a))[0];
    lastScan.replaceChildren(...(latest ? ["Last tenant scan: ", when(latest)] : []));
    endorsement.replaceChildren(h("b", {}, "Endorsement"), ...endorsementLines(t, scan));
    terms.replaceChildren(...termLines(t, p, draw));
  };
  const failed = (err) => {
    if (!here()) return;
    const why = h("p", { class: "fail-text small" }, trustError(err));
    endorsement.replaceChildren(h("b", {}, "Endorsement"), why);
    terms.replaceChildren(h("b", {}, "Business terms"), why.cloneNode(true));
  };
  const scan = async () => {
    endorsement.querySelectorAll(".trust-scan").forEach((a) => { a.textContent = "Scanning..."; });
    try {
      const res = await rpc("platform_scan_endorsement", { product_id: p.id });
      const missed = res.not_reported?.length ? ` Fabric's scan didn't report ${res.not_reported.join(", ")}, so it stays unknown.` : "";
      toast(res.message || `Scanned ${res.workspaces} workspace${res.workspaces === 1 ? "" : "s"}: ${res.recorded} item${res.recorded === 1 ? "" : "s"} read.${missed}`);
      draw(res.trust || await rpc("platform_product_trust", { product_id: p.id }));
    } catch (err) {
      endorsement.querySelectorAll(".trust-scan").forEach((a) => { a.textContent = "Scan now"; });
      endorsement.append(h("p", { class: "fail-text small" }, trustError(err)));
    }
  };
  try {
    draw(await rpc("platform_product_trust", { product_id: p.id }));
  } catch (err) {
    failed(err);
  }
}

// Who was given access to a product, and taking it away. The product's owner or a platform admin
// can; the catalog checks that again. Links projects already made stay, so their runs keep reading.
function productAccessCard(p) {
  const card = h("div", { class: "card pad product-access" }, h("b", {}, "Access given"), loading());
  let reviews = null;
  const draw = async () => {
    let me;
    try { me = await rpc("platform_whoami", {}); } catch { me = {}; }
    const mine = !!me.admin || String(p.owner || "").toLowerCase().split(/[,;]/).map((x) => x.trim()).includes(String(me.user || "").toLowerCase());
    if (mine && previewShown() && !reviews) reviews = await rpc("platform_access_reviews", {}).catch(() => null);
    const review = (proj) => (reviews?.grants || []).find((g) => g.product_id === p.id && g.project === proj);
    const confirmed = (proj) => {
      const r = review(proj);
      if (!r) return null;
      const text = r.reviewed_at ? `Confirmed ${relTime(r.reviewed_at)}` : `Given ${r.granted_at ? relTime(r.granted_at) : ""}, never confirmed`;
      return h("span", {}, r.due ? h("span", { class: "pill-s wrn" }, "Review due") : null, " ", h("span", { class: "muted-text small" }, text));
    };
    const reviewing = !!reviews && reviews.available !== false;
    const linked = (proj) => (p.consumers || []).some((c) => c.consumer_project === proj);
    const open = p.access === "public" ? "Everyone can link to it without being given access."
      : p.access === "domain" ? `Projects in ${p.domain_id || "its domain"} can link to it without being given access.` : "";
    const granted = p.granted || [];
    card.replaceChildren(h("div", { class: "row tight" }, h("b", {}, "Access given"), previewTag()),
      open ? h("p", { class: "muted-text small" }, open) : null,
      granted.length ? h("table", { class: "list" },
        h("thead", {}, h("tr", {}, ["Project", "Linked", reviewing ? "Review" : null, ""].filter((x) => x !== null).map((x) => h("th", {}, x)))),
        h("tbody", {}, granted.map((proj) => h("tr", { "data-project": proj },
          h("td", {}, h("b", {}, proj)),
          h("td", {}, linked(proj) ? h("span", { class: "pill-s pass" }, "Linked") : h("span", { class: "muted-text small" }, "Not yet")),
          reviewing ? h("td", { class: "access-review" }, confirmed(proj)) : null,
          h("td", {}, mine ? h("span", { class: "row tight" }, reviewing ? btn("Still needed", null, () => confirm([proj]), "tool outline small") : null,
            btn("Take access away", null, () => revoke(proj), "tool outline small")) : null)))))
        : h("p", { class: "muted-text small" }, "No project has been given access yet."),
      reviewing && granted.length > 1 ? h("div", { class: "row tight" }, btn("All still needed", null, () => confirm(granted), "tool outline small"),
        h("span", { class: "muted-text small" }, `Confirm every ${reviews.every_days} days who still needs access. Nothing is taken away by itself.`)) : null,
      reviews?.available === false ? h("p", { class: "muted-text small" }, reviews.problem) : null,
      mine || !granted.length ? null : h("p", { class: "muted-text small" }, "Only the product's owner or a platform admin can take access away."));
  };
  const confirm = async (projects) => {
    try {
      reviews = await rpc("platform_confirm_access", { product_id: p.id, projects });
      toast(projects.length === 1 ? `Confirmed ${projects[0]} still needs ${p.name}.` : `Confirmed all ${projects.length} projects still need ${p.name}.`);
      draw();
    } catch (err) {
      card.append(h("p", { class: "fail-text small" }, trustError(err)));
    }
  };
  const revoke = async (proj) => {
    const keeps = (p.consumers || []).some((c) => c.consumer_project === proj)
      ? ` ${proj} already links to it: that link stays and its runs keep reading. Ask its team to remove the link if they mustn't.` : "";
    if (!(await confirmDialog(`Take ${proj}'s access to ${p.name} away?`,
      `${proj} can't make a new link to ${p.name}, and must ask again to get access back.${keeps} Nothing changes in OneLake.`,
      "Take access away", { danger: true }))) return;
    try {
      const left = await rpc("platform_revoke", { product_id: p.id, project: proj });
      p.granted = left.map((g) => g.project).sort();
      if (reviews?.grants) reviews.grants = reviews.grants.filter((g) => !(g.product_id === p.id && g.project === proj));
      toast(`${proj} no longer has access to ${p.name}.`);
      draw();
    } catch (err) {
      card.append(h("p", { class: "fail-text small" }, trustError(err)));
    }
  };
  draw();
  return card;
}

function endorsementLines(t, scan) {
  const scanLink = () => (t.admin ? h("a", { href: "#", class: "trust-scan", onclick: (e) => { e.preventDefault(); scan(); } }, "Scan now") : null);
  if (!t.items.length) {
    return [h("p", { class: "muted-text small" }, "This product has no port in Fabric yet. Once it's published to Test or Prod, its lakehouse's endorsement can be read.")];
  }
  const varies = new Set(t.items.map((i) => i.endorsement || "")).size > 1;
  const intro = varies ? [h("p", { class: "small trust-varies" }, "Its items' endorsements differ. Each one's is below.")] : [];
  return [...intro, ...t.items.map((i) => {
    const fabric = (label) => h("a", { href: "#", class: "trust-fabric", onclick: (e) => { e.preventDefault(); openLink(fabricItemLink({ type: i.item_type, workspace_id: i.workspace_id, item_id: i.item_id })); } }, label);
    const head = h("div", { class: "row tight" }, endorsementBadge(i.endorsement), h("span", { class: "mono small" }, i.item_name || i.item_id),
      h("span", { class: "muted-text small" }, ENV_LABEL[i.env] || i.env));
    if (!i.endorsement) {
      return h("div", { class: "trust-item" }, head,
        h("p", { class: "small" }, "Fabric only shows endorsement to Fabric admins. Once an admin has scanned, everyone sees the last scan's badge here. ", fabric("See it in Fabric")),
        t.admin ? h("p", { class: "small" }, scanLink()) : null);
    }
    const said = i.endorsement === "None" ? "Not endorsed in Fabric."
      : i.endorsement === "Certified" && i.certified_by ? `Certified by ${i.certified_by} in Fabric.` : `${i.endorsement} in Fabric.`;
    return h("div", { class: "trust-item" }, head, h("p", { class: "small" }, said),
      h("p", { class: "muted-text small" }, "From the tenant scan ", when(i.scanned_at), ". ", scanLink()),
      h("p", { class: "muted-text small" }, `To change it, open the ${ITEM_WORD[i.item_type] || "item"}'s settings in Fabric. KratosForge can't set endorsement. `, fabric("Open in Fabric")));
  })];
}

function termLines(t, p, done) {
  const rows = t.terms.map((x) => h("tr", {},
    h("td", {}, h("b", {}, x.term)),
    h("td", {}, h("a", { href: "#", class: "trust-purview", onclick: (e) => { e.preventDefault(); openLink(x.url); } }, "Open in Purview")),
    h("td", {}, t.can_edit_terms ? btn("Remove", "delete", async () => {
      if (!(await confirmDialog(`Remove ${x.term}?`, "The term and its link come off this product. The term itself stays in Purview.", "Remove", { danger: true }))) return;
      try {
        done(await rpc("platform_remove_product_term", { product_id: p.id, term: x.term }));
        toast(`Removed ${x.term}`);
      } catch (err) {
        toast(trustError(err), true);
      }
    }, "tool link small") : null)));
  return [
    h("div", { class: "row" }, h("b", { class: "grow" }, "Business terms"),
      t.can_edit_terms ? btn("Add a term", "add", () => termDialog(p, done), "tool outline small") : null),
    rows.length ? h("table", { class: "list trust-term-list" }, h("tbody", {}, rows))
      : h("p", { class: "muted-text small" }, t.can_edit_terms ? "No business terms yet. Add one with its link in Purview."
        : "No business terms yet. The product's owner or a platform admin adds them."),
  ];
}

function termDialog(p, done) {
  const term = h("input", { placeholder: "Like Active customer", "aria-label": "Term" });
  const url = h("input", { placeholder: "https://purview.microsoft.com/...", "aria-label": "Link to the term in Purview" });
  const problem = h("p", { class: "fail-text small", role: "alert" });
  showDialog("Add a term", h("div", {},
    h("label", { class: "field" }, "Term", term),
    h("label", { class: "field" }, "Link to the term in Purview", url),
    h("p", { class: "muted-text small" }, "Copy the term's address from Purview. KratosForge doesn't read Purview."), problem), [
    btn("Cancel", null, closeDialog, "tool outline"),
    btn("Add", "add", async () => {
      const name = term.value.trim();
      const why = name ? purviewUrlProblem(url.value) : "Give the term's name.";
      if (why) { problem.textContent = why; return; }
      try {
        const t = await rpc("platform_add_product_term", { product_id: p.id, term: name, url: url.value.trim() });
        closeDialog();
        toast(`Added ${name}`);
        done(t);
      } catch (err) {
        problem.textContent = trustError(err);
      }
    }, "tool primary")]);
}
