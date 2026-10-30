// Learn (Preview, LX-05): the lessons and labs that ship inside the app (learn_service.py), from the
// approved mockup. Home, Paths, Labs and Best practices, and one module or lab open at a time: its
// lesson, its exercises in a lab on this PC, Run, Check my answer and hints. Labs use data generated
// here; they never read or write OneLake and never publish to Fabric.

const learnPage = { tab: "home", open: null, exercise: 0, results: {}, checks: {}, hints: {}, busy: false };
const LEARN_TABS = [["home", "Home"], ["paths", "Paths"], ["labs", "Labs"], ["practices", "Best practices"]];
const LEARN_LEVELS = { beginner: "Beginner", intermediate: "Intermediate", advanced: "Advanced" };
const LEARN_ENGINES = { duckdb: "DuckDB", polars: "Polars", pandas: "pandas", delta: "Delta", kernel: "notebook",
  pipeline: "pipeline", model: "semantic model" };
// Also Profile's words for a passed exercise's checks (profileLearnRow).
const LEARN_CHECKS = { result_matches: "Same result", table_shape: "Table shape", integrity: "Keys match",
  no_findings: "No best-practice findings", faster_than: "Faster", defects_found: "Problems found" };
const LEARN_LOCAL = "Lessons and labs run on this PC with data generated here. They never read or write OneLake and never publish to Fabric.";

function learnLast(id) {
  try {
    if (id) localStorage.setItem("learn.last", id);
    return localStorage.getItem("learn.last");
  } catch { return null; }
}

function learnMeta(m) {
  return [LEARN_LEVELS[m.level] || m.level, `${m.minutes} min`, (m.engines || []).map((e) => LEARN_ENGINES[e] || e).join(" or ")]
    .filter(Boolean).join(" · ");
}

function learnStatus(m) {
  if (m.status === "draft") return h("span", { class: "learn-badge draft" }, "Draft");
  return m.started ? h("span", { class: "learn-badge started" }, "Started") : h("span", { class: "learn-badge" }, "New");
}

function learnOpen(id) {
  learnLast(id);
  Object.assign(learnPage, { open: id, exercise: 0, results: {}, checks: {}, hints: {} });
  go("learn");
}

async function renderLearn() {
  if (learnPage.open) return renderLearnModule(learnPage.open);
  if (!LEARN_TABS.some(([id]) => id === learnPage.tab)) learnPage.tab = "home";
  const cont = h("span", { class: "learn-continue-slot" });
  const actions = h("div", { class: "head-actions" }, learnGuidancePicker(), cont);
  const body = h("div", { class: "learn-body" }, h("p", { class: "muted-text" }, "Reading the lessons..."));
  setView(h("div", { class: "page learn" },
    h("div", { class: "page-head" }, h("div", {},
      h("h1", {}, "Learn ", previewTag()),
      pageIntro("Short lessons and practice labs for SQL, Python and Fabric, with the best-practice rules behind them. ",
        docLink("learn.md", "Learn more"))), actions),
    h("div", { class: "subtabs", role: "tablist", "aria-label": "Learn" }, LEARN_TABS.map(([id, label]) =>
      h("button", { class: `tab${learnPage.tab === id ? " active" : ""}`, role: "tab", "aria-selected": learnPage.tab === id ? "true" : "false",
        onclick: () => { learnPage.tab = id; render(); } }, label))),
    withHowItWorks(body, howItWorks("learn", {
      reads: "The lessons and labs that ship with KratosForge, and each lab's folder on this PC.",
      changes: "Starting a lab makes a folder under this PC's KratosForge data (learn/<id>) with generated data and your answers. Reset lab deletes that folder and makes it again. Nothing in Fabric or OneLake changes.",
      results: "This page only. Your answers stay in the lab's folder on this PC.",
    }))));
  if (learnPage.tab === "practices") return learnPractices(body);
  const cat = await rpc("learn_catalog").catch((err) => { if (body.isConnected) fill(body, h("p", { class: "muted-text" }, friendlyError(err).text)); return null; });
  if (!cat || state.view !== "learn" || !body.isConnected) return;
  const byId = Object.fromEntries(cat.modules.map((m) => [m.id, m]));
  const inOrder = cat.paths.flatMap((p) => p.modules).map((id) => byId[id]).filter(Boolean);
  const next = byId[learnLast()] || inOrder.find((m) => m.started) || inOrder[0] || cat.modules[0];
  if (next && learnPage.tab !== "home") cont.replaceChildren(btn(`${next.started ? "Continue" : "Start"}: ${next.title}`, null, () => learnOpen(next.id), "tool primary"));
  if (learnPage.tab === "paths") fill(body, ...learnPaths(cat, byId));
  else if (learnPage.tab === "labs") fill(body, learnCards(cat.modules.filter((m) => m.kind === "lab"), "No labs yet."));
  else fill(body, learnAsk(), ...learnHome(cat, byId, next, inOrder));
}

