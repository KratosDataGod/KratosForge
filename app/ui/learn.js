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
  const suggest = learnMode() === "guided" ? after.find((m) => !m.started) || inOrder.find((m) => !m.started && m !== next) : null;
  return [suggest ? h("div", { class: "learn-home-top" }, cont, h("section", { class: "card pad learn-suggest" },
    h("div", { class: "learn-eyebrow" }, "Suggested next ", engineInfo("Shown in Guided: the next lesson you haven't started.")),
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

// Settings > Appearance > Learning guidance (app_settings learn_mode): Expert, Professional (the
// default) or Guided. A preference only: checks and policies are the same at every level. With preview
// features hidden it's always Expert, which adds nothing but Dismiss, and that is Preview too.
// A subject ("sql" or "notebooks", GD-08) can have its own level; Same follows Learning guidance.
const learnMode = (subject = null) => {
  if (!previewShown()) return "expert";
  const own = subject && LAYOUT_CHOICES[`learn_mode_${subject}`] ? layoutChoice(`learn_mode_${subject}`) : "same";
  return own !== "same" ? own : layoutChoice("learn_mode");
};

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
// Save or Skip both store a choice, so it's never asked again; Skip keeps Professional, the default.
function learnAsk() {
  const key = "learn_mode";
  if (!previewShown() || state.settings?.[key]?.source !== "default") return null;
  // The card goes when the page renders again, so keyboard focus moves on to the guidance picker.
  const choose = async (value) => {
    if (!(await saveSettings({ [key]: value }))) return;
    render();  // Learn's head, with the picker, is built before the lessons are read
    document.querySelector(".learn-guidance select")?.focus();
  };
  const say = { expert: "Findings, their About link and Dismiss. No explanations unless you open About.",
    professional: "A line on why each finding matters, with Explain and an example when you want them.",
    guided: "Explanations open beside each finding, plus the lesson that covers it and a suggested next lesson on Learn." };
  const options = LAYOUT_CHOICES[key][2].map(([value, label]) => h("label", { class: "learn-ask-option" },
    h("input", { type: "radio", name: "learn-ask", value, checked: value === "professional" ? true : null }),
    h("span", {}, h("b", {}, label), value === "professional" ? h("span", { class: "muted-text small" }, " · the default") : null,
      h("span", { class: "muted-text small learn-ask-say" }, say[value]))));
  const card = h("section", { class: "card pad learn-ask", "aria-labelledby": "learn-ask-title" },
    h("h2", { id: "learn-ask-title" }, "Would you like KratosForge to help you improve your development skills?"),
    h("p", { class: "muted-text" }, "This only changes how much guidance you see beside your work. Checks run the same either way, and you can change it at any time with Learning guidance, above, or in Settings > Appearance > Layout."),
    h("fieldset", { class: "learn-ask-modes" }, h("legend", {}, "How much guidance would you like?"), options),
    h("div", { class: "row" },
      btn("Save", null, () => choose(card.querySelector("input[name=learn-ask]:checked")?.value || "professional"), "tool primary"),
      btn("Skip", null, () => choose("professional"), "tool outline")),
    h("p", { class: "muted-text small" }, "Your organization can set this for everyone. Learning progress stays on this PC."));
  return card;
}

// The lessons, read once per session, for the Lesson link beside findings.
let learnCatalogLoad = null;
function learnCatalogCached() {
  return (learnCatalogLoad ||= rpc("learn_catalog").catch(() => { learnCatalogLoad = null; return null; }));
}

// Guided: "Lesson: LM-01" beside a best-practice finding whose rule a lesson covers. Only live lessons (drafts stay on Learn), and a lesson before a lab: the
// lesson explains the rule.
function learnFindingLink(ruleId, subject = null) {
  if (learnMode(subject) !== "guided") return null;
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

// ---------- beside a finding (LX-09, from the approved mockup) ----------

// Explain and Show example read each rule's section of the best-practices guide, shipped with the
// app (learn/guides.py). Dismiss hides a finding in this person's own panels on this PC
// (learn/dismissals.py); the check still runs. Expert shows only Dismiss. Professional adds the
// guide's first sentence, Explain and Show example; Guided opens Explain to start with and adds the
// Lesson link. A rule a governance policy for the project turns on can't be dismissed. All of it is
// Preview.
let learnGuidesLoad = null;
function learnGuidesCached() {
  return (learnGuidesLoad ||= rpc("learn_rule_guides").catch(() => { learnGuidesLoad = null; return {}; }));
}
let learnGuides = {};
let learnDismissed = null;  // the dismissals, once read; null until then
let learnDismissedLoad = null;
function learnDismissalsCached() {
  return (learnDismissedLoad ||= Promise.all([rpc("learn_dismissals"), learnGuidesCached()])
    .then(([rows, g]) => { learnGuides = g || {}; learnDismissed = rows; return rows; })
    .catch(() => { learnDismissedLoad = null; learnDismissed = []; return []; }));
}
const learnDetail = new Map();   // which finding has Explain or Show example open: key -> "explain" | "example"
const learnShowHidden = new Set();  // the panels (by where) showing their dismissed list

// SHA-256 of the trimmed line, so a line dismissal keeps no code (a line can hold a secret: NB001).
async function learnLineHash(text) {
  const bytes = new TextEncoder().encode(String(text || "").trim());
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// Gives each finding its line_hash (lineOf: the finding's line of code), and reads the dismissals.
async function learnPrepareFindings(rows, lineOf) {
  await learnDismissalsCached();
  await Promise.all((rows || []).map(async (f) => { f.line_hash = await learnLineHash(lineOf(f)); }));
  return rows;
}

function learnDismissalFor(f, where) {
  return (learnDismissed || []).find((d) => d.rule_id === f.rule_id && (d.scope === "never"
    || (d.where === where && (d.scope === "file" || d.line === f.line_hash))));
}

// {shown, hidden}: hidden are [finding, its dismissal]. With preview hidden, or a rule a policy
// names (required), nothing is hidden.
function learnSplitFindings(rows, where, required) {
  const shown = [];
  const hidden = [];
  (rows || []).forEach((f) => {
    const d = previewShown() && !required(f.rule_id) ? learnDismissalFor(f, where) : null;
    if (d) hidden.push([f, d]); else shown.push(f);
  });
  return { shown, hidden };
}

const LEARN_SCOPE_SAID = { line: "Not on this line", file: "Not in this file", never: "Never shown" };
const LEARN_SCOPE_TOAST = { line: "on this line", file: "in this file", never: "everywhere" };

// Every press redraws the whole panel, so keyboard focus goes back to the control that was pressed
// (data-learn-focus), or, when that control went away, to the first of `fallbacks` still there.
function learnRedraw(ctx, ...keys) {
  ctx.redraw();
  const all = [...document.querySelectorAll("[data-learn-focus]")];
  for (const key of keys) {
    const el = all.find((e) => e.dataset.learnFocus === key);
    if (el) { el.focus(); return; }
  }
}

const learnFocusKey = (ctx, f, what) => `${ctx.where}|${f.rule_id}|${f.line_hash || f.line}|${what}`;

async function learnDismiss(f, scope, ctx) {
  const rows = await attempt(() => rpc("learn_dismiss", { rule_id: f.rule_id, scope, where: ctx.where, line: f.line_hash || "" }), "Not dismissed");
  if (!rows) return;
  learnDismissed = rows;
  toast(`${f.rule_id} hidden ${LEARN_SCOPE_TOAST[scope]}. Show it again from Dismissed below.`);
  learnRedraw(ctx, `${ctx.where}|dismissed`);
}

async function learnUndismiss(id, ctx, back = []) {
  const rows = await attempt(() => rpc("learn_undismiss", id ? { dismissal_id: id } : {}), "Not shown again");
  if (!rows) return;
  learnDismissed = rows;
  learnRedraw(ctx, ...back, `${ctx.where}|dismissed`);
}

// What each importance means, as the docs' rule pages say it (GD-03); the tag's tooltip.
const LEARN_IMPORTANCE = {
  Critical: "Critical: it can leak a secret, lose data or put wrong data in place.",
  Recommended: "Recommended: sound practice that most code should follow.",
  Educational: "Educational: a habit worth knowing, with little risk when it's skipped.",
  Advanced: "Advanced: a tradeoff that needs judgment, where the exceptions matter.",
};

// The buttons under one finding, and what Explain or Show example opened. ctx: {where, required, redraw}.
function learnFindingActions(f, ctx) {
  if (!previewShown()) return null;
  const mode = learnMode(ctx.subject);
  const guided = mode !== "expert";
  const guide = learnGuides[f.rule_id];
  const key = `${ctx.where}|${f.rule_id}|${f.line_hash || f.line}`;
  // Guided starts with Explain open; closing it is remembered like any other press (null).
  const open = learnDetail.has(key) ? learnDetail.get(key) : (mode === "guided" && guide ? "explain" : null);
  const toggle = (what) => {
    learnDetail.set(key, open === what ? null : what);
    learnRedraw(ctx, learnFocusKey(ctx, f, what));
  };
  const pressed = (what) => ({ "aria-expanded": String(open === what), "data-learn-focus": learnFocusKey(ctx, f, what) });
  const row = h("div", { class: "row learn-actions" });
  // Try the fix (GD-02), at every level: only a finding the engine found a safe rewrite for has one.
  if (f.fix && ctx.fix) row.append(btn("Try the fix", null, () => toggle("fix"), `tool small outline${open === "fix" ? " toggled" : ""}`, pressed("fix")));
  // Professional: the guide's first sentence, so why it matters is there without a press.
  const why = mode === "professional" && guide?.why ? h("p", { class: "learn-why muted-text small" }, learnFirstSentence(guide.why)) : null;
  if (guided && guide) {
    row.append(btn("Explain", null, () => toggle("explain"), `tool small outline${open === "explain" ? " toggled" : ""}`, pressed("explain")));
    if (guide.after) row.append(btn("Show example", null, () => toggle("example"), `tool small outline${open === "example" ? " toggled" : ""}`, pressed("example")));
  }
  const lesson = guided ? learnFindingLink(f.rule_id, ctx.subject) : null;
  if (lesson) row.append(lesson);
  if (ctx.required(f.rule_id)) {
    row.append(h("span", { class: "muted-text small" }, "Your organization requires this rule."));
  } else {
    const menu = h("div", { class: "learn-dismiss-menu", hidden: true },
      [["line", "Not on this line", `Hides ${f.rule_id} on this line, and any identical line here, until it changes`],
        ["file", "Not in this file", "This notebook or query only"],
        ["never", `Never show ${f.rule_id}`, "Everywhere, for you on this PC"]].map(([scope, label, say]) =>
        h("button", { class: "learn-dismiss-item", onclick: () => learnDismiss(f, scope, ctx) },
          h("span", {}, label), h("span", { class: "muted-text small" }, say))));
    const opener = btn("Dismiss ▾", null, (e) => {
      menu.hidden = !menu.hidden;
      e.currentTarget.setAttribute("aria-expanded", String(!menu.hidden));
      if (!menu.hidden) menu.querySelector("button")?.focus();
    }, "tool small outline", { "aria-expanded": "false", "aria-label": `Dismiss ${f.rule_id}: choose where`,
      "data-learn-focus": learnFocusKey(ctx, f, "dismiss") });
    const close = (refocus) => { menu.hidden = true; opener.setAttribute("aria-expanded", "false"); if (refocus) opener.focus(); };
    const wrap = h("span", { class: "learn-dismiss" }, opener, menu);
    menu.addEventListener("keydown", (e) => { if (e.key === "Escape") close(true); });
    // One menu at a time: it closes when focus or a click goes anywhere else.
    wrap.addEventListener("focusout", (e) => { if (!wrap.contains(e.relatedTarget)) close(false); });
    row.append(wrap);
  }
  if (open === "fix" && f.fix && ctx.fix) return [why, row, learnFixPreview(f, ctx, key)];
  if (!guided || !guide || !open || open === "fix") return why ? [why, row] : row;
  const detail = open === "explain"
    ? h("div", { class: "learn-detail" },
      // GD-03: how important the rule is and its area, one tag row. A label only: severity and policies decide.
      guide.importance ? h("span", { class: `badge learn-importance ${guide.importance.toLowerCase()}`,
        "aria-label": `Importance: ${[guide.importance, guide.area].filter(Boolean).join(", ")}`, title: LEARN_IMPORTANCE[guide.importance] || null },
      [guide.importance, guide.area].filter(Boolean).join(" · ")) : null,
      h("b", {}, "Why it matters"), markdown(guide.why),
      // What to do: the guide's own instruction where it has one, otherwise the principle behind the rule.
      guide.instead || guide.principle ? [h("b", {}, "What to do"), markdown(guide.instead || guide.principle)] : null,
      guide.fine ? [h("b", {}, "When it's fine"), markdown(guide.fine)] : null,
      guide.check ? [h("b", {}, "How to check"), markdown(guide.check)] : null,
      h("span", { class: "muted-text small" }, (guide.importance ? "Importance is the guide's view. The severity and your organization's policies decide what's flagged and required. " : "")
        + "From the best-practices guide that ships with KratosForge. Nothing is sent anywhere."))
    : h("div", { class: "learn-detail" }, h("b", {}, "Before"), h("pre", { class: "learn-code before" }, guide.before),
      h("b", {}, "After"), h("pre", { class: "learn-code after" }, guide.after),
      h("div", { class: "row" }, btn("Copy example", null, () => navigator.clipboard.writeText(guide.after)
        .then(() => toast("Copied the example."), () => toast("Couldn't copy it.", true)), "tool small"),
      h("span", { class: "muted-text small" }, "Copies only. Your code isn't changed for you.")));
  return [why, row, detail];
}

// ---------- Decision patterns (GD-05) ----------

// A choice with no single right answer, explained where it's made (the engine picker): when each option
// works and fails, cost and reliability, and an example that runs on this PC. From the docs that ship
// with the app (learn/patterns.py), so it works offline. Copy example copies only; nothing runs.
async function learnPatternDialog(id) {
  const all = await attempt(() => rpc("learn_patterns"), "Couldn't read the decision patterns");
  const p = all?.[id];
  if (!p) return;
  const body = h("div", { class: "scroll learn-pattern" }, markdown(p.body),
    p.example ? [h("b", {}, "Try it on this PC"), h("pre", { class: "learn-code" }, p.example),
      h("div", { class: "row" }, btn("Copy example", null, () => navigator.clipboard.writeText(p.example)
        .then(() => toast("Copied the example."), () => toast("Couldn't copy it.", true)), "tool small"),
      h("span", { class: "muted-text small" }, "Paste it into a Python cell on this PC. It reads nothing from Fabric."))] : null);
  showDialog(p.title, body, [docLink(`decision-patterns.md#${id.toLowerCase()}`, "Open in the guide"),
    btn("Close", null, closeDialog, "tool primary")]);
}

// ---------- Try the fix (GD-02, from the approved mockup) ----------

// The engine offers a rewrite only where it keeps the result (learn/fixes.py), for the rules below.
// Nothing changes until Apply, which is one edit in the editor, undone with Ctrl+Z. Compare runs the
// statement as written and as fixed, read-only, where the page runs SQL, and compares the rows on this
// PC (learn_fix_compare): the page's ctx.fix says whether it can (a warehouse or SQL analytics endpoint).
const LEARN_FIX_RULES = ["SQ002", "SQ005"];
const learnFixCompare = new Map();  // finding key -> {state: "running"|"done"|"failed", result, error}

// Gives each finding the engine found a safe fix for its .fix. provider: the editor's completion
// provider (tables(), cachedColumns(t), columns(t)), so the engine sees the catalog the page has.
async function learnPrepareFixes(rows, text, target, provider) {
  if (!previewShown() || !provider || !(rows || []).some((f) => LEARN_FIX_RULES.includes(f.rule_id))) return rows;
  const catalog = () => {
    const tables = provider.tables() || [];
    const columns = {};
    tables.forEach((t) => { const c = provider.cachedColumns(t); if (c?.length) columns[`${t.schema}.${t.name}`] = c; });
    return { tables, columns };
  };
  const ask = () => rpc("learn_fixes", { query: text, target, catalog: catalog(),
    findings: rows.map((f) => ({ rule_id: f.rule_id, line: f.line, col: f.col })) });
  try {
    let out = await ask();
    if (out.needs?.length) {  // column types the page hasn't read yet: read them as autocomplete does, then ask again
      await Promise.all(out.needs.map((t) => provider.columns(t).catch(() => [])));
      out = await ask();
    }
    (out.fixes || []).forEach((fx) => { if (rows[fx.index]) rows[fx.index].fix = fx; });
  } catch { /* no fixes: the findings show as they are */ }
  return rows;
}

function learnFixPreview(f, ctx, key) {
  const fx = f.fix;
  const cmp = learnFixCompare.get(key);
  const focusKey = (what) => learnFocusKey(ctx, f, what);
  const compare = async () => {
    learnFixCompare.set(key, { state: "running" });
    learnRedraw(ctx, focusKey("compare"));
    try {
      const result = await ctx.fix.compare(f);
      if (result) learnFixCompare.set(key, { state: "done", result });
      else learnFixCompare.delete(key);  // the person said no to running it, or pressed Stop
    } catch (err) {
      learnFixCompare.set(key, { state: "failed", error: friendlyError(err).text });
    }
    learnRedraw(ctx, focusKey("apply"));
  };
  const close = () => {
    if (cmp?.state === "running") ctx.fix.stop();
    learnDetail.set(key, null); learnFixCompare.delete(key); learnRedraw(ctx, focusKey("fix"));
  };
  const running = cmp?.state === "running";
  const apply = () => {
    if (!ctx.fix.apply(f)) return;
    learnDetail.delete(key); learnFixCompare.delete(key);
    learnRedraw(ctx);  // Ctrl+Z can bring the same finding back: its buttons start closed, not from this preview
    // GD-06: a fix applied after Compare found the same rows goes in Profile; the engine compares again first.
    const runs = cmp?.state === "done" && cmp.result.result === "match" ? cmp.result.runs : null;
    if (runs) {
      rpc("learn_fix_checked", { before: runs.before, after: runs.after, rule_id: f.rule_id }).then((out) => {
        if (out.recorded) toast(`Applied the fix for ${f.rule_id}. Ctrl+Z undoes it. Profile records it as fixed and checked.`);
      }, () => {});  // Profile is a record, never a reason for Apply to fail
    }
  };
  const canCompare = ctx.fix.canCompare(f);
  const done = cmp?.state === "done";
  return h("div", { class: "learn-detail learn-fix" },
    h("b", {}, "Preview · nothing has changed yet"),
    h("span", { class: "muted-text small" }, "Now"), h("pre", { class: "learn-code before" }, fx.before),
    h("span", { class: "muted-text small" }, "With the fix"), h("pre", { class: "learn-code after" }, fx.text),
    h("span", {}, fx.why_safe),
    cmp ? learnFixResult(cmp, ctx) : null,
    h("div", { class: "row" },
      canCompare && !done && !running ? btn("Compare before applying", null, compare, "tool small primary", { "data-learn-focus": focusKey("compare") }) : null,
      running ? btn("Stop", null, () => ctx.fix.stop(), "tool small outline", { "data-learn-focus": focusKey("compare") }) : null,
      btn("Apply", null, apply, `tool small${canCompare && !done ? " outline" : " primary"}`, { disabled: running ? true : null, "data-learn-focus": focusKey("apply") }),
      btn("Cancel", null, close, "tool small outline")),
    h("span", { class: "muted-text small" }, canCompare
      ? "Apply is one edit you can undo with Ctrl+Z. Compare runs both versions, read-only, and changes nothing."
      : `Apply is one edit you can undo with Ctrl+Z. ${ctx.fix.whyNoCompare(f)}`),
    h("div", { class: "learn-principle" }, h("b", {}, "The principle"), h("span", {}, fx.principle)));
}

// What Compare found: the same rows or not, each run's measured time, and what the comparison covered.
function learnFixResult(cmp, ctx) {
  if (cmp.state === "running") return h("p", { class: "muted-text small", role: "status" }, `Running both versions ${ctx.fix.where}...`);
  if (cmp.state === "failed") return h("p", { class: "output error", role: "status" }, `Couldn't compare: ${cmp.error}`);
  const r = cmp.result;
  const same = r.result === "match";
  const secs = (v) => (typeof v === "number" ? `${v.toLocaleString(undefined, { maximumFractionDigits: 2 })} s` : "not available");
  const rows = (n) => `${n.toLocaleString()} row${n === 1 ? "" : "s"}`;
  return h("div", { class: "learn-fix-result", role: "status" },
    h("b", {}, `Compared ${ctx.fix.where} · read-only`),
    h("div", { class: "row" }, h("span", { class: `pill-s ${same ? "pass" : "fail"}` }, same ? "Same rows" : "Different rows"),
      h("span", {}, same
        ? `${rows(r.rows.after)}, ${r.level === "complete" ? "every row compared" : "counts and a checksum of every row compared"}`
        : `${rows(r.rows.before)} now, ${rows(r.rows.after)} with the fix. Don't apply it before finding out why.`)),
    h("table", { class: "learn-fix-times" },
      h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "Now"), h("th", {}, "With the fix"))),
      h("tbody", {}, h("tr", {}, h("th", { scope: "row" }, "Time · measured"), h("td", {}, secs(r.seconds.before)), h("td", {}, secs(r.seconds.after))))),
    h("span", { class: "muted-text small" }, "One run each, the fixed one second, so data cached by the first can favor it. Times vary between runs."),
    (r.limitations || []).map((l) => h("span", { class: "muted-text small" }, l)));
}

// "Why: the engine skips... Wrapped in a function..." -> its first sentence, without Markdown marks.
// A sentence ends at . ! or ? followed by a capital letter or the end, so "@...", "e.g." and
// "dbo.sales" don't end it.
function learnFirstSentence(text) {
  const plain = String(text).replace(/`([^`]*)`/g, "$1").replace(/\*\*?([^*]+)\*\*?/g, "$1").replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");
  return (plain.match(/^.*?(?<!\b(?:e\.g|i\.e|etc|vs))(?<!\.\.)[.!?](?=\s+[A-Z]|\s*$)/) || [plain])[0];
}

// "2 dismissed · Show", and when shown, each with Show again, and Show all again.
function learnDismissedBlock(hidden, ctx) {
  if (!hidden.length) return null;
  const where = ctx.where;
  const on = learnShowHidden.has(where);
  const flip = () => { if (on) learnShowHidden.delete(where); else learnShowHidden.add(where); learnRedraw(ctx, `${where}|dismissed`); };
  return h("div", { class: "learn-dismissed" },
    btn(`${hidden.length} dismissed · ${on ? "Hide" : "Show"}`, null, flip, "link-btn", { "aria-expanded": String(on), "data-learn-focus": `${where}|dismissed` }),
    on ? h("ul", { class: "learn-dismissed-list" }, hidden.map(([f, d]) => h("li", {},
      h("span", {}, h("b", {}, `${f.rule_id} · ${f.rule}`), h("span", { class: "muted-text small" },
        ` ${LEARN_SCOPE_SAID[d.scope]}${d.scope === "line" ? ` · line ${f.line}` : d.scope === "never" ? " · everywhere" : ""}`)),
      // The finding comes back with its Dismiss button: focus goes there.
      btn("Show again", null, () => learnUndismiss(d.id, ctx, [learnFocusKey(ctx, f, "dismiss")]), "tool small")))) : null,
    on ? btn("Show all again", null, () => learnUndismiss(null, ctx, hidden.map(([f]) => learnFocusKey(ctx, f, "dismiss"))), "link-btn") : null);
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
