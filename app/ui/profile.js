// Profile: a private record of work KratosForge verified, what it adds up to, and a review-ready
// report (progress.py and progress_service.py in the engine). Opens from the account menu; it has
// no rail slot. Levels and achievements are for fun; skills show evidence coverage, never a score.
// Loaded after features.js; everything here runs after startup.

const profilePage = { tab: "overview", data: null, days: 90, achievements: false, report: null };
const RECORDING_ON = "Recording is on: notebooks that pass Validate are kept in a private record on this PC. Turn it off in Profile > Settings.";

async function loadProfile() {
  profilePage.data = await attempt(() => rpc("profile_get", {}), "Could not open your profile");
  return profilePage.data;
}

async function saveProfile(params, done = "Saved") {
  const out = await attempt(() => rpc("profile_save", params), "Could not save your profile");
  if (out) { profilePage.data = out; if (done) toast(done); render(); }
  return out;
}

function profileTabs() {
  const tab = (id, label) => h("button", { class: `tab${profilePage.tab === id ? " active" : ""}`, role: "tab",
    "aria-selected": profilePage.tab === id ? "true" : "false", onclick: () => { profilePage.tab = id; render(); } }, label);
  return h("div", { class: "subtabs", role: "tablist", "aria-label": "Profile" },
    tab("overview", "Overview"), tab("report", "Professional report"), tab("settings", "Settings"));
}

async function renderProfile() {
  setView(h("div", { class: "page" }, h("h1", {}, "Profile"), h("p", { class: "muted-text" }, "Loading...")));
  const p = await loadProfile();
  if (state.view !== "profile") return;
  if (!p) {
    setView(h("div", { class: "page" }, h("h1", {}, "Profile"), h("p", { class: "muted-text" }, "Your profile opens once the local engine is running.")));
    return;
  }
  const who = p.identity;
  const head = h("div", { class: "page-head" },
    h("div", { class: "profile-id" },
      h("div", { class: "profile-avatar", "aria-hidden": "true" }, (who.display_name || state.user || "?")[0].toUpperCase()),
      h("div", {}, previewMarked("profile", h("h1", {}, who.display_name || "Your profile")),
        h("p", { class: "sub" }, [who.role, who.technologies, state.user ? `Signed in as ${state.user}` : "Not signed in: this profile is kept for this PC"]
          .filter(Boolean).join(" · "), " ", learnMore("profile-preview")))),
    btn("Edit profile", "person", editProfileIdentity, "tool outline"));
  const body = { overview: profileOverview, report: profileReport, settings: profileSettings }[profilePage.tab](p);
  const notice = p.notice ? h("div", { class: "callout git-warn", role: "alert" }, h("span", { class: "grow" }, p.notice),
    btn("Dismiss", null, () => saveProfile({ dismiss_notice: true }, null), "tool outline small")) : null;
  setView(h("div", { class: "page profile" }, head, notice, profileTabs(), body));
}

function editProfileIdentity() {
  const who = profilePage.data.identity;
  const field = (label, key, placeholder) => h("label", { class: "field" }, label,
    h("input", { value: who[key] || "", placeholder, "data-key": key, maxlength: 200 }));
  const fields = [field("Display name", "display_name", state.user || ""), field("Role or specialization", "role", "Data engineer"),
    field("Preferred technologies", "technologies", "Python, SQL, Delta Lake")];
  showDialog("Edit profile", h("div", { class: "profile-form" }, ...fields), [
    btn("Cancel", null, closeDialog, "tool outline"),
    btn("Save", null, async () => {
      const identity = Object.fromEntries(fields.map((f) => { const i = f.querySelector("input"); return [i.dataset.key, i.value.trim()]; }));
      closeDialog();
      await saveProfile({ identity });
    }, "tool primary")]);
}

// ---------- Overview ----------

