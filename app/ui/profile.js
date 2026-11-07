// Profile: a private record of work KratosForge verified, what it adds up to, and a review-ready
// report (progress.py and progress_service.py in the engine). Opens from the account menu; it has
// no rail slot. Levels and achievements are for fun; skills show evidence coverage, never a score.
// Loaded after features.js; everything here runs after startup.

const profilePage = { tab: "overview", data: null, days: 90, achievements: false, report: null, fullPath: false };
const RECORDING_ON = "Recording is on: notebooks that pass Validate or Prove are kept in a private record on this PC. Turn it off in Profile > Settings.";
// Badge glyphs (20x22), from the approved mockup. Locked achievements show the lock.
const BADGE_GLYPHS = {
  flame: "M10 2c1 4 6 6 6 11a6 6 0 0 1-12 0c0-3 2-4 2-7 2 1 3 3 3 5 1-2 1-5 1-9z",
  check: "M3 11.5l4.5 4.5L17 6l-1.6-1.6L7.5 12.8 4.6 9.9z",
  proof: "M3 4h9l5 5v9H3zM11 4v5h6",
  lock: "M6 9V7a4 4 0 0 1 8 0v2h1v10H5V9zm2 0h4V7a2 2 0 0 0-4 0z",
  spark: "M10 1l2 6 6 1-5 4 2 7-5-4-5 4 2-7-5-4 6-1z",
  anvil: "M2 5h12l4 3h-5v3l3 5H4l3-5V8H2z",
  bolt: "M11 1L4 11h5l-1 8 8-11h-5z",
  eye: "M10 4c5 0 8 6 8 6s-3 6-8 6-8-6-8-6 3-6 8-6zm0 3a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  shield: "M10 1l7 3v5c0 5-3 8-7 10-4-2-7-5-7-10V4z",
};

async function loadProfile() {
  profilePage.data = await attempt(() => rpc("profile_get", {}), "Could not open your profile");
  return profilePage.data;
}

async function saveProfile(params, done = "Saved") {
  const out = await attempt(() => rpc("profile_save", params), "Could not save your profile");
  if (out) { profilePage.data = out; if (done) toast(done); render(); }
  return out;
}

function profileTabs(p) {
  const tab = (id, label) => h("button", { class: `tab${profilePage.tab === id ? " active" : ""}`, role: "tab",
    "aria-selected": profilePage.tab === id ? "true" : "false", onclick: () => { profilePage.tab = id; render(); } }, label);
  return h("div", { class: "subtabs", role: "tablist", "aria-label": "Profile" },
    tab("overview", "Overview"), p.settings.progression ? tab("journey", "Journey") : null,
    p.settings.progression ? tab("ranks", "Ranks") : null,
    p.settings.progression ? tab("achievements", "Achievements") : null,
    tab("impact", "Impact"), tab("goals", "Goals"),
    tab("report", "Professional report"), tab("settings", "Settings"));
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
      profileAvatar(p, "profile-avatar"),
      h("div", {}, previewMarked("profile", h("h1", {}, who.display_name || "Your profile")),
        h("p", { class: "sub" }, [who.role, who.technologies, state.user ? `Signed in as ${state.user}` : "Not signed in: this profile is kept for this PC"]
          .filter(Boolean).join(" · "), " ", learnMore("profile-preview")))),
    btn("Edit profile", "person", editProfileIdentity, "tool outline"));
  // Journey and Achievements are progression: with it off they aren't shown.
  if (!p.settings.progression && ["journey", "ranks", "achievements"].includes(profilePage.tab)) profilePage.tab = "overview";
  const body = { overview: profileOverview, journey: profileJourney, ranks: profileRanks, achievements: profileAchievements, impact: profileImpact, goals: profileGoals, report: profileReport,
    settings: profileSettings }[profilePage.tab](p);
  const notice = p.notice ? h("div", { class: "callout git-warn", role: "alert" }, h("span", { class: "grow" }, p.notice),
    btn("Dismiss", null, () => saveProfile({ dismiss_notice: true }, null), "tool outline small")) : null;
  setView(h("div", { class: "page profile" }, head, notice, profileTabs(p), body));
}