function learnHome(cat, byId, next, inOrder) {
  const path = next && cat.paths.find((p) => p.id === next.path);
  const at = path ? path.modules.indexOf(next.id) + 1 : 0;
  const cont = next ? h("section", { class: "card pad learn-continue" },
    h("div", { class: "learn-eyebrow" }, `${next.started ? "Continue" : "Start here"}${path ? ` · ${path.title}` : ""}`),
    h("h2", {}, `${next.id} · ${next.title}`),
    h("p", {}, (next.objectives || []).join(". ") + (next.objectives?.length ? "." : "")),
    h("p", { class: "muted-text small" }, learnMeta(next), path ? ` · ${at} of ${path.modules.length} in the path` : ""),
    h("div", { class: "row" }, btn(next.started ? "Resume" : "Start", null, () => learnOpen(next.id), "tool primary"),
      path ? btn("See the path", null, () => { learnPage.tab = "paths"; render(); }, "tool outline") : null))
    : emptyState({ icon: "beaker", title: "No lessons yet", text: "Lessons arrive with app updates." });
  const after = next ? inOrder.slice(inOrder.indexOf(next) + 1) : [];
  const suggest = learnMode() === "learning" ? after.find((m) => !m.started) || inOrder.find((m) => !m.started && m !== next) : null;
  return [suggest ? h("div", { class: "learn-home-top" }, cont, h("section", { class: "card pad learn-suggest" },
    h("div", { class: "learn-eyebrow" }, "Suggested next ", engineInfo("Shown in Learning mode: the next lesson you haven't started.")),
    h("b", {}, `${suggest.id} · ${suggest.title}`),
    h("span", { class: "muted-text small" }, learnMeta(suggest)),
    h("div", { class: "row" }, btn("Open", null, () => learnOpen(suggest.id), "tool outline")))) : cont,
    h("h2", { class: "learn-section" }, "Practice labs"),
    learnCards(cat.modules.filter((m) => m.kind === "lab"), "No labs yet."),
    h("p", { class: "muted-text small" }, LEARN_LOCAL)];
}

function learnCards(list, none) {
  if (!list.length) return h("p", { class: "muted-text" }, none);
  return h("div", { class: "learn-grid" }, list.map((m) =>
    h("button", { class: "card pad learn-card", onclick: () => learnOpen(m.id), "aria-label": `Open ${m.id}, ${m.title}` },
      h("div", { class: "row" }, h("b", {}, `${m.id} · ${m.title}`), h("span", { class: "spacer" }), learnStatus(m)),
      h("span", { class: "muted-text small" }, learnMeta(m)),
      h("span", {}, (m.objectives || [])[0] || ""))));
}

function learnPaths(cat, byId) {
  return [h("div", { class: "learn-paths" }, cat.paths.map((p) => {
    const mods = p.modules.map((id) => byId[id]).filter(Boolean);
    return h("section", { class: "card pad" }, h("h2", {}, p.title),
      mods.length ? h("ol", { class: "learn-path-list" }, mods.map((m) => h("li", {},
        h("a", { href: "#", onclick: (e) => { e.preventDefault(); learnOpen(m.id); } }, `${m.id} · ${m.title}`),
        h("span", { class: "muted-text small" }, ` ${learnMeta(m)} `), learnStatus(m))))
        : h("p", { class: "muted-text small" }, "Lessons for this path come in a later update."));
  }))];
}

