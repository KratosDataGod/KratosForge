// Academy > Assessments (Preview, ACD-013): the Assessment Center from the approved mockup
// (docs/academy/, mockup v2). Practice exams, domain diagnostics and objective drills built from the
// pack's original questions (academy_exam_service.py). The Academy page (academy.js) calls
// renderAssessments(container, pack) for its Assessments tab (pack defaults to dp-700); this file draws only the
// tab's content.
//
// Honest clock: time counts only while the exam is on screen. The screen sends a heartbeat every 15 seconds;
// hiding the window, leaving the tab or closing the app pauses it, and the engine never counts more than a
// minute for any gap. Results are scored once at Finish and never recalculated. Nothing here reads or
// writes OneLake. Questions are original, never the vendor's (Microsoft, Databricks), and the result is never
// shown as the vendor's score or a prediction of passing.

const ASSESS_DEFAULT_PACK = "dp-700";
const ASSESS_BEAT_MS = 15000;
const ASSESS_TYPES = { single: "Single answer", multiple: "Multiple answers", order: "Ordering" };
const ASSESS_COUNT_WORDS = ["", "one", "two", "three", "four", "five", "six", "seven", "eight"];
const assess = {
  mode: "home",          // home | exam | review | results | answers
  view: null,            // the running exam (academy_exam_* view)
  cur: 0,                // index of the question on screen
  result: null,          // a finished exam's stored result
  answers: null,         // academy_exam_review items
  filter: "attention",   // review before finishing: flagged | attention | all
  answersFilter: "wrong",
  checks: {},            // a drill's checked answers, by question id
  timeUp: false,
  pick: null,            // home: "domain" or "objectives" while choosing
  timed: true,
  container: null,
  pack: ASSESS_DEFAULT_PACK,
  timers: { tick: null, beat: null },
  announce: "",
};

function assessKeysOn(value) {
  try {
    if (value !== undefined) localStorage.setItem("assess.keys", value ? "1" : "0");
    return localStorage.getItem("assess.keys") !== "0";
  } catch { return true; }
}

function assessClock(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;  // minutes:seconds, as "100:00"
}

function assessWhen(stamp) {
  return stamp ? dateTimeText(stamp) : "";
}

function assessWeight(w) {
  if (!w) return "";
  return typeof w === "object" ? `${w.min}–${w.max}%` : `${w}%`;
}

async function assessCall(method, params) {
  try {
    return await rpc(method, params);
  } catch (err) {
    toast(friendlyError(err).text, true);
    return null;
  }
}

// ---------- the clock ----------

function assessStopClock() {
  clearInterval(assess.timers.tick);
  clearInterval(assess.timers.beat);
  assess.timers.tick = assess.timers.beat = null;
}

function assessStartClock() {
  assessStopClock();
  if (!assess.view || assess.view.status !== "in_progress") return;
  assess.timers.tick = setInterval(assessTick, 1000);
  assess.timers.beat = setInterval(assessBeat, ASSESS_BEAT_MS);
}

function assessOnScreen() {
  return Boolean(assess.container?.isConnected) && document.visibilityState !== "hidden";
}

function assessTick() {
  const v = assess.view;
  if (!v || !v.running) return;
  if (!assessOnScreen()) { assessPause(); return; }
  v.elapsed += 1;
  if (v.time_left !== null && v.time_left !== undefined) v.time_left = Math.max(0, v.time_left - 1);
  assessDrawClock();
  if (v.time_left === 0 && !assess.timeUp) assessTimeUp();
}

async function assessBeat() {
  const v = assess.view;
  if (!v || !v.running) return;
  if (!assessOnScreen()) { assessPause(); return; }
  const out = await rpc("academy_exam_heartbeat", { session_id: v.id }).catch(() => null);
  if (!out || assess.view !== v) return;
  Object.assign(v, { elapsed: out.elapsed ?? v.elapsed, time_left: out.time_left ?? v.time_left, running: out.running });
  assessDrawClock();
  if (v.time_left === 0 && !assess.timeUp) assessTimeUp();
}

async function assessPause() {
  const v = assess.view;
  assessStopClock();
  if (!v || !v.running) return;
  v.running = false;
  const out = await rpc("academy_exam_pause", { session_id: v.id }).catch(() => null);
  if (out && assess.view === v) Object.assign(v, { elapsed: out.elapsed, time_left: out.time_left, running: false });
  if (assess.container?.isConnected) assessDraw();
}

async function assessResume() {
  const v = assess.view;
  if (!v) return;
  const fresh = await assessCall("academy_exam_open", { session_id: v.id });
  if (!fresh) return;
  assess.view = fresh;
  assessStartClock();
  assessDraw();
}