function profileOverview(p) {
  if (!p.settings.record && !p.evidence.length) {
    return withHowItWorks(h("div", { class: "card pad" },
      h("h2", {}, "Recording is off"),
      h("p", {}, "Keep a private record of work KratosForge has verified, starting with notebooks that pass Validate with a clean run. "
        + "Use it to see your progress and to put together a review-ready report. You turned it off; turn it on to start again."),
      h("div", { class: "row" }, btn("Turn on", null, () => saveProfile({ settings: { record: true } }, "Recording is on"), "tool primary"))),
      profileHowItWorks());
  }
  const pr = p.progression;
  const left = h("div", { class: "profile-col" },
    h("section", { class: "card pad" },
      h("h2", {}, "Recorded accomplishments", h("span", { class: "muted-text small" }, ` ${p.evidence.filter((r) => !r.excluded).length} verified · stored only on this PC`)),
      p.evidence.length ? h("div", { class: "profile-rows" }, p.evidence.map(profileRow))
        : h("div", {}, h("p", { class: "muted-text" }, "Nothing yet. Validate a notebook that reads or writes a lakehouse table; when its clean run finishes Ready, it is recorded here."),
          h("p", { class: "muted-text small" }, RECORDING_ON)),
      p.evidence.length ? h("p", { class: "muted-text small" }, "Validating the same code again adds nothing. Change the notebook and validate it to record a new revision.") : null),
    h("section", { class: "card pad" },
      h("h2", {}, "Skills", h("span", { class: "muted-text small" }, ` Evidence coverage, rules v${pr.rules_version}`)),
      h("div", { class: "profile-skills" }, p.skills.map((s) => h("div", { class: "profile-skill" }, h("span", {}, s.name),
        h("span", { class: "muted-text small" }, s.state === "Insufficient evidence" ? `Insufficient evidence (${s.rows} of 3)` : s.state)))),
      h("p", { class: "muted-text small" }, "Insufficient evidence means KratosForge hasn't seen enough yet. It is not a rating.")));
  const right = h("div", { class: "profile-col" },
    p.settings.progression ? profileLevelCard(pr) : null,
    p.settings.progression ? profileAchievementsCard(pr) : null,
    h("section", { class: "card pad" }, h("h2", {}, "Professional report"),
      h("p", {}, ((n) => `${n} verified accomplishment${n === 1 ? "" : "s"} to draw on.`)(p.evidence.filter((r) => !r.excluded).length)),
      h("div", {}, btn("Open the report", null, () => { profilePage.tab = "report"; render(); }, "tool link"))));
  return h("div", { class: "profile-grid" }, left, right);
}

function profileRow(r) {
  const verdict = r.verdict === "ready" ? "Ready" : "Ready with warnings";
  const d = r.detail || {};
  return h("div", { class: `profile-row${r.excluded ? " excluded" : ""}` },
    h("div", { class: "grow" },
      h("div", { class: "row wrap" }, h("b", {}, `Validated notebook ${r.artifact.name}`),
        r.excluded ? h("span", { class: "pill-s neu" }, "Excluded") : h("span", { class: "pill-s pass" }, "Verified"),
        r.origin === "backfill" ? h("span", { class: "pill-s neu" }, "Added from history") : null),
      h("div", {}, `Clean run finished ${verdict} on this PC. Reads ${d.reads || 0} lakehouse table${d.reads === 1 ? "" : "s"}, writes ${d.writes || 0} (sandboxed).`),
      h("div", { class: "muted-text small mono" }, `Validate ${r.ref.id} · code revision ${String(r.ref.revision || "").slice(0, 12)} · ${new Date(r.at).toLocaleString()}`),
      h("div", { class: "muted-text small" }, `Skills: ${r.dimensions.join(", ")}${profilePage.data.settings.progression && r.xp ? ` · +${r.xp} XP` : ""}`)),
    btn(r.excluded ? "Include" : "Exclude", null, async () => {
      const out = await attempt(() => rpc("profile_exclude", { id: r.id, excluded: !r.excluded }), "Could not change the accomplishment");
      if (out) { profilePage.data = out; render(); }
    }, "tool outline small"));
}