// Every notebook and SQL rule, with its explanation in the docs: the same rules the panels check.
async function learnPractices(body) {
  const [nb, sql] = await Promise.all([rpc("notebook_practice_rules"), rpc("sql_practice_rules")])
    .catch((err) => { if (body.isConnected) fill(body, h("p", { class: "muted-text" }, friendlyError(err).text)); return [null, null]; });
  if (!nb || state.view !== "learn" || !body.isConnected) return;
  const table = (title, rules) => h("section", { class: "card pad" }, h("h2", {}, title),
    h("table", { class: "list learn-rules" },
      h("thead", {}, h("tr", {}, h("th", {}, "Rule"), h("th", {}, "Severity"), h("th", {}, "What it checks"), h("th", {}, ""))),
      h("tbody", {}, rules.map((r) => h("tr", {}, h("td", {}, r.id), h("td", {}, r.severity), h("td", {}, r.name),
        h("td", {}, r.url ? docLink(r.url, `About ${r.id}`) : null))))));
  fill(body, table("Notebooks", nb), table("SQL", sql),
    h("p", { class: "muted-text small" }, "The notebook's Best practices panel and the SQL page check these as you work. A governance policy can turn a rule off or change its severity."));
}

// ---------- guidance (LX-06) ----------

// Settings > Appearance > Learning guidance (app_settings learn_mode). Focus is the default and adds
// nothing beside your work; with preview features hidden it's always Focus.
const learnMode = () => (previewShown() ? layoutChoice("learn_mode") : "focus");

function learnGuidancePicker() {
  const key = "learn_mode";
  const locked = typeof fixedBy === "function" && fixedBy(state.settings, key) !== null;
  return h("label", { class: "row learn-guidance" }, "Learning guidance ",
    h("select", { disabled: locked ? true : null, title: locked ? "Set by your organization's policy" : null,
      onchange: async (e) => { if (await saveSettings({ [key]: e.target.value })) render(); } },
    LAYOUT_CHOICES[key][2].map(([value, label]) => h("option", { value, selected: learnMode() === value ? true : null }, label))));
}

// The first time Learn opens (from the approved mockup): would you like help improving your skills?
// Asked once, only while the mode is still the default and nobody (policy or environment) set it.
// Save or Skip both store a choice, so it's never asked again; Skip keeps Focus.
function learnAsk() {
  const key = "learn_mode";
  if (!previewShown() || state.settings?.[key]?.source !== "default") return null;
  const choose = async (value) => { if (await saveSettings({ [key]: value })) render(); };
  const say = { focus: "Findings and their About link, as today. No suggestions.",
    guided: "Each finding also links the Learn lesson that covers it.",
    learning: "Guided, plus a suggested next lesson here on Learn." };
  const options = LAYOUT_CHOICES[key][2].map(([value, label]) => h("label", { class: "learn-ask-option" },
    h("input", { type: "radio", name: "learn-ask", value, checked: value === "focus" ? true : null }),
    h("span", {}, h("b", {}, label), value === "focus" ? h("span", { class: "muted-text small" }, " · the default") : null,
      h("span", { class: "muted-text small learn-ask-say" }, say[value]))));
  const card = h("section", { class: "card pad learn-ask", "aria-labelledby": "learn-ask-title" },
    h("h2", { id: "learn-ask-title" }, "Would you like KratosForge to help you improve your development skills?"),
    h("p", { class: "muted-text" }, "This only changes how much guidance you see beside your work. Checks run the same either way, and you can change it at any time with Learning guidance, above, or in Settings > Appearance > Layout."),
    h("fieldset", { class: "learn-ask-modes" }, h("legend", {}, "How much guidance would you like?"), options),
    h("div", { class: "row" },
      btn("Save", null, () => choose(card.querySelector("input[name=learn-ask]:checked")?.value || "focus"), "tool primary"),
      btn("Skip", null, () => choose("focus"), "tool outline")),
    h("p", { class: "muted-text small" }, "Your organization can set this for everyone. Learning progress stays on this PC."));
  return card;
}

