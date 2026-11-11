// Academy (Preview, ACD-007/008): certification prep that runs on this PC, from the approved mockup
// (docs/academy/). Six tabs: Home, Path, Practice, Assessments, Readiness and Portfolio, and one exercise
// open at a time in its workspace (Run, Check my work, hints, compare, reset). Academy takes Learn's spot
// in the rail; Learn's lessons and labs open from Practice and rank and profile picture stay in Profile.
// Exercises use data generated here; nothing reads or writes OneLake or starts anything in Fabric.

const academyPage = { tab: "home", pack: "dp-700", open: null, panel: "task", left: false, open2: {},
  run: null, check: null, hint: null, busy: false };
const ACADEMY_TABS = [["home", "Home"], ["path", "Path"], ["practice", "Practice"], ["assessments", "Assessments"],
  ["readiness", "Readiness"], ["portfolio", "Portfolio"]];
const ACADEMY_CLASS = { local_verified: "LOCAL VERIFIED", simulated: "SIMULATED", knowledge_only: "KNOWLEDGE ONLY",
  fabric_verified: "FABRIC VERIFIED", databricks_verified: "DATABRICKS VERIFIED" };
const ACADEMY_MODES = { guided: "Guided", challenge: "Challenge", troubleshoot: "Troubleshooting" };
const ACADEMY_STATES = { shown: "✓ Proved", practised: "Practised", not_started: "Not started" };
const ACADEMY_MEASURES = [["knowledge", "Knowledge"], ["guided", "Guided"], ["independent", "Independent"],
  ["troubleshooting", "Troubleshooting"], ["platform_verified", "Verified on the platform"], ["retention", "Retention"]];
const ACADEMY_LOCAL = "Exercises run on this PC with data generated here. They never read or write OneLake and never start anything in Fabric.";

function academyPill(cls) {
  return h("span", { class: `academy-pill ${cls}` }, ACADEMY_CLASS[cls] || cls);
}

function academyMeta(e) {
  return [ACADEMY_MODES[e.mode] || e.mode, e.minutes ? `about ${e.minutes} min` : null].filter(Boolean).join(" · ");
}

function academyOpen(eid) {
  Object.assign(academyPage, { open: eid, panel: "task", run: null, check: null, hint: null });
  go("academy");
}

async function academyData() {
  const [cat, ready] = await Promise.all([rpc("academy_catalog"), rpc("academy_readiness", { pack_id: academyPage.pack })]);
  return { cat, ready: academyReady(ready), pack: cat.packs.find((p) => p.pack.id === academyPage.pack) || cat.packs[0] };
}

// Readiness names its platform's keys platform_* once a pack can be on another platform (#1784); an
// engine from before that says fabric_*. Read either.
function academyReady(r) {
  const pick = (o, a, b) => (a in o ? o[a] : o[b]);
  return { ...r, platform: r.platform || "fabric",
    domains: r.domains.map((d) => ({ ...d, need_platform: pick(d, "need_platform", "need_fabric") })),
    objectives: r.objectives.map((o) => ({ ...o, platform_required: pick(o, "platform_required", "fabric_required"),
      needs_platform: pick(o, "needs_platform", "needs_fabric"),
      measures: { ...o.measures, platform_verified: pick(o.measures, "platform_verified", "fabric_verified") } })) };
}

