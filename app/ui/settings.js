// Settings, the account menu, Report a problem, What's new and
// Get started. The values live in the sidecar's app_settings.py: this person's choices, or
// what their organization fixed in its policy file.

const SOURCE_TEXT = {
  policy: "Set by your organization's policy",
  environment: "Set by the FABRIC_DESKTOP_CLIENT_ID environment variable",
  user: "Set by you",
  built_in: "Comes with KratosForge",
  default: "Default",
};
// When sign-in isn't Microsoft's own, people see that app's name, publisher and consent prompt instead,
// so Settings says so plainly and how to go back (unit testing item 4).
function clientIdNote(source) {
  const lead = "Sign-in isn't using Microsoft's Fabric sign-in. Microsoft shows this app's own name and publisher, and may ask you or an admin to approve it.";
  if (source === "policy") return `${lead} Your organization set it, so only they can change it.`;
  if (source === "environment") return `${lead} It comes from the FABRIC_DESKTOP_CLIENT_ID setting on this PC. To go back, remove that variable (setx FABRIC_DESKTOP_CLIENT_ID ""), restart KratosForge and sign in again.`;
  if (source === "user") return `${lead} To go back, clear the ID and sign in again.`;
  if (source === "built_in") return `${lead} It came with this build.`;
  return null;
}
// Shown under the app ID when nothing set one.
const MICROSOFT_SIGN_IN = "Microsoft's Fabric sign-in (no app registration)";

async function loadSettings() {
  state.settings = await rpc("app_settings").catch(() => state.settings || null);
  applyMotion();  // motion.js
  applyAppearance();  // features.js
  return state.settings;
}

async function saveSettings(changes, label = "Not saved") {
  const res = await attempt(() => rpc("save_app_settings", { changes }), label);
  if (res) state.settings = res;
  applyMotion();
  applyAppearance();  // features.js
  return res;
}

function clientId() {
  return state.settings?.client_id?.value || "";
}

// The link an Entra admin opens once to approve Fabric Desktop for everyone in their organization.
function adminConsentUrl(id = clientId()) {
  return `https://login.microsoftonline.com/organizations/adminconsent?client_id=${encodeURIComponent(id)}`;
}

async function copyText(text, what) {
  try {
    await navigator.clipboard.writeText(text);
    toast(`${what} copied`);
    return true;
  } catch {
    return false;
  }
}

function openLink(url) {
  return attempt(() => invoke("open_link", { url }), "Could not open the link");
}

// ---------- sign-out ----------

async function signOut() {
  if (!(await confirmDialog("Sign out?", "KratosForge forgets your sign-in on this PC, in the app and in the Fabric views. " +
    "Scheduled runs on this PC wait until someone signs in again. Notebooks and settings stay.", "Sign out"))) return;
  // A refused sign-out keeps the sign-in: saying "Signed out" over the error would leave it on this PC (#1437).
  if (!(await attempt(() => rpc("sign_out"), "Sign-out failed"))) return;
  await invoke("fabric_view_sign_out").catch(() => {});
  Object.assign(state, { user: null, tenant: null, workspaces: null, openWorkspace: null });
  try { localStorage.removeItem("fd.ready.write"); } catch { /* */ }
  toast("Signed out");
  go("home");
}

// ---------- account menu ----------

function accountMenu(anchor) {
  const items = state.user
    ? [{ info: `Signed in as ${state.user}` }]
    : [{ label: "Sign in", icon: "person", action: () => signIn().then((ok) => ok && render()) }];
  items.push(
    previewShown() ? { label: "Profile (Preview)", icon: "person", action: () => { profilePage.tab = "overview"; go("profile"); } } : null,
    { label: "Settings", icon: "settings", action: () => go("settings") },
    { label: "Help and user guide", icon: "help", action: () => invoke("open_doc", { page: "user-guide.md" }).catch(() => {}) },
    { label: "Report a problem", icon: "bug", action: reportProblem },
    { label: "What's new", icon: "rocket", action: () => showWhatsNew(true) },
    { label: "Check for updates", icon: "sync", action: checkForUpdates },
    { info: `KratosForge build ${state.build || "unknown"} · ${channelLabel()}` },
  );
  if (state.user) items.push({ label: "Sign out", icon: "signOut", action: signOut });
  showMenu(anchor, items.filter(Boolean));
}

function channelLabel() {
  return state.settings?.channel?.value === "preview" ? "Preview channel" : "Stable channel";
}

// ---------- Report a problem ----------