function assessTimeUp() {
  assess.timeUp = true;
  assessStopClock();
  if (assess.view) assess.view.running = false;
  assess.mode = "review";
  announce("Time is up. Finish the exam to see your results.");
  assessDraw();
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden" && assess.view?.running) assessPause();
});
window.addEventListener("beforeunload", () => {
  if (assess.view?.running) rpc("academy_exam_pause", { session_id: assess.view.id }).catch(() => {});
});

function assessDrawClock() {
  const v = assess.view;
  const root = assess.container;
  if (!v || !root) return;
  const clock = root.querySelector(".assess-clock");
  const sub = root.querySelector(".assess-clock-sub");
  if (clock) clock.textContent = assessClock(v.elapsed);
  if (sub) sub.textContent = v.minutes ? `used of ${assessClock(v.minutes * 60)} · ${assessClock(v.time_left)} left` : "used · untimed";
}

// ---------- entry ----------

// Called by the Academy page's Assessments tab, and again by render() after an exam.
async function renderAssessments(container, pack) {
  assess.container = container;
  if (pack && pack !== assess.pack) {   // another certification: start from its home
    assess.pack = pack;
    assess.mode = "home"; assess.view = null; assess.result = null; assess.answers = null;
  }
  if (assess.mode !== "home" && (assess.view || assess.result)) { assessDraw(); return; }
  assess.mode = "home";
  fill(container, loading("Reading the assessments..."));
  const home = await assessCall("academy_exam_home", { pack: assess.pack });
  if (!home || assess.container !== container || !container.isConnected) return;
  assess.home = home;
  assessDraw();
}

function assessDraw() {
  const root = assess.container;
  if (!root) return;
  const body = { home: assessHome, exam: assessExam, review: assessReview, results: assessResults, answers: assessAnswers }[assess.mode] || assessHome;
  fill(root, h("div", { class: "assess" }, body()));
  if (assess.announce) announce(assess.announce);  // the page's polite status region (motion.js)
  assess.announce = "";
  if (assess.mode === "exam") {
    assessDrawClock();
    const focus = root.querySelector("[data-assess-focus]");
    if (focus && assess.focusNext) focus.focus();
  }
  assess.focusNext = false;
}

function assessNotice(text) {
  return h("div", { class: "assess-notice", role: "note" }, icon("info", 16), h("span", {}, text));
}

// ---------- home ----------

function assessHome() {
  const home = assess.home;
  if (!home) return loading("Reading the assessments...");
  const code = home.certification.code || home.certification.id.toUpperCase();
  const practice = h("section", { class: "card pad assess-card" },
    h("h2", {}, `${code} practice exam`),
    h("p", {}, `${home.practice.count} questions across all ${home.domains.length} domains, spread evenly over the objectives.`),
    h("div", { class: "assess-meta" }, "KNOWLEDGE ONLY"),
    h("fieldset", { class: "assess-timing" }, h("legend", {}, "Time"),
      h("label", {}, h("input", { type: "radio", name: "assess-time", checked: assess.timed, onchange: () => { assess.timed = true; } }), ` ${home.practice.minutes} minutes`),
      h("label", {}, h("input", { type: "radio", name: "assess-time", checked: !assess.timed, onchange: () => { assess.timed = false; } }), " Untimed")),
    btn("Start practice exam", null, () => assessStart({ kind: "practice", timed: assess.timed }), "tool primary"));
  const diagnostic = h("section", { class: "card pad assess-card" },
    h("h2", {}, "Domain diagnostic"),
    h("p", {}, `${home.diagnostic_per_objective} questions per objective in one domain, to see which objectives need work.`),
    h("div", { class: "assess-meta" }, "Untimed · pick a domain"),
    btn("Choose domain", null, () => { assess.pick = assess.pick === "domain" ? null : "domain"; assessDraw(); }, "tool outline",
      { "aria-expanded": assess.pick === "domain" ? "true" : "false" }),
    assess.pick === "domain" ? h("div", { class: "assess-pick", role: "group", "aria-label": "Domains" },
      home.domains.map((d) => btn(`${d.id} ${d.title}`, null, () => assessStart({ kind: "diagnostic", domain: d.id }), "tool outline"))) : null);
  const drill = h("section", { class: "card pad assess-card" },
    h("h2", {}, "Objective drill"),
    h("p", {}, "Every question for the objectives you pick, with the answer and why after each one."),
    h("div", { class: "assess-meta" }, "Untimed · 4 or more questions per objective"),
    btn("Pick objectives", null, () => { assess.pick = assess.pick === "objectives" ? null : "objectives"; assessDraw(); }, "tool outline",
      { "aria-expanded": assess.pick === "objectives" ? "true" : "false" }));
  return [
    assessNotice(home.notice),
    h("div", { class: "assess-cards" }, practice, diagnostic, drill),
    assess.pick === "objectives" ? assessObjectivePicker(home) : null,
    assessHistory(home),
  ];
}