async function renderAcademy() {
  if (academyPage.open) return renderAcademyExercise(academyPage.open);
  if (!ACADEMY_TABS.some(([id]) => id === academyPage.tab)) academyPage.tab = "home";
  const body = h("div", { class: "academy-body" }, h("p", { class: "muted-text" }, "Reading the certification..."));
  setView(h("div", { class: "page academy" },
    h("div", { class: "page-head" }, h("div", {},
      h("h1", {}, "Academy ", previewTag()),
      pageIntro("Certification prep you practise on this PC, checked the way each exercise says. ", docLink("academy-guide.md", "Learn more"))),
      h("div", { class: "head-actions" })),
    h("div", { class: "subtabs", role: "tablist", "aria-label": "Academy" }, ACADEMY_TABS.map(([id, label]) =>
      h("button", { class: `tab${academyPage.tab === id ? " active" : ""}`, role: "tab", "aria-selected": academyPage.tab === id ? "true" : "false",
        onclick: () => { academyPage.tab = id; render(); } }, label))),
    withHowItWorks(body, howItWorks("academy", {
      reads: "The certification packs that ship with KratosForge, each exercise's folder on this PC, and your Profile's evidence.",
      changes: "Starting an exercise makes a folder under this PC's KratosForge data (academy/<pack>/<exercise>) with generated data and your answer. Reset deletes that folder and makes it again. A passed exercise is recorded in Profile. Nothing in Fabric or OneLake changes.",
      results: "This page and Profile. Readiness is KratosForge's own estimate, not the vendor's score.",
    }))));
  // The Assessment Center (academy-assessments.js, its own PR) draws into the tab when it's there.
  if (academyPage.tab === "assessments") return typeof renderAssessments === "function" ? renderAssessments(body, academyPage.pack) : fill(body, academyAssessments());
  const data = await academyData().catch((err) => { if (body.isConnected) fill(body, errorPanel(err, { label: "Couldn't load Academy", retry: () => render() })); return null; });
  if (!data || state.view !== "academy" || !body.isConnected) return;
  const tab = academyPage.tab;
  if (tab === "path") fill(body, ...academyPath(data));
  else if (tab === "practice") fill(body, ...academyPractice(data));
  else if (tab === "readiness") fill(body, ...academyReadiness(data));
  else if (tab === "portfolio") fill(body, ...academyPortfolio(data));
  else fill(body, ...academyHome(data));
}

// ---------- Home ----------

function academyHome({ pack, ready }) {
  const cert = pack.certification;
  const live = Object.fromEntries(pack.exercises.filter((e) => e.status === "live").map((e) => [e.id, e]));
  const next = ready.next.map((n) => ({ ...n, ...live[n.exercise] })).filter((n) => n.id);
  const first = next[0];
  const nextCard = first ? h("section", { class: "card pad academy-next", "aria-labelledby": "academy-next" },
    h("div", { class: "learn-eyebrow" }, `Your next step · ${cert.id.toUpperCase()}`),
    h("h2", { id: "academy-next" }, `${first.id} ${first.title}`),
    h("div", { class: "row wrap" }, academyPill(first.class), h("span", { class: "academy-chip" }, first.objective), h("span", { class: "academy-chip" }, academyMeta(first))),
    h("div", { class: "row" }, btn("Continue", null, () => academyOpen(first.id), "tool primary"),
      btn("See the path", null, () => { academyPage.tab = "path"; render(); }, "tool outline")))
    : emptyState({ icon: "beaker", title: "Every published exercise is passed", text: "New exercises arrive with app updates. Readiness shows what's still open." });
  const domains = h("section", { class: "card pad", "aria-labelledby": "academy-cert" },
    h("h2", { id: "academy-cert", class: "academy-cert-title" }, `${cert.id.toUpperCase()}: ${cert.title || cert.id}`),
    h("p", { class: "muted-text small academy-cert-sub" }, `Study guide of ${cert.version} · ${pack.coverage.objectives} objectives`),
    h("div", { class: "academy-domains" }, ready.domains.map((d) => h("a", { href: "#", class: "academy-domain",
      onclick: (e) => { e.preventDefault(); academyPage.tab = "path"; academyPage.open2[d.id] = true; render(); } },
      h("b", {}, `${d.id} ${d.title} `), h("span", { class: "muted-text small" }, `${d.weight.min}–${d.weight.max}%`),
      h("div", { class: "academy-bar", role: "img", "aria-label": `${d.shown} of ${d.objectives} proved` },
        h("div", { style: `width:${d.objectives ? Math.round((100 * d.shown) / d.objectives) : 0}%` })),
      h("span", { class: "muted-text small" }, `${d.shown} of ${d.objectives} objectives proved · ${d.need_platform} need ${academyPlatform(ready)}`)))));
  const moved = h("section", { class: "card pad academy-moved", "aria-labelledby": "academy-moved" },
    h("h2", { id: "academy-moved" }, "Your Learn lessons and rank moved here"),
    h("p", {}, "Every Learn lesson and lab is in Practice, with your progress kept. Your rank, achievements and profile picture are in Profile. Nothing was reset."),
    h("div", { class: "row" }, btn("Open lessons", null, () => { academyPage.tab = "practice"; render(); }, "tool link"),
      btn("Your rank", null, () => go("profile"), "tool link")));
  const rec = h("section", { class: "card pad", "aria-labelledby": "academy-rec" }, h("h2", { id: "academy-rec" }, "Recommended for you"),
    next.length > 1 ? h("ul", { class: "academy-list" }, next.slice(1).map((n) => h("li", {},
      h("a", { href: "#", onclick: (e) => { e.preventDefault(); academyOpen(n.id); } }, `${n.id} ${n.title}`),
      h("span", { class: "muted-text small" }, ` ${n.objective} · ${academyMeta(n)} `), academyPill(n.class))))
      : h("p", { class: "muted-text" }, "Nothing else to suggest yet."));
  return [nextCard, domains, moved, rec, academyLegend(), h("p", { class: "muted-text small" }, ACADEMY_LOCAL)];
}