function profileLevelCard(pr) {
  const span = pr.next_level_xp - pr.level_xp;
  const into = pr.xp - pr.level_xp;
  return h("section", { class: "card pad" },
    h("h2", {}, "KratosForge level"),
    h("div", { class: "row" }, h("img", { src: "wolf-forge-emblem.png", alt: "", width: 48, height: 48 }),
      h("div", {}, h("b", { class: "profile-level" }, `Level ${pr.level} · ${pr.title}`),
        h("div", { class: "muted-text" }, `${pr.xp} XP · ${pr.next_level_xp - pr.xp} to level ${pr.level + 1}`))),
    h("div", { class: "profile-bar", role: "progressbar", "aria-label": `XP to level ${pr.level + 1}`,
      "aria-valuemin": "0", "aria-valuemax": String(span), "aria-valuenow": String(into) },
      h("div", { style: `width:${Math.round((100 * into) / span)}%` })),
    h("p", { class: "muted-text small" }, "Levels show how far you've come in KratosForge. They are not a skill rating."));
}

function profileAchievementsCard(pr) {
  return h("section", { class: "card pad" },
    h("h2", {}, "Achievements"),
    pr.achievements.length ? pr.achievements.map((a) => h("div", { class: "profile-badge-row" },
      h("div", { class: "profile-badge", "aria-hidden": "true" }, icon(a.badge === "flame" ? "flash" : "ok", 18)),
      h("div", {}, h("b", {}, a.name), h("div", { class: "muted-text small" }, `${a.description} · ${new Date(a.at).toLocaleDateString()} · +${a.xp} XP`))))
      : h("p", { class: "muted-text" }, "None yet."),
    pr.next.length ? h("div", { class: "profile-next" }, h("div", { class: "muted-text small" }, "Next"),
      pr.next.map((n) => h("div", { class: "profile-skill" }, h("span", {}, n.name), h("span", { class: "muted-text small" }, n.progress)))) : null);
}

function profileHowItWorks() {
  return howItWorks("profile", {
    reads: "Notebooks that pass Validate with a clean run (the result Validate already keeps on this PC). Nothing else yet.",
    changes: "Writes one line per verified accomplishment to a file in %LOCALAPPDATA%\\FabricDesktop\\profile\\, for the account you're signed in with: "
      + "what passed, when, and the id of the Validate result. It never stores code, cell outputs, table rows, passwords or tokens.",
    results: "Only on this PC. Nothing is sent anywhere, no Fabric job is started and no telemetry is added. Reports and exports go to Downloads as new files.",
    more: [["Fair by design", "Running the same code again adds nothing. XP comes from milestones, never from hours, run counts or compute used. "
      + "KratosForge can't tell who wrote a notebook, so it records what was validated, not who authored it."]],
  });
}

// ---------- Professional report ----------

