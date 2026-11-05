// Run as: which identity runs your workloads in each place, the credential it uses, and where data
// and credentials go (run_as_list, workloads/run_as.py). Facts only: agents publish no keys, secrets,
// paths or thumbprints, and nothing here signs in, rotates a credential or changes access.
// Monitor > KratosForge > Operations > Run as shows it.

const RA_STATE = {
  ok: ["pass", "OK"],
  expiring: ["warn", "Expires soon"],
  expired: ["fail", "Expired"],
  failed: ["fail", "Last token request failed"],
  signed_out: ["neu", "Signed out"],
  not_visible: ["neu", "Not visible"],
  unknown: ["neu", "Unknown"],
};
const RA_RUNS_AS = { service_principal: "Service principal", managed_identity: "Managed identity", user: "User sign-in",
  schedule_owner: "Its schedule's owner", signed_out: "Nobody signed in", unknown: "Unknown" };
const RA_CREDENTIAL = { certificate: "Certificate", secret: "Client secret", managed_identity: "Managed by Azure",
  user: "Your sign-in token", fabric: "Held by Fabric" };

// "in 25 days", "tomorrow", "today": relTime only counts back.
function raDaysLeft(iso) {
  const days = Math.floor((new Date(iso) - new Date()) / 86400000);
  return days > 1 ? `in ${days} days` : days === 1 ? "tomorrow" : "today";
}

function raDate(iso) {
  return dateText(iso);
}

function raPill(c) {
  const [cls, label] = RA_STATE[c.state] || RA_STATE.unknown;
  const text = c.state === "expiring" && c.expires_at ? `Expires ${raDaysLeft(c.expires_at)}` : label;
  return h("span", { class: `pill-s ${cls}` }, text);
}

function raPlace(r) {
  const p = r.place || {};
  const where = p.kind === "agent" ? btn(`Agent ${p.name}`, null, () => openAgent(p.name), "tool link small") : h("b", {}, p.name);
  const sub = p.kind === "agent" ? `${r.env ? envName(r.env) : "Several environments"} · ${r.live ? `reported ${relTime(r.as_of)}` : `stopped reporting ${relTime(r.as_of)}`}`
    : p.kind === "pc" ? "Runs and schedules you start here" : p.kind === "fabric_jobs" ? r.detail : r.detail;
  return [where, sub ? h("div", { class: "muted-text small" }, sub) : null];
}

function raRunsAs(r) {
  const a = r.runs_as || {};
  if (a.kind === "you") return ["You, ", a.name];
  const label = RA_RUNS_AS[a.kind] || RA_RUNS_AS.unknown;
  return [label, a.kind === "schedule_owner" && a.name ? h("div", { class: "muted-text small" }, `${a.name} · Fabric runs it as them`)
    : r.place?.kind === "agent" && a.kind !== "unknown" ? h("div", { class: "muted-text small" }, "As set in its agent.toml; the app doesn't see its ids") : null];
}

function raState(r) {
  const c = r.credential || {};
  const lines = [];
  if (c.expires_at) lines.push(`${c.state === "expired" ? "Ended" : "Ends"} ${raDate(c.expires_at)}`);
  if (c.state === "failed") lines.push(`${c.error_at ? relTime(c.error_at) : ""}${r.live ? "" : ", at its last report"}: ${c.error}`);
  if (c.state === "unknown" && r.place?.kind === "agent") lines.push(c.kind === "certificate" ? "The agent couldn't read its certificate's dates" : "An older version that doesn't report it");
  if (c.state === "not_visible") lines.push(r.place?.kind === "fabric_schedule" ? "Fabric doesn't say when the owner's access lapses" : "Its expiry isn't visible to the agent");
  if (c.last_token_at && c.state !== "failed") lines.push(`Last token ${relTime(c.last_token_at)}`);
  if (["secret", "managed_identity"].includes(c.kind) && c.state === "ok") lines.push("Its expiry isn't visible to the agent");
  return [raPill(c), lines.map((l) => h("div", { class: "muted-text small" }, l))];
}

function raAttention(out) {
  const items = out.attention || [];
  if (!items.length) return null;
  const say = (a) => a.state === "expired" ? `${a.place.name}'s certificate has expired`
    : a.state === "expiring" ? `${a.place.name}'s certificate expires ${raDaysLeft(a.expires_at)}`
    : `${a.place.name}'s last token request failed${a.live ? "" : ", at its last report"}`;
  return h("div", { class: "ag-overlap", role: "status" },
    h("b", {}, `${items.length} to look at. `), items.map(say).join(". "), ". ",
    h("span", { class: "muted-text" }, "Renewing a certificate or fixing an identity happens in Microsoft Entra and on the agent's server, not here."));
}