function editProfileIdentity() {
  const who = profilePage.data.identity;
  const field = (label, key, placeholder) => h("label", { class: "field" }, label,
    h("input", { value: who[key] || "", placeholder, "data-key": key, maxlength: 200 }));
  const fields = [field("Display name", "display_name", state.user || ""), field("Role or specialization", "role", "Data engineer"),
    field("Preferred technologies", "technologies", "Python, SQL, Delta Lake")];
  const picture = h("div", { class: "field" }, "Profile picture",
    h("div", { class: "row" }, profileAvatar(profilePage.data, "profile-avatar"),
      btn("Choose picture", null, () => { closeDialog(); profileChoosePicture(); }, "tool outline small"),
      profilePage.data.avatar ? btn("Remove picture", null, async () => {
        closeDialog();
        const out = await attempt(() => rpc("profile_avatar_remove", {}), "Could not remove your picture");
        if (out) { profilePage.data = out; state.avatar = null; toast("Picture removed"); render(); }
      }, "tool outline small") : null),
    h("span", { class: "muted-text small" }, "PNG, JPEG or WebP up to 5 MB. Kept only on this PC, cropped to a square. "
      + "Each rank on Profile > Ranks has a prompt you can copy into your own image tool."));
  showDialog("Edit profile", h("div", { class: "profile-form" }, picture, ...fields), [
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
        : h("div", {}, h("p", { class: "muted-text" }, "Nothing yet. Validate a notebook that reads or writes a lakehouse table; when its clean run finishes Ready, it is recorded here. "
          + "A proof whose tables all match in Fabric is recorded too."),
          h("p", { class: "muted-text small" }, RECORDING_ON)),
      p.evidence.length ? h("p", { class: "muted-text small" }, "Validating or proving the same code again adds nothing. Change the notebook to record a new revision.") : null),
    h("section", { class: "card pad" },
      h("h2", {}, "Skills", h("span", { class: "muted-text small" }, ` Evidence coverage, rules v${pr.rules_version}`)),
      h("div", { class: "profile-skills" }, p.skills.map((s) => h("div", { class: "profile-skill" }, h("span", {}, s.name),
        h("span", { class: "muted-text small" }, s.state === "Insufficient evidence" ? `Insufficient evidence (${s.rows} of 3)` : s.state)))),
      h("p", { class: "muted-text small" }, "Insufficient evidence means KratosForge hasn't seen enough yet. It is not a rating.")));
  const right = h("div", { class: "profile-col" },
    p.settings.progression ? profileRankCard(p.ranks) : null,
    p.settings.progression ? profileLevelCard(pr) : null,
    p.settings.progression ? profileAchievementsCard(pr) : null,
    h("section", { class: "card pad" }, h("h2", {}, "Professional report"),
      h("p", {}, ((n) => `${n} verified accomplishment${n === 1 ? "" : "s"} to draw on.`)(p.evidence.filter((r) => !r.excluded).length)),
      h("div", {}, btn("Open the report", null, () => { profilePage.tab = "report"; render(); }, "tool link"))));
  return h("div", { class: "profile-grid" }, left, right);
}

// A Learn row (rules v2): an exercise passed, or a lesson or lab finished, on this PC.
function profileLearnRow(r) {
  const d = r.detail || {};
  const m = r.measurement || {};
  const what = r.artifact.kind === "lab" ? "lab" : "lesson";
  const title = `${r.artifact.name}${r.artifact.title ? ` · ${r.artifact.title}` : ""}`;
  const done = r.type === "module.complete";
  const n = d.exercises || 0;
  const text = done
    ? (n === 1 ? "Its exercise passed on this PC." : `All ${n} exercises of the ${what} passed on this PC.`)
    : `Every check passed on this PC: ${(d.checks || []).map((k) => LEARN_CHECKS[k] || k).join(", ")}.${typeof m.speedup === "number"
      ? ` Measured ${m.speedup}× faster than the starting query on this PC (${m.seconds} s against ${m.baseline_seconds} s).` : ""}`;
  return [done ? `Finished ${what} ${title}` : `Passed exercise ${r.artifact.number || r.artifact.exercise} in ${title}`, text,
    `Learn ${r.ref.id} · ${what} version ${String(r.ref.revision).replace(/^v/, "")}`];
}

// A best-practice fix applied after Compare found the same rows (GD-06, rules v3).
function profileFixRow(r) {
  const d = r.detail || {};
  const m = r.measurement || {};
  const rows = typeof d.rows === "number" ? `${d.rows.toLocaleString()} row${d.rows === 1 ? "" : "s"}` : "rows";
  const times = typeof m.before_seconds === "number" && typeof m.after_seconds === "number"
    ? ` Measured, one run each: ${m.before_seconds} s as written, ${m.after_seconds} s fixed.` : "";
  return [`Fixed ${r.artifact.name}${r.artifact.where ? ` on ${r.artifact.where}` : ""}, and checked it`,
    `Compare ran the query as written and as fixed: the same ${rows}${d.level === "complete" ? ", every row compared" : ""}.${times}`,
    `Compare · runs ${String(r.ref.revision).slice(0, 12)} and ${String(r.ref.id).slice(0, 12)}`];
}

function profileRow(r) {
  const d = r.detail || {};
  const proved = r.type === "prove.match";
  const learned = r.ref?.source === "learn" ? profileLearnRow(r) : r.type === "fix.checked" ? profileFixRow(r) : null;
  const m = r.measurement || {};
  const times = [["this PC", m.pc_seconds], ["Fabric", m.fabric_seconds]].filter(([, v]) => typeof v === "number")
    .map(([where, v]) => `${where} ${v} s`).join(", ");
  const text = proved
    ? `Same results on this PC and in Fabric: ${d.tables || 0} table${d.tables === 1 ? "" : "s"} compared.${times ? ` Measured end to end: ${times}.` : ""}`
    : `Clean run finished ${r.verdict === "ready" ? "Ready" : "Ready with warnings"} on this PC. Reads ${d.reads || 0} lakehouse table${d.reads === 1 ? "" : "s"}, writes ${d.writes || 0} (sandboxed).`;
  const ref = proved ? `Run receipt ${String(r.ref.id).slice(0, 12)}` : `Validate ${r.ref.id}`;
  return h("div", { class: `profile-row${r.excluded ? " excluded" : ""}` },
    h("div", { class: "grow" },
      h("div", { class: "row wrap" }, h("b", {}, learned ? learned[0] : `${proved ? "Proved" : "Validated"} notebook ${r.artifact.name}`),
        r.excluded ? h("span", { class: "pill-s neu" }, "Excluded") : h("span", { class: "pill-s pass" }, "Verified"),
        r.origin === "backfill" ? h("span", { class: "pill-s neu" }, "Added from history") : null),
      h("div", {}, learned ? learned[1] : text),
      h("div", { class: "muted-text small mono" }, learned ? `${learned[2]} · ${dateTimeText(r.at)}`
        : `${ref} · code revision ${String(r.ref.revision || "").slice(0, 12)} · ${dateTimeText(r.at)}`),
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
      h("div", {}, h("b", { class: "profile-level" }, `Level ${pr.level}`),
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
      h("div", { class: "profile-badge", "aria-hidden": "true" }, profileGlyph(a.badge, true)),
      h("div", {}, h("b", {}, a.name), h("div", { class: "muted-text small" }, `${a.description} · ${dateText(a.at)} · +${a.xp} XP`))))
      : h("p", { class: "muted-text" }, "None yet."),
    pr.next.length ? h("div", { class: "profile-next" }, h("div", { class: "muted-text small" }, "Next"),
      pr.next.map((n) => h("div", { class: "profile-skill" }, h("span", {}, n.name), h("span", { class: "muted-text small" }, n.progress)))) : null);
}