function academyPlatform(ready) {
  return ready.platform === "databricks" ? "Databricks" : "Fabric";
}

function academyLegend() {
  const row = (cls, text) => h("li", {}, academyPill(cls), " ", text);
  return h("section", { class: "card pad", "aria-labelledby": "academy-legend" }, h("h2", { id: "academy-legend" }, "How work is checked"),
    h("ul", { class: "academy-list" }, row("local_verified", "Your work ran on this PC and passed its checks."),
      row("simulated", "Checked against a model of the service, not the service itself."),
      row("fabric_verified", "Ran in your own Fabric workspace. Not available yet."),
      row("knowledge_only", "Questions; nothing ran.")));
}

// ---------- Path ----------

function academyPath({ cat, pack, ready }) {
  const picker = h("select", { "aria-label": "Certification", onchange: (e) => { academyPage.pack = e.target.value; render(); } },
    cat.packs.map((p) => h("option", { value: p.pack.id, selected: p.pack.id === pack.pack.id ? "" : null }, p.certification.title || p.pack.id)));
  const leftBox = h("input", { type: "checkbox", onchange: (e) => { academyPage.left = e.target.checked; render(); } });
  leftBox.checked = academyPage.left;
  const plat = academyPlatform(ready);
  const live = new Set(pack.exercises.filter((e) => e.status === "live").map((e) => e.id));
  const byId = Object.fromEntries(ready.objectives.map((o) => [o.id, o]));
  const objText = Object.fromEntries((pack.certification.domains || []).flatMap((d) => (d.skills || [{ id: d.id, objectives: d.objectives }])
    .flatMap((s) => (s.objectives || []).map((o) => [o.id, o]))));
  const gapText = Object.fromEntries((pack.certification.gaps || []).map((g) => [g.id, g]));
  const sections = (pack.certification.domains || []).map((d, i) => {
    const mine = ready.objectives.filter((o) => o.domain === d.id);
    const dr = ready.domains.find((x) => x.id === d.id) || {};
    const open = academyPage.open2[d.id] ?? i === 0;
    const toggle = h("button", { type: "button", class: "academy-acc", "aria-expanded": open ? "true" : "false",
      onclick: () => { academyPage.open2[d.id] = !open; render(); } },
      h("span", { class: "academy-acc-title" }, `${d.id} ${d.title}`),
      h("span", { class: "muted-text small" }, ` ${d.weight.min}–${d.weight.max}% · ${dr.shown || 0} of ${mine.length} proved · ${dr.need_platform || 0} need ${plat}`));
    const groups = d.skills || [{ id: d.id, title: "", objectives: d.objectives }];
    return h("section", { class: "card academy-domain-card" }, h("h2", { class: "academy-acc-head" }, toggle),
      open ? groups.map((s) => {
        const rows = (s.objectives || []).map((o) => byId[o.id]).filter((o) => o && (!academyPage.left || o.state !== "shown"));
        if (!rows.length) return null;
        return h("table", { class: "list academy-table" },
          s.title ? h("caption", {}, `${s.id} ${s.title}`) : null,
          h("thead", {}, h("tr", {}, ["Id", "Objective", "Best evidence", "Status", "Exercises"].map((c) => h("th", { scope: "col" }, c)))),
          h("tbody", {}, rows.map((o) => {
            const exs = (objText[o.id]?.exercises || []).filter((e) => live.has(e));
            return h("tr", {}, h("td", {}, o.id),
              h("th", { scope: "row" }, o.text, o.platform_required ? h("span", { class: "academy-needs" }, ` Needs ${plat}`) : null,
                ...o.gaps.map((g) => h("div", { class: "muted-text small" }, `Gap ${g}: ${gapText[g]?.what || gapText[g]?.reason || ""}`))),
              h("td", {}, o.best_class ? academyPill(o.best_class) : h("span", { class: "muted-text" }, "Not yet")),
              h("td", { class: `academy-state ${o.state}` }, o.state === "practised" && o.needs_platform ? `Practised · needs ${plat}` : ACADEMY_STATES[o.state]),
              h("td", {}, exs.length ? exs.map((e, k) => [k ? ", " : "", h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); academyOpen(e); } }, e)])
                : h("span", { class: "muted-text small" }, "Coming")));
          })));
      }) : null);
  });
  return [h("div", { class: "row wrap academy-path-head" }, h("label", { class: "row" }, h("b", {}, "Certification"), picker),
    h("span", { class: "spacer" }), h("label", { class: "row small" }, leftBox, "Show only what's left")),
  h("p", { class: "muted-text small" }, `Objectives are the vendor's, word for word, from the study guide of ${ready.version}. An objective that needs ${plat} stays incomplete until you pass a ${plat.toUpperCase()} VERIFIED exercise in your own workspace; local work still counts as practice. Exercises in ${plat} aren't available yet: when they are, each asks first and shows its risk check, because their OneLake reads and writes use your capacity.`),
  ...sections];
}