function assessObjectivePicker(home) {
  const boxes = [];
  const start = btn("Start drill", null, () => {
    const ids = boxes.filter((b) => b.checked).map((b) => b.value);
    if (ids.length) assessStart({ kind: "drill", objectives: ids });
  }, "tool primary", { disabled: true });
  const sync = () => { start.disabled = !boxes.some((b) => b.checked); };
  return h("section", { class: "card pad assess-objectives", "aria-label": "Pick objectives" },
    h("h2", {}, "Pick objectives"),
    home.domains.map((d) => h("fieldset", { class: "assess-domain" }, h("legend", {}, `${d.id} ${d.title}`),
      d.objectives.map((o) => {
        const box = h("input", { type: "checkbox", value: o.id, onchange: sync, disabled: o.questions === 0 });
        boxes.push(box);
        return h("label", { class: "assess-objective" }, box, h("span", {}, `${o.id} ${o.text}`),
          h("span", { class: "muted-text small" }, `${o.questions} questions`));
      }))),
    h("div", { class: "row" }, start, btn("Cancel", null, () => { assess.pick = null; assessDraw(); }, "tool outline")));
}

function assessHistory(home) {
  const unfinished = home.unfinished.length ? h("table", { class: "assess-table" },
    h("thead", {}, h("tr", {}, h("th", {}, "Assessment"), h("th", {}, "Started"), h("th", {}, "Questions"), h("th", {}, h("span", { class: "sr-only" }, "Action")))),
    h("tbody", {}, home.unfinished.map((s) => h("tr", {},
      h("td", {}, assessKindName(s, home)), h("td", {}, assessWhen(s.created_at)), h("td", {}, String(s.total)),
      h("td", {}, btn("Resume", null, () => assessOpen(s.id), "tool outline", { "aria-label": `Resume ${assessKindName(s, home)} started ${assessWhen(s.created_at)}` }))))))
    : h("p", { class: "muted-text" }, "Nothing unfinished.");
  const finished = home.finished.length ? h("table", { class: "assess-table" },
    h("thead", {}, h("tr", {}, h("th", {}, "Assessment"), h("th", {}, "Finished"), h("th", {}, home.label), h("th", {}, h("span", { class: "sr-only" }, "Action")))),
    h("tbody", {}, home.finished.map((s) => h("tr", {},
      h("td", {}, assessKindName(s, home)), h("td", {}, assessWhen(s.finished_at)),
      h("td", {}, s.percent === null || s.percent === undefined ? "" : `${s.percent}%`),
      h("td", {}, btn("View", null, () => assessOpen(s.id), "tool outline", { "aria-label": `View ${assessKindName(s, home)} finished ${assessWhen(s.finished_at)}` }))))))
    : h("p", { class: "muted-text" }, "No finished assessments yet.");
  return [h("h3", {}, "Unfinished"), unfinished, h("h3", {}, "Finished"), finished];
}

function assessKindName(s, home) {
  const code = home.certification.code || home.certification.id.toUpperCase();
  return { practice: `${code} practice exam`, diagnostic: "Domain diagnostic", drill: "Objective drill" }[s.kind] || s.kind;
}

async function assessStart(params) {
  const v = await assessCall("academy_exam_start", { pack: assess.pack, ...params });
  if (!v) return;
  assessEnterExam(v);
}

async function assessOpen(id) {
  const out = await assessCall("academy_exam_open", { session_id: id });
  if (!out) return;
  if (out.status === "finished") { assessShowResult(out); return; }
  assessEnterExam(out);
}

function assessEnterExam(v) {
  Object.assign(assess, { view: v, mode: "exam", checks: {}, timeUp: false, pick: null, result: null, answers: null, focusNext: true });
  const firstOpen = v.items.findIndex((it) => !it.answer);
  assess.cur = firstOpen < 0 ? 0 : firstOpen;
  if (v.time_left === 0) { assessTimeUp(); return; }
  assessStartClock();
  assessDraw();
}

// ---------- a question ----------

function assessExam() {
  const v = assess.view;
  const it = v.items[assess.cur];
  return h("div", { class: "assess-exam" }, assessQuestion(v, it), assessSide(v));
}