function profileGlyph(badge, earned) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", "20"); svg.setAttribute("height", "22"); svg.setAttribute("viewBox", "0 0 20 22");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", BADGE_GLYPHS[earned ? badge : "lock"] || BADGE_GLYPHS.check);
  svg.append(path);
  return svg;
}

// ---------- Profile picture (PB-125) ----------

// The picture, or the first letter of the name when there isn't one.
function profileAvatar(p, cls) {
  const who = p.identity || {};
  const letter = (who.display_name || state.user || "?")[0].toUpperCase();
  return profilePicture(p.avatar, `${cls} has-picture`) || h("div", { class: cls, "aria-hidden": "true" }, letter);
}

// Only the engine's own picture: a base64 PNG data URL, which <img> decodes as an image and never runs.
function profilePicture(url, cls) {
  const ok = /^data:image\/png;base64,[A-Za-z0-9+/]+=*$/.test(url || "");
  return ok ? h("img", { class: cls, src: url, alt: "" }) : null;
}

const PROFILE_PICTURE_TYPES = ["image/png", "image/jpeg", "image/webp"];
const PROFILE_PICTURE_MAX = 5 * 1024 * 1024;
const PROFILE_PICTURE_SIZE = 256;

function profileChoosePicture() {
  const input = h("input", { type: "file", accept: PROFILE_PICTURE_TYPES.join(",") });
  input.addEventListener("change", () => input.files?.[0] && profileSetPicture(input.files[0]));
  input.click();
}

// Crop the middle square, scale it to 256 px and save it again as PNG here, so only pixels reach the
// engine: no photo metadata such as location, and nothing but an image to decode.
async function profileSetPicture(file) {
  if (!PROFILE_PICTURE_TYPES.includes(file.type)) { toast("Choose a PNG, JPEG or WebP picture.", true); return; }
  if (file.size > PROFILE_PICTURE_MAX) { toast("Choose a picture of 5 MB or less.", true); return; }
  let png;
  try {
    const image = await createImageBitmap(file);
    const side = Math.min(image.width, image.height);
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = PROFILE_PICTURE_SIZE;
    canvas.getContext("2d").drawImage(image, (image.width - side) / 2, (image.height - side) / 2, side, side,
      0, 0, PROFILE_PICTURE_SIZE, PROFILE_PICTURE_SIZE);
    image.close?.();
    png = canvas.toDataURL("image/png").split(",")[1];
  } catch {
    toast("That picture couldn't be read. Try a PNG or JPEG.", true);
    return;
  }
  const out = await attempt(() => rpc("profile_avatar_set", { png_base64: png }), "Could not save your picture");
  if (out) { profilePage.data = out; state.avatar = out.avatar; toast("Picture saved"); render(); }
}

// The account button shows the picture too; read once the engine is up (app.js).
async function profileLoadAvatar() {
  if (!previewShown()) return;
  const out = await rpc("profile_avatar", {}).catch(() => null);
  state.avatar = out?.avatar || null;
  renderChrome();
}

// ---------- Ranks (PB-125) ----------

function profileRankCard(r) {
  const nxt = r.next;
  return h("section", { class: "card pad" },
    h("h2", {}, "Rank", h("span", { class: "muted-text small" }, ` ${r.number} of ${r.of}`)),
    h("div", { class: "row" }, h("img", { src: "wolf-forge-emblem.png", alt: "", width: 48, height: 48 }),
      h("div", {}, h("b", { class: "profile-level" }, r.rank),
        h("div", { class: "muted-text" }, r.ladder[r.number - 1].tier))),
    nxt ? h("div", { class: "profile-next" }, h("div", { class: "muted-text small" }, `Next: ${nxt.name}${nxt.coming_soon ? " (coming soon)" : ""}`),
      nxt.checks.map(profileRankCheck)) : h("p", { class: "muted-text" }, "You hold every rank there is."),
    h("div", { class: "row" }, btn("See all ranks", null, () => { profilePage.tab = "ranks"; render(); }, "tool link small")));
}