function profileReport(p) {
  const period = h("select", { "aria-label": "Reporting period", onchange: (e) => { profilePage.days = Number(e.target.value); profilePage.report = null; render(); } },
    [[90, "Last 90 days"], [180, "Last 6 months"], [365, "Last 12 months"]].map(([v, l]) => h("option", { value: v, selected: profilePage.days === v ? true : null }, l)));
  const games = h("label", { class: "row" }, h("input", { type: "checkbox", checked: profilePage.achievements ? true : null,
    onchange: (e) => { profilePage.achievements = e.target.checked; profilePage.report = null; render(); } }), "Include KratosForge achievements");
  const summary = h("textarea", { rows: 4, maxlength: 2000, placeholder: "Leave empty to use a summary worded from your verified accomplishments." }, p.report.summary || "");
  const preview = h("div", { class: "card pad profile-report", "aria-live": "polite" }, h("p", { class: "muted-text" }, "Loading..."));
  const params = () => ({ days: profilePage.days, include_achievements: profilePage.achievements });
  (async () => {
    const out = profilePage.report || await attempt(() => rpc("profile_report", params()), "Could not put the report together");
    if (!out) return;
    profilePage.report = out;
    preview.replaceChildren(...markdownBlocks(out.markdown));
  })();
  const controls = h("div", { class: "card pad profile-report-controls" },
    h("label", { class: "field" }, "Reporting period", period), games,
    h("p", { class: "muted-text small" }, "Achievements are off by default: a workplace report shows the work, not the game."),
    h("label", { class: "field" }, "Executive summary (optional)", summary),
    h("div", { class: "row wrap" },
      btn("Save summary", null, async () => { profilePage.report = null; await saveProfile({ report_summary: summary.value }); }, "tool outline small")),
    h("p", { class: "muted-text small" }, "Exclude an accomplishment on Overview to leave it out. Every line says whether it is verified, entered by you or worded from the evidence."),
    h("div", { class: "row wrap" },
      btn("Save Markdown", "save", async () => {
        const out = await attempt(() => rpc("profile_report_save", params()), "Could not save the report");
        if (out) toast(`Saved ${out.path}`);
      }, "tool primary"),
      btn("Copy summary", "copyArrowRight", async () => {
        if (profilePage.report && !(await copyText(profilePage.report.summary, "Summary"))) toast("Couldn't copy the summary.", true);
      }, "tool outline")),
    h("p", { class: "muted-text small" }, "Files go to Downloads and never replace one that is there."));
  return h("div", { class: "profile-report-layout" }, controls, preview);
}

// Just enough Markdown for the report: headings, bullets, paragraphs, *emphasis* and \-escapes
// (the engine escapes notebook names, so a name like *x* stays text).
function markdownBlocks(md) {
  const inline = (text) => {
    const out = [];
    const re = /\\(.)|\*([^*\\]+)\*/g;
    let last = 0;
    for (let m = re.exec(text); m; m = re.exec(text)) {
      if (m.index > last) out.push(text.slice(last, m.index));
      out.push(m[1] !== undefined ? m[1] : h("em", {}, m[2]));
      last = re.lastIndex;
    }
    if (last < text.length) out.push(text.slice(last));
    return out;
  };
  const out = [];
  let list = null;
  for (const line of md.split("\n")) {
    if (line.startsWith("- ")) {
      if (!list) out.push(list = h("ul", {}));
      list.append(h("li", {}, ...inline(line.slice(2))));
      continue;
    }
    list = null;
    if (line.startsWith("## ")) out.push(h("h3", {}, line.slice(3)));
    else if (line.startsWith("# ")) out.push(h("h2", {}, line.slice(2)));
    else if (line.trim()) out.push(h("p", {}, ...inline(line)));
  }
  return out;
}

// ---------- Settings ----------

function profileSettings(p) {
  const s = p.settings;
  const toggle = (key, on, after) => h("label", { class: "switch" }, h("input", { type: "checkbox", checked: on ? true : null,
    onchange: async (e) => { if (await saveProfile({ settings: { [key]: e.target.checked } }, null)) after?.(e.target.checked); } }), h("span", {}, on ? "On" : "Off"));
  const rows = h("section", { class: "card pad" },
    settingRow("Record my accomplishments", "Keep a private record of work KratosForge verified, such as notebooks that pass Validate. On unless you turn it off; turning it off keeps what's recorded.",
      toggle("record", s.record)),
    settingRow("Developer progression", "Show levels, XP and achievements. Turn off to keep only your accomplishments and the professional report.",
      toggle("progression", s.progression)),
    settingRow("Achievement notifications", "Show a short message when you earn an achievement.", toggle("notifications", s.notifications)),
    settingRow("Profile visibility", "Your profile is never shared. There is no leaderboard and no manager view.", h("span", {}, "Private")),
    s.record && !s.backfilled ? settingRow("Include past validations",
      "Once: add notebooks that passed Validate before you turned recording on, dated when they ran.",
      btn("Add them", null, async () => {
        const out = await attempt(() => rpc("profile_backfill", {}), "Could not add past validations");
        if (out) { profilePage.data = out; toast(`Added ${out.added} past validation${out.added === 1 ? "" : "s"}`); render(); }
      }, "tool outline")) : null);
  const data = h("section", { class: "card pad" }, h("h2", {}, "Your data"),
    h("p", { class: "muted-text" }, "Everything here is stored only on this PC, for the account you're signed in with."),
    h("div", { class: "row wrap" },
      btn("Export my profile", "arrowDownload", async () => {
        const out = await attempt(() => rpc("profile_export", {}), "Could not export your profile");
        if (out) toast(`Saved ${out.path}`);
      }, "tool outline"),
      btn("Delete my profile data", "delete", async () => {
        if (!(await confirmDialog("Delete your profile data?", "This deletes your recorded accomplishments, profile details and report summary on this PC. "
          + "Validate history and run receipts stay as they are. It can't be undone.", "Delete", { danger: true, typeToConfirm: "DELETE" }))) return;
        if (await attempt(() => rpc("profile_delete", {}), "Could not delete your profile data")) {
          profileForget();
          toast("Your profile data was deleted"); profilePage.tab = "overview"; render();
        }
      }, "tool outline danger")));
  return withHowItWorks(h("div", {}, rows, data), profileHowItWorks());
}