// The lessons, read once per session, for the Lesson link beside findings.
let learnCatalogLoad = null;
function learnCatalogCached() {
  return (learnCatalogLoad ||= rpc("learn_catalog").catch(() => { learnCatalogLoad = null; return null; }));
}

// Guided and Learning: "Lesson: LM-01" beside a best-practice finding whose rule a lesson covers.
// Focus shows nothing extra. Only live lessons (drafts stay on Learn), and a lesson before a lab: the
// lesson explains the rule.
function learnFindingLink(ruleId) {
  if (learnMode() === "focus") return null;
  const slot = h("span", { class: "learn-finding" });
  learnCatalogCached().then((cat) => {
    const covering = (cat?.modules || []).filter((m) => m.status === "live" && (m.rules || []).includes(ruleId));
    const m = covering.find((x) => x.kind === "module") || covering[0];
    if (m) fill(slot, h("a", { href: "#", class: "learn-more", title: `${m.id} on Learn`,
      onclick: (e) => { e.preventDefault(); learnOpen(m.id); } }, `Lesson: ${m.title}`));
    else slot.remove();
  });
  return slot;
}

// ---------- one module or lab ----------

// A lesson's Markdown: fenced code as code blocks, the rest through the notebook's renderer, which
// escapes everything (the content checker also refuses raw HTML in lessons).
function learnLesson(src) {
  const wrap = h("div", { class: "learn-lesson" });
  src.split(/^```[^\n]*\n([\s\S]*?)^```\s*$/m).forEach((part, i) => {
    if (i % 2) wrap.append(h("pre", { class: "learn-code" }, part.replace(/\n$/, "")));
    else if (part.trim()) wrap.append(markdown(part.replace(/^(#{1,2})\s/gm, "#$1 ")));  // the page's title is the h1
  });
  return wrap;
}

async function renderLearnModule(id) {
  setView(h("div", { class: "page learn" }, h("p", { class: "muted-text" }, "Opening the lesson...")));
  const m = await rpc("learn_module", { module_id: id }).catch((err) => { toast(friendlyError(err).text); return null; });
  if (!m) { learnPage.open = null; return render(); }
  if (state.view !== "learn" || learnPage.open !== id) return;
  const leave = () => { learnPage.open = null; render(); };
  const ex = m.exercises[Math.min(learnPage.exercise, m.exercises.length - 1)];
  const head = h("div", { class: "page-head" }, h("div", {},
    h("a", { href: "#", class: "small", onclick: (e) => { e.preventDefault(); leave(); } }, "Learn"),
    h("h1", {}, `${m.id} · ${m.title} `, previewTag(), m.status === "draft" ? learnStatus(m) : null),
    h("p", { class: "sub" }, learnMeta(m), m.datasets.length ? ` · lab data generated on this PC (size ${m.size})` : "")),
    h("div", { class: "head-actions" },
      m.lab.started ? btn("Reset lab", "restart", () => learnReset(m), "tool outline") : null,
      btn("Leave", null, leave, "tool outline")));
  const work = h("div", { class: "learn-work" });
  setView(h("div", { class: "page learn learn-module" }, head,
    h("div", { class: "learn-split" },
      h("div", { class: "learn-main" }, learnLesson(m.lesson), work),
      learnAside(m, ex))));
  fill(work, ...learnExercise(m, ex));
}

function learnExercise(m, ex) {
  if (!ex) return [];
  const picker = m.exercises.length > 1 ? h("div", { class: "subtabs", role: "tablist", "aria-label": "Exercises" },
    m.exercises.map((e, i) => h("button", { class: `tab${e === ex ? " active" : ""}`, role: "tab", "aria-selected": e === ex ? "true" : "false",
      onclick: () => { learnPage.exercise = i; render(); } }, `Exercise ${i + 1}`))) : null;
  const top = [h("h2", {}, "Exercise"), picker, h("p", {}, ex.prompt)];
  if (!m.lab.started) {
    return [...top, h("div", { class: "row" },
      btn("Start the lab", "play", () => learnStart(m), "tool primary"),
      h("span", { class: "muted-text small" }, "Makes this lab's data on this PC. Nothing is downloaded."))];
  }
  const editor = h("textarea", { class: "learn-editor", spellcheck: "false", "aria-label": `Your SQL for ${m.id}`, rows: 12 });
  editor.value = m.lab.answers?.[ex.id] ?? "";
  let saveTimer = null;
  const save = () => rpc("learn_lab_save", { module_id: m.id, exercise_id: ex.id, text: editor.value }).catch(() => {});
  const out = h("div", { class: "learn-result" }, learnResult(learnPage.results[ex.id]));
  editor.addEventListener("input", () => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 1500);
    if (learnPage.results[ex.id]) { delete learnPage.results[ex.id]; fill(out); }  // a result is for the SQL that ran
  });
  const runIt = async () => {
    if (learnPage.busy) return;
    learnPage.busy = true;
    fill(out, h("p", { class: "muted-text" }, "Running on this PC..."));
    clearTimeout(saveTimer);
    save();
    try { learnPage.results[ex.id] = await rpc("learn_lab_run", { module_id: m.id, sql: editor.value }); }
    catch (err) { learnPage.results[ex.id] = { error: friendlyError(err).text }; }
    finally { learnPage.busy = false; }
    if (out.isConnected) fill(out, learnResult(learnPage.results[ex.id]));
  };
  const checkIt = async () => {
    if (learnPage.busy) return;
    learnPage.busy = true;
    clearTimeout(saveTimer);
    save();
    learnPage.checks[ex.id] = { running: true };
    learnChecksRefresh(ex);
    try { learnPage.checks[ex.id] = await rpc("learn_lab_check", { module_id: m.id, exercise_id: ex.id, answer: editor.value }); }
    catch (err) { learnPage.checks[ex.id] = { error: friendlyError(err).text }; }
    finally { learnPage.busy = false; }
    learnChecksRefresh(ex);
    profileEarned(learnPage.checks[ex.id].earned);  // a pass in a published lesson, recorded in Profile
  };
  editor.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); runIt(); } });
  return [...top, editor,
    h("div", { class: "row" }, btn("Run", "play", runIt, "tool primary"), btn("Check my answer", "ok", checkIt, "tool outline"),
      h("span", { class: "muted-text small" }, "Ctrl+Enter runs. Changes are undone after each run, so the lab's data stays as it was.")),
    out];
}