// ---------- Practice, Assessments, Portfolio ----------

function academyPractice({ pack }) {
  const live = pack.exercises.filter((e) => e.status === "live");
  return [h("h2", { class: "learn-section" }, "Exercises"),
    live.length ? h("div", { class: "learn-grid" }, live.map((e) =>
      h("button", { class: "card pad learn-card", onclick: () => academyOpen(e.id), "aria-label": `Open ${e.id}, ${e.title}` },
        h("div", { class: "row" }, h("b", {}, `${e.id} ${e.title}`)),
        h("div", { class: "row wrap" }, academyPill(e.class), h("span", { class: "muted-text small" }, `${e.objectives.join(", ")} · ${academyMeta(e)}`)))))
      : h("p", { class: "muted-text" }, "No exercises yet."),
    h("section", { class: "card pad", "aria-labelledby": "academy-lessons" }, h("h2", { id: "academy-lessons" }, "Lessons and labs"),
      h("p", {}, "The Learn lessons, practice labs and best-practice rules, with your progress kept."),
      h("div", { class: "row" }, btn("Open lessons and labs", "beaker", () => { learnPage.open = null; learnPage.tab = "home"; go("learn"); }, "tool outline"))),
    h("p", { class: "muted-text small" }, ACADEMY_LOCAL)];
}

function academyAssessments() {
  return h("section", { class: "card pad" }, h("h2", {}, "Diagnostics and practice exams"),
    h("p", {}, "Timed practice exams and short diagnostics by domain arrive with the Assessment Center. They use KratosForge's own questions, never real exam questions, and their results count as Knowledge in Readiness."));
}