function profileRankCheck(c) {
  return h("div", { class: "profile-skill" }, h("span", {}, `${c.met ? "\u2713 " : ""}${c.text}`),
    h("span", { class: "muted-text small" }, c.met ? "Done" : c.coming_soon ? "Coming soon" : `${c.have} of ${c.need}`));
}

// Profile > Ranks, laid out as Chris's mockup (2026-11-07): a hero, Your rank, the Forge progression, Next
// rank, its requirements and what the rank gives. Everything shown comes from ranks.standing() and the XP
// in progress.progression(); nothing is estimated. The hero and shields are drawn here as placeholders
// until the approved artwork arrives; text and controls stay real UI either way.
const RANK_GLYPHS = {
  hammer: "M3 2h9v4H9v12H6V6H3z",
  gear: "M10 1l1.6 2.4 2.8-.8.4 2.8 2.6 1.4-1.4 2.6 1.4 2.4-2.6 1.4-.4 2.8-2.8-.8L10 19l-1.6-2.4-2.8.8-.4-2.8-2.6-1.4 1.4-2.4-1.4-2.6 2.6-1.4.4-2.8 2.8.8zM10 7a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  crown: "M2 15h16v2H2zM2 5l4.5 4L10 3l3.5 6L18 5l-1.6 9H3.6z",
  paw: "M10 9c3 0 5 4 5 6.5 0 1.5-2 1.5-5 1.5s-5 0-5-1.5C5 13 7 9 10 9zM4.5 6a1.6 2.1 0 1 1 0 4.2 1.6 2.1 0 0 1 0-4.2zm11 0a1.6 2.1 0 1 1 0 4.2 1.6 2.1 0 0 1 0-4.2zM7.5 2a1.7 2.2 0 1 1 0 4.4 1.7 2.2 0 0 1 0-4.4zm5 0a1.7 2.2 0 1 1 0 4.4 1.7 2.2 0 0 1 0-4.4z",
};
const RANK_GLYPH = { check: "check", "apprentice": "flame", "forge-initiate": "anvil", "iron-forger": "hammer", "steel-forger": "gear",
  "master-forger": "crown", "pack-runner": "paw", "trailfinder": "eye", "wolf-guardian": "shield", "pack-leader": "paw",
  "argonaut": "spark", "cyclops-smith": "bolt", "forge-architect": "proof", "legend-of-the-forge": "spark" };

function rankGlyph(id, size = 34) {
  const d = RANK_GLYPHS[RANK_GLYPH[id]] || BADGE_GLYPHS[RANK_GLYPH[id]] || BADGE_GLYPHS.spark;
  const el = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  el.setAttribute("width", size); el.setAttribute("height", size); el.setAttribute("viewBox", "0 0 20 22");
  el.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", d); path.setAttribute("fill-rule", "evenodd");
  el.append(path);
  return el;
}

// The shields on the progression: the ranks of the tier you're in, then the top rank if it is further on.
function rankStrip(r) {
  const here = r.number - 1;
  const tier = r.ladder[here].tier;
  const last = r.ladder.length - 1;
  const picks = r.ladder.map((x, i) => i).filter((i) => r.ladder[i].tier === tier);
  if (!picks.includes(last)) picks.push(last);
  return picks;
}

function rankShield(r, i) {
  const x = r.ladder[i];
  const here = i === r.number - 1;
  const cls = ["rk-shield", x.held ? "held" : "locked", here ? "current" : "", i === r.ladder.length - 1 ? "top" : ""].filter(Boolean).join(" ");
  const state = here ? "your rank" : x.held ? "held" : x.coming_soon ? "coming soon" : r.next?.id === x.id ? "next" : "not yet";
  return h("li", { class: cls, "aria-current": here ? "step" : null, "aria-label": `${x.name}, rank ${i + 1} of ${r.of}, ${state}` },
    h("div", { class: "rk-shield-edge" }, h("div", { class: "rk-shield-face" }, rankGlyph(x.id),
      h("b", { class: "rk-shield-name" }, x.name), h("span", { class: "rk-shield-sub" }, `Rank ${i + 1}`))));
}

function rankProgression(r) {
  const picks = rankStrip(r);
  const items = [];
  picks.forEach((i, k) => {
    if (k) {
      const skipped = i - picks[k - 1] - 1;
      items.push(h("li", { class: `rk-link${r.ladder[i].held ? " held" : ""}${skipped ? " gap" : ""}`, "aria-hidden": "true" },
        skipped ? h("span", {}, `+${skipped} ranks`) : null));
    }
    items.push(rankShield(r, i));
  });
  return h("section", { class: "card pad rk-progression" },
    h("div", { class: "row" }, h("h2", { class: "grow" }, `The ${r.ladder[r.number - 1].tier.replace(/^The /, "")} progression`),
      btn(profilePage.fullPath ? "Hide full rank path" : "View full rank path", null, () => {
        profilePage.fullPath = !profilePage.fullPath; render();
      }, "tool link small", { "aria-expanded": profilePage.fullPath ? "true" : "false" })),
    h("ol", { class: "rk-strip", "aria-label": "Rank progression" }, items));
}

function rankCheckRow(c) {
  return h("li", { class: `rk-req${c.met ? " met" : ""}` },
    h("span", { class: "rk-req-mark", "aria-hidden": "true" }, c.met ? rankGlyph("check", 14) : null),
    h("span", { class: "grow" }, c.text, c.met ? h("span", { class: "sr-only" }, " (done)") : null),
    h("span", { class: "muted-text small" }, c.met ? "Done" : c.coming_soon ? "Coming soon" : `${c.have} of ${c.need}`));
}