function assessQuestion(v, it) {
  if (it.missing) {
    return h("section", { class: "assess-qpanel card" }, h("p", {}, "This question was removed from the bank. It won't count."), assessNav(v));
  }
  const stemId = `assess-stem-${it.n}`;
  const check = assess.checks[it.q];
  const locked = Boolean(it.checked || check) || !v.running;
  const panel = h("section", { class: "assess-qpanel card", "aria-labelledby": stemId, onkeydown: (e) => assessKeys(e, v, it) },
    h("div", { class: "assess-qhead" }, h("span", {}, `Question ${it.n} of ${v.total} · objective ${it.objective}`), h("span", {}, ASSESS_TYPES[it.type] || "")),
    it.case ? h("details", { class: "assess-case", open: true }, h("summary", {}, `Case study: ${it.case.title}`), h("p", {}, it.case.text)) : null,
    h("p", { class: "assess-stem", id: stemId, tabindex: "-1", "data-assess-focus": "" }, it.stem),
    it.type === "order" ? assessOrder(v, it, stemId, locked) : assessChoices(v, it, stemId, locked),
    !v.running ? h("p", { class: "muted-text" }, "Paused. Resume to answer.") : null,
    v.kind === "drill" ? assessDrillCheck(v, it, check) : null,
    assessNav(v));
  return panel;
}

function assessChoices(v, it, stemId, locked) {
  const multi = it.type === "multiple";
  const chosen = new Set(it.answer || []);
  const legendId = `${stemId}-legend`;
  const howMany = multi ? `Choose ${ASSESS_COUNT_WORDS[it.select] || it.select}` : "Choose one answer";
  return h("fieldset", { class: "assess-options", "aria-labelledby": `${stemId} ${legendId}` },
    h("legend", { id: legendId }, howMany),
    it.options.map((o, i) => h("label", { class: "assess-opt" },
      h("input", { type: multi ? "checkbox" : "radio", name: `assess-q-${it.q}`, value: o.id, checked: chosen.has(o.id), disabled: locked,
        "data-n": String(i + 1),
        onchange: (e) => {
          const next = multi ? it.options.filter((x) => (x.id === o.id ? e.target.checked : chosen.has(x.id))).map((x) => x.id) : [o.id];
          assessAnswer(v, it, next, `.assess-options input[value="${o.id}"]`);
        } }),
      h("span", { class: "assess-optkey", "aria-hidden": "true" }, String(i + 1)),
      h("span", {}, assessText(o.text)))));
}