// ---------- after a validation ----------

// Achievements already shown, so following the same Validate result again (or after a restart) never
// announces or reveals an award twice. The engine reports an award as earned only on the run that earned it.
// Kept per account, as the profile is.
const PROFILE_CELEBRATED = "kf.profile.celebrated";
const profileShown = new Set();  // this session's, for when the PC's storage can't be read
function profileCelebrated() {
  let kept = [];
  try { kept = JSON.parse(localStorage.getItem(PROFILE_CELEBRATED) || "[]"); } catch { /* unreadable: this session's list only */ }
  return new Set([...profileShown, ...(Array.isArray(kept) ? kept : [])]);
}

// Deleting the profile deletes its achievements, so earning one again shows again.
function profileForget() {
  const mine = (k) => k.startsWith(`${state.user || ""}|`);
  [...profileShown].filter(mine).forEach((k) => profileShown.delete(k));
  try { localStorage.setItem(PROFILE_CELEBRATED, JSON.stringify([...profileCelebrated()].filter((k) => !mine(k)))); } catch { /* nothing kept to forget */ }
}

// Called by Validate when a clean run earned something; shown after Validate's own message, with the
// Achievement Reveal on that message when Achievement celebrations is on (motion.js, off by default).
function profileEarned(earned) {
  if (!earned?.length) return;
  const seen = profileCelebrated();
  const key = (a) => `${state.user || ""}|${a.id}`;
  const fresh = earned.filter((a) => !seen.has(key(a)));
  if (!fresh.length) return;
  fresh.forEach((a) => { seen.add(key(a)); profileShown.add(key(a)); });
  try { localStorage.setItem(PROFILE_CELEBRATED, JSON.stringify([...seen])); } catch { /* not kept: only this session remembers */ }
  const names = fresh.map((a) => `${a.name} (+${a.xp} XP)`).join(", ");
  // The first achievement is also the first time most people hear that recording is on.
  const first = fresh.some((a) => a.id === "first-flame") ? ` ${RECORDING_ON}` : "";
  setTimeout(() => {
    toast(`Achievement earned: ${names}. See it in Profile.${first}`, false, { log: true });
    forgeEvent("achievement.unlocked", document.getElementById("toast"));  // one reveal per message, however many awards it names
  }, 2500);
}

registerFeature({
  id: "profile", preview: true,
  views: { profile: renderProfile },
  commands: [
    { label: "Profile", icon: "person", keywords: "profile accomplishments achievements level xp skills report review", preview: true,
      run: () => { profilePage.tab = "overview"; go("profile"); } },
    { label: "Professional report", icon: "person", keywords: "profile performance review accomplishments export", preview: true,
      run: () => { profilePage.tab = "report"; go("profile"); } },
  ],
});