function learnResult(r) {
  if (!r) return null;
  if (r.error) return h("p", { class: "learn-error", role: "alert" }, r.error);
  if (!r.columns.length) return h("p", { class: "muted-text" }, `Done in ${r.ms} ms. The statement returns no rows.`);
  return h("div", {},
    h("div", { class: "learn-table-wrap" }, h("table", { class: "list compact" },
      h("thead", {}, h("tr", {}, r.columns.map((c) => h("th", {}, c)))),
      h("tbody", {}, r.rows.map((row) => h("tr", {}, row.map((v) => h("td", {}, v === null ? "NULL" : String(v)))))))),
    h("p", { class: "muted-text small" }, `${r.rows.length}${r.more ? "+" : ""} rows in ${r.ms} ms on this PC${r.more ? `; the first ${r.rows.length} are shown` : ""}.`));
}

function learnAside(m, ex) {
  const aside = h("aside", { class: "card pad learn-aside", "aria-label": "Checks and hints" });
  aside.dataset.exercise = ex?.id || "";
  learnAsideFill(aside, m, ex);
  return aside;
}

function learnChecksRefresh(ex) {
  const aside = document.querySelector(".learn-aside");
  if (aside && aside.dataset.exercise === ex.id && aside._module) learnAsideFill(aside, aside._module, ex);
}