function profileRanks(p) {
  const r = p.ranks;
  const pr = p.progression;
  const here = r.ladder[r.number - 1];
  const nxt = r.next;
  const top = r.ladder[r.ladder.length - 1];
  const hero = h("section", { class: "rk-hero" },
    h("div", { class: "rk-hero-text" },
      h("h2", { class: "rk-hero-title" }, "Ranks"),
      h("p", { class: "rk-hero-kicker" }, "Your journey through KratosForge"),
      h("p", { class: "rk-hero-line" }, `Build skills, pass exercises and rise through the Forge from ${r.ladder[0].name} to ${top.name}.`)));
  const into = pr.xp - pr.level_xp;
  const span = Math.max(1, pr.next_level_xp - pr.level_xp);
  const yours = h("section", { class: "card pad rk-yours" },
    h("h2", {}, "Your rank"),
    h("div", { class: "row" }, h("img", { src: "wolf-forge-emblem.png", alt: "", width: 72, height: 72 }),
      h("div", {}, h("b", { class: "rk-name" }, here.name), h("div", { class: "muted-text" }, here.tier),
        h("span", { class: "pill-s pass" }, "Current rank"))),
    h("div", {}, h("b", { class: "profile-level" }, `Level ${pr.level}`),
      h("div", { class: "muted-text" }, `${pr.xp} XP / ${pr.next_level_xp} XP`)),
    h("div", { class: "profile-bar", role: "progressbar", "aria-label": `XP to level ${pr.level + 1}`,
      "aria-valuemin": "0", "aria-valuemax": String(span), "aria-valuenow": String(into) },
      h("div", { style: `width:${Math.round((100 * into) / span)}%` })),
    h("span", { class: "muted-text small" }, `Rank ${r.number} of ${r.of}. XP levels show how far you've come; they are not a skill rating.`));
  const first = nxt && (nxt.checks.find((c) => !c.met) || nxt.checks[0]);
  const nextCard = h("section", { class: "card pad rk-next" },
    h("h2", {}, "Next rank"),
    nxt ? h("div", { class: "row" }, h("span", { class: "rk-next-icon" }, rankGlyph(nxt.id, 30)),
      h("div", {}, h("b", { class: "rk-name" }, nxt.name),
        h("div", { class: "muted-text" }, nxt.coming_soon ? "Coming soon: its lessons haven't shipped yet" : first ? first.text : "")))
      : h("p", { class: "muted-text" }, "You hold every rank there is."),
    first ? h("div", { class: "row rk-next-bar" },
      h("div", { class: "profile-bar grow", role: "progressbar", "aria-label": first.text,
        "aria-valuemin": "0", "aria-valuemax": String(first.need), "aria-valuenow": String(first.have) },
        h("div", { style: `width:${Math.round((100 * first.have) / first.need)}%` })),
      h("span", { class: "muted-text small" }, `${first.have} of ${first.need}`)) : null,
    nxt ? btn("View requirements", null, () => document.getElementById("rk-requirements")?.focus(), "tool outline") : null);
  const reqs = h("section", { class: "card pad rk-reqs", id: "rk-requirements", tabindex: "-1" },
    h("h2", {}, nxt ? `Rank requirements: ${nxt.name}` : "Rank requirements"),
    nxt ? h("ul", { class: "rk-req-list" }, nxt.checks.map(rankCheckRow))
      : h("p", { class: "muted-text" }, "Every rank is held. There's nothing left to earn here."),
    h("p", { class: "muted-text small" }, "Only work KratosForge verified on this PC counts. Each rank needs every rank below it."));
  const reward = (text, extra) => h("li", { class: "rk-req met" }, h("span", { class: "rk-req-mark", "aria-hidden": "true" }, rankGlyph("check", 14)),
    h("span", { class: "grow" }, text), extra || null);
  const rewards = h("section", { class: "card pad rk-rewards" },
    h("h2", {}, "Rank rewards"),
    h("ul", { class: "rk-req-list" },
      reward(`The ${here.name} title on your profile`),
      reward("A picture prompt for your own profile badge",
        btn("Copy prompt", null, () => copyText(here.prompt, "Picture prompt"), "tool link small")),
      p.settings.record ? reward("Progress and XP from verified work") : null,
      nxt ? reward(`Opens the next rank: ${nxt.name}`) : null));
  const full = profilePage.fullPath ? profileRankPath(r) : null;
  return h("div", { class: "profile-col rk-page" }, hero,
    h("div", { class: "rk-row rk-row-1" }, yours, rankProgression(r)),
    h("div", { class: "rk-row rk-row-2" }, nextCard, reqs, rewards), full);
}