// Option text may carry `code` in backticks.
function assessText(text) {
  return String(text).split(/(`[^`]+`)/).map((part) => (part.startsWith("`") && part.endsWith("`") && part.length > 1 ? h("code", {}, part.slice(1, -1)) : part));
}

function assessOrder(v, it, stemId, locked) {
  const steps = it.answer ? it.answer.map((id) => it.options.find((o) => o.id === id)).filter(Boolean) : it.options.slice();
  const helpId = `${stemId}-help`;
  const move = (from, to) => {
    if (to < 0 || to >= steps.length) return;
    const ids = steps.map((s) => s.id);
    const [one] = ids.splice(from, 1);
    ids.splice(to, 0, one);
    assess.announce = `Moved to position ${to + 1} of ${ids.length}.`;
    assess.focusOrder = { id: one, dir: to > from ? "down" : "up" };
    assessAnswer(v, it, ids);
  };
  const list = h("ol", { class: "assess-order", "aria-labelledby": stemId, "aria-describedby": helpId },
    steps.map((s, i) => {
      const keys = (e) => {
        if (!e.altKey || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return;
        e.preventDefault();
        e.stopPropagation();
        move(i, e.key === "ArrowUp" ? i - 1 : i + 1);
      };
      return h("li", { "data-step": s.id }, h("span", { class: "assess-step-n", "aria-hidden": "true" }, String(i + 1)), h("span", { class: "assess-step" }, assessText(s.text)),
        h("button", { class: "tool outline small", type: "button", "data-dir": "up", disabled: locked || i === 0, "aria-label": `Move '${s.text}' up`, onclick: () => move(i, i - 1), onkeydown: keys }, "↑"),
        h("button", { class: "tool outline small", type: "button", "data-dir": "down", disabled: locked || i === steps.length - 1, "aria-label": `Move '${s.text}' down`, onclick: () => move(i, i + 1), onkeydown: keys }, "↓"));
    }));
  return h("div", {},
    h("p", { class: "muted-text small", id: helpId }, "Move a step with its ↑ and ↓ buttons. ", h("kbd", {}, "Alt"), "+", h("kbd", {}, "↑"), " / ", h("kbd", {}, "Alt"), "+", h("kbd", {}, "↓"),
      " moves the step whose button has focus, and focus stays with that step."),
    list,
    !it.answer && !locked ? btn("Answer with this order", null, () => assessAnswer(v, it, steps.map((s) => s.id)), "tool outline small") : null,
    it.answer ? h("p", { class: "muted-text small" }, "Answered with the order shown.") : h("p", { class: "muted-text small" }, "Not answered yet: move a step or choose Answer with this order."));
}

function assessDrillCheck(v, it, check) {
  if (!check && !it.checked) {
    return h("div", { class: "row assess-checkrow" }, btn("Check answer", null, () => assessCheck(v, it), "tool primary", { disabled: !it.answer || !v.running }));
  }
  if (!check) return h("p", { class: "muted-text small" }, "Checked. Finish the drill to see every answer again.");
  return assessAnswerList(it, check.answer, check.key, check.also_accept, check.correct, check.explanation, check.source);
}

function assessNav(v) {
  const it = v.items[assess.cur];
  const flagged = Boolean(it.flagged);
  return h("div", { class: "assess-qfoot" },
    it.missing ? h("span") : btn(flagged ? "⚑ Flagged" : "⚑ Flag for review", null, () => assessFlag(v, it, !flagged), `tool outline${flagged ? " assess-flagged" : ""}`,
      { "aria-pressed": flagged ? "true" : "false", disabled: !v.running }),
    h("div", { class: "row" },
      btn("◂ Previous", null, () => assessGo(assess.cur - 1), "tool outline", { disabled: assess.cur === 0 }),
      assess.cur < v.total - 1 ? btn("Next ▸", null, () => assessGo(assess.cur + 1), "tool primary")
        : btn("Review and finish", null, () => { assess.mode = "review"; assessDraw(); }, "tool primary")));
}

function assessSide(v) {
  const grid = h("div", { class: "assess-grid", role: "group", "aria-label": "Go to question" },
    v.items.map((it, i) => {
      const words = [`Question ${it.n}`, it.answer ? "answered" : "not answered", it.flagged ? "flagged" : null, i === assess.cur ? "current" : null].filter(Boolean).join(", ");
      return h("button", { type: "button", class: `${it.answer ? "done" : ""}${it.flagged ? " flag" : ""}${i === assess.cur ? " cur" : ""}`.trim(),
        "aria-label": words, "aria-current": i === assess.cur ? "true" : null, onclick: () => assessGo(i) }, String(it.n));
    }));
  const keysOn = assessKeysOn();
  return h("aside", { class: "assess-side", "aria-label": "Exam progress" },
    h("div", { class: "card pad" },
      h("div", { class: "assess-clock", role: "timer", "aria-live": "off" }, assessClock(v.elapsed)),
      h("div", { class: "assess-clock-sub muted-text small" }),
      h("div", { class: "muted-text small" }, "Time counts only while this screen is open."),
      v.running ? btn("Pause", null, () => assessPause(), "tool outline")
        : btn("Resume", null, () => assessResume(), "tool primary")),
    h("div", { class: "card pad" },
      h("div", {}, h("strong", {}, "Questions"), h("span", { class: "muted-text small" }, ` · ${v.answered} answered · ${v.flagged} flagged`)),
      grid,
      h("div", { class: "assess-legend muted-text small", "aria-hidden": "true" }, h("span", {}, "■ answered"), h("span", {}, "◥ flagged"), h("span", {}, "▢ not answered")),
      btn("Review and finish", null, () => { assess.mode = "review"; assessDraw(); }, "tool outline")),
    h("div", { class: "card pad assess-keys muted-text small" },
      h("label", { class: "row" }, h("input", { type: "checkbox", checked: keysOn, onchange: (e) => { assessKeysOn(e.target.checked); assessDraw(); } }), "Keyboard shortcuts"),
      keysOn ? h("span", {}, "While focus is in the question: ", h("kbd", {}, "1"), "–", h("kbd", {}, "8"), " choose · ", h("kbd", {}, "F"), " flag · ",
        h("kbd", {}, "N"), " next · ", h("kbd", {}, "P"), " previous · ", h("kbd", {}, "R"), " review.")
        : h("span", {}, "Off: only ", h("kbd", {}, "Tab"), ", ", h("kbd", {}, "Space"), " and ", h("kbd", {}, "Enter"), ", which always work.")));
}

function assessKeys(e, v, it) {
  if (!assessKeysOn() || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.closest("textarea, input[type=text]")) return;
  const k = e.key.toLowerCase();
  if (/^[1-8]$/.test(k) && it.type !== "order" && v.running && !it.checked && !assess.checks[it.q]) {
    const input = assess.container.querySelector(`.assess-options input[data-n="${k}"]`);
    if (!input) return;
    e.preventDefault();
    input.focus();
    input.click();
  } else if (k === "f" && v.running) { e.preventDefault(); assessFlag(v, it, !it.flagged); }
  else if (k === "n" && assess.cur < v.total - 1) { e.preventDefault(); assessGo(assess.cur + 1); }
  else if (k === "p" && assess.cur > 0) { e.preventDefault(); assessGo(assess.cur - 1); }
  else if (k === "r") { e.preventDefault(); assess.mode = "review"; assessDraw(); }
}

function assessGo(i) {
  const v = assess.view;
  if (!v || i < 0 || i >= v.total) return;
  Object.assign(assess, { cur: i, mode: "exam", focusNext: true });
  assessDraw();
}

function assessKeep(fresh) {
  // The clock on screen keeps counting between calls; the engine's figure wins when it arrives.
  assess.view = fresh;
}

async function assessAnswer(v, it, choice, refocus = null) {
  const fresh = await assessCall("academy_exam_answer", { session_id: v.id, q: it.q, choice });
  if (!fresh || assess.view?.id !== v.id) { assessDraw(); return; }
  assessKeep(fresh);
  if (fresh.time_left === 0) { assessTimeUp(); return; }
  assessDraw();
  if (refocus) assess.container?.querySelector(refocus)?.focus();
  assessRefocusOrder();
}

function assessRefocusOrder() {
  const f = assess.focusOrder;
  assess.focusOrder = null;
  if (!f) return;
  const li = assess.container?.querySelector(`.assess-order li[data-step="${f.id}"]`);
  if (!li) return;
  const want = li.querySelector(`button[data-dir="${f.dir}"]`);
  (want && !want.disabled ? want : li.querySelector("button:not(:disabled)"))?.focus();
}

async function assessFlag(v, it, on) {
  const fresh = await assessCall("academy_exam_flag", { session_id: v.id, q: it.q, on });
  if (!fresh || assess.view?.id !== v.id) return;
  assessKeep(fresh);
  assess.announce = on ? `Question ${it.n} flagged for review.` : `Question ${it.n} no longer flagged.`;
  assessDraw();
  assess.container?.querySelector(".assess-qfoot button[aria-pressed]")?.focus();
}

async function assessCheck(v, it) {
  const out = await assessCall("academy_exam_check", { session_id: v.id, q: it.q });
  if (!out || assess.view?.id !== v.id) return;
  assess.checks[it.q] = out;
  assessKeep(out.view);
  assess.announce = out.correct ? "Correct." : "Not correct. The right answer and why are shown.";
  assessDraw();
  assess.container?.querySelector(".assess-why")?.setAttribute("tabindex", "-1");
  assess.container?.querySelector(".assess-why")?.focus();
}

// ---------- review before finishing ----------

function assessReview() {
  const v = assess.view;
  const rows = v.items.map((it, i) => ({ it, i }));
  const flagged = rows.filter((r) => r.it.flagged);
  const attention = rows.filter((r) => r.it.flagged || !r.it.answer);
  const shown = { flagged, attention, all: rows }[assess.filter] || attention;
  const switcher = h("div", { class: "assess-switch", role: "group", "aria-label": "Show" },
    [["flagged", `Flagged (${flagged.length})`], ["attention", `Flagged or not answered (${attention.length})`], ["all", `All ${rows.length}`]].map(([id, label]) =>
      h("button", { type: "button", "aria-pressed": assess.filter === id ? "true" : "false", onclick: () => { assess.filter = id; assessDraw(); } }, label)));
  const time = v.minutes ? `${assessClock(v.elapsed)} used of ${assessClock(v.minutes * 60)}` : `${assessClock(v.elapsed)} used`;
  return [
    h("div", { class: "assess-head" }, h("h2", {}, assess.timeUp ? "Time is up" : "Review before finishing"),
      h("div", { class: "row" },
        assess.timeUp ? null : btn("Back to questions", null, () => assessGo(assess.cur), "tool outline"),
        btn("Finish exam", null, () => assessFinish(), "tool primary"))),
    h("p", { class: "muted-text" }, `${v.answered} of ${v.total} answered · ${v.flagged} flagged · ${time}. Unanswered questions count as wrong. You can't change answers after finishing.`),
    assess.timeUp ? h("p", { class: "muted-text" }, "The time for this exam is used, so it takes no more answers.") : null,
    switcher,
    shown.length ? h("table", { class: "assess-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Objective"), h("th", {}, "Question"), h("th", {}, "Status"))),
      h("tbody", {}, shown.map(({ it, i }) => h("tr", {},
        h("td", {}, String(it.n)), h("td", {}, it.objective),
        h("td", {}, assess.timeUp ? assessShort(it.stem) : h("a", { href: "#", onclick: (e) => { e.preventDefault(); assessGo(i); } }, assessShort(it.stem))),
        h("td", {}, it.answer ? null : h("span", { class: "pill-s fail" }, "Not answered"), it.flagged ? h("span", { class: "pill-s wrn" }, "Flagged") : null,
          it.answer && !it.flagged ? h("span", { class: "pill-s neu" }, "Answered") : null)))))
      : h("p", { class: "muted-text" }, "Nothing to show here."),
  ];
}