function academyPortfolio({ pack, ready }) {
  const passed = new Map();
  for (const o of ready.objectives) for (const [key, label] of ACADEMY_MEASURES.slice(1)) for (const ref of o.measures[key]) {
    const id = ref.split("/").pop();
    if (!passed.has(id)) passed.set(id, new Set());
    passed.get(id).add(label);
  }
  const byId = Object.fromEntries(pack.exercises.map((e) => [e.id, e]));
  return [h("section", { class: "card pad", "aria-labelledby": "academy-done" }, h("h2", { id: "academy-done" }, "Exercises you've passed"),
    passed.size ? h("table", { class: "list" }, h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Exercise"), h("th", { scope: "col" }, "Checked as"), h("th", { scope: "col" }, "Counts for"))),
      h("tbody", {}, [...passed].map(([id, kinds]) => h("tr", {}, h("th", { scope: "row" }, `${id} ${byId[id]?.title || ""}`),
        h("td", {}, byId[id] ? academyPill(byId[id].class) : null), h("td", {}, [...kinds].join(", "))))))
      : h("p", { class: "muted-text" }, "Nothing yet. A passed exercise appears here and in Profile.")),
  h("section", { class: "card pad", "aria-labelledby": "academy-rank" }, h("h2", { id: "academy-rank" }, "Rank, achievements and profile picture"),
    h("p", {}, "They stay in Profile, with everything you've earned."), btn("Open Profile", null, () => go("profile"), "tool outline"))];
}

// ---------- Readiness ----------

function academyReadiness({ ready }) {
  const plat = academyPlatform(ready);
  const count = (s) => ready.objectives.filter((o) => o.state === s).length;
  const tile = (label, n, of) => h("div", { class: "card pad academy-tile" }, h("span", { class: "muted-text small" }, label),
    h("b", {}, String(n), of ? h("span", { class: "muted-text small" }, ` of ${of}`) : null));
  const cell = (o, key) => {
    if (key === "knowledge") {
      const k = o.measures.knowledge;
      return k.answered ? `${k.correct} of ${k.answered}` : h("span", { class: "muted-text" }, "Not yet");
    }
    if (key === "platform_verified" && !o.platform_required) return h("span", { class: "muted-text" }, "Not needed");
    const ids = o.measures[key];
    return ids.length ? h("span", { class: "academy-ok" }, `✓ ${ids.map((r) => r.split("/").pop()).join(", ")}`)
      : h("span", { class: key === "platform_verified" ? "academy-needs" : "muted-text" }, key === "platform_verified" ? "Needed" : "Not yet");
  };
  return [h("div", { role: "note", class: "academy-note" }, `${ready.note} Results stay tied to the study guide of ${ready.version}.`,
    ready.earlier_version_rows ? ` ${ready.earlier_version_rows} earlier results belong to an older study guide and are kept as they were.` : ""),
  h("div", { class: "academy-tiles" }, tile("Objectives proved", count("shown"), ready.objectives.length),
    tile(`Practised on this PC, still need ${plat}`, ready.objectives.filter((o) => o.state === "practised" && o.needs_platform).length),
    tile("Not started", count("not_started"))),
  ...ready.domains.map((d) => h("section", { class: "card academy-domain-card", "aria-labelledby": `academy-r-${d.id}` },
    h("h2", { id: `academy-r-${d.id}`, class: "academy-acc-head" }, `${d.id} ${d.title}`),
    h("div", { class: "academy-scroll", tabindex: "0", role: "region", "aria-label": `${d.id} ${d.title}, by objective` }, h("table", { class: "list academy-table" },
      h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Objective"), ACADEMY_MEASURES.map(([key, label]) =>
        h("th", { scope: "col" }, key === "platform_verified" ? `${plat} verified` : label)))),
      h("tbody", {}, ready.objectives.filter((o) => o.domain === d.id).map((o) => h("tr", {},
        h("th", { scope: "row" }, `${o.id} ${o.text}`), ACADEMY_MEASURES.map(([key]) => h("td", {}, cell(o, key)))))))))),
  h("p", { class: "muted-text small" }, "Independent means a challenge passed with at most one hint and the explanation closed. Retention means the same exercise passed again a week or more later.")];
}

// ---------- Exercise workspace ----------

