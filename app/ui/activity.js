// What's running, where, and how it went: the environment picker and compute pill in the top
// bar, the activity tray behind the bell, plain-language errors, and the small helpers every
// page uses to show a run the same way. Loaded before app.js; everything here runs after startup.

// ---------- times ----------

// Dates and times, the same on every page (#956), in the person's own Windows format. Each takes
// an ISO string, a Date or milliseconds (callers turn epoch seconds into milliseconds), and gives
// opts.empty ("" unless the page names a word such as "never") for a missing or unreadable value,
// never "Invalid Date". opts.seconds adds seconds; opts.weekday adds the day of the week; opts.utc reads a
// plain day ("2026-10-28") as it is.
function readDate(at) {
  if (at === null || at === undefined || at === "") return null;
  const d = at instanceof Date ? at : new Date(at);
  return Number.isNaN(d.getTime()) ? null : d;
}

function dateText(at, opts = {}) {
  const d = readDate(at);
  if (!d) return opts.empty ?? "";
  const zone = opts.utc ? { timeZone: "UTC" } : {};
  return opts.weekday ? d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric", ...zone })
    : d.toLocaleDateString(undefined, { dateStyle: "medium", ...zone });
}

function timeText(at, opts = {}) {
  const d = readDate(at);
  return d ? d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", ...(opts.seconds ? { second: "2-digit" } : {}) }) : (opts.empty ?? "");
}

function dateTimeText(at, opts = {}) {
  const d = readDate(at);
  return d ? d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: opts.seconds ? "medium" : "short" }) : (opts.empty ?? "");
}

// How long ago, then the date once it's two days back.
function relTime(at) {
  const t = readDate(at);
  if (!t) return "";
  const s = Math.round((Date.now() - t.getTime()) / 1000);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 172800) return "yesterday";
  return dateText(t);
}

// A relative time with the exact time on hover.
function when(iso) {
  return h("span", { title: dateTimeText(iso, { seconds: true }) }, relTime(iso));
}