function assessShort(text) {
  const s = String(text || "");
  return s.length > 90 ? `${s.slice(0, 88)}…` : s;
}

async function assessFinish() {
  const v = assess.view;
  const open = v.total - v.answered;
  if (open && !(await confirmDialog("Finish the exam?", `${open} question${open === 1 ? " is" : "s are"} not answered and will count as wrong. You can't change answers after finishing.`, "Finish exam"))) return;
  assessStopClock();
  const out = await assessCall("academy_exam_finish", { session_id: v.id });
  if (!out) { assessStartClock(); return; }
  assessShowResult(out);
}

function assessShowResult(out) {
  assessStopClock();
  Object.assign(assess, { view: null, result: out.result, resultId: out.id, answers: null, mode: "results", timeUp: false });
  assessDraw();
}

// ---------- results ----------

function assessResults() {
  const r = assess.result;
  const time = r.minutes ? `Time used ${assessClock(r.elapsed)} of ${assessClock(r.minutes * 60)}.` : `Time used ${assessClock(r.elapsed)}.`;
  const weak = r.by_objective.filter((o) => o.correct < o.total).sort((a, b) => a.percent - b.percent || a.objective.localeCompare(b.objective, undefined, { numeric: true })).slice(0, 5);
  return [
    h("div", { class: "assess-headline card" },
      h("div", {}, h("div", { class: "muted-text small" }, `${r.label} · ${r.title}`),
        h("div", { class: "assess-big" }, `${r.correct} of ${r.total} `, h("span", { class: "muted-text" }, `(${r.percent}%)`))),
      h("p", { class: "muted-text small assess-headline-note" }, `Not ${r.vendor ? `a ${r.vendor}` : "the vendor's"} score, not a scaled score and not a prediction of passing. It shows which objectives your answers were strong or weak on in this ${r.kind === "practice" ? "practice exam" : "assessment"}. ${time}`),
      h("div", { class: "row wrap" },
        btn("See answers and why", null, () => assessOpenAnswers(), "tool primary"),
        weak.length ? btn("Practice weak objectives", null, () => assessStart({ kind: "drill", objectives: weak.map((o) => o.objective) }), "tool outline") : null,
        btn("Back to assessments", null, () => assessHomeAgain(), "tool outline"))),
    h("h3", {}, "By domain (published weight)"),
    h("table", { class: "assess-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Domain"), h("th", {}, "Weight"), h("th", {}, "Correct"), h("th", { class: "assess-barcol" }, h("span", { class: "sr-only" }, "Share correct")))),
      h("tbody", {}, r.by_domain.map((d) => h("tr", {},
        h("td", {}, d.title ? `${d.domain} ${d.title}` : `Domain ${d.domain}`), h("td", {}, assessWeight(d.weight)),
        h("td", {}, `${d.correct} of ${d.total}`),
        h("td", { "aria-hidden": "true" }, h("div", { class: "assess-bar" }, h("i", { style: `width:${d.percent}%` }))))))),
    h("h3", {}, "Weakest objectives"),
    weak.length ? h("table", { class: "assess-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Objective"), h("th", {}, "Correct"), h("th", {}, "Next step"))),
      h("tbody", {}, weak.map((o) => h("tr", {},
        h("td", {}, o.text ? `${o.objective} ${o.text}` : o.objective), h("td", {}, `${o.correct} of ${o.total}`),
        h("td", {}, h("a", { href: "#", onclick: (e) => { e.preventDefault(); assessStart({ kind: "drill", objectives: [o.objective] }); } }, `Objective drill for ${o.objective}`))))))
      : h("p", { class: "muted-text" }, "Every objective in this assessment was answered correctly."),
    r.left_out?.length ? h("p", { class: "muted-text small" }, `${r.left_out.length} question${r.left_out.length === 1 ? " was" : "s were"} removed from the bank during this exam and left out of the result.`) : null,
    h("p", { class: "muted-text small" }, `Saved on this PC under ${r.code || r.pack.toUpperCase()} version ${r.cert_version}. It's never recalculated if questions change later.`),
  ];
}