function runAsPanel() {
  const box = h("div", { class: "inc-panel" }, h("p", { class: "muted-text monitor-empty" }, "Reading who runs what..."));
  loadRunAs(box);
  return h("div", { class: "mon-how" }, withHowItWorks(box, howItWorks("monitor.runas", {
    reads: "Your sign-in on this PC and your organization's policy, each agent's status record in the environments' LH_Meta, and the Fabric schedules Monitor > Fabric > Schedules last read. It makes no Fabric call.",
    changes: "Nothing. Nothing here signs in, renews a certificate, rotates a secret or changes access. That happens in Microsoft Entra and on the agent's server.",
    results: "Nothing is stored. Agents report facts about their credential (a certificate's end date, their last token request), never the credential, its path or its thumbprint.",
    more: [["Unknown and Not visible", "Unknown means an older agent doesn't report it. Not visible means Fabric or Azure holds the credential and doesn't say when it lapses. Neither means it is broken."]],
    anchor: "run-as-preview",
  })));
}

async function loadRunAs(box) {
  let out;
  try {
    out = await rpc("run_as_list", {});
  } catch (err) {
    box.replaceChildren(h("p", { class: "reason monitor-empty" }, `Couldn't read who runs your workloads: ${friendlyError(err).text}`));
    return;
  }
  if (!box.isConnected) return;
  const rows = (out.rows || []).filter((r) => matches(`${r.place?.name} ${r.runs_as?.name || ""} ${r.env || ""}`));
  const s = out.sources || {};
  const fab = s.fabric_schedules || {};
  box.replaceChildren(
    h("div", { class: "monitor-filters" },
      h("p", { class: "muted-text small grow" }, "Facts only: no keys, secrets, paths or thumbprints leave an agent, and nothing here signs in, rotates or changes access."),
      btn("Refresh", "sync", () => loadRunAs(box), "tool outline")),
    h("div", { class: "wl-sources inc-watchers" },
      h("span", {}, h("b", {}, "This PC"), " your sign-in"),
      h("span", {}, h("b", {}, "Agents"), " from their status records"),
      (s.agents || []).filter((e) => e.count === null).map((e) => h("span", {}, h("span", { class: "pill-s neu" }, e.observation?.label || "Can't read"), ` ${envName(e.env)}: ${e.observation?.detail || "couldn't be read"}`)),
      h("span", {}, h("b", {}, "Fabric schedules"), fab.state === "read" ? ` as Schedules last read them, ${relTime(fab.at)}`
        : fab.state === "reading" ? " being read on Schedules"
        : fab.state === "error" ? `: ${friendlyError(fab.detail).text}`
        : [" not read yet. ", btn("Read them on Fabric > Schedules", null, () => { monitorPage.panel = "fabric_schedules"; go("monitor"); }, "tool link small")])),
    raAttention(out) || "",  // replaceChildren would print a null
    rows.length ? h("div", { class: "monitor-scroll" }, h("table", { class: "list inc-list" },
      h("thead", {}, h("tr", {}, ["Place", "Runs as", "Credential", "Its state", "Where data and credentials go"].map((c) => h("th", {}, c)))),
      h("tbody", {}, rows.map((r) => h("tr", { class: r.live ? null : "inc-done" },
        h("td", {}, raPlace(r)), h("td", {}, raRunsAs(r)),
        h("td", {}, RA_CREDENTIAL[r.credential?.kind] || h("span", { class: "muted-text" }, "Unknown")),
        h("td", {}, raState(r)),
        h("td", {}, r.boundary, r.place?.kind === "pc" && r.detail ? h("div", { class: "muted-text small" }, r.detail) : null))))))
      : h("p", { class: "muted-text monitor-empty" }, state.query ? "Nothing matches your search." : "No places to show."),
    h("p", { class: "muted-text small inc-foot" }, "Only places this app knows: this PC, agents that publish a status record in your environments, and Fabric schedules on items in your environments' workspaces. Workspace roles and OneLake access are on each item's Access tab."));
}

registerFeature({
  id: "runAs", preview: true,
  commands: [
    { label: "Run as", icon: "person", keywords: "monitor run as identity credential certificate expiry service principal managed identity who runs",
      run: () => { monitorPage.panel = "runas"; go("monitor"); } },
  ],
});