function took(seconds) {
  if (seconds === null || seconds === undefined || seconds === "") return "";
  const s = Number(seconds);
  if (s > 0 && s < 0.05) return "<0.1s";  // measured, just too short to round to a tenth: never "0s"
  if (s < 10) return `${Math.round(s * 10) / 10}s`;
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${Math.round(s % 60)}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

// What a metadata run's status means in one line: the reason when it didn't succeed.
function runReason(r) {
  if (r.status === "ok") return "";
  return r.message || (r.status === "needs_decision" ? "Waiting for your choice" : "");
}

// ---------- plain-language errors ----------

// Sidecar errors arrive as "ExceptionName: message". Known causes become a sentence; the raw
// text stays available under Details.
function friendlyError(err) {
  const raw = String((err && typeof err === "object" ? err.message : err) || "").trim();  // new Error("") has no reason, not "Error"
  // Errors about files and folders on this PC, and the engine's own time limits ("TimeoutError: The
  // query ran longer than..."): Python's exception name dropped, "[Errno 2] No such file or
  // directory: 'x'" said in words, the raw text kept under Details.
  function localError(raw) {
    const m = /^(FileNotFoundError|FileExistsError|IsADirectoryError|NotADirectoryError|PermissionError|TimeoutError): ([\s\S]*)$/.exec(raw);
    if (!m) return null;
    const [, name, message] = m;
    const os = /^\[(?:Errno|WinError) \d+\] [^:]*(?::\s*'?(.*?)'?)?$/.exec(message.trim());
    if (!os) return { text: message.replace(/^'(.*)'$/, "$1") || "The app couldn't use a file on this PC.", details: raw };
    if (name === "TimeoutError") return null;  // "[Errno 110] Connection timed out" is the network: the patterns below say so
    const at = os[1] ? ` ${os[1].replace(/\\\\/g, "\\")}` : "";  // Python's repr doubles Windows backslashes
    const text = {
      FileNotFoundError: `There is no file or folder at${at || " that path"} on this PC. Check the path, or pick the file again.`,
      FileExistsError: `Something already exists at${at || " that path"} on this PC. Pick another name or folder.`,
      IsADirectoryError: `${at ? at.trim() : "That path"} is a folder, and a file was expected. Pick a file.`,
      NotADirectoryError: `Part of${at || " that path"} is a file, and a folder was expected. Check the path.`,
      PermissionError: `This PC didn't allow access to${at || " that file or folder"}. Close any program that has it open, or pick a folder you can write to.`,
    }[name];
    return { text, details: raw };
  }
  // Order matters (PR-UX-02): the engine's own hung, stopped and blocked messages first, then errors about
  // files on this PC (localError, above), and only then Fabric's HTTP patterns, so a missing local
  // file is never "Fabric couldn't find that item" and a local time limit is never "Couldn't reach Fabric".
  const engine = [
    [/^The engine stopped answering/, "The local engine stopped answering, so the app restarted it. Notebook variables were cleared, so run the notebook's cells again. The details below name the request that hung."],
    [/^The Python sidecar stopped/, "The local engine stopped while doing this. It restarts on your next action; if it keeps stopping, the details below show why."],
    [/Application Control policy|WinError 4551/i, "Windows blocked part of the local engine (Smart App Control or an App Control policy). Restart KratosForge to see which files and what to do."],
  ];
  const known = [
    [/AADSTS53000|AADSTS53001|AADSTS53003|AADSTS530003|ConditionalAccess/i, "Your organization's sign-in rules (Conditional Access) blocked this sign-in, for example because this PC isn't managed by your organization. Ask IT; docs/permissions.md says what they can allow."],
    [/AADSTS700016|AADSTS90094|AADSTS90008|admin_consent|needs? admin approval/i, "Your organization hasn't allowed this sign-in yet. Ask a Microsoft Entra admin; docs/permissions.md says what they can do."],
    [/Capacity\.Read\.All|list_?capacities/i, "Your sign-in can't list capacities, so pick the workspace's capacity in Fabric instead. docs/permissions.md explains why and what IT can change."],
    [/InsufficientScopes|AADSTS65001|consent_required/i, "Your sign-in doesn't carry the permission this feature needs. docs/permissions.md lists each feature's permission and what IT can change."],
    [/AADSTS50076|AADSTS50079|AADSTS50074|AADSTS50072/, "Your organization needs an extra sign-in step (multi-factor authentication). Sign in again and finish the step in the Microsoft window."],
    [/AADSTS50020|AADSTS50034|AADSTS90072/, "That account doesn't belong to your organization's Fabric tenant. Sign in with your work account."],
    // #1079: Windows sign-in refused a token for one service (seen for Azure SQL and Kusto); the rest still works
    [/IncorrectConfiguration|AADSTS65002/i, "Windows sign-in wouldn't give KratosForge's built-in sign-in a token for this service (warehouse queries or warehouse Monitoring often need one). The rest of the app keeps working. docs/troubleshooting.md says what IT can check."],
    [/AADSTS50058|AADSTS50173|AADSTS700082|AADSTS70043|invalid_grant|\b401\b|Unauthorized|token.*expired/i, "Your sign-in expired. Sign in again and retry."],
    [/^SignInError: (AADSTS\d+)/, "Microsoft sign-in didn't finish. Sign in again; if it happens again, ask IT and quote the code in the details below."],
    [/^SignInError: /, "Microsoft sign-in didn't finish. Sign in again; if it happens again, the details below help IT."],
    [/AADSTS|interaction_required/i, "Your sign-in needs attention. Sign in again and retry; if it happens again, ask IT and quote the code in the details below."],
    [/\b403\b|Forbidden|InsufficientPrivileges|PrincipalTypeNotSupported/i, "You don't have permission for that in this workspace. A workspace admin can make you a Contributor or above."],
    [/\b404\b|NotFound|ItemNotFound|does not exist/i, "Fabric couldn't find that item. It may have been deleted or renamed."],
    [/\b429\b|TooManyRequests|throttl/i, "Fabric is busy and asked the app to slow down. Try again in a minute."],
    [/ConnectionError|Max retries|timed out|Timeout|NameResolution|getaddrinfo|ECONN|network/i, "Couldn't reach Fabric. Check the internet connection and try again."],
    [/DisplayName is Invalid|InvalidDisplayName/i, "Fabric doesn't accept that name. A lakehouse name can use only letters, numbers and underscores, and must start with a letter."],
    [/CapacityNotActive|capacity.*paused|CapacityLimitExceeded/i, "The workspace's Fabric capacity is paused or full."],
  ];
  if (/^SignInCancelled: /.test(raw)) return { text: "", details: "", cancelled: true };  // closed the sign-in window: not a failure
  if (!raw) return { text: "Something went wrong and no reason was given. Try again; if it keeps happening, report a problem.", details: "" };
  for (const [re, text] of engine) if (re.test(raw)) return { text, details: raw };
  const local = localError(raw);
  if (local) return local;
  for (const [re, text] of known) if (re.test(raw)) return { text, details: raw };
  // The sidecar's own errors already explain themselves; only drop the exception name.
  // PlatformError is the platform refusing in words a person can act on (central/core.py), like "Only platform admins can ...".
  // NotInInventory is a LookupError (workload_service.py) whose name ends in neither Error nor Exception.
  const m = /^(ValueError|LookupError|KeyError|RuntimeError|PermissionError|PlatformError|FileNotFoundError|GateError|GitError|Conflict|NotInInventory): ([\s\S]*)$/.exec(raw);
  if (m) return { text: m[2].replace(/^'(.*)'$/, "$1"), details: m[1] === "GateError" || m[1] === "GitError" ? "" : raw };
  if (/^[A-Z][A-Za-z]*(Error|Exception): /.test(raw)) {
    return { text: "Something went wrong inside the app. The details below help with a bug report.", details: raw };
  }
  return { text: raw, details: "" };
}

// UX-011: one way to show a failure inside a page or panel. A sentence from friendlyError, what to
// do, Retry when the caller passes one, and the raw text under Details with Copy. Pass retry only
// for a read, or an action that is safe to repeat: it runs again with no further question.
// Returns null for a cancelled sign-in, which isn't a failure.
function errorPanel(err, { retry = null, label = "", hint = "" } = {}) {
  const f = friendlyError(err);
  if (f.cancelled) return null;
  const raw = f.details || "";
  const what = hint || (retry ? "Try again. If it keeps failing, copy the details into Report a problem."
    : raw ? "If it keeps happening, copy the details into Report a problem." : "");
  const again = retry && btn("Retry", "sync", async () => {
    again.disabled = true;
    try { await retry(); } finally { if (again.isConnected) again.disabled = false; }
  }, "tool small error-panel-retry");
  const copy = () => navigator.clipboard.writeText(raw)
    .then(() => toast("Copied the details"), () => toast("Couldn't copy. Select the text and copy it instead.", true, { quiet: true }));
  return h("div", { class: "error-panel", role: "alert" },
    label && !f.text.startsWith(label) ? h("b", { class: "error-panel-label" }, label) : null,
    h("p", { class: "error-panel-text" }, f.text),
    what ? h("p", { class: "error-panel-hint" }, what) : null,
    again || null,
    raw ? h("details", { class: "error-panel-details" }, h("summary", {}, "Details"),
      h("pre", {}, raw), btn("Copy", null, copy, "tool small", { "aria-label": "Copy the details" })) : null);
}

// ---------- activity ----------

// Items: {id, status: info|ok|error|failed|running, title, text, details, at, progress, results, url, request}.
// Kept on this PC so the history survives a restart; nothing in them is needed to work.
const activity = { items: [], open: false, filter: "all", seenAt: 0, pollTimer: null, platform: null };

(function loadActivity() {
  try {
    const saved = JSON.parse(localStorage.getItem("fd.activity") || "{}");
    activity.items = (saved.items || []).map((i) => (i.status === "running" ? { ...i, stale: true } : i));
    activity.seenAt = saved.seenAt || 0;
  } catch { /* private window or cleared storage: start empty */ }
})();

function saveActivity() {
  try {
    localStorage.setItem("fd.activity", JSON.stringify({ items: activity.items.slice(0, 80), seenAt: activity.seenAt }));
  } catch { /* storage full or blocked: the tray still works for this session */ }
}

function logActivity(item) {
  const id = item.id || `a${Date.now()}${Math.random().toString(36).slice(2, 6)}`;
  const i = activity.items.findIndex((x) => x.id === id);
  const next = { at: new Date().toISOString(), ...(i >= 0 ? activity.items[i] : {}), ...item, id, stale: false };
  if (i >= 0) activity.items.splice(i, 1);
  activity.items.unshift(next);
  activity.items = activity.items.slice(0, 80);
  saveActivity();
  renderBell();
  if (activity.open) renderTray();
  return next;
}

const PROBLEM = new Set(["error", "failed"]);

function renderBell() {
  const bell = document.getElementById("bell");
  if (!bell) return;
  const running = activity.items.filter((i) => i.status === "running" && !i.stale).length;
  const unseen = (i) => new Date(i.at).getTime() > activity.seenAt;
  const problems = activity.items.filter((i) => PROBLEM.has(i.status) && unseen(i)).length;
  const notices = activity.items.filter((i) => i.notice && !PROBLEM.has(i.status) && unseen(i)).length;
  const count = running + problems + notices;
  const parts = [running ? `${running} running` : "", problems ? `${problems} new problem${problems === 1 ? "" : "s"}` : "",
    notices ? `${notices} notice${notices === 1 ? "" : "s"}` : ""].filter(Boolean);
  bell.setAttribute("aria-label", `Activity${parts.length ? `, ${parts.join(", ")}` : ""}`);
  bell.title = parts.length ? `Activity: ${parts.join(", ")}` : "Activity";
  bell.classList.toggle("on", activity.open);
  bell.replaceChildren(icon("alert", 20), ...(count ? [h("span", { class: `badge${problems ? " bad" : ""}` }, count)] : []));
}

function toggleTray(open = !activity.open) {
  activity.open = open;
  if (open) activity.seenAt = Date.now();
  saveActivity();
  renderBell();
  renderTray();
  placeFabricView();
  if (open) loadPlatformNotices().then(() => { if (activity.open) renderTray(); });
}

function renderTray() {
  const root = document.getElementById("tray-root");
  if (!activity.open) { motionLeave(root.querySelector(":scope > .tray")); root.replaceChildren(); return; }
  const running = activity.items.filter((i) => i.status === "running" && !i.stale);
  const problems = activity.items.filter((i) => PROBLEM.has(i.status));
  const platform = platformNoticesShown();
  if (activity.filter === "platform" && !platform) activity.filter = "all";
  const shown = activity.filter === "running" ? running : activity.filter === "problems" ? problems : activity.items;
  const chip = (id, label) => h("button", { class: `chip${activity.filter === id ? " on" : ""}`, onclick: () => { activity.filter = id; renderTray(); } }, label);
  root.replaceChildren(h("aside", { class: "tray", "aria-label": "Activity" },
    h("div", { class: "tray-head" }, h("h2", {}, "Activity"), h("span", { class: "spacer" }),
      btn("Clear finished", null, () => {
        activity.items = activity.items.filter((i) => i.status === "running" && !i.stale);
        saveActivity(); renderBell(); renderTray();
      }, "tool link small"),
      h("button", { class: "tool small", "aria-label": "Close", onclick: () => toggleTray(false) }, icon("dismiss", 16))),
    h("div", { class: "tray-chips" }, chip("all", "All"), chip("running", `Running ${running.length}`), chip("problems", `Problems ${problems.length}`),
      platform ? chip("platform", `Platform ${activity.platform.open.length}`) : null),
    h("div", { class: "tray-list" }, activity.filter === "platform" ? platformTrayList() : shown.length ? shown.map(trayItem)
      : h("p", { class: "muted-text tray-empty" }, activity.filter === "all" ? "Nothing yet. Runs, publishes and problems show up here." : "Nothing here."))));
}

const TRAY_LABEL = { update: ["Update", "info"], running: ["Running", "info"], ok: ["Done", "pass"], info: ["Done", "pass"], cancelled: ["Cancelled", "neu"], failed: ["Failed", "fail"], error: ["Couldn't finish", "fail"] };

function trayItem(i) {
  const [label, cls] = i.stale ? ["Unknown", "neu"] : TRAY_LABEL[i.status] || ["", "neu"];
  const links = [];
  if (i.results) links.push(btn("See results", null, () => { toggleTray(false); showRunResults(i.results, i.results.find((r) => r.status === "needs_decision")); }, "tool link small"));
  if (i.monitor) links.push(btn("Open in Monitor", null, () => { toggleTray(false); go("monitor"); }, "tool link small"));
  if (i.url) links.push(btn("Open in Fabric", null, () => { toggleTray(false); openInFabric(i.url); }, "tool link small"));
  if (i.status === "running" && !i.stale && remoteActivity(i) && cancelFabricRunShown()) {
    links.push(btn("Cancel run", null, () => cancelFabricJob(i.id.slice("fabric:".length)), "tool link small danger"));
  }
  if (i.update) links.push(btn("Check for updates", null, () => { toggleTray(false); checkForUpdates(); }, "tool link small"));
  if (i.details) links.push(btn("Copy details", null, () => copyText(i.details, "Details"), "tool link small"));
  return h("div", { class: `tray-item${PROBLEM.has(i.status) ? " bad" : ""}` },
    h("div", { class: "row" }, h("span", { class: `pill-s ${cls}` }, label), h("b", { class: "grow" }, i.title || ""), h("span", { class: "muted-text small" }, when(i.at))),
    i.status === "running" && !i.stale ? runningBar(i) : null,
    i.text ? h("div", { class: "tray-text" }, i.text) : null,
    i.stale ? h("div", { class: "muted-text small" }, "The app was closed while this ran. Monitor has its final state.") : null,
    i.details ? h("details", {}, h("summary", {}, "Details"), h("pre", {}, i.details)) : null,
    links.length ? h("div", { class: "row wrap" }, links) : null);
}

// A job the app started in Fabric (id fabric:…) reports no progress of its own.
const remoteActivity = (i) => String(i.id || "").startsWith("fabric:") && i.progress === undefined;

// A running entry's bar: how far it is when that's known, the navy wolf for a run in Fabric, else indeterminate.
function runningBar(i) {
  if (remoteActivity(i)) return remoteProgress(i.title);
  return h("div", { class: "progress", role: "progressbar", "aria-label": i.title },
    h("div", { class: i.progress === undefined ? "bar indeterminate" : "bar", style: i.progress === undefined ? "" : `width:${Math.round(i.progress * 100)}%` }));
}

// Jobs the app started in Fabric: follow every unfinished one until it ends, wherever the user is.
async function trackFabricJobs() {
  clearTimeout(activity.pollTimer);
  const runs = await rpc("fabric_runs", { limit: 50 }).catch(() => null);
  if (!runs) return;
  let open = 0;
  for (const r of runs) {
    const id = `fabric:${r.job_id}`;
    const item = activity.items.find((i) => i.id === id);
    const done = FABRIC_DONE.has(r.status);
    if (!done) {
      open++;
      if (!item || item.status !== "running" || item.stale) {
        logActivity({ id, status: "running", title: item?.title || `${r.name} · ${r.kind}`, text: `In Fabric on ${r.workspace_name || "the workspace"}`, url: r.url, at: r.started_at, monitor: true });
      }
    } else if (item && item.status === "running" && !item.request) {
      // Metadata runs (item.request) finish through watchMetaFabricRun, which also reads their results.
      const ok = r.status === "Completed";
      const cancelled = r.status === "Cancelled";  // someone stopped it: not a problem
      logActivity({ id, status: ok ? "ok" : cancelled ? "cancelled" : "failed",
        text: ok ? "Finished in Fabric" : `${r.status} in Fabric. ${r.message || ""}`.trim(), at: r.finished_at || new Date().toISOString() });
      toast(`${item.title}: ${ok ? "finished" : r.status.toLowerCase()} in Fabric`, !ok && !cancelled, { quiet: true });
    }
  }
  if (open) activity.pollTimer = setTimeout(trackFabricJobs, 15000);
}

// Cancel in Monitor > Runs and the tray is Preview until a cancel has been checked on a tenant.
const cancelFabricRunShown = () => previewShown() && !!PREVIEW_FEATURES.cancelFabricRun;

// Cancel a Fabric job this app started (Monitor > Runs and the tray), after a danger confirm. Fabric
// stops it in its own time: the run reads Cancelling until fabric_runs reads how it ended.
async function cancelFabricJob(jobId) {
  const runs = await rpc("fabric_runs", { limit: 50, refresh: false }).catch(() => null);
  const r = runs?.find((x) => x.job_id === jobId);
  if (!r) { toast("This run isn't in the app's list of Fabric runs any more. Cancel it from Fabric's Monitoring hub.", true); return false; }
  const name = r.name || "this run";
  const ws = r.workspace_name || "its workspace";
  if (FABRIC_DONE.has(r.status)) { toast(`${name} already ended in Fabric (${r.status}).`); return false; }
  const ok = await confirmDialog(`Cancel ${name} in Fabric?`,
    `This asks Fabric to stop the ${(r.kind || "job").toLowerCase()} on ${ws}. What it already wrote stays written, and the capacity it used so far is still used. It can't be resumed: run it again to start over.`,
    "Cancel run", { danger: true, cancelLabel: "Keep running" });
  if (!ok) return false;
  const out = await attempt(() => rpc("cancel_fabric_run", { workspace_id: r.workspace_id, item_id: r.item_id, job_id: jobId }), "Could not cancel");
  if (!out) return false;
  const id = `fabric:${jobId}`;
  if (activity.items.some((i) => i.id === id)) logActivity({ id, text: `Cancelling in Fabric on ${ws}` });
  toast(`Asked Fabric to cancel ${name}. It shows Cancelled once Fabric stops it.`);
  trackFabricJobs();
  return true;
}

// ---------- environment and compute in the top bar ----------

function envWhere(p, env) {
  const cfg = p?.environments?.[env] || {};
  const local = (cfg.target || (env === "dev" ? "local" : "onelake")) === "local";
  const ws = cfg.workspace_name || cfg.workspace_id;
  if (!p || local) return { fabric: false, text: "Runs on this PC", short: "This PC" };
  if (!ws) return { fabric: false, text: "No workspace yet", short: "Not set up" };
  if (cfg.compute === "fabric") return { fabric: true, text: `Runs in Fabric · ${ws}`, short: `Fabric · ${ws}` };
  return { fabric: false, text: `Runs on this PC · writes to ${ws}`, short: `This PC · writes to ${ws}` };
}

function setGlobalEnv(env) {
  state.meta.env = env;
  state.meta.runs = null;
  try { localStorage.setItem("fd.env", env); } catch { /* not remembered: fine */ }
  render();
}

// Only the Data mesh pages (and the entity page) work against one environment, so the picker
// shows there and nowhere else. Home's environment tiles pick one by opening it.
const ENV_VIEWS = new Set(["metadata", "entity"]);

function renderEnvBar() {
  const slot = document.getElementById("env-slot");
  if (!slot || !state.meta) return;
  if (!ENV_VIEWS.has(state.view) || !state.meta.project?.path) { slot.replaceChildren(); return; }
  const env = state.meta.env;
  const where = envWhere(state.meta.project, env);
  const select = h("select", { class: "env-select", "aria-label": "Environment", onchange: (e) => setGlobalEnv(e.target.value) },
    Object.entries(ENV_LABEL).map(([id, label]) => h("option", { value: id, selected: env === id ? true : null }, label)));
  slot.replaceChildren(
    h("label", { class: "env-label" }, h("span", { class: "muted-text" }, "Data mesh environment"), select),
    h("button", { class: `pill compute${where.fabric ? " fabric" : ""}`, title: "Where this environment runs. Change it in Data mesh > Environments and promote.",
      onclick: () => goMeta("environments") }, icon(where.fabric ? "cloud" : "desktop", 14), where.text));
}

// ---------- one way to show a run ----------

// An entity name that wraps after its layer ("silver." then "orders"), never mid-word.
function entityName(name) {
  const [layer, ...rest] = String(name).split(".");
  return rest.length ? [`${layer}.`, h("wbr"), rest.join(".")] : name;
}

// A metadata run row for the entity page: status, where and when, and the reason if it failed.
// Where a metadata run ran: Fabric, an agent (by name and host) or this PC, as the engine says.
function ranOn(r) {
  return r.ran_on || (r.compute === "fabric" ? "Fabric" : "This PC");
}

function runLine(r, onOpen) {
  const where = ranOn(r);
  const reason = runReason(r);
  return h("div", { class: "run-line" },
    statusPill(r.status),
    h("div", { class: "grow" },
      h("div", {}, `${ENV_LABEL[r.env] || r.env} · ${where} · `, when(r.started_at)),
      h("div", { class: reason ? "reason" : "muted-text small" },
        reason || `${r.rows_written ?? 0} rows written${r.quarantined ? `, ${r.quarantined} quarantined` : ""} · ${took(r.seconds)}`)),
    onOpen ? btn("Details", null, onOpen, "tool link small") : null);
}