function assessHomeAgain() {
  Object.assign(assess, { mode: "home", view: null, result: null, answers: null });
  render();
}

async function assessOpenAnswers() {
  const out = await assessCall("academy_exam_review", { session_id: assess.resultId });
  if (!out) return;
  const wrong = out.items.filter((it) => !it.missing && !it.correct);
  Object.assign(assess, { answers: out.items, mode: "answers", answersFilter: wrong.length ? "wrong" : "all", answerAt: 0 });
  assessDraw();
}

// ---------- answers and why ----------

function assessAnswers() {
  const all = assess.answers.filter((it) => !it.missing);
  const wrong = all.filter((it) => !it.correct);
  const flagged = all.filter((it) => it.flagged);
  const shown = { wrong, flagged, all }[assess.answersFilter] || all;
  const at = Math.min(assess.answerAt || 0, Math.max(0, shown.length - 1));
  const it = shown[at];
  const switcher = h("div", { class: "assess-switch", role: "group", "aria-label": "Show" },
    [["wrong", `Wrong or not answered (${wrong.length})`], ["flagged", `Flagged (${flagged.length})`], ["all", `All ${all.length}`]].map(([id, label]) =>
      h("button", { type: "button", "aria-pressed": assess.answersFilter === id ? "true" : "false", onclick: () => { assess.answersFilter = id; assess.answerAt = 0; assessDraw(); } }, label)));
  const head = h("div", { class: "assess-head" }, h("h2", {}, "Answers and why"), btn("Back to results", null, () => { assess.mode = "results"; assessDraw(); }, "tool outline"));
  if (!it) return [head, switcher, h("p", { class: "muted-text" }, "Nothing to show here.")];
  return [head, switcher,
    h("section", { class: "assess-qpanel card", "aria-labelledby": "assess-ans-stem" },
      h("div", { class: "assess-qhead" }, h("span", {}, `Question ${it.n} of ${assess.answers.length} · objective ${it.objective}`),
        h("span", {}, it.correct ? h("span", { class: "pill-s pass" }, "Correct") : h("span", { class: "pill-s fail" }, it.answer ? "Wrong" : "Not answered"))),
      h("p", { class: "assess-stem", id: "assess-ans-stem" }, it.stem),
      it.changed_since ? h("p", { class: "muted-text small" }, "This question was edited after your exam. Your result stays as it was scored.") : null,
      assessAnswerList(it, it.answer, it.key, it.also_accept, it.correct, it.explanation, it.source),
      h("div", { class: "assess-qfoot" }, h("span", { class: "muted-text small" }, `${at + 1} of ${shown.length} shown`),
        h("div", { class: "row" },
          btn("◂ Previous", null, () => { assess.answerAt = at - 1; assessDraw(); }, "tool outline", { disabled: at === 0 }),
          btn("Next ▸", null, () => { assess.answerAt = at + 1; assessDraw(); }, "tool primary", { disabled: at >= shown.length - 1 }))))];
}