// Every rank by tier, with what earns it and its picture prompt (PB-125), behind "View full rank path".
function profileRankPath(r) {
  const tiers = [...new Set(r.ladder.map((x) => x.tier))];
  return h("div", { class: "profile-col", id: "rk-full-path" },
    h("p", { class: "muted-text" }, `Ranks come only from work KratosForge verified on this PC (ladder v${r.ladder_version}). `
      + "Each rank needs every rank below it. Copy a rank's picture prompt into your own image tool to make a profile picture; KratosForge doesn't make images."),
    tiers.map((tier) => h("section", { class: "card pad" }, h("h2", {}, tier),
      r.ladder.filter((x) => x.tier === tier).map((x) => h("div", { class: `profile-rank${x.held ? " held" : ""}` },
        h("div", { class: "row wrap" }, h("b", {}, x.name),
          h("span", { class: `pill-s ${x.held ? "pass" : "neu"}` }, x.held ? "Held" : x.coming_soon ? "Coming soon" : r.next?.id === x.id ? "Next" : "Not yet"),
          h("span", { class: "grow" }),
          btn("Copy picture prompt", null, () => copyText(x.prompt, "Picture prompt"), "tool link small")),
        x.checks.length ? x.checks.map(profileRankCheck) : h("div", { class: "muted-text small" }, "Everyone starts here."))))));
}

// ---------- Journey ----------

function profileJourney(p) {
  const pr = p.progression;
  const span = pr.next_level_xp - pr.level_xp;
  const into = pr.xp - pr.level_xp;
  const got = new Set(pr.achievements.map((a) => a.id));
  const when = (at) => dateText(at);
  const ring = h("div", { class: "profile-ring", style: `--profile-ring:${Math.round((100 * into) / span)}%`, "aria-hidden": "true" },
    h("img", { src: "wolf-forge-emblem.png", alt: "", width: 96, height: 96 }));
  const level = h("section", { class: "card pad profile-journey-level" }, ring,
    h("div", { class: "muted-text" }, `Level ${pr.level}`), h("b", { class: "profile-title" }, pr.title),
    h("div", { class: "muted-text small" }, `${pr.xp} XP · ${pr.next_level_xp - pr.xp} to level ${pr.level + 1}`),
    h("div", { class: "profile-bar", role: "progressbar", "aria-label": `XP to level ${pr.level + 1}`,
      "aria-valuemin": "0", "aria-valuemax": String(span), "aria-valuenow": String(into) },
      h("div", { style: `width:${Math.round((100 * into) / span)}%` })),
    h("div", { class: "row muted-text small profile-bar-ends" }, h("span", {}, `Level ${pr.level} at ${pr.level_xp}`),
      h("span", {}, `Level ${pr.level + 1} at ${pr.next_level_xp}`)));
  const recent = h("section", { class: "card pad" }, h("h2", {}, "Recent milestones"),
    pr.milestones.length ? pr.milestones.slice(0, 8).map((m) => h("div", { class: "profile-skill" }, h("span", {}, m.text),
      h("span", { class: "muted-text small" }, when(m.at))))
      : h("p", { class: "muted-text" }, "None yet. Your first validated notebook earns First Flame."));
  const badges = h("section", { class: "card pad" },
    h("h2", {}, "Achievements", h("span", { class: "muted-text small" }, ` ${got.size} of ${p.catalog.length} earned`)),
    h("div", { class: "profile-badges" }, p.catalog.map((a) => {
      const earned = pr.achievements.find((x) => x.id === a.id);
      return h("div", { class: `profile-shield-cell${earned ? " earned" : ""}`, title: a.criteria },
        h("div", { class: "profile-shield" }, profileGlyph(a.badge, Boolean(earned))),
        h("span", {}, a.name), h("span", { class: "muted-text small" }, earned ? when(earned.at) : "Not yet"));
    })));
  const next = h("section", { class: "card pad" }, h("h2", {}, "Next you could earn"),
    pr.next.length ? pr.next.map((n) => h("div", { class: "profile-skill" }, h("span", {}, h("b", {}, n.name), ` · ${n.progress}`),
      h("span", { class: "muted-text small" }, `+${n.xp} XP`)))
      : h("p", { class: "muted-text" }, "You've earned every achievement there is so far."));
  return h("div", { class: "profile-grid" }, h("div", { class: "profile-col" }, level, recent), h("div", { class: "profile-col" }, badges, next));
}

// ---------- Achievements: the rules, in full ----------

function profileAchievements(p) {
  const pr = p.progression;
  return h("div", { class: "profile-col" },
    h("p", { class: "muted-text" }, `Every achievement, what earns it and the evidence it rests on (rules v${pr.rules_version}). `
      + "Achievements come from milestones, never from hours, run counts or compute used."),
    p.catalog.map((a) => {
      const earned = pr.achievements.find((x) => x.id === a.id);
      return h("section", { class: "card pad" }, h("div", { class: "profile-badge-row" },
        h("div", { class: `profile-shield${earned ? "" : " locked"}` }, profileGlyph(a.badge, Boolean(earned))),
        h("div", { class: "grow" },
          h("div", { class: "row wrap" }, h("b", {}, a.name), h("span", { class: `pill-s ${earned ? "pass" : "neu"}` },
            earned ? `Earned ${dateText(earned.at)}` : "Not yet"), h("span", { class: "muted-text small" }, `+${a.xp} XP`)),
          h("div", {}, a.description),
          h("div", { class: "muted-text small" }, `How to earn it: ${a.criteria}`),
          h("div", { class: "muted-text small" }, `Evidence: ${a.evidence}`))));
    }),
    h("p", { class: "muted-text small" }, "After your first notebook, each new notebook validated earns 10 XP and each new notebook proven earns 20 XP, "
      + "up to three new notebooks a day. New revisions of a notebook are kept as evidence and earn no XP."));
}