async function renderAcademyExercise(eid) {
  setView(h("div", { class: "page academy" }, h("p", { class: "muted-text" }, "Opening the exercise...")));
  const pack = academyPage.pack;
  const ex = await rpc("academy_exercise", { pack_id: pack, exercise_id: eid }).catch((err) => { toast(friendlyError(err).text, true); return null; });
  if (!ex) { academyPage.open = null; return render(); }
  if (state.view !== "academy" || academyPage.open !== eid) return;
  const ws = ex.workspace;
  const leave = () => { academyPage.open = null; render(); };
  const head = h("div", { class: "page-head" }, h("div", {},
    h("div", { class: "small academy-crumb" }, h("a", { href: "#", onclick: (e) => { e.preventDefault(); leave(); } }, "Academy"),
      ` / ${pack.toUpperCase()} · ${ex.objectives.join(", ")}`),
    h("h1", {}, `${ex.id} ${ex.title} `, previewTag()),
    h("p", { class: "sub row wrap" }, academyPill(ex.class), ` ${academyMeta(ex)} · ${ex.verification.engine}`)));
  const work = h("div", { class: "academy-work" });
  const aside = h("aside", { class: "card academy-panel", "aria-label": "Exercise panel" });
  setView(h("div", { class: "page academy academy-exercise" }, head, h("div", { class: "learn-split" }, work, aside)));
  if (!ws?.started) {
    fill(work, h("p", {}, ex.scenario), h("div", { class: "row" },
      btn("Start the exercise", "play", async () => {
        fill(work, h("p", { class: "muted-text", role: "status" }, "Making the exercise's data on this PC..."));
        await attempt(() => rpc("academy_start", { pack_id: pack, exercise_id: eid }), "Could not start the exercise");
        render();
      }, "tool primary"), h("span", { class: "muted-text small" }, "Makes this exercise's data on this PC. Nothing is downloaded.")));
    return academyPanel(aside, ex, ws);
  }
  const editor = h("textarea", { class: "learn-editor", spellcheck: "false", "aria-label": `Your SQL for ${ex.id}`, rows: 14 });
  editor.value = ws.answer || "";
  const saved = h("span", { class: "muted-text small", role: "status" }, "Saved on this PC");
  let saveTimer = null;
  const save = () => rpc("academy_save", { pack_id: pack, exercise_id: eid, text: editor.value })
    .then(() => { saved.textContent = `Saved on this PC ${timeText(new Date())}`; })
    .catch(() => { saved.textContent = "Not saved yet"; });
  const out = h("div", { class: "learn-result" }, learnResult(academyPage.run));
  editor.addEventListener("input", () => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 1500);
    saved.textContent = "Saving...";
    if (academyPage.run) { academyPage.run = null; fill(out); }
  });
  const runIt = async () => {
    if (academyPage.busy) return;
    academyPage.busy = true;
    fill(out, h("p", { class: "muted-text" }, "Running on this PC..."));
    clearTimeout(saveTimer);
    save();
    try { academyPage.run = await rpc("academy_run", { pack_id: pack, exercise_id: eid, sql: boxChosenText(editor) }); }
    catch (err) { academyPage.run = { error: friendlyError(err).text }; }
    finally { academyPage.busy = false; }
    if (out.isConnected) fill(out, learnResult(academyPage.run));
  };
  const checkIt = async () => {
    if (academyPage.busy) return;
    academyPage.busy = true;
    clearTimeout(saveTimer);
    await save();
    academyPage.check = { running: true };
    academyPage.panel = "check";
    academyPanel(aside, ex, ws);
    try { academyPage.check = await rpc("academy_validate", { pack_id: pack, exercise_id: eid, answer: editor.value }); }
    catch (err) { academyPage.check = { error: friendlyError(err).text }; }
    finally { academyPage.busy = false; }
    if (aside.isConnected) academyPanel(aside, ex, ws);
  };
  const more = btn("…", null, () => showMenu(more, [
    { label: "Compare with the expected result", action: () => academyCompare(ex) },
    { label: "Reset the exercise…", icon: "restart", action: () => academyReset(ex) },
  ]), "tool outline", { "aria-label": "More actions", "aria-haspopup": "menu", "aria-expanded": "false" });
  editor.addEventListener("keydown", (e) => { if (isRunKey(e)) { e.preventDefault(); runIt(); } });
  fill(work, h("div", { role: "toolbar", "aria-label": "Exercise", class: "row wrap" },
    btn("Run", "play", runIt, "tool primary", RUN_BOX_TIP), btn("Check my work", "ok", checkIt, "tool outline"), more,
    h("span", { class: "spacer" }), saved),
  editor, h("p", { class: "muted-text small" }, "F5 or Ctrl+Enter runs (just the selected text, when you select some). Changes are undone after each run, so the exercise's data stays as it was."), out);
  academyPanel(aside, ex, ws);
}