// The options with the learner's answer and the right one marked in words, then why and the source page.
function assessAnswerList(it, mine, key, also, correct, explanation, source) {
  const text = Object.fromEntries(it.options.map((o) => [o.id, o.text]));
  let list;
  if (it.type === "order") {
    list = h("div", { class: "assess-orders" },
      h("div", {}, h("div", { class: "assess-tag" }, correct ? "Your order ✓" : "Your order ✗"),
        mine?.length ? h("ol", {}, mine.map((id) => h("li", {}, assessText(text[id] || "")))) : h("p", { class: "muted-text" }, "Not answered.")),
      correct ? null : h("div", {}, h("div", { class: "assess-tag ok" }, "Correct order ✓"), h("ol", {}, key.map((id) => h("li", {}, assessText(text[id] || ""))))),
      also?.length ? h("p", { class: "muted-text small" }, `${also.length} other order${also.length === 1 ? " is" : "s are"} also accepted.`) : null);
  } else {
    const picked = new Set(mine || []);
    const right = new Set(key);
    list = h("ul", { class: "assess-anslist" }, it.options.map((o) => {
      const isKey = right.has(o.id), isMine = picked.has(o.id);
      const tag = isKey && isMine ? "Your answer, correct ✓" : isKey ? "Correct answer ✓" : isMine ? "Your answer ✗" : "";
      return h("li", { class: isKey ? "key" : isMine ? "mine" : "" }, h("span", { class: "assess-tag" }, tag), h("span", {}, assessText(o.text)));
    }));
  }
  return h("div", {}, list,
    h("div", { class: "assess-why" }, h("strong", {}, "Why: "), explanation, h("br"), "Based on: ",
      h("a", { href: "#", onclick: (e) => { e.preventDefault(); openLink(source); } }, assessSourceName(source))));
}

function assessSourceName(url) {
  try {
    const u = new URL(url);
    const host = { "learn.microsoft.com": "Microsoft Learn", "docs.databricks.com": "Databricks documentation" }[u.hostname] || u.hostname;
    const last = u.pathname.split("/").filter(Boolean).pop() || "";
    return `${last.replace(/[-_]/g, " ")} (${host})`;
  } catch { return url; }
}