function profileHowItWorks() {
  return howItWorks("profile", {
    reads: "Notebooks that pass Validate with a clean run, and proofs whose tables all matched (the results and run receipts already kept on this PC). Nothing else yet.",
    changes: "Writes one line per verified accomplishment to a file in %LOCALAPPDATA%\\FabricDesktop\\profile\\, for the account you're signed in with: "
      + "what passed, when, and the id of the Validate result or run receipt, plus the goals you set. It never stores code, cell outputs, table rows, passwords or tokens.",
    results: "Only on this PC. Nothing is sent anywhere, no Fabric job is started and no telemetry is added. Reports (Markdown or PDF) and exports go to Downloads as new files.",
    more: [["Fair by design", "Running the same code again adds nothing. XP comes from milestones, never from hours, run counts or compute used. "
      + "KratosForge can't tell who wrote a notebook, so it records what was validated, not who authored it."]],
  });
}

// ---------- Impact (KF-DX-009): measured results only ----------

const PROFILE_SECONDS = (v) => (typeof v === "number" ? `${v < 10 ? Number(v.toFixed(2)) : Math.round(v * 10) / 10} s` : "Not measured");

function profileImpact(p) {
  const im = p.impact;
  const o = im.outcomes;
  const stat = (n, label) => h("div", { class: "qprofile-stat" }, h("b", {}, String(n)), h("span", {}, label));
  const outcomes = h("section", { class: "card pad" }, h("h2", {}, "Verified outcomes"),
    h("div", { class: "qprofile-stats" },
      stat(o.notebooks_validated, "notebooks validated with a clean run"),
      stat(o.fixes_checked, "fixes checked: the same rows as written and as fixed"),
      stat(o.quality_labs, "data quality exercises: every planted problem found"),
      stat(o.notebooks_proven, "notebooks proven the same on this PC and in Fabric")),
    h("p", { class: "muted-text small" }, "Counts of verified work in your profile. Exclude an accomplishment on Overview to leave it out here too."));
  const row = (x) => {
    let figures;
    if (x.kind === "proof") figures = `This PC ${PROFILE_SECONDS(x.this_pc_seconds)} · Fabric ${PROFILE_SECONDS(x.fabric_seconds)}, end to end in the same proof`;
    else {
      const change = x.kind === "speed" && typeof x.speedup === "number" ? `${x.speedup}× faster`
        : typeof x.change_percent === "number" ? (x.change_percent >= 0 ? `${x.change_percent}% less time` : `${-x.change_percent}% more time`) : "";
      figures = `${PROFILE_SECONDS(x.before_seconds)} before · ${PROFILE_SECONDS(x.after_seconds)} after${change ? ` · ${change}` : ""}`;
    }
    return h("div", { class: "profile-row" }, h("div", { class: "grow" },
      h("div", { class: "row wrap" }, h("b", {}, x.what), h("span", { class: "pill-s pass" }, "Measured")),
      h("div", {}, figures),
      h("div", { class: "muted-text small" }, `${x.source} · ${dateTimeText(x.at)}`)));
  };
  const measured = h("section", { class: "card pad" },
    h("h2", {}, "Measured results", h("span", { class: "muted-text small" }, ` ${im.measured.length} measured · ${im.faster} faster after the change`)),
    im.measured.length ? h("div", { class: "profile-rows" }, im.measured.map(row))
      : h("p", { class: "muted-text" }, "Nothing measured yet. A Learn speed check, a fix checked with Compare, or a proof's run times show here, with the figures exactly as measured."),
    h("p", { class: "muted-text small" }, "Each figure is from one measurement on the machine named, never an estimate or a total. A change that made things slower is shown too."));
  const capacity = h("section", { class: "card pad" }, h("h2", {}, "Capacity and cost"),
    h("div", { class: "row wrap" }, h("span", { class: "pill-s neu" }, "Not measured"), h("span", {}, im.capacity.why)));
  return h("div", { class: "profile-col" }, outcomes, measured, capacity);
}

// ---------- Goals and the development roadmap (KF-DX-011) ----------

async function profileGoalCall(method, params, done) {
  const out = await attempt(() => rpc(method, params), "Could not change your goals");
  if (out) { profilePage.data = out; profilePage.report = null; if (done) toast(done); render(); }
}

function profileCheckRow(c) {
  return h("div", { class: "profile-skill" }, h("span", {}, `${c.met ? "\u2713 " : ""}${c.text}`),
    h("span", { class: "muted-text small" }, c.met ? "Done" : c.coming_soon ? "Coming soon" : `${c.have} of ${c.need}`));
}