function academyPanel(aside, ex, ws) {
  const tabs = [["task", "Task"], ["hints", ex.hints ? `Hints (${Math.max(academyPage.hint?.level || 0, ws?.hints_used || 0)} of ${ex.hints} used)` : "Hints"], ["check", "Check"]];
  const body = h("div", { class: "academy-panel-body", role: "tabpanel" });
  fill(aside, h("div", { class: "subtabs", role: "tablist", "aria-label": "Exercise panel" }, tabs.map(([id, label]) =>
    h("button", { class: `tab${academyPage.panel === id ? " active" : ""}`, role: "tab", "aria-selected": academyPage.panel === id ? "true" : "false",
      onclick: () => { academyPage.panel = id; academyPanel(aside, ex, ws); } }, label))), body);
  if (academyPage.panel === "hints") return fill(body, ...academyHints(aside, ex, ws));
  if (academyPage.panel === "check") return fill(body, ...academyCheck(aside, ex, ws));
  fill(body, h("h2", {}, "Scenario"), h("p", {}, ex.scenario),
    h("h2", {}, "Requirements"), h("p", {}, ex.objective_text),
    ex.expected?.description ? h("p", { class: "muted-text small" }, `Expected: ${ex.expected.description}`) : null,
    h("h2", {}, "How it's checked"), h("ul", { class: "academy-list" }, ex.verification.verifies.map((t) => h("li", {}, t))),
    h("p", { class: "muted-text small" }, "It doesn't prove: ", ex.verification.does_not_verify.join(" ")),
    ex.lesson ? h("details", {}, h("summary", {}, "Lesson"), learnLesson(ex.lesson)) : null,
    ex.remediation.length ? h("p", { class: "small" }, `If you're stuck, revisit ${ex.remediation.join(" and ")} in Practice.`) : null);
}

function academyHints(aside, ex, ws) {
  if (!ex.hints) return [h("p", { class: "muted-text" }, "This exercise has no hints.")];
  const hint = academyPage.hint;
  return [h("p", { class: "muted-text small" }, "Hints go from a nudge to nearly the answer. A challenge passed with more than one hint doesn't count as independent."),
    hint ? h("div", { class: "learn-hint" }, h("b", {}, `Hint ${hint.level} of ${hint.of}`), h("p", {}, hint.text)) : null,
    !hint || hint.level < hint.of ? btn(hint ? "Show the next hint" : "Show a hint", null, async () => {
      try { academyPage.hint = await rpc("academy_hint", { pack_id: academyPage.pack, exercise_id: ex.id, level: (hint?.level || 0) + 1 }); }
      catch (err) { toast(friendlyError(err).text, true); return; }
      academyPanel(aside, ex, ws);
    }, "tool outline") : null];
}