// Takes out what a public issue mustn't carry: tokens, keys, passwords, email addresses (a guest's too),
// the tenant's onmicrosoft.com and SharePoint names, the organization on OneDrive folders, the Windows user name in paths, and tenant,
// workspace and item ids. The same rules as the engine's
// redact.py; both are tested against app/tests/redact-cases.json.
function redactReport(text, level = "support") {
  const slash = "(?:\\\\\\\\|\\\\|/)+";
  const out = String(text)
    .replace(/eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]*/g, "[removed]")
    .replace(/(\bbearer\s+)[A-Za-z0-9._~+/=\-]{8,}/gi, "$1[removed]")
    .replace(/(\bauthorization\s*[:=]\s*basic\s+)[A-Za-z0-9+/=]{4,}/gi, "$1[removed]")
    .replace(/(\b(?:basic|Basic|BASIC)\s+)(?=[A-Za-z0-9+/]*(?:[0-9+/=]|[a-z][A-Z]))[A-Za-z0-9+/]{8,}={0,2}/g, "$1[removed]")
    .replace(/([?&]sig=)[^&\s"'<>]+/gi, "$1[removed]")
    .replace(/(\b[a-z][a-z0-9+.\-]*:\/\/)[^/\s:@"'<>]+:[^@/\s"'<>]+@/gi, "$1[removed]@")
    .replace(/https:\/\/(?:[A-Za-z0-9.\-]+\.webhook\.office\.com\/|outlook\.office(?:365)?\.com\/webhook(?:b2)?\/)[^\s"'<>]+/gi, "[removed]")
    .replace(/\/webhook(?:b2)?\/[^\s"'<>/]+@[^\s"'<>/]+\/IncomingWebhook\/[^\s"'<>]+/gi, "[removed]")
    .replace(/\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b/g, "[removed]")
    .replace(/(?<![A-Za-z0-9_~.\-])[A-Za-z0-9_~.\-]{3}\dQ~[A-Za-z0-9_~.\-]{31,34}(?![A-Za-z0-9_~.\-])/g, "[removed]")
    .replace(/\bAKIA[0-9A-Z]{16}\b/g, "[removed]")
    .replace(/\bdapi[0-9a-f]{32}(?:-\d+)?\b/g, "[removed]")
    .replace(/\bdose[0-9a-f]{32}\b/g, "[removed]")
    .replace(/("(?:client_secret|password|passwd|(?:[a-z0-9]+[_-])*passphrase|pwd|accountkey|sharedaccesskey|(?:[a-z0-9]+[_-])*token|api[_-]?key|secret)"\s*:\s*")(?:[^"\\]|\\.)*/gi, "$1[removed]")
    .replace(/('(?:client_secret|password|passwd|(?:[a-z0-9]+[_-])*passphrase|pwd|accountkey|sharedaccesskey|(?:[a-z0-9]+[_-])*token|api[_-]?key|secret)'\s*:\s*')(?:[^'\\]|\\.)*/gi, "$1[removed]")
    .replace(/\b((?:client_secret|password|passwd|(?:[a-z0-9]+[_-])*passphrase|pwd|accountkey|sharedaccesskey|(?:[a-z0-9]+[_-])*token|api[_-]?key|secret)\s*[=:]\s*")(?:[^"\\]|\\.)*/gi, "$1[removed]")
    .replace(/\b((?:client_secret|password|passwd|(?:[a-z0-9]+[_-])*passphrase|pwd|accountkey|sharedaccesskey|(?:[a-z0-9]+[_-])*token|api[_-]?key|secret)\s*[=:]\s*')(?:[^'\\]|\\.)*/gi, "$1[removed]")
    .replace(/\bxox[abposr]-[A-Za-z0-9\-]{10,}\b/g, "[removed]")
    .replace(/-----BEGIN (?:[A-Z]+ )*PRIVATE KEY(?: BLOCK)?-----(?:[\s\S]*?-----END (?:[A-Z]+ )*PRIVATE KEY(?: BLOCK)?-----|[\s\S]*)/g, "[removed]")
    .replace(/\b(client_secret|password|passwd|(?:[a-z0-9]+[_-])*passphrase|pwd|accountkey|sharedaccesskey|(?:[a-z0-9]+[_-])*token|api[_-]?key|secret)("?\s*[=:]\s*"?)[^\s"'&;,]+/gi, "$1$2[removed]");
  if (level !== "support") return out;
  return out
    .replace(/[A-Za-z0-9._%+\-#]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+/g, "[email]")
    .replace(/\b[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9][a-z0-9\-]*)*(\.onmicrosoft\.(?:com|us|de|cn))\b/gi, "[tenant]$1")
    .replace(/(OneDrive - )([^\\/"'<>:\r\n]+)/g, "$1[organization]")
    .replace(/\b[a-z0-9][a-z0-9\-]*?(-my|-admin)?(\.sharepoint\.(?:com|us|de|cn))\b/gi, "[tenant]$1$2")
    .replace(/(\.sharepoint\.(?:com|us|de|cn)\/(?:sites|teams)\/)[^\/\s"'<>?#]+/gi, "$1[site]")
    .replace(/(\/personal\/)[a-z0-9][a-z0-9_.\-]*/gi, "$1[user]")
    .replace(new RegExp(`([a-z]:${slash}(?:users|documents and settings)${slash})([^\\\\/"'<>\\r\\n]+)`, "gi"), "$1[user]")
    .replace(/(\/home\/|\/Users\/)([^\\/\s"'<>]+)/g, "$1[user]")
    .replace(/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi, "[id]");
}

function diagnostics() {
  const errors = activity.items.filter((i) => i.status === "error" || i.status === "failed").slice(0, 10);
  return [
    `KratosForge build: ${state.build || "unknown"} (${channelLabel()})`,
    `Engine Python: ${state.python || "unknown"}`,
    `Signed in: ${state.tenant ? "yes" : "no"}`,
    `Page: ${state.view}`,
    `Windows: ${navigator.userAgent}`,
    "",
    errors.length ? "Recent problems (newest first):" : "No recent problems in Activity.",
    ...errors.map((i) => `- ${new Date(i.at).toISOString()} ${i.title}${i.text ? `: ${i.text}` : ""}${i.details ? `\n    ${String(i.details).slice(0, 400).replace(/\n/g, "\n    ")}` : ""}`),
  ].map((line) => redactReport(line)).join("\n");
}

function reportProblem() {
  const what = h("textarea", { rows: 3, placeholder: "What were you doing, and what went wrong?" });
  const info = h("textarea", { rows: 9, class: "mono", readonly: true }, diagnostics());
  const support = state.settings?.support_url?.value || "";
  const report = () => redactReport(`${what.value.trim() || "(no description)"}\n\n${info.value}`);
  const send = async () => {
    const text = report();
    if (support.startsWith("mailto:")) {
      // Mail apps cap a link's length; the full text is also on the clipboard.
      await copyText(text, "Report");
      openLink(`${support}${support.includes("?") ? "&" : "?"}subject=${encodeURIComponent("KratosForge problem")}&body=${encodeURIComponent(text.slice(0, 1500))}`);
    } else {
      await copyText(text, "Report");
      openLink(support || "https://github.com/KratosDataGod/KratosForge/issues/new");
    }
    closeDialog();
  };
  showDialog("Report a problem", h("div", { class: "setup" },
    h("label", { class: "field" }, "What happened", what),
    h("label", { class: "field" }, "Sent with it (no data, file contents, passwords, email addresses or ids)", info),
    h("p", { class: "muted-text small" }, support
      ? "Send copies all of this and opens your organization's support contact. Paste it there if it isn't filled in."
      : "Send copies all of this and opens a new issue on GitHub. Paste it there.")),
  [btn("Copy only", null, () => copyText(report(), "Report"), "tool outline"), h("span", { class: "spacer" }),
    btn("Cancel", null, closeDialog, "tool outline"), btn("Send", null, send, "tool primary")]);
}

// ---------- Check for updates ----------

// What a manual update check found (check_for_update): "newer" offers Install and restart; every
// other kind offers only the channel's release page.
function updateCheckResult(report) {
  const short = (c) => c.slice(0, 7);
  const { commit, latest } = report;
  const channel = latest.channel === "preview" ? "Preview" : "Stable";
  if (!commit) return { kind: "local", text: "This is a local build, so it doesn't update itself." };
  if (!latest.commit) return { kind: "error", text: `Couldn't check the ${channel} channel: ${latest.error}` };
  if (latest.refused) return { kind: "error", text: latest.refused };
  if (latest.commit === commit) return { kind: "current", text: `You're up to date. The ${channel} channel has nothing newer than this build.` };
  if (report.auto_update === false) {
    return { kind: "managed", text: `Build ${short(latest.commit)} is out. Your organization rolls out new builds, so ask IT for it.` };
  }
  return { kind: "newer", text: `Build ${short(latest.commit)} is out. Install it now: KratosForge downloads it, ` +
    "saves the open notebook, closes and starts the new build." };
}

// The startup update on demand (account menu, Settings): this build next to the channel's newest,
// installed through the same checked download and hand-over as at startup (update_app), or
// downloaded by hand from the channel's release page.
async function checkForUpdates() {
  const short = (c) => (c ? c.slice(0, 7) : "unknown");
  const show = (lines, actions) => showDialog("Check for updates", h("div", { class: "setup" }, lines), actions);
  show([h("p", {}, "Checking for a newer build…")], []);
  let report;
  try {
    report = await invoke("check_for_update");
  } catch (err) {
    show([h("p", { class: "error-text" }, String(err))],
      [btn("Try again", null, checkForUpdates, "tool outline"), btn("Close", null, closeDialog, "tool primary")]);
    return;
  }
  const { latest } = report;
  const result = updateCheckResult(report);
  const facts = h("dl", { class: "about" },
    h("dt", {}, "This build"), h("dd", {}, report.commit ? short(report.commit) : state.build || "local build"),
    h("dt", {}, `Latest on ${latest.channel === "preview" ? "Preview" : "Stable"}`), h("dd", {}, short(latest.commit)));
  const manual = btn("Download manually", "arrowDownload", () => openLink(latest.page), "tool outline");
  const note = h("p", { class: "muted-text small" }, result.kind === "newer"
    ? "Running cells stop. Or download kratosforge.exe from the release page and run it in place of this one."
    : result.kind === "current" ? "" : "You can download kratosforge.exe from the channel's release page and run it in place of this one.");
  if (latest.note) note.prepend(`${latest.note} `);
  const text = h("p", { class: result.kind === "error" ? "error-text" : "" }, result.text);
  if (result.kind !== "newer") {
    show([facts, text, note.textContent ? note : null], [
      result.kind === "error" ? btn("Try again", null, checkForUpdates, "tool outline") : null,
      latest.page ? manual : null, h("span", { class: "spacer" }), btn("Close", null, closeDialog, "tool primary")].filter(Boolean));
    return;
  }
  const install = btn("Install and restart", "arrowDownload", async () => {
    install.disabled = true;
    later.disabled = true;
    try {
      if (await installUpdate(latest.commit, (label) => { install.lastChild.textContent = label; })) return;
      install.lastChild.textContent = "Install and restart";
    } catch (err) {
      text.className = "error-text";
      text.textContent = `Update to build ${short(latest.commit)} failed: ${err} You are still on ${short(report.commit)}.`;
      install.lastChild.textContent = "Try again";
    }
    install.disabled = later.disabled = false;
  }, "tool primary");
  const later = btn("Later", null, closeDialog, "tool outline");
  show([facts, text, note], [manual, h("span", { class: "spacer" }), later, install]);
}

// Saves the open notebook, then downloads the build and hands over to it, the same checked
// download as at startup (update_app). False when the notebook wasn't saved; throws when the
// update failed. setLabel shows how far the download is.
async function installUpdate(commit, setLabel) {
  if (state.notebook?.dirty && !(await saveNotebook())) return false;
  setLabel("Downloading…");
  const unlisten = await window.__TAURI__.event.listen("component-log", (e) => {
    const m = /^progress (\d+)/.exec(e.payload);
    if (m) setLabel(m[1] === "100" ? "Restarting…" : `Downloading… ${m[1]}%`);
  });
  try {
    await invoke("update_app", { commit });
    setLabel("Restarting…");  // the new build takes over
    return true;
  } finally {
    unlisten();
  }
}

// ---------- New build banner ----------

// While the app is open, the same check as Check for updates (the channel's version.json, nothing
// else sent) every 4 hours. A newer build shows a banner below the top bar and a bell entry; Later
// hides the banner for that build only. Off in Settings > Updates, or with preview features hidden.
const UPDATE_WATCH_OFF = "fd.updatewatch.off";
const UPDATE_WATCH_LATER = "fd.updatewatch.later";
const UPDATE_WATCH_MS = 4 * 60 * 60 * 1000;
const updateWatch = { timer: null, notice: null, checkedAt: null, busy: false };

function updateWatchOn() {
  try { return localStorage.getItem(UPDATE_WATCH_OFF) !== "1"; } catch { return true; }
}

// What the banner says about a check_for_update report: null unless the channel has a newer build.
// A PC whose organization rolls out builds hears it's out but gets no install.
function updateNotice(report) {
  const result = updateCheckResult(report);
  if (result.kind !== "newer" && result.kind !== "managed") return null;
  const short = (c) => c.slice(0, 7);
  const channel = report.latest.channel === "preview" ? "Preview" : "Stable";
  return {
    kind: result.kind, commit: report.latest.commit, page: report.latest.page || "",
    title: result.kind === "newer" ? "A new KratosForge build is ready." : `Build ${short(report.latest.commit)} is out.`,
    text: result.kind === "newer"
      ? `Build ${short(report.latest.commit)} is out on ${channel} (you're on ${short(report.commit)}). Restarting saves your open notebook and stops running cells.`
      : "Your organization rolls out KratosForge updates, so ask IT when it reaches you.",
  };
}

function scheduleUpdateWatch() {
  clearTimeout(updateWatch.timer);
  updateWatch.timer = setTimeout(watchForUpdates, UPDATE_WATCH_MS);
}

async function watchForUpdates() {
  scheduleUpdateWatch();
  if (!updateWatchOn() || !previewShown() || !state.commit) return;
  let report;
  try {
    report = await invoke("check_for_update");
  } catch {
    return;  // offline or no Python: the next check tries again, and Check for updates says why
  }
  updateWatch.checkedAt = new Date();
  updateWatch.notice = updateNotice(report);
  const n = updateWatch.notice;
  if (n && !activity.items.some((i) => i.id === `update:${n.commit}`)) {
    logActivity({ id: `update:${n.commit}`, status: "update", notice: true, update: true,
      title: n.kind === "newer" ? `Update ready: build ${n.commit.slice(0, 7)}` : n.title, text: n.text });
  }
  renderUpdateBanner();
}

function updateLater() {
  try { return localStorage.getItem(UPDATE_WATCH_LATER) || ""; } catch { return ""; }
}

function renderUpdateBanner() {
  const root = document.getElementById("update-root");
  if (!root) return;
  const n = updateWatch.notice;
  if (!n || !updateWatchOn() || !previewShown() || n.commit === updateLater()) {
    if (root.childElementCount) { root.replaceChildren(); placeFabricView(); }
    return;
  }
  const later = () => {
    try { localStorage.setItem(UPDATE_WATCH_LATER, n.commit); } catch { /* hidden until the app restarts */ }
    updateWatch.notice = null;
    renderUpdateBanner();
  };
  const text = h("div", { class: "grow" }, h("b", {}, n.title), " ", n.text);
  const actions = [];
  if (n.page) actions.push(btn("Release notes", null, () => openLink(n.page), "tool link small"));
  if (n.kind === "newer") {
    const restart = btn("Restart to update", "arrowDownload", async () => {
      restart.disabled = true;
      try {
        if (await installUpdate(n.commit, (label) => { restart.lastChild.textContent = label; })) return;
        restart.lastChild.textContent = "Restart to update";
      } catch (err) {
        text.replaceChildren(h("b", { class: "error-text" }, `Update to build ${n.commit.slice(0, 7)} failed: ${err}`),
          " You are still on this build. Check for updates in the account menu can try again.");
        restart.lastChild.textContent = "Try again";
      }
      restart.disabled = false;
    }, "tool primary small");
    actions.push(btn("Later", null, later, "tool outline small"), restart);
  } else {
    actions.push(btn("Dismiss", null, later, "tool outline small"));
  }
  root.replaceChildren(h("div", { class: "update-banner", role: "status" }, icon("arrowDownload", 18), text, ...actions));
  placeFabricView();
}

// ---------- What's new ----------

// After an update, the release notes of the build now running, once. From the account menu,
// whenever asked. Below the notes, everything that reached main in the 30 days before the build
// (version.json "history", newest first): the 10 newest show, the rest behind Show more.
function showWhatsNew(asked = false) {
  const release = state.release?.commit && state.release.commit === state.commit ? state.release : null;
  const notes = release?.notes || "";
  let seen = "";
  try { seen = localStorage.getItem("fd.whatsnew.seen") || ""; } catch { /* show it */ }
  if (!asked && (!notes || !state.commit || seen === state.commit)) return;
  try { localStorage.setItem("fd.whatsnew.seen", state.commit || ""); } catch { /* shown again next time */ }
  const { lines, items } = whatsNewItems(notes, release?.history);
  const repo = "https://github.com/KratosDataGod/KratosForge";
  const day = (d) => {
    const [y, m, dd] = String(d || "").split("-").map(Number);
    return y ? dateText(new Date(y, m - 1, dd)) : "";
  };
  const item = (i) => h("li", {}, h("span", {}, i.title),
    h("span", { class: "muted-text small" }, day(i.date),
      i.pr ? [i.date ? " · " : "", h("a", { href: "#", class: "learn-more", onclick: (e) => { e.preventDefault(); openLink(`${repo}/pull/${i.pr}`); } }, `#${i.pr}`)] : null));
  const list = h("ul", { class: "whatsnew whatsnew-history" }, items.slice(0, 10).map(item));
  const more = items.length > 10 ? btn(`Show ${items.length - 10} more`, null, () => {
    list.append(...items.slice(10).map(item));
    more.remove();
  }, "tool link small whatsnew-more") : null;
  showDialog("What's new in KratosForge", h("div", { class: "setup" },
    lines.length ? h("ul", { class: "whatsnew" }, lines.map((l) => h("li", {}, l))) : null,
    items.length ? [h("b", {}, "New in the last 30 days"), h("div", { class: "whatsnew-scroll" }, list), more] : null,
    !lines.length && !items.length ? h("p", {}, "No notes for this build.") : null,
    h("p", { class: "muted-text small" }, `Build ${state.build || "unknown"} · ${channelLabel()}. `, learnMore("updates", "About updates"),
      " · ", h("a", { href: "#", class: "learn-more", onclick: (e) => { e.preventDefault(); openLink(repo); } }, "KratosForge on GitHub"))),
  [btn("Got it", null, closeDialog, "tool primary")]);
}

// ---------- The forge splash ----------

// The full-window forge: the emblem strikes the anvil, sparks fly, embers rise behind the
// lettering. The new name and each update use it. Button, Escape or Enter closes it. It follows
// Motion & effects when it opens: Off, Minimal or Windows' reduced motion show it still, and with
// Forge sparks off no sparks fly and no embers rise. Returns the button so the caller can focus it
// once it is on screen.
function forgeSplash({ kicker, tag, body, link, extra, done }) {
  // Sparks leave the anvil once; embers keep rising behind the name. Spread is fixed, not random,
  // so every start looks the same.
  const sparks = Array.from({ length: 14 }, (_, i) => h("span", { class: "splash-spark",
    style: `--a:${-160 + i * (140 / 13)}deg;--d:${90 + (i * 37) % 70}px;--t:${0.5 + (i % 4) * 0.08}s` }));
  const embers = Array.from({ length: 26 }, (_, i) => h("span", { class: "splash-ember",
    style: `--x:${(i * 41) % 100}%;--s:${2 + (i % 3)}px;--dur:${5 + (i * 7) % 6}s;--delay:${-((i * 13) % 50) / 10}s;--drift:${(i % 2 ? 1 : -1) * (10 + (i * 11) % 30)}px` }));
  const enter = btn("Enter the forge", null, done, "splash-go");
  // Decided once, from the chosen level and not the load drop, so a busy start doesn't stop it halfway.
  const still = systemReducesMotion() || chosenMotionLevel() < 2;
  const sparksOn = motionSetting("motion_sparks", true) === true;
  const look = ["overlay splash", still && "splash-still", !sparksOn && "splash-nosparks"];
  const overlay = h("div", { class: look.filter(Boolean).join(" "), role: "dialog", "aria-modal": "true",
    "aria-labelledby": "splash-title", "aria-describedby": "splash-text",
    onkeydown: (e) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done(); }
    } },
    h("div", { class: "splash-embers", "aria-hidden": "true" }, embers),
    h("div", { class: "splash-stage" },
      h("div", { class: "splash-anvil", "aria-hidden": "true" },
        h("span", { class: "splash-ring" }),
        sparks,
        h("img", { class: "splash-emblem", src: "wolf-forge-emblem.png", width: 128, height: 128, alt: "" })),
      h("h1", { id: "splash-title", class: "splash-title" },
        h("span", { class: "splash-old" }, kicker),
        h("img", { class: "splash-letters", src: "kratosforge-lettering.png", width: 800, height: 117, alt: "KratosForge" })),
      h("p", { class: "splash-tag" }, tag),
      body,
      h("div", { class: "splash-actions" }, enter,
        h("a", { href: "#", class: "splash-link", onclick: (e) => { e.preventDefault(); link.run(); } }, link.label)),
      extra || null));
  document.getElementById("dialog-root").replaceChildren(overlay);
  return enter;
}

// ---------- The new name ----------

// Fabric Desktop became KratosForge. The first start after that update shows the forge, once;
// then() runs when it closes (What's new waits behind it). From the command palette, whenever
// asked. Returns whether it showed, so the start can show the update splash instead.
const RENAME_SEEN = "fd.rename.kratosforge.seen";

function showRenameSplash(then = () => {}, asked = false) {
  let seen = false;
  try { seen = localStorage.getItem(RENAME_SEEN) === "1"; } catch { /* show it */ }
  if (seen && !asked) return false;
  try { localStorage.setItem(RENAME_SEEN, "1"); } catch { /* shown again next start */ }
  const done = () => { closeDialog(); then(); };
  forgeSplash({
    kicker: [h("s", {}, "Fabric Desktop"), " is now"],
    tag: "Same engine. Same projects. Forged for what's next.",
    body: h("p", { id: "splash-text", class: "splash-text" },
      "Everything you built is where you left it: notebooks, metadata projects, schedules, your sign-in and settings. " +
      "Only the name and the look changed."),
    link: { label: "What changed and what didn't",
      run: () => attempt(() => invoke("open_doc", { page: "kratosforge.md" }), "Could not open the guide") },
    done,
  }).focus();
  return true;
}

// ---------- After an update ----------

// The first start on a new build shows the forge with what's new since the build this PC ran
// before, once; a new install shows nothing. Turned off in Settings > Updates, the plain What's
// new dialog shows instead. Shares What's new's "seen" mark, so only one of them shows. From the
// command palette, whenever asked.
const UPDATE_SPLASH_OFF = "fd.updatesplash.off";

function updateSplashOn() {
  try { return localStorage.getItem(UPDATE_SPLASH_OFF) !== "1"; } catch { return true; }
}

function showUpdateSplash(asked = false) {
  if (!asked && !updateSplashOn()) { showWhatsNew(); return false; }
  const release = state.release?.commit && state.release.commit === state.commit ? state.release : null;
  let seen = "";
  try { seen = localStorage.getItem("fd.whatsnew.seen") || ""; } catch { /* show it */ }
  const { lines, items } = whatsNewItems(release?.notes, release?.history);
  if (!asked && (!state.commit || seen === state.commit || (!lines.length && !items.length))) return false;
  // A first start on this PC is an install, not an update: nothing to announce.
  if (!asked && !seen) {
    try { localStorage.setItem("fd.whatsnew.seen", state.commit); } catch { /* checked again next start */ }
    return false;
  }
  try { localStorage.setItem("fd.whatsnew.seen", state.commit || ""); } catch { /* shown again next time */ }
  const news = updateHighlights(lines, items, seen === state.commit ? "" : seen);
  const shown = news.slice(0, 5);
  const off = h("input", { type: "checkbox", checked: updateSplashOn() ? true : null,
    onchange: (e) => { try { localStorage.setItem(UPDATE_SPLASH_OFF, e.target.checked ? "0" : "1"); } catch { /* kept for this start */ } } });
  forgeSplash({
    kicker: `Updated to build ${state.build || "unknown"}`,
    tag: "Fresh from the forge.",
    body: h("div", { id: "splash-text", class: "splash-text splash-news" },
      shown.length ? h("ul", {}, shown.map((t) => h("li", {}, t))) : h("p", {}, "Fixes and polish. Everything you built is where you left it."),
      news.length > shown.length ? h("p", { class: "splash-more" }, `and ${news.length - shown.length} more`) : null),
    link: { label: "See everything new", run: () => { closeDialog(); showWhatsNew(true); } },
    extra: h("label", { class: "splash-optout" }, off, "Show this after every update"),
    done: closeDialog,
  }).focus();
  return true;
}

// What reached this PC with the update, newest first: the build's own notes, then each change
// since the build it ran before (history down to that commit). Without that build in the month's
// history, the newest change stands in for the list.
function updateHighlights(lines, items, since) {
  const at = since ? items.findIndex((i) => i.commit === since) : -1;
  const fresh = (at >= 0 ? items.slice(0, at) : items.slice(0, 1)).map((i) => i.title.trim());
  return [...new Set([...lines, ...fresh])];
}

// The build's own notes, unless they only repeat the newest history item (on Preview the notes
// are the last pull request's title), and the month's history, newest first.
function whatsNewItems(notes, history) {
  const items = (Array.isArray(history) ? history : []).filter((i) => i && typeof i.title === "string" && i.title.trim());
  let lines = String(notes || "").split(/\r?\n/).map((l) => l.replace(/^[-*]\s*/, "").trim()).filter(Boolean);
  if (lines.length === 1 && items.length && lines[0] === items[0].title.trim()) lines = [];
  return { lines, items };
}

// ---------- Settings page ----------

const MOTION_LEVEL_HELP = ["No animation at all. Progress, results and errors still show.",
  "Quick page and panel transitions only. No branded effects.",
  "Subtle sparks, embers and the wolf on real progress. Recommended.",
  "The same moments with more character: extra sparks, halftone and offset rings."];
const MOTION_FAMILIES = [["motion_wolf", "Wolf animations", "The wolf on run progress, its arrival, the Wolf Run after long work, and the navy wolf on work in Fabric or with a remote."],
  ["motion_sparks", "Forge sparks", "Sparks when work succeeds, the anvil strike after a deployment or promote, the ember when something fails."],
  ["motion_ambient", "Running indicators", "The glow on a running cell, the flow into a running pipeline step, the ring when work starts."]];
// Achievement celebrations (off by default, #361) shows only while Profile (a Preview feature) can
// award something; a switch that does nothing would only confuse.
const MOTION_ACHIEVEMENTS = ["motion_achievements", "Achievement celebrations",
  "A short reveal on the message when Profile awards you an achievement. Off unless you turn it on."];
const motionFamilies = () => (previewShown() ? [...MOTION_FAMILIES, MOTION_ACHIEVEMENTS] : MOTION_FAMILIES);

function motionSettingsCard(s, locked) {
  const level = s.motion_level.value;
  const source = (key) => (s[key]?.source === "policy" ? "policy" : null);
  const save = async (changes) => { if (await saveSettings(changes)) render(); };
  const levelRow = (n) => h("label", { class: `choice-row${level === n ? " selected" : ""}` },
    h("input", { type: "radio", name: "motion_level", value: String(n), checked: level === n ? true : null,
      disabled: locked("motion_level") ? true : null, onchange: () => save({ motion_level: n }) }),
    h("span", {}, h("b", {}, `${n} ${MOTION_LEVELS[n]}`), h("span", { class: "muted-text small" }, MOTION_LEVEL_HELP[n])));
  const toggle = (key, label, on, disabled) => h("label", { class: "switch" },
    h("input", { type: "checkbox", checked: on ? true : null, disabled: disabled || locked(key) ? true : null, "aria-label": label,
      onchange: (e) => save({ [key]: e.target.checked }) }),
    h("span", {}, on ? "On" : "Off"));
  const reduced = reducedMotion() && s.motion_follow_system?.value !== false;
  const choice = (key) => {
    const [title, help, options] = LAYOUT_CHOICES[key];
    const now = layoutChoice(key);
    return settingRow(title, help, h("div", { class: "seg", role: "group", "aria-label": title }, options.map(([value, label]) =>
      h("button", { class: now === value ? "on" : "", "aria-pressed": String(now === value), disabled: locked(key) ? true : null,
        onclick: () => now !== value && save({ [key]: value }) }, label))), source(key));
  };
  return h("section", { class: "card pad motion-settings" }, h("h2", {}, "Appearance"),
    h("h3", {}, "Layout"),
    h("p", { class: "muted-text small" }, "How much every page shows. Nothing is removed: what you tuck away is one click from where it was."),
    Object.keys(LAYOUT_CHOICES).map(choice),
    h("h3", {}, "Motion & effects"),
    h("p", { class: "muted-text small" }, "Effects only follow real work as it finishes; they never delay a run, take focus or cover your code. ",
      reduced ? h("b", {}, "Off now: Windows asks apps to reduce motion. ") : null, learnMore("motion-and-effects")),
    h("div", { class: "choices motion-levels", role: "radiogroup", "aria-label": "Visual intensity" }, [0, 1, 2, 3].map(levelRow)),
    source("motion_level") ? h("div", { class: "setting-source" }, SOURCE_TEXT.policy) : null,
    motionFamilies().map(([key, label, help]) => settingRow(label, level < 2 ? `${help} Plays at Refined and Expressive.` : help,
      toggle(key, label, s[key]?.value ?? key !== "motion_achievements", level < 2), source(key))),
    settingRow("Respect Windows' reduced-motion setting",
      "When Windows' Animation effects are off, KratosForge shows no motion. Untick to keep the level you chose anyway.",
      toggle("motion_follow_system", "Respect Windows' reduced-motion setting", s.motion_follow_system?.value !== false), source("motion_follow_system")),
    h("div", { class: "row" }, btn("Preview effects", "play", showMotionGallery, "tool outline")));
}

// Settings > Motion & effects > Preview effects: each effect on a sample, at the level and switches
// in force now, and Energy Flow on a real three-step pipeline run on this PC.
function showMotionGallery() {
  const stats = h("p", { class: "muted-text small", "aria-live": "polite" });
  const showStats = () => {
    const d = motionDiagnostics();
    const played = Object.values(d.played).reduce((a, b) => a + b, 0);
    stats.textContent = `Level now: ${d.level} ${MOTION_LEVELS[d.level]}${d.level !== d.chosen ? ` (you chose ${d.chosen})` : ""}. `
      + `${played} ${played === 1 ? "effect" : "effects"} played.`;
  };
  const sample = (label, cls = "ok") => h("span", { class: `motion-sample ${cls}` }, label);
  const row = (name, help, target, play) => h("div", { class: "motion-demo" },
    h("div", {}, h("b", {}, name), h("div", { class: "muted-text small" }, help)), h("div", { class: "motion-target" }, target),
    btn("Play", "play", () => { play(); setTimeout(showStats, 50); }, "tool small"));
  const spark = sample("Succeeded");
  const failed = sample("Failed", "bad");
  const run = h("button", { class: "tool outline small", tabindex: "-1", "aria-hidden": "true" }, "Run all");
  const deployed = sample("Deployed 4 steps.");
  const passed = sample("Ready: no errors or warnings");
  const lane = h("span", { class: "motion-lane" }, "Kernel idle");
  const award = sample("Achievement earned: First Flame");
  const trackHost = h("span", { class: "motion-track" });
  const remoteHost = h("span", { class: "motion-track" });
  const flowHost = h("div", { class: "motion-flow" });
  const flowNote = h("span", { class: "muted-text small" });
  let flowing = false;
  const runPipeline = async () => {
    if (flowing) return;
    flowing = true;
    flowHost.replaceChildren();
    const flow = pipelineFlow(flowHost);
    const started = await attempt(() => rpc("pipeline_run_start", { text: MOTION_SAMPLE_PIPELINE }), "Sample pipeline didn't start");
    if (!started?.ok) { flowing = false; flowNote.textContent = started?.problems?.[0]?.message || ""; return; }
    forgeEvent("pipeline.started", flowHost.closest(".motion-demo")?.querySelector("button"));
    for (;;) {
      const st = await rpc("pipeline_run_status", { run_id: started.run_id, values: false }).catch(() => null);
      if (!st || !flowHost.isConnected) break;
      flow.update(st);
      const ok = st.counts?.succeeded || 0;
      flowNote.textContent = st.state === "running" || st.state === "pending" ? `Running on this PC: ${ok} succeeded so far`
        : `${st.state[0].toUpperCase()}${st.state.slice(1)}: ${ok} of ${st.activities?.length || 0} activities succeeded`;
      if (st.state !== "running" && st.state !== "pending") {
        forgeEvent(st.state === "succeeded" ? "pipeline.succeeded" : "pipeline.failed", flowNote);
        announce(`Sample pipeline ${st.state}`);
        break;
      }
      await new Promise((r) => setTimeout(r, 300));
    }
    flowing = false;
    showStats();
  };
  const body = h("div", { class: "motion-gallery" },
    h("p", { class: "muted-text small" }, "Each effect plays on a sample at the level and switches set now. The samples are not real work, except the pipeline, "
      + "which runs a three-step sample (two short waits and a variable) on this PC and is kept in this PC's local pipeline run history. Nothing goes to Fabric."),
    row("Forge Spark", "A cell, query, save, model build or Git push succeeded.", spark, () => forgeEvent("notebook.cell.succeeded", spark)),
    row("Controlled Failure", "Something failed. The red label is there from the first frame.", failed, () => forgeEvent("notebook.cell.failed", failed)),
    row("Pulse", "Run all started.", run, () => forgeEvent("notebook.run.started", run)),
    row("Wolf track and arrival", "Run all: the wolf sits at the share of cells done (sample: 4 steps).", trackHost, () => {
      if (trackHost.childElementCount) return;
      const t = wolfTrack(() => trackHost, "Sample run", "Step", "on this PC");
      let i = 0;
      t.set(0, 4);
      const tick = setInterval(() => { i += 1; if (i < 4) t.set(i, 4); else { clearInterval(tick); t.set(4, 4); t.arrive(); } }, 450);
    }),
    row("Wolf Run", "A cell that ran 10 s or more succeeded.", lane, () => forgeEvent("notebook.long.succeeded", lane)),
    row("Navy wolf", "Work in Fabric or with a remote that has no measure: the wolf trots in place on an empty track, with no fill or percentage.",
      remoteHost, () => { if (!remoteHost.childElementCount) { remoteHost.append(remoteProgress("Sample run in Fabric")); setTimeout(() => remoteHost.replaceChildren(), 3000); } }),
    row("Anvil Strike", "A deployment, a promote, a pipeline run or a Data mesh run succeeded.", deployed, () => forgeEvent("deploy.succeeded", deployed)),
    row("Cooling spark", "Validate passed, a proof matched, or Fabric confirmed a publish.", passed, () => forgeEvent("validate.passed", passed)),
    previewShown() ? row("Achievement Reveal", "Profile awarded you an achievement. Plays only with Achievement celebrations on.", award,
      () => forgeEvent("achievement.unlocked", award)) : null,
    h("div", { class: "motion-demo" }, h("div", {}, h("b", {}, "Energy Flow"),
      h("div", { class: "muted-text small" }, "A pipeline's activities as they really run: the link into the running step flows, a finished step settles.")),
    h("div", { class: "motion-target" }, flowNote), btn("Run sample on this PC", "play", runPipeline, "tool small")),
    flowHost, stats);
  showStats();
  showDialog("Motion & effects", body, [btn("Done", null, closeDialog, "primary")]);
}

// Three activities that only wait and set a variable: nothing is read, written or sent.
const MOTION_SAMPLE_PIPELINE = JSON.stringify({ name: "Motion sample", properties: {
  variables: { shaped: { type: "String" } },
  activities: [
    { name: "Land files", type: "Wait", dependsOn: [], typeProperties: { waitTimeInSeconds: 1 } },
    { name: "Shape", type: "SetVariable", dependsOn: [{ activity: "Land files", dependencyConditions: ["Succeeded"] }],
      typeProperties: { variableName: "shaped", value: "yes" } },
    { name: "Publish", type: "Wait", dependsOn: [{ activity: "Shape", dependencyConditions: ["Succeeded"] }], typeProperties: { waitTimeInSeconds: 1 } },
  ] } });

function settingRow(title, help, control, source) {
  return h("div", { class: "setting" },
    h("div", { class: "setting-text" }, h("b", {}, title), help ? h("div", { class: "muted-text small" }, help) : null,
      source ? h("div", { class: "setting-source" }, SOURCE_TEXT[source] || source) : null),
    h("div", { class: "setting-control" }, control));
}

async function renderSettings() {
  setView(h("div", { class: "page settings" }, h("h1", {}, "Settings"), h("p", { class: "muted-text" }, "Loading...")));
  const s = await loadSettings();
  if (state.view !== "settings") return;
  if (!s) {
    setView(h("div", { class: "page settings" }, h("h1", {}, "Settings"),
      h("p", { class: "muted-text" }, "Settings load once the local engine is running.")));
    return;
  }
  // Policy or an environment variable sets it: the field shows the value, read-only, and says which.
  const locked = (key) => fixedBy(s, key) !== null;

  // Account
  const account = h("section", { class: "card pad" }, h("h2", {}, "Account"),
    settingRow(state.user ? `Signed in as ${state.user}` : "Not signed in",
      state.user ? (state.tenant ? `Directory ${state.tenant}` : "") : "Sign in to see your Fabric workspaces and lakehouses.",
      state.user ? btn("Sign out", "signOut", signOut, "tool outline")
        : btn("Sign in", "person", async () => { if (await signIn()) render(); }, "tool primary")),
    s.client_id.source !== "default" ? settingRow("Admin approval",
      "If sign-in says your organization needs to approve KratosForge, send this link to a Microsoft Entra admin. They open it once for everyone.",
      btn("Copy the link", "copyArrowRight", async () => {
        if (!(await copyText(adminConsentUrl(), "Link"))) toast(adminConsentUrl());
      }, "tool outline")) : null);

  // Updates
  const channel = (value, label, help) => h("label", { class: `choice-row${s.channel.value === value ? " selected" : ""}` },
    h("input", { type: "radio", name: "channel", value, checked: s.channel.value === value ? true : null, disabled: locked("channel") ? true : null,
      onchange: async () => { if (await saveSettings({ channel: value })) { toast(`${label}: takes effect the next time KratosForge starts`); render(); } } }),
    h("span", {}, h("b", {}, label), h("span", { class: "muted-text small" }, help)));
  const updates = h("section", { class: "card pad" }, h("h2", {}, "Updates"),
    h("p", { class: "muted-text small" }, s.auto_update?.value === false
      ? "Your organization rolls out new builds, so KratosForge doesn't update itself. "
      : "KratosForge checks for an update each time it starts and installs it before opening. ",
      `This is build ${state.build || "unknown"}. `, learnMore("updates")),
    h("div", { class: "choices" },
      channel("stable", "Stable", "Builds that have been checked and released. Recommended."),
      channel("preview", "Preview", "Every new build as soon as it's ready. For testers; things may change or break.")),
    s.channel.source === "default" ? null : h("div", { class: "setting-source" }, SOURCE_TEXT[s.channel.source]),
    settingRow("Show the forge after an update", "The first start on a new build shows what's new in a full-window splash. Off: a small What's new window instead.",
      h("label", { class: "switch" }, h("input", { type: "checkbox", checked: updateSplashOn() ? true : null,
        onchange: (e) => {
          try { localStorage.setItem(UPDATE_SPLASH_OFF, e.target.checked ? "0" : "1"); } catch { /* kept for this start */ }
          render();
        } }), h("span", {}, updateSplashOn() ? "On" : "Off"))),
    preview("updateBanner", settingRow("Tell me when a new build is out",
      "While KratosForge is open, checks your channel's version file every 4 hours, the same check as at startup; nothing else is sent. "
        + (updateWatch.checkedAt ? `Last checked ${timeText(updateWatch.checkedAt)}.` : "Not checked yet since this start."),
      h("label", { class: "switch" }, h("input", { type: "checkbox", checked: updateWatchOn() ? true : null,
        onchange: (e) => {
          try { localStorage.setItem(UPDATE_WATCH_OFF, e.target.checked ? "0" : "1"); } catch { /* kept for this start */ }
          renderUpdateBanner();
          render();
        } }), h("span", {}, updateWatchOn() ? "On" : "Off")))),
    h("div", { class: "row" }, btn("Check for updates", "sync", checkForUpdates, "tool link small"),
      btn("What's new in this build", "rocket", () => showWhatsNew(true), "tool link small"),
      btn("Show the update splash", "rocket", () => showUpdateSplash(true), "tool link small")));

  // Appearance > Motion & effects (motion.js, docs/forge-animations.md)
  const display = s.motion_level ? motionSettingsCard(s, locked) : null;

  // Preview features
  const previews = h("section", { class: "card pad" }, h("h2", {}, "Preview features"),
    settingRow("Show features in preview",
      `These work but haven't been tried on many Fabric tenants yet, so they may change: ${Object.entries(PREVIEW_FEATURES).filter(([id]) => !PREVIEW_ALWAYS_SHOWN.includes(id)).map(([, name]) => name).join(", ")}. `
        + `${PREVIEW_ALWAYS_SHOWN.map((id) => PREVIEW_FEATURES[id]).join(", ")} are core steps: they stay visible, tagged Preview, either way.`,
      h("label", { class: "switch" }, h("input", { type: "checkbox", checked: previewShown() ? true : null, disabled: locked("show_preview") ? true : null,
        onchange: async (e) => { if (await saveSettings({ show_preview: e.target.checked })) render(); } }), h("span", {}, previewShown() ? "On" : "Off")),
      s.show_preview.source === "policy" ? "policy" : null));

  // A limit from Settings: a fixed list, plus the current value when the policy file set another.
  function limitRow(key, title, help, options) {
    const current = Number(s[key].value || 0);
    if (!options.some(([v]) => v === current)) options = [...options, [current, key.endsWith("_gb") ? `${current} GB` : key.endsWith("_minutes") ? `${current} minutes` : String(current)]];
    return settingRow(h("span", {}, title, " ", previewTag()), help,
      h("select", { "aria-label": title, disabled: locked(key) ? true : null,
        onchange: async (e) => { if (await saveSettings({ [key]: Number(e.target.value) })) render(); } },
        options.map(([v, label]) => h("option", { value: v, selected: v === current ? true : null }, label))),
      fixedBy(s, key));
  }

  // Advanced (UX-006): what most people never change, folded until opened once.
  const advanced = advancedSection(s, locked, limitRow);

  // Usage data (FD-137): off unless this person turns it on; telemetry.py in the engine.
  const usage = await usageSection(s);

  // Notifications
  const notify = h("section", { class: "card pad" }, h("h2", {}, "Notifications"),
    settingRow("Teams alerts", "Post late and failed tables to a Teams channel. The bell always shows them.",
      btn("Set up", "alert", () => alertsDialog(), "tool outline")),
    await platformNoticesSection());

  // Help and about
  const about = h("section", { class: "card pad" }, h("h2", {}, "Help and about"),
    h("div", { class: "row wrap" },
      btn("User guide", "help", () => invoke("open_doc", { page: "user-guide.md" }).catch(() => {}), "tool outline"),
      btn("Keyboard shortcuts", "help", shortcutsDialog, "tool outline"),
      btn("Report a problem", "bug", reportProblem, "tool outline"),
      btn("Guide for IT admins", "shield", () => invoke("open_doc", { page: "permissions.md" }).catch(() => {}), "tool outline")),
    h("dl", { class: "about" },
      h("dt", {}, "Build"), h("dd", {}, state.build || "unknown"),
      h("dt", {}, "Engine Python"), h("dd", {}, state.python || "unknown"),
      h("dt", {}, "Organization settings file"), h("dd", { class: "mono" }, s.policy_file),
      s.allow_fabric_runs?.value === false ? [h("dt", {}, "Runs in Fabric"),
        h("dd", {}, "Off: your organization keeps work on this PC")] : null,
      s.allowed_tenants?.value?.length ? [h("dt", {}, "Sign-in"),
        // The nil GUID alone is the engine's "no tenant" (app_settings.NO_TENANT): the file can't be read.
        h("dd", {}, s.allowed_tenants.value.includes("00000000-0000-0000-0000-000000000000")
          ? "No account, until the problems below are fixed" : "Only your organization's own work accounts")] : null,
      s.policy_problems?.length ? [h("dt", {}, "Problems in that file"),
        h("dd", {}, h("ul", { class: "plain" }, s.policy_problems.map((p) => h("li", {}, p))))] : null));

  // Integrations (external AI tools, integrations.js) is a tab: the left rail is full.
  const tab = previewShown() && state.settingsTab === "integrations" ? "integrations" : "general";
  const tabs = previewShown() ? h("div", { class: "subtabs", role: "tablist", "aria-label": "Settings" },
    [["general", "General"], ["integrations", "Integrations"]].map(([id, label]) =>
      h("button", { class: `tab${tab === id ? " active" : ""}`, role: "tab", "aria-selected": String(tab === id), onclick: () => { state.settingsTab = id; render(); } },
        label, id === "integrations" ? previewTag() : null))) : null;
  const integrations = h("div", { class: "integrations" });
  // The platform is set up in one place, Platform > Settings (UX-009); Settings only points there.
  const platform = previewShown() ? h("section", { class: "card pad platform-link" },
    h("div", { class: "row" }, h("div", { class: "grow" }, h("h2", {}, "Platform ", previewTag()),
      h("p", { class: "muted-text small" }, "The central team's workspace for every project is set up, joined and repaired on Platform > Settings.")),
    btn("Open Platform > Settings", "building", () => goPlatform("settings"), "tool outline"))) : null;
  // Usage and Notifications are read above: whoever moved on to another page meanwhile stays there.
  if (state.view !== "settings") return;
  setView(h("div", { class: "page settings" },
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Settings"),
      pageIntro("How KratosForge signs in, updates and talks to you. ", learnMore("settings")))),
    tabs, tab === "integrations" ? integrations : [account, updates, display, previews, usage, platform, notify, about, advanced].filter(Boolean)));
  if (tab === "integrations") {
    renderIntegrationsTab(integrations);
    return;
  }
  const optimize = previewShown() ? await optimizeSettingsSection() : null;
  if (state.view === "settings" && optimize) notify.before(optimize);
}

// "policy" or "environment" when that sets the value, so Settings shows it read-only and says which
// (SOURCE_TEXT); null when this person can change it.
function fixedBy(s, key) {
  const source = s?.[key]?.source;
  return source === "policy" || source === "environment" ? source : null;
}

// The App (client) ID, notebook process limits and SQL limits: one fold, closed by default (UX-006).
function advancedSection(s, locked, limitRow) {
  const idFixed = locked("client_id");
  const idInput = h("input", { value: idFixed || s.client_id.source === "user" ? s.client_id.value : "", spellcheck: "false",
    "aria-label": "App (client) ID", placeholder: s.client_id.source === "default" ? "Empty: Microsoft's Fabric sign-in" : s.client_id.value,
    readonly: idFixed ? true : null, "aria-readonly": idFixed ? "true" : null });
  const signInGroup = h("div", { class: "advanced-group" }, h("h3", {}, "Sign-in"),
    settingRow("App (client) ID", "Leave it empty: KratosForge signs in through Microsoft's own Fabric sign-in, so nobody has to register an app. Only paste an ID if your organization gave you its own app.",
      h("div", { class: "row" }, idInput,
        idFixed ? null : btn("Save", null, async () => {
          if (await saveSettings({ client_id: idInput.value.trim() })) { toast("Saved. Sign in again to use it."); render(); }
        }, "tool outline")),
      s.client_id.source === "default" ? MICROSOFT_SIGN_IN : s.client_id.source),
    clientIdNote(s.client_id.source) ? h("div", { class: "callout git-warn small", role: "note" },
      h("span", { class: "grow" }, clientIdNote(s.client_id.source)),
      s.client_id.source === "user" ? btn("Use Microsoft's sign-in", null, async () => {
        if (await saveSettings({ client_id: "" })) { toast("Saved. Sign in again to use it."); render(); }
      }, "tool outline small") : null) : null);
  const kernel = s.kernel_process ? h("div", { class: "advanced-group" }, h("h3", {}, "Notebooks"),
    settingRow("Run notebooks in their own process (Preview)",
      "Cells run in a Python process of their own instead of inside the app's engine. Stop and a crash then end only that process, and the rest of the app keeps working. Variables are cleared when you switch.",
      h("label", { class: "switch" }, h("input", { type: "checkbox", checked: s.kernel_process.value ? true : null, disabled: locked("kernel_process") ? true : null,
        onchange: async (e) => { await saveSettings({ kernel_process: e.target.checked }); render(); } }),
      h("span", {}, s.kernel_process.value ? "On" : "Off")),
      fixedBy(s, "kernel_process")),
    s.kernel_process.value && s.cell_time_limit_minutes ? limitRow("cell_time_limit_minutes", "Cell time limit",
      "A cell that runs longer is interrupted. Variables are kept. If it doesn't stop within 30 seconds, its process is ended and variables are cleared. Proofs and side by side use the same limit.",
      [[0, "Off"], [15, "15 minutes"], [60, "1 hour"], [240, "4 hours"], [720, "12 hours"]]) : null,
    s.kernel_process.value && s.kernel_memory_limit_gb ? limitRow("kernel_memory_limit_gb", "Notebook memory limit",
      "The notebook's process is ended if it uses more, so one notebook can't slow the whole PC. Variables are cleared.",
      [[0, "Off"], [4, "4 GB"], [8, "8 GB"], [16, "16 GB"], [32, "32 GB"]]) : null,
    s.kernel_process.value && s.kernel_max_sessions ? limitRow("kernel_max_sessions", "Notebooks with a session",
      "Each notebook you run gets a process of its own. Opening one more than this stops the least recently used idle one, and its variables are cleared. A running notebook is never stopped.",
      [[2, "2"], [5, "5"], [10, "10"], [20, "20"]]) : null,
    s.kernel_process.value && s.kernel_idle_minutes ? limitRow("kernel_idle_minutes", "Stop idle sessions after",
      "A notebook you haven't run for this long, and don't have open, has its process stopped to give back memory. Its variables are cleared.",
      [[0, "Never"], [20, "20 minutes"], [60, "1 hour"], [240, "4 hours"]]) : null) : null;
  const onelake = s.onelake_writes ? oneLakeWritesSection(s, locked) : null;
  // SQL queries (SQL-003): the limits every SQL page query runs with on this PC.
  const sql = s.sql_page_rows ? sqlSettingsSection(s, locked) : null;
  return foldable("settings.advanced", h("h2", {}, "Advanced"), [
    h("p", { class: "muted-text small" }, "Most people never need these: your own app registration, and the limits notebooks and SQL queries run with on this PC. ",
      learnMore("advanced-settings")),
    signInGroup, kernel, onelake, sql].filter(Boolean), { start: true, cls: "card pad advanced" });
}

// How notebooks on this PC write lakehouse tables (lakehouse.write_table). onelake_writes is only the
// default: each notebook can choose its own under Runs on.
const ONELAKE_WRITES = [["local_first", "Write here, then upload"], ["direct", "Straight to OneLake"]];
function oneLakeWritesSection(s, locked) {
  const choice = (key, title, help, options) => settingRow(title, help,
    h("select", { "aria-label": title, disabled: locked(key) ? true : null,
      onchange: async (e) => { if (await saveSettings({ [key]: e.target.value })) render(); } },
      options.map(([v, label]) => h("option", { value: v, selected: v === s[key].value ? true : null }, label))),
    fixedBy(s, key));
  return h("div", { class: "advanced-group" }, h("h3", {}, "OneLake writes"),
    choice("onelake_writes", "New notebooks write", "Write here, then upload: a cell's table writes are kept on this PC and upload in the background while the cell goes on; the cell ends when they're in OneLake. Straight to OneLake: each write returns once it's uploaded, which suits a fast upload line. Each notebook can choose its own under Runs on.",
      ONELAKE_WRITES),
    choice("onelake_compression", "Compression", "Zstandard files are about a quarter smaller than Snappy, so they upload sooner, and cost the same to write. Fabric's SQL analytics endpoint reads both. Choose Snappy if a Fabric reader you use can't read Zstandard.",
      [["zstd", "Zstandard"], ["snappy", "Snappy"]]),
    settingRow("Skip unchanged tables", "A table overwritten with exactly the rows it already holds isn't uploaded again. KratosForge keeps a fingerprint of the rows in the table's Delta log to tell.",
      h("label", { class: "switch" }, h("input", { type: "checkbox", checked: s.onelake_skip_unchanged.value ? true : null, disabled: locked("onelake_skip_unchanged") ? true : null,
        onchange: async (e) => { await saveSettings({ onelake_skip_unchanged: e.target.checked }); render(); } }),
      h("span", {}, s.onelake_skip_unchanged.value ? "On" : "Off")),
      fixedBy(s, "onelake_skip_unchanged")),
    localWritesRow());
}

// The files local writes keep on this PC (local_writes_service.py), with a way to open each folder.
function localWritesRow() {
  const status = h("div", { class: "muted-text small" }, "Looking...");
  const extra = h("div", { class: "local-writes" });
  const open = btn("Open folder", "folderOpen", () => openLocalWrites(), "tool small");  // app.js
  const clear = btn("Delete left-overs", "delete", () => clearLocalWrites(), "tool small", { disabled: true });
  rpc("local_writes").then((w) => {
    status.textContent = localWritesText(w);
    clear.disabled = !w.files && !w.tpch.length;
    extra.replaceChildren(...w.tpch.map((t) => h("div", { class: "row wrap" },
      h("span", { class: "muted-text small grow" }, `TPC-H data left in the temp folder: ${sizeText(t.bytes)}, ${t.folder}`),
      btn("Open", "folderOpen", () => openLocalWrites(t.folder), "tool small"))));
  }).catch(() => { status.textContent = "Couldn't read the folder."; });
  return settingRow("Files on this PC",
    "A write kept on this PC waits here until it has uploaded, then it's deleted, so this is normally empty. Open the folder to look, or Delete left-overs to clear up after a run that was ended mid-upload.",
    h("div", { style: "display:flex;flex-direction:column;gap:6px" }, h("div", { class: "row wrap" }, status, open, clear), extra));
}

async function clearLocalWrites() {
  const w = await attempt(() => rpc("local_writes"), "Couldn't read the folder");
  if (!w) return;
  const total = w.bytes + w.tpch.reduce((n, t) => n + t.bytes, 0);
  const what = [w.files ? `${w.files.toLocaleString()} file${w.files === 1 ? "" : "s"} in ${w.folder}` : "",
    ...w.tpch.map((t) => `TPC-H data in ${t.folder}`)].filter(Boolean);
  if (!(await confirmDialog("Delete left-overs?", `This deletes ${sizeText(total)} from this PC: ${what.join("; ")}. ` +
    "Your tables in OneLake aren't touched. A write that hasn't finished uploading is lost, so it's refused while a notebook is running.",
  "Delete", { danger: true }))) return;
  const res = await attempt(() => rpc("local_writes_clear"), "Not deleted");
  if (!res) return;
  toast(`Deleted ${sizeText(res.bytes)}.${res.in_use.length ? ` ${res.in_use.length} still in use, left: try again when it has finished.` : ""}`);
  render();
}

function localWritesText(w) {
  if (!w.files) return `Empty · ${w.folder}`;
  const up = w.uploading.files ? `, ${w.uploading.files} waiting to upload or left over (${sizeText(w.uploading.bytes)})` : "";
  return `${sizeText(w.bytes)} in ${w.files.toLocaleString()} file${w.files === 1 ? "" : "s"}${up} · ${w.folder}`;
}

// The SQL page's limits. Each is checked by the engine (app_settings.SQL_RANGES); a value set by the
// organization shows as locked.
const SQL_SETTINGS = [
  ["sql_page_rows", "Rows per page", "Rows the results show at a time. Next and Previous move through the rest.", 100, 10000, ""],
  ["sql_time_limit_minutes", "Time limit", "Minutes a query may run before it's stopped. 0 for no limit.", 0, 1440, "minutes"],
  ["sql_memory_limit_percent", "Memory", "Share of this PC's memory one query may use. A bigger result spills to the temp folder.", 10, 90, "% of RAM"],
  ["sql_max_concurrent", "Queries at once", "More wait in a queue until one finishes.", 1, 8, ""],
  ["sql_result_retention_hours", "Keep results", "Hours a finished query's result stays for paging and export. 0 keeps it until the app closes.", 0, 168, "hours"],
];

function sqlSettingsSection(s, locked) {
  const inputs = {};
  const rows = SQL_SETTINGS.map(([key, title, help, min, max, unit]) => {
    inputs[key] = h("input", { type: "number", min: String(min), max: String(max), step: "1", value: String(s[key].value),
      class: "num", "aria-label": title, readonly: locked(key) ? true : null, "aria-readonly": locked(key) ? "true" : null });
    return settingRow(title, help, h("div", { class: "row" }, inputs[key], unit ? h("span", { class: "muted-text small" }, unit) : null),
      fixedBy(s, key));
  });
  const temp = h("input", { value: s.sql_temp_dir.value || "", spellcheck: "false", "aria-label": "Temp folder",
    placeholder: "Empty: the app's data folder", readonly: locked("sql_temp_dir") ? true : null, "aria-readonly": locked("sql_temp_dir") ? "true" : null });
  rows.push(settingRow("Temp folder", "Where spilled data and results are kept while you page and export. A fast local drive with free space works best.",
    folderField(temp, "the temp folder"), fixedBy(s, "sql_temp_dir")));
  // WH-10: off, the Warehouse page only reads; on, each change still asks first (sql_exec.py).
  rows.push(settingRow("Warehouse changes", "Lets the Warehouse page change a warehouse (create, alter, insert, update, delete, run procedures) after you see what it changes and confirm. Off, every warehouse only reads.",
    h("label", { class: "switch" }, h("input", { type: "checkbox", checked: s.sql_warehouse_writes.value ? true : null, disabled: locked("sql_warehouse_writes") ? true : null,
      "aria-label": "Warehouse changes", onchange: async (e) => { await saveSettings({ sql_warehouse_writes: e.target.checked }); render(); } }),
    h("span", {}, s.sql_warehouse_writes.value ? "On" : "Off")),
    fixedBy(s, "sql_warehouse_writes")));
  // Clicking a warehouse's or SQL analytics endpoint's name: the Warehouse page or Fabric's own editor.
  const openIn = s.wh_open_in?.value || "kratosforge";
  const openChoice = (value, title, help) => h("label", { class: "choice" },
    h("input", { type: "radio", name: "wh-open-in", value, checked: openIn === value ? true : null, disabled: locked("wh_open_in") ? true : null,
      onchange: async () => { await saveSettings({ wh_open_in: value }); render(); } }),
    h("span", { class: "main" }, h("span", { class: "n" }, title), h("span", { class: "w" }, help)));
  rows.push(settingRow("Open warehouses in", "What clicking a warehouse's or SQL analytics endpoint's name does in Workspaces and the OneLake catalog. Open in Fabric stays on every row.",
    h("div", { class: "choices", role: "radiogroup", "aria-label": "Open warehouses in" },
      openChoice("kratosforge", "KratosForge", "The Warehouse page: explorer, T-SQL editor, templates, project files, deploy and monitoring. Queries still run in Fabric and use capacity."),
      openChoice("fabric", "Fabric portal", "Fabric's own warehouse editor, in the Fabric portal tab.")),
    fixedBy(s, "wh_open_in")));
  // WH-19: workspaces whose new warehouses get monitoring views without asking (whmonitor.js).
  const auto = s.wh_monitoring_auto?.value || [];
  rows.push(settingRow("Monitoring views without asking", "Workspaces where you said yes, on a build confirm, to monitoring views for new warehouses. The app builds a warehouse's KQL queryset when it opens one there. Remove a workspace and it asks again.",
    auto.length ? h("ul", { class: "wh-mon-auto" }, auto.map((ws) => h("li", {}, h("span", {}, whMonWorkspaceName(ws)),
      locked("wh_monitoring_auto") ? null : btn("Remove", null, async () => {
        await saveSettings({ wh_monitoring_auto: auto.filter((w) => w !== ws) });
        render();
      }, "tool outline small", { "aria-label": `Remove ${whMonWorkspaceName(ws)}` }))))
      : h("span", { class: "muted-text small" }, "None."),
    fixedBy(s, "wh_monitoring_auto")));
  const save = async () => {
    const changes = {};
    for (const [key] of SQL_SETTINGS) {
      if (!locked(key) && inputs[key].value !== String(s[key].value)) changes[key] = inputs[key].value;
    }
    if (!locked("sql_temp_dir") && temp.value.trim() !== (s.sql_temp_dir.value || "")) changes.sql_temp_dir = temp.value.trim();
    if (!Object.keys(changes).length) return;
    if (await saveSettings(changes)) { toast("Saved. New SQL queries use these limits."); render(); }
  };
  const all = [...SQL_SETTINGS.map(([key]) => key), "sql_temp_dir"].every(locked);
  return h("div", { class: "advanced-group" }, h("h3", {}, "SQL queries"),
    h("p", { class: "muted-text small" }, "Each query on the SQL page runs on this PC in a process of its own with these limits, so Stop ends only that query. ",
      h("a", { href: "#", class: "learn-more", onclick: (e) => {
        e.preventDefault();
        attempt(() => invoke("open_doc", { page: "sql-execution.md" }), "Could not open the guide");
      } }, "Learn more")),
    ...rows, all ? null : h("div", { class: "row" }, btn("Save", null, save, "tool outline")));
}

// ---------- Usage data ----------

const USAGE_WHERE = { kratosbi: "KratosBI's Application Insights", organization: "your organization's Application Insights" };

async function usageSection(s) {
  const u = await rpc("telemetry_status").catch(() => null);
  if (!u) return null;
  const locked = s.telemetry?.source === "policy";
  const today = u.waiting.find((w) => w.day === u.today);
  const actions = today ? Object.values(today.features).reduce((n, f) => n + f.count, 0) : 0;
  const failures = today ? Object.values(today.features).reduce((n, f) => n + f.errors, 0) : 0;
  const waiting = !u.on ? "Nothing (off)" : u.waiting.length
    ? `${u.waiting.length} day${u.waiting.length === 1 ? "" : "s"}${today ? `; today so far ${actions} action${actions === 1 ? "" : "s"}, ${failures} failed` : ""}`
    : "Nothing yet";
  const toggle = h("label", { class: "switch" }, h("input", { type: "checkbox", checked: u.on ? true : null,
    disabled: locked || !u.available ? true : null,
    onchange: async (e) => { if (await saveSettings({ telemetry: e.target.checked })) { toast(e.target.checked ? "Usage data on" : "Usage data off; nothing waiting is sent"); render(); } } }),
  h("span", {}, u.on ? "On" : "Off"));
  return h("section", { class: "card pad" }, h("h2", {}, "Usage data ", previewTag()),
    settingRow(`Share usage data with ${u.destination === "organization" ? "your organization" : "KratosBI"}`,
      u.available
        ? "Once a day, sends how often each action was used, how often it failed and how long it took, with the build and Windows version. Never your data, code, queries, file, table or workspace names, your account or your organization."
        : "This build of KratosForge has nowhere to send usage data, so it sends nothing.",
      toggle, locked ? "policy" : null),
    h("dl", { class: "about" },
      h("dt", {}, "Goes to"), h("dd", {}, USAGE_WHERE[u.destination] || "Nowhere"),
      h("dt", {}, "Last sent"), h("dd", {}, u.last_sent || "Never"),
      h("dt", {}, "Waiting to send"), h("dd", {}, waiting),
      u.install_id ? [h("dt", {}, "Install ID"), h("dd", { class: "mono" }, `${u.install_id} (random, not tied to you)`)] : null),
    h("div", { class: "row wrap" },
      btn("See what's sent", null, () => usageDialog(u), "tool outline"),
      u.install_id ? btn("New install ID", null, async () => {
        if (await attempt(() => rpc("telemetry_new_install_id"), "Not changed")) { toast("New install ID"); render(); }
      }, "tool outline") : null,
      btn("What's collected, field by field", null, () => invoke("open_doc", { page: "telemetry.md" }).catch(() => {}), "tool link")));
}

function usageDialog(u) {
  const example = { name: "daily_usage", day: u.today, install_id: u.install_id || "(made when it's turned on)",
    build: state.commit?.slice(0, 12) || "dev", channel: state.settings?.channel?.value || "stable", windows: "10.0.26100", python: state.python || "3.12",
    features: { run_sql: { count: 9, errors: 0, seconds: 4.2 } }, errors: {} };
  const text = JSON.stringify(u.waiting.length ? u.waiting : [example], null, 2);
  showDialog("What's sent", h("div", { class: "setup" },
    h("p", {}, u.waiting.length
      ? `This is the exact text waiting to go to ${USAGE_WHERE[u.destination] || "nowhere"}. A day is sent once it's over.`
      : "Nothing is waiting. This is an example of one day, in the exact shape that's sent."),
    h("p", { class: "muted-text small" }, "Actions are the app's own names for them. A failure carries only its kind, never its message."),
    h("textarea", { rows: 16, class: "mono", readonly: true }, text)),
  [btn("Copy", null, () => copyText(text, "Usage data"), "tool outline"), h("span", { class: "spacer" }),
    btn("Close", null, closeDialog, "tool primary")]);
}

// ---------- Ready to work (Home): can this PC do real work yet? ----------

const READY_MARK = { ok: ["ok", "Ready"], fail: ["error", "Needs attention"], warn: ["warning", "Worth a look"],
  wait: ["info", "Not checked yet"] };

function readinessFix(row, rerun) {
  const fixes = {
    sign_in: ["Sign in", async () => { if (await signIn()) render(); }],
    retry: ["Check again", rerun],
    workspaces: ["Open Workspaces", () => go("workspaces")],
    capacity: ["How to get capacity", () => invoke("open_doc", { page: row.doc }).catch(() => {})],
    settings: ["Open Settings", () => go("settings")],
  };
  const fix = fixes[row.fix];
  return fix ? btn(fix[0], null, fix[1], row.status === "fail" ? "tool primary small" : "tool outline small") : null;
}

function readinessRow(row, rerun, extra = null) {
  const [mark, label] = READY_MARK[row.status] || READY_MARK.wait;
  return h("li", { class: `ready-row ${row.status}` },
    h("span", { class: "ready-mark", role: "img", "aria-label": label }, icon(mark, 18)),
    h("div", { class: "grow" }, h("b", {}, row.title), h("div", { class: "muted-text small" }, row.text),
      row.status !== "ok" && row.status !== "wait" ? h("div", { class: "row" }, readinessFix(row, rerun)) : null),
    extra);
}

function writeTestDialog(done) {
  const wsSelect = h("select", {}, h("option", { value: "" }, "Loading workspaces..."));
  const lhSelect = h("select", { disabled: true }, h("option", { value: "" }, "Pick a workspace first"));
  const why = h("div", { class: "muted-text small" });
  const start = btn("Write and delete the test file", null, async () => {
    const ws = wsSelect.selectedOptions[0];
    start.disabled = true;
    const result = await attempt(() => rpc("readiness_write_test",
      { workspace_id: ws.value, lakehouse_id: lhSelect.value, workspace_name: ws.textContent }), "Write test");
    closeDialog();
    done(result, { workspace_id: ws.value, lakehouse_id: lhSelect.value, workspace: ws.textContent });  // no result: refused or failed
  }, "tool primary", { disabled: true });
  const canGo = () => { start.disabled = !(wsSelect.value && lhSelect.value); };
  wsSelect.addEventListener("change", async () => {
    lhSelect.disabled = true;
    lhSelect.replaceChildren(h("option", { value: "" }, "Loading lakehouses..."));
    const lakehouses = await attempt(() => rpc("list_lakehouses", { workspace_id: wsSelect.value }), "Lakehouses") || [];
    lhSelect.replaceChildren(...(lakehouses.length ? lakehouses.map((l) => h("option", { value: l.id }, l.displayName))
      : [h("option", { value: "" }, "No lakehouses in this workspace")]));
    lhSelect.disabled = !lakehouses.length;
    canGo();
  });
  lhSelect.addEventListener("change", canGo);
  showDialog("Test a write to OneLake", h("div", { class: "stack" },
    h("p", {}, "This writes one small file to the lakehouse you pick, reads it back and deletes it. No table is touched."),
    h("label", { class: "field" }, "Workspace", wsSelect),
    h("label", { class: "field" }, "Lakehouse", lhSelect),
    h("div", { class: "callout small" }, "Writes Files/_fabric_desktop_check/write-test.txt, then deletes that folder. ",
      "Workspaces whose name says Prod aren't offered."), why),
    [btn("Cancel", null, closeDialog, "tool outline"), start]);
  rpc("list_workspaces").then((spaces) => {
    const usable = spaces.filter((w) => w.capacityId && !/(^|[^a-z])(prod|prd|production)([^a-z]|$)/i.test(w.displayName));
    wsSelect.replaceChildren(h("option", { value: "" }, usable.length ? "Pick a workspace" : "No Dev or Test workspace on a capacity"),
      ...usable.map((w) => h("option", { value: w.id }, w.displayName)));
  }).catch((err) => { why.textContent = friendlyError(err).text; });
}

// A passed write test is remembered for a week, for the same account, tenant and lakehouse, so Home
// doesn't ask for it on every visit (#191). A failed or refused test and signing out forget it.
const WRITE_PASS_DAYS = 7;

function savedWriteRow(saved, user, tenant, now = Date.now()) {
  if (!saved || !user || saved.user !== user || saved.tenant !== tenant || !saved.lakehouse_id) return null;
  const at = Date.parse(saved.at);
  if (Number.isNaN(at) || now - at > WRITE_PASS_DAYS * 86400000 || at > now) return null;
  return { key: "write", title: "Writing to OneLake", status: "ok",
    text: `Test write passed in ${saved.workspace || "a lakehouse"} on ${dateText(at)}. Checked again after ${WRITE_PASS_DAYS} days.` };
}

function rememberWrite(row, target) {
  try {
    if (row?.status === "ok" && target?.lakehouse_id && state.user) {
      localStorage.setItem("fd.ready.write", JSON.stringify({ user: state.user, tenant: state.tenant || null, ...target,
        at: new Date().toISOString() }));
    } else localStorage.removeItem("fd.ready.write");
  } catch { /* not remembered */ }
}

// Whether Home shows Ready to work: until someone hides it.
function readinessShown() {
  try { return localStorage.getItem("fd.ready.hidden") !== "1"; } catch { return true; }
}

function readinessCard() {
  if (!readinessShown()) return null;
  const box = h("section", { class: "card pad ready", "aria-labelledby": "ready-title" });
  let writeRow = null;
  try { writeRow = savedWriteRow(JSON.parse(localStorage.getItem("fd.ready.write") || "null"), state.user, state.tenant || null); } catch { /* ask again */ }
  let lastRows = [];
  const retest = () => writeTestDialog((r, target) => { rememberWrite(r, target); writeRow = r || null; draw(lastRows, "just now"); });
  const draw = (rows, checkedAt) => {
    lastRows = rows;
    const all = writeRow ? [...rows, writeRow] : rows;
    const good = all.filter((r) => r.status === "ok").length;
    const head = h("div", { class: "row" }, h("h2", { class: "grow", id: "ready-title" }, "Ready to work"),
      h("span", { class: "muted-text small" }, `${good} of ${rows.length + 1} ready`),
      btn("Check again", null, run, "tool outline small"),
      btn("Hide", null, () => { try { localStorage.setItem("fd.ready.hidden", "1"); } catch { /* */ } box.remove(); }, "tool link small"));
    if (rows.every((r) => r.status === "ok") && writeRow?.status === "ok") {
      box.replaceChildren(h("div", { class: "row" }, icon("ok", 18), h("b", { class: "grow" }, "Ready to work."),
        h("span", { class: "muted-text small" }, `Checked ${checkedAt}`), btn("Check again", null, run, "tool link small"),
        btn("Test again", null, retest, "tool link small")));
      return;
    }
    const capacityOk = rows.find((r) => r.key === "capacity")?.status === "ok";
    const write = writeRow || { key: "write", title: "Writing to OneLake", status: "wait",
      text: capacityOk ? "Not checked. It writes a small test file, so it waits until you ask."
        : "Not checked. It needs a lakehouse you can write to, so it waits for the check above." };
    const tester = btn(writeRow?.status === "ok" ? "Test again" : "Test a write", null, retest,
      "tool outline small", { disabled: capacityOk ? null : true });
    box.replaceChildren(head, withHowItWorks(
      h("ol", {}, rows.map((r) => readinessRow(r, run)), readinessRow(write, run, tester)),
      howItWorks("home.ready", {
        reads: "Your saved sign-in, the workspaces and capacities Fabric lists for you, and your organization's policy file on this PC. It reads when Home opens and when you press Check again, and no check opens a sign-in window.",
        changes: "Nothing, unless you choose Test a write. That asks you first, then writes one small file, Files/_fabric_desktop_check/write-test.txt, to the lakehouse you pick, reads it back and deletes the folder. Workspaces named Prod are refused, and no table is touched.",
        results: "Only on this PC. A passed write test is remembered here for 7 days for the same account and lakehouse, and is forgotten when a test fails or you sign out. Nothing is sent anywhere.",
        anchor: "ready-to-work",
      })));
  };
  async function run() {
    box.replaceChildren(h("h2", { id: "ready-title" }, "Ready to work"), h("div", { class: "muted-text" }, "Checking..."));
    const rows = await attempt(() => rpc("readiness_check"), "Ready to work");
    if (rows) draw(rows, timeText(new Date()));
  }
  run();
  return box;
}

// ---------- Getting started with KratosForge (Home) ----------

function markDone(step) {
  try { localStorage.setItem(`fd.done.${step}`, "1"); } catch { /* not remembered */ }
}

// The main starting points, one small tile each, in a card that folds to its header line and
// remembers that (fd.fold.home.getstarted). Someone who hid the old Get started card starts folded.
// Tiles tick once tried: a notebook, a lakehouse opened, a metadata project; the rest never tick.
// Returns { el, mark(project, notebookCount) } so Home ticks them once it has read those.
function gettingStarted() {
  let hidden = false;
  try { hidden = localStorage.getItem("fd.getstarted.hidden") === "1"; } catch { /* shown */ }
  const done = (step) => { try { return localStorage.getItem(`fd.done.${step}`) === "1"; } catch { return false; } };
  const points = [
    ["notebook", "notebook", "Run a notebook on this PC", "Python and Spark-style code on your own processor.", "New notebook", () => newNotebook()],
    ["import", "arrowUpload", "Bring your notebooks", "Import from Fabric or Databricks and run them here.", "Import", () => nbfImportDialog()],
    ["lakehouse", "onelake", "Explore OneLake", "Browse lakehouse tables and files, or query them with SQL.", "OneLake catalog", () => go("onelake")],
    ["project", "metadata", "Build medallion tables", "Describe bronze, silver and gold once; KratosForge builds and runs them.", "Data mesh", () => go("metadata")],
    ["optimize", "gauge", "Spend less Fabric capacity", "See what uses capacity and what could run on this PC.", "Optimize", () => go("optimize")],
    ["platform", "building", "Set up the platform", "For admins: workspaces, environments, access and governance.", "Platform", () => go("platform")],
  ];
  const tiles = new Map();
  const grid = h("div", { class: "gs-grid" }, points.map(([key, ic, title, text, verb, run]) => {
    const tile = h("div", { class: `gs-tile${key === "lakehouse" && done("lakehouse") ? " done" : ""}` },
      h("div", { class: "row gs-title" }, icon(ic, 16), h("b", { class: "grow" }, title), h("span", { class: "gs-tick", title: "Tried" }, icon("ok", 14))),
      h("div", { class: "muted-text small" }, text),
      btn(verb, null, run, "tool link small"));
    tiles.set(key, tile);
    return tile;
  }));
  const learn = PREVIEW_FEATURES.learn && previewShown()
    ? btn("New to SQL or data work? Try the lessons in Learn", "beaker", () => go("learn"), "tool link small") : null;
  const el = foldable("home.getstarted",
    h("h2", {}, "Getting started with KratosForge"),
    h("div", { class: "gs-body" }, grid,
      h("div", { class: "row wrap gs-foot" }, learn, learnMore("get-started", "Read the user guide"))),
    { cls: "card pad get-started", start: hidden });
  const mark = (project, notebookCount) => {
    tiles.get("notebook").classList.toggle("done", notebookCount > 0);
    tiles.get("project").classList.toggle("done", !!project?.path);
  };
  return { el, mark };
}

registerFeature({
  id: "settings",
  rail: { label: "Settings", icon: "settings", order: 100, bottom: true },
  views: { settings: renderSettings },
  commands: [
    { label: "Open Settings", icon: "settings", keywords: "preferences options channel app id", run: () => go("settings") },
    { label: "Sign out", icon: "signOut", keywords: "log out account", run: signOut, when: () => !!state.user },
    { label: "Sign in", icon: "person", keywords: "log in account", run: () => signIn().then((ok) => ok && render()), when: () => !state.user },
    { label: "Report a problem", icon: "bug", keywords: "bug support help feedback", run: reportProblem },
    { label: "What's new", icon: "rocket", keywords: "release notes update changelog", run: () => showWhatsNew(true) },
    { label: "About the new name: KratosForge", icon: "rocket", keywords: "rename fabric desktop kratosforge brand splash", run: () => showRenameSplash(() => {}, true) },
    { label: "Show the update splash", icon: "rocket", keywords: "what's new release notes update forge splash", run: () => showUpdateSplash(true) },
    { label: "Open the user guide", icon: "help", keywords: "help docs documentation", run: () => invoke("open_doc", { page: "user-guide.md" }).catch(() => {}) },
    { label: "Keyboard shortcuts", icon: "help", keywords: "keys hotkeys help accessibility", run: shortcutsDialog },
  ],
});