function profileGoals(p) {
  const rm = p.roadmap;
  const full = rm.goals.length >= rm.max_goals ? { disabled: true, title: `Up to ${rm.max_goals} goals: remove one first` } : {};
  const own = h("input", { class: "profile-goal-input", placeholder: "A goal in your own words, such as: present the Gold layer design to the team", maxlength: 300, "aria-label": "Goal in your own words" });
  const addOwn = () => own.value.trim() && profileGoalCall("profile_goal_add", { text: own.value.trim() }, "Goal added");
  own.addEventListener("keydown", (e) => { if (e.key === "Enter") addOwn(); });
  const goal = (g) => h("div", { class: "profile-row" }, h("div", { class: "grow" },
    h("div", { class: "row wrap" }, h("b", {}, g.title),
      g.source === "entered"
        ? h("span", { class: `pill-s ${g.met ? "pass" : "neu"}` }, g.met ? "Done (marked by you)" : "In progress")
        : h("span", { class: `pill-s ${g.met ? "pass" : "neu"}` }, g.met ? "Met, verified" : "In progress"),
      g.track ? h("span", { class: "muted-text small" }, g.track) : null),
    g.source === "entered" ? h("div", { class: "muted-text small" }, "Entered by you. The report says so.")
      : h("div", {}, g.checks.map(profileCheckRow))),
    g.source === "entered" ? btn(g.met ? "Not done" : "Mark done", null,
      () => profileGoalCall("profile_goal_done", { id: g.id, done: !g.met }), "tool outline small") : null,
    btn("Remove", null, () => profileGoalCall("profile_goal_remove", { id: g.id }, "Goal removed"), "tool outline small"));
  const mine = h("section", { class: "card pad" },
    h("h2", {}, "My goals", h("span", { class: "muted-text small" }, ` ${rm.goals.length} of ${rm.max_goals}`)),
    rm.goals.length ? h("div", { class: "profile-rows" }, rm.goals.map(goal))
      : h("p", { class: "muted-text" }, "No goals yet. Pick a milestone from the roadmap below, or write one in your own words."),
    h("div", { class: "row" }, own, btn("Add goal", null, addOwn, "tool outline", full)),
    h("p", { class: "muted-text small" }, "A roadmap goal is met only when its checks are, from work KratosForge verified. Goals are for your own planning; "
      + "they appear in your professional report under Development goals."));
  const suggest = rm.suggestions.length ? h("section", { class: "card pad" }, h("h2", {}, "Suggested next"),
    rm.suggestions.map((s) => h("div", { class: "profile-skill" }, h("span", {}, h("b", {}, s.title), ` · ${s.track}`),
      btn("Make it a goal", null, () => profileGoalCall("profile_goal_add", { milestone: s.id }, "Goal added"), "tool link small", full))),
    h("p", { class: "muted-text small" }, "Suggestions sit next to work you've already shown. They never compare you with anyone.")) : null;
  const milestone = (m) => h("div", { class: `profile-rank${m.met ? " held" : ""}` },
    h("div", { class: "row wrap" }, h("b", {}, m.title),
      h("span", { class: `pill-s ${m.met ? "pass" : "neu"}` }, m.met ? "Met" : m.coming_soon ? "Coming soon" : "Not yet"),
      m.goal ? h("span", { class: "pill-s neu" }, "Your goal") : null, h("span", { class: "grow" }),
      m.goal || m.met ? null : btn("Make it a goal", null, () => profileGoalCall("profile_goal_add", { milestone: m.id }, "Goal added"), "tool link small", full)),
    h("div", { class: "muted-text small" }, `Skills: ${m.skills.join(", ")}`),
    h("div", {}, m.activities),
    m.checks.map(profileCheckRow),
    m.achievements.length && p.settings.progression ? h("div", { class: "muted-text small" }, `Related achievements: ${m.achievements.map((a) => a.name).join(", ")}`) : null,
    m.learn.length ? h("div", { class: "row wrap" }, h("span", { class: "muted-text small" }, "Start in Learn:"),
      m.learn.map((l) => btn(`${l.id} ${l.title}`, null, () => learnOpen(l.id), "tool link small"))) : null);
  const tracks = rm.tracks.map((t) => h("section", { class: "card pad" },
    h("h2", {}, t.title, h("span", { class: "muted-text small" }, ` ${t.met} of ${t.of} met`)), t.milestones.map(milestone)));
  return h("div", { class: "profile-col" }, mine, suggest,
    h("p", { class: "muted-text" }, `Development roadmap (v${rm.version}). Milestones are suggestions, not a fixed order: specialize where you like. `
      + "Every milestone can be met on this PC, so none needs a Fabric run."),
    tracks);
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
      btn("Save PDF", "arrowDownload", async () => {
        const out = await attempt(() => rpc("profile_report_pdf", params()), "Could not save the PDF");
        if (out) toast(`Saved ${out.path}`);
      }, "tool outline"),
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
        if (!(await confirmDialog("Delete your profile data?", "This deletes your recorded accomplishments, profile details, picture, goals and report summary on this PC. "
          + "Validate history and run receipts stay as they are. It can't be undone.", "Delete", { danger: true, typeToConfirm: "DELETE" }))) return;
        if (await attempt(() => rpc("profile_delete", {}), "Could not delete your profile data")) {
          profileForget();
          state.avatar = null; renderChrome();
          toast("Your profile data was deleted"); profilePage.tab = "overview"; render();
        }
      }, "tool outline danger")));
  return withHowItWorks(h("div", {}, rows, data), profileHowItWorks());
}

// ---------- after a validation or proof ----------

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

// Called by Validate and Prove when a run earned something; shown after their own message, with the
// Achievement Reveal on that message when Achievement celebrations is on (motion.js, on by default).
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
  const first = fresh.some((a) => a.first) ? ` ${RECORDING_ON}` : "";
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
    { label: "Development goals", icon: "person", keywords: "profile goals roadmap milestones learning plan skills growth", preview: true,
      run: () => { profilePage.tab = "goals"; go("profile"); } },
    { label: "Professional report", icon: "person", keywords: "profile performance review accomplishments export", preview: true,
      run: () => { profilePage.tab = "report"; go("profile"); } },
  ],
});