function learnAsideFill(aside, m, ex) {
  aside._module = m;
  if (!ex) return fill(aside, h("p", { class: "muted-text" }, "This lesson has no exercises."));
  const c = learnPage.checks[ex.id];
  const list = c?.running ? h("p", { class: "muted-text", role: "status" }, "Checking on this PC...")
    : c?.error ? h("p", { class: "learn-error", role: "alert" }, c.error)
    : c ? h("div", { role: "status" }, h("ul", { class: "learn-checks" }, c.results.map((r) => h("li", { class: r.passed ? "ok" : "no" },
      h("span", { class: "learn-mark", "aria-hidden": "true" }, r.passed ? "✓" : "✗"),
      h("div", {}, h("b", {}, `${LEARN_CHECKS[r.kind] || r.kind}${r.passed ? "" : " (not yet)"}`), h("div", { class: "muted-text small" }, r.detail))))))
    : h("ul", { class: "learn-checks" }, ex.checks.map((k) => h("li", {}, h("span", { class: "learn-mark", "aria-hidden": "true" }, "○"),
      h("b", {}, LEARN_CHECKS[k] || k))));
  const hint = learnPage.hints[ex.id];
  const hints = ex.hints ? h("div", { class: "learn-hint" },
    hint ? [h("b", {}, `Hint ${hint.level} of ${hint.of}`), h("p", {}, hint.text)] : null,
    !hint || hint.level < hint.of ? btn(hint ? "Show the next hint" : "Show a hint", null, async () => {
      try { learnPage.hints[ex.id] = await rpc("learn_lab_hint", { module_id: m.id, exercise_id: ex.id, level: (hint?.level || 0) + 1 }); }
      catch (err) { toast(friendlyError(err).text); return; }
      learnAsideFill(aside, m, ex);
    }, "tool link") : null) : null;
  fill(aside, h("h2", {}, "Checks"),
    c && !c.running && !c.error ? h("p", { class: c.passed ? "learn-pass" : "muted-text" }, c.passed ? "Passed. Every check holds." : "Not yet. See what each check found.") : null,
    list, hints,
    h("p", { class: "muted-text small" }, "Checks run on this PC. Speed is measured here only; a Fabric Warehouse plans and runs queries differently."),
    (m.docs || []).filter((d) => !d.startsWith("https://")).length ? h("p", { class: "small" }, "Read more: ",
      ...(m.docs || []).filter((d) => !d.startsWith("https://")).map((d) => docLink(d, d.replace(/\.md(#.*)?$/, "").replace(/-/g, " ")))) : null);
}

async function learnStart(m) {
  fill(document.querySelector(".learn-work") || h("div"), h("p", { class: "muted-text", role: "status" }, "Making the lab's data on this PC..."));
  await attempt(() => rpc("learn_lab_start", { module_id: m.id }), "Could not start the lab");
  render();
}

async function learnReset(m) {
  const yes = await confirmDialog("Reset this lab?", `This deletes ${m.id}'s lab on this PC, your answers included, and makes it again from the start.`,
    "Reset lab", { danger: true });
  if (!yes) return;
  Object.assign(learnPage, { results: {}, checks: {}, hints: {} });
  await attempt(() => rpc("learn_lab_reset", { module_id: m.id }), "Could not reset the lab");
  render();
}

registerFeature({
  id: "learn",
  preview: true,
  rail: { label: "Learn", icon: "beaker", order: 80, bottom: true },
  views: { learn: renderLearn },
  commands: [
    { label: "Open Learn", icon: "beaker", keywords: "learn lessons labs training practice tutorial", run: () => { learnPage.open = null; go("learn"); } },
    { label: "Practice labs", icon: "beaker", keywords: "learn lab exercise practice", run: () => { learnPage.open = null; learnPage.tab = "labs"; go("learn"); } },
  ],
});