function academyCheck(aside, ex, ws) {
  const c = academyPage.check;
  if (!c) {
    return [h("p", { class: "muted-text" }, "Check my work runs these checks on this PC:"),
      h("ul", { class: "learn-checks" }, ex.checks.map((k) => h("li", {}, h("span", { class: "learn-mark", "aria-hidden": "true" }, "○"), h("b", {}, LEARN_CHECKS[k] || k)))),
      ws?.last ? h("p", { class: "muted-text small" }, `Last time: ${ws.last.passed ? "passed" : "not yet"}.`) : null];
  }
  if (c.running) return [h("p", { class: "muted-text", role: "status" }, "Checking on this PC...")];
  if (c.error) return [h("p", { class: "learn-error", role: "alert" }, c.error)];
  const passed = c.results.filter((r) => r.passed).length;
  const v = c.verification;
  return [h("div", { role: "status", class: `academy-status${c.passed ? " ok" : ""}` },
    h("span", {}, c.passed ? `✓ Passed: ${passed} of ${c.results.length} checks` : `Not yet: ${passed} of ${c.results.length} checks pass`)),
  h("ul", { class: "learn-checks" }, c.results.map((r) => h("li", { class: r.passed ? "ok" : "no" },
    h("span", { class: "learn-mark", "aria-hidden": "true" }, r.passed ? "✓" : "✗"),
    h("div", {}, h("b", {}, `${LEARN_CHECKS[r.kind] || r.kind}${r.passed ? "" : " (not yet)"}`), h("div", { class: "muted-text small" }, r.message || r.detail))))),
  h("div", {}, h("div", { class: "learn-eyebrow" }, "What this proves"), v.verifies.join(" ")),
  h("div", {}, h("div", { class: "learn-eyebrow" }, "What it doesn't"), v.does_not_verify.join(" ")),
  c.passed ? h("div", {}, h("div", { class: "learn-eyebrow" }, "Explanation"), btn("Read why this works", null, async () => {
    const e = await rpc("academy_explanation", { pack_id: academyPage.pack, exercise_id: ex.id }).catch((err) => { toast(friendlyError(err).text, true); return null; });
    if (e && aside.isConnected) aside.querySelector(".academy-explain")?.replaceChildren(h("p", {}, e.text));
  }, "tool link"), h("div", { class: "academy-explain" })) : null,
  c.passed ? h("div", { class: "academy-recorded" }, h("b", {}, "Progress"),
    c.recorded ? ` · Recorded for ${ex.objectives.join(", ")} as ${(ACADEMY_MODES[ex.mode] || ex.mode).toLowerCase()} practice${c.hints_used ? `, ${c.hints_used} hint${c.hints_used > 1 ? "s" : ""} used` : ""}. `
      : " · Not recorded: Profile is off or this pass was already recorded today. ",
    h("a", { href: "#", onclick: (e) => { e.preventDefault(); academyPage.open = null; academyPage.tab = "readiness"; render(); } }, "See readiness")) : null,
  c.passed ? academyNextButton(ex) : null];
}

function academyNextButton(ex) {
  const slot = h("div", { class: "row" });
  rpc("academy_readiness", { pack_id: academyPage.pack }).then((r) => {
    const n = (r.next || []).find((x) => x.exercise !== ex.id);
    if (n && slot.isConnected) slot.replaceChildren(btn(`Next: ${n.exercise}`, null, () => academyOpen(n.exercise), "tool primary"));
  }).catch(() => {});
  return slot;
}

async function academyCompare(ex) {
  const yours = academyPage.run;
  await confirmDialog("Compare with the expected result",
    `Expected: ${ex.expected?.description || "see the requirements"}${yours?.columns ? ` Your last run returned ${yours.rows.length}${yours.more ? "+" : ""} rows with columns ${yours.columns.join(", ")}.` : " Run your SQL first to compare."}`,
    "Close", { cancelLabel: "Back" });
}

async function academyReset(ex) {
  const yes = await confirmDialog("Reset this exercise?", `This deletes ${ex.id}'s folder on this PC, your answer included, and makes it again from the start.`,
    "Reset exercise", { danger: true });
  if (!yes) return;
  Object.assign(academyPage, { run: null, check: null, hint: null, panel: "task" });
  await attempt(() => rpc("academy_reset", { pack_id: academyPage.pack, exercise_id: ex.id }), "Could not reset the exercise");
  render();
}

registerFeature({
  id: "academy",
  preview: true,
  rail: { label: "Academy", icon: "beaker", order: 80, bottom: true, also: ["learn"] },
  views: { academy: renderAcademy },
  commands: [
    { label: "Open Academy", icon: "beaker", keywords: "academy certification exam dp-700 study practice readiness", run: () => { academyPage.open = null; go("academy"); } },
    { label: "Academy readiness", icon: "beaker", keywords: "academy readiness exam objectives", run: () => { academyPage.open = null; academyPage.tab = "readiness"; go("academy"); } },
  ],
});
