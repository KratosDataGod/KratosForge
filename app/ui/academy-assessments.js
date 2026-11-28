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
const ASSESS_TYPES = { single: "Single answer", multiple: "Multiple answers", order: "Ordering", matching: "Matching",
  hotarea: "Hot area", yesno: "Yes or no", code: "Code completion" };
// Multi-part questions (one answer per part, partial credit): what a part is called in the review's score line.
const ASSESS_PARTS = { matching: ["match", "matches"], hotarea: ["blank", "blanks"], yesno: ["statement", "statements"], code: ["gap", "gaps"] };
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

// Answered: any choice, or for a multi-part question every part (the engine says which in "answered").
function assessDone(it) {
  return it.answered ?? Boolean(it.answer);
}

// A score in points: whole, or to 2 places (partial credit).
function assessPoints(n) {
  return String(Math.round(Number(n || 0) * 100) / 100);
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
  if (pack && pack !== assess.pack) {   // another certification: stop any running clock, start from its home
    assessStopClock();
    if (assess.view?.running) rpc("academy_exam_pause", { session_id: assess.view.id }).catch(() => {});
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
    h("div", { class: "assess-meta" }, "Untimed · one domain or all of them"),
    btn("Choose a diagnostic", null, () => { Object.assign(assess, { pick: "diagnostic", fromHome: false }); assessDraw(); }, "tool outline"));
  const drill = h("section", { class: "card pad assess-card" },
    h("h2", {}, "Objective drill"),
    h("p", {}, "Every question for the objectives you pick, with the answer and why after each one."),
    h("div", { class: "assess-meta" }, "Untimed · 4 or more questions per objective"),
    btn("Pick objectives", null, () => { assess.pick = assess.pick === "objectives" ? null : "objectives"; assessDraw(); }, "tool outline",
      { "aria-expanded": assess.pick === "objectives" ? "true" : "false" }));
  if (assess.pick === "diagnostic") return [assessNotice(home.notice), assessDiagnostic(home)];
  return [
    assessNotice(home.notice),
    h("div", { class: "assess-cards" }, practice, diagnostic, drill),
    assess.pick === "objectives" ? assessObjectivePicker(home) : null,
    assessHistory(home),
  ];
}

// Board 6 of the Academy mockup: one diagnostic screen, reached from Academy Home's "Take a diagnostic" or
// from the Domain diagnostic card. Each domain, or all of them, with its size, about how long it takes (at
// the practice exam's pace) and the last time it was finished on this PC.
function assessDiagnostic(home) {
  const code = home.certification.code || home.certification.id.toUpperCase();
  const choices = [...home.domains.map((d) => ({ id: d.id, title: `${d.id} ${d.title}`, ...d.diagnostic })),
    { id: "all", title: `All of ${code}`, ...home.diagnostic_all }].filter((c) => c.questions);
  if (!choices.some((c) => c.id === assess.diagPick)) assess.diagPick = choices[0]?.id;
  const meta = (c) => [`${c.questions} questions`, c.minutes ? `about ${c.minutes} min` : null,
    c.last ? `last finished ${assessWhen(c.last.finished_at)}, ${c.last.correct} of ${c.last.total} right` : "not taken yet"].filter(Boolean).join(" · ");
  const back = () => {
    assess.pick = null;
    if (assess.fromHome && typeof academyPage !== "undefined") { assess.fromHome = false; academyPage.tab = "home"; render(); } else assessDraw();
  };
  return h("section", { class: "card pad assess-diag", "aria-labelledby": "assess-diag-h" },
    h("h2", { id: "assess-diag-h" }, "Diagnostic"),
    h("p", {}, `${ASSESS_COUNT_WORDS[home.diagnostic_per_objective] || home.diagnostic_per_objective} questions per objective, to find what you already know and which objectives need work. It isn't timed, so you can stop and come back, and it isn't an exam.`.replace(/^./, (c) => c.toUpperCase())),
    h("fieldset", { class: "assess-diag-choices" }, h("legend", {}, `Which part of ${code}?`),
      choices.map((c) => h("label", { class: "assess-diag-choice" },
        h("input", { type: "radio", name: "assess-diag", value: c.id, checked: assess.diagPick === c.id, onchange: () => { assess.diagPick = c.id; } }),
        h("span", {}, h("b", {}, c.title), h("span", { class: "assess-meta" }, meta(c)))))),
    h("p", { class: "assess-diag-note" }, "Right answers count toward Knowledge in Readiness, and nothing else. Exercises are still needed to prove an objective. ",
      h("span", { class: "academy-pill knowledge_only" }, "KNOWLEDGE ONLY")),
    h("div", { class: "row" }, btn("Start the diagnostic", null, () => choices.length && assessStart({ kind: "diagnostic", domain: assess.diagPick }), "tool primary"),
      btn(assess.fromHome ? "Back to Home" : "Back", null, back, "tool outline")));
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
  if (s.kind === "diagnostic" && s.domain === "all") return `Diagnostic: all of ${code}`;
  if (s.kind === "diagnostic" && s.domain) return `Domain diagnostic: ${s.domain}`;
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
  const firstOpen = v.items.findIndex((it) => !assessDone(it));
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
    assessBody(v, it, stemId, locked),
    !v.running ? h("p", { class: "muted-text" }, "Paused. Resume to answer.") : null,
    v.kind === "drill" ? assessDrillCheck(v, it, check) : null,
    assessNav(v));
  return panel;
}

function assessBody(v, it, stemId, locked) {
  const draw = { order: assessOrder, matching: assessMatching, hotarea: assessSnippet, code: assessSnippet, yesno: assessYesNo }[it.type];
  return (draw || assessChoices)(v, it, stemId, locked);
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
    return h("div", { class: "row assess-checkrow" }, btn("Check answer", null, () => assessCheck(v, it), "tool primary", { disabled: !assessDone(it) || !v.running }));
  }
  if (!check) return h("p", { class: "muted-text small" }, "Checked. Finish the drill to see every answer again.");
  return assessAnswerList(it, check.answer, check.key, check.also_accept, check.correct, check.explanation, check.source, check);
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
      const words = [`Question ${it.n}`, assessDone(it) ? "answered" : "not answered", it.flagged ? "flagged" : null, i === assess.cur ? "current" : null].filter(Boolean).join(", ");
      return h("button", { type: "button", class: `${assessDone(it) ? "done" : ""}${it.flagged ? " flag" : ""}${i === assess.cur ? " cur" : ""}`.trim(),
        "aria-label": words, "aria-current": i === assess.cur ? "true" : null, onclick: () => assessGo(i) }, String(it.n));
    }));
  const keysOn = assessKeysOn();
  const cur = v.items[assess.cur];
  const numbers = !cur || cur.type === "single" || cur.type === "multiple" || cur.type === "order";
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
      keysOn && numbers ? h("span", {}, "While focus is in the question: ", h("kbd", {}, "1"), "–", h("kbd", {}, "8"), " choose · ", h("kbd", {}, "F"), " flag · ",
        h("kbd", {}, "N"), " next · ", h("kbd", {}, "P"), " previous · ", h("kbd", {}, "R"), " review.")
        : keysOn ? h("span", {}, "While focus is in the question: ", h("kbd", {}, "F"), " flag · ", h("kbd", {}, "N"), " next · ", h("kbd", {}, "P"),
          " previous · ", h("kbd", {}, "R"), " review. Number keys choose only in single and multiple answer questions.")
        : h("span", {}, "Off: only ", h("kbd", {}, "Tab"), ", ", h("kbd", {}, "Space"), " and ", h("kbd", {}, "Enter"), ", which always work.")));
}

function assessKeys(e, v, it) {
  if (!assessKeysOn() || e.ctrlKey || e.metaKey || e.altKey) return;
  // A list keeps its letters for type-ahead (choosing "Notebook" with N), so shortcuts wait outside it.
  if (e.target.closest("textarea, input[type=text], select")) return;
  const k = e.key.toLowerCase();
  if (/^[1-8]$/.test(k) && (it.type === "single" || it.type === "multiple") && v.running && !it.checked && !assess.checks[it.q]) {
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
  const parts = ASSESS_PARTS[it.type];
  assess.announce = out.correct ? "Correct."
    : parts && out.parts_right ? `${out.parts_right} of ${out.part_count} ${parts[1]} right. The right answers and why are shown.`
      : "Not correct. The right answer and why are shown.";
  assessDraw();
  assess.container?.querySelector(".assess-why")?.setAttribute("tabindex", "-1");
  assess.container?.querySelector(".assess-why")?.focus();
}

// ---------- multi-part questions: matching, hot area, yes or no, code completion ----------
// One answer per part, sent to the engine as a list with "" for an empty part. Every part is a native
// control (a list or a radio group), so the keyboard and a screen reader can answer all of it; dragging
// an item onto a requirement is only a shortcut for the mouse.

function assessPartsOf(it) {
  const n = it.parts.length;
  return it.answer && it.answer.length === n ? it.answer.slice() : Array(n).fill("");
}

// Option text without the backticks that mark code (a native list shows plain text).
function assessPlain(text) {
  return String(text).replace(/`/g, "");
}

function assessSetPart(v, it, i, id, refocus) {
  const next = assessPartsOf(it);
  if (it.type === "code" && id) {        // a token sits in one gap at most: choosing it again moves it
    const j = next.indexOf(id);
    if (j >= 0 && j !== i) {
      next[j] = "";
      const tok = it.parts[i].options.find((o) => o.id === id);
      assess.announce = `${assessPlain(tok?.text || "")} moved from gap ${j + 1} to gap ${i + 1}. Gap ${j + 1} is empty.`;
    }
  }
  next[i] = id;
  assessAnswer(v, it, next, refocus);
}

function assessPartStatus(it, locked, words) {
  if (locked && assess.view?.running) return null;   // checked in a drill: the answer below says the rest
  const mine = assessPartsOf(it);
  const filled = mine.filter(Boolean).length;
  const text = filled === mine.length ? words.done : filled === 0 ? words.empty : words.some(filled, mine.length);
  return h("p", { class: "muted-text small" }, text);
}

function assessMatching(v, it, stemId, locked) {
  const helpId = `${stemId}-help`;
  const itemsId = `${stemId}-items`;
  const mine = assessPartsOf(it);
  const uses = (id) => mine.filter((m) => m === id).length;
  const pool = h("div", { class: "assess-pool", role: "group", "aria-labelledby": itemsId },
    h("h3", { id: itemsId }, "Items"),
    h("ul", {}, it.options.map((o) => h("li", {
      class: "assess-chip", draggable: locked ? null : "true", "data-item": o.id,
      ondragstart: (e) => { e.dataTransfer.setData("text/plain", o.id); e.dataTransfer.effectAllowed = "copy"; e.currentTarget.classList.add("lifted"); },
      ondragend: (e) => e.currentTarget.classList.remove("lifted"),
    }, h("span", { class: "assess-grip", "aria-hidden": "true" }, "⋮⋮"), h("span", {}, assessText(o.text)),
    uses(o.id) ? h("span", { class: "assess-uses" }, `used ${uses(o.id)}×`) : null))));
  const targets = h("div", { class: "assess-targets", role: "group", "aria-label": "Requirements" },
    h("h3", {}, "Requirements"),
    it.parts.map((p, i) => {
      const tid = `${stemId}-t${i}`;
      const focus = `.assess-targets select[data-part="${i}"]`;
      const list = h("select", { class: mine[i] ? "" : "empty", "aria-labelledby": tid, "aria-describedby": helpId, "data-part": String(i), disabled: locked,
        onchange: (e) => assessSetPart(v, it, i, e.target.value, focus) },
      h("option", { value: "" }, "Choose an item"),
      it.options.map((o) => h("option", { value: o.id, selected: mine[i] === o.id }, assessPlain(o.text))));
      const over = (e, on) => { if (!locked) { e.preventDefault(); e.currentTarget.classList.toggle("over", on); } };
      return h("div", { class: "assess-target", "data-part": String(i),
        ondragover: (e) => over(e, true), ondragleave: (e) => over(e, false),
        ondrop: (e) => {
          over(e, false);
          const id = e.dataTransfer.getData("text/plain");
          if (locked || !it.options.some((o) => o.id === id)) return;
          assess.announce = `${assessPlain(it.options.find((o) => o.id === id).text)} matched to requirement ${i + 1}.`;
          assessSetPart(v, it, i, id, null);
        } },
      h("span", { id: tid }, assessText(p.text)),
      h("span", { class: "assess-slot" }, list,
        mine[i] && !locked ? btn("Clear", null, () => assessSetPart(v, it, i, "", focus), "tool outline small", { type: "button", "aria-label": `Clear ${assessPlain(p.text)}` }) : null));
    }));
  return h("div", { class: "assess-parts" },
    h("p", { class: "muted-text small", id: helpId }, "Drag an item onto a requirement, or choose it in the requirement's list. The lists work with the keyboard and a screen reader."),
    h("div", { class: "assess-match" }, pool, targets),
    assessPartStatus(it, locked, { empty: "Not answered yet. Fill every requirement to answer.",
      some: (k, n) => `${k} of ${n} filled. Not answered until every requirement has an item.`,
      done: "Answered. You can change any match until you finish." }));
}

// Hot area and code completion: a list at each numbered blank (gap) of the snippet, written {{N}}.
function assessSnippet(v, it, stemId, locked) {
  const code = it.type === "code";
  const word = code ? "gap" : "blank";
  const mine = assessPartsOf(it);
  const n = mine.length;
  const at = new Map(mine.map((m, i) => [m, i]).filter(([m]) => m));
  const body = String(it.snippet).split(/\{\{(\d)\}\}/).map((piece, k) => {
    if (k % 2 === 0) return piece;
    const i = Number(piece) - 1;
    const p = it.parts[i];
    if (!p) return piece;
    const focus = `.assess-snippet select[data-part="${i}"]`;
    return [h("span", { class: "assess-gapn", "aria-hidden": "true" }, String(i + 1)),
      h("select", { class: mine[i] ? "" : "empty", "aria-label": code ? `Gap ${i + 1}` : `Blank ${i + 1}, ${p.label}`, "data-part": String(i), disabled: locked,
        onchange: (e) => assessSetPart(v, it, i, e.target.value, focus) },
      h("option", { value: "" }, "Choose"),
      p.options.map((o) => {
        const where = at.get(o.id);
        const elsewhere = code && where !== undefined && where !== i ? ` (in gap ${where + 1})` : "";
        return h("option", { value: o.id, selected: mine[i] === o.id }, `${assessPlain(o.text)}${elsewhere}`);
      }))];
  });
  const bankId = `${stemId}-tokens`;
  return h("div", { class: "assess-parts" },
    h("p", { class: "muted-text small" }, code ? "Choose a token for each numbered gap. Each token is used once at most, and some aren't used."
      : "Choose a value for each numbered blank. Tab moves from blank to blank."),
    h("div", { class: "assess-snippet", role: "group", "aria-label": `${it.snippet_label} with ${n} ${word}s` }, body),
    code ? h("div", { class: "assess-bank" }, h("span", { class: "muted-text small", id: bankId }, "Tokens:"),
      h("ul", { "aria-labelledby": bankId }, it.options.map((o) => h("li", { class: at.has(o.id) ? "used" : "" }, assessPlain(o.text),
        at.has(o.id) ? h("span", { class: "sr-only" }, ` (in gap ${at.get(o.id) + 1})`) : null)))) : null,
    assessPartStatus(it, locked, code
      ? { empty: `Not answered yet. Fill all ${n} gaps to answer.`, some: () => `Not answered yet. Fill all ${n} gaps to answer.`,
        done: "Answered. Choosing a token already in another gap moves it here and empties that gap." }
      : { empty: `Not answered yet. Choose all ${n} blanks to answer.`, some: () => `Not answered yet. Choose all ${n} blanks to answer.`,
        done: "Answered. You can change any blank until you finish." }));
}

// Yes or no: one radio group per statement, labelled by the statement; arrow keys choose Yes or No.
function assessYesNo(v, it, stemId, locked) {
  const mine = assessPartsOf(it);
  const n = mine.length;
  const table = h("table", { class: "assess-yn" },
    h("thead", {}, h("tr", {}, h("th", {}, "Statement"), h("th", { class: "c" }, "Yes"), h("th", { class: "c" }, "No"))),
    h("tbody", {}, it.parts.map((p, i) => {
      const sid = `${stemId}-s${i}`;
      const name = `assess-yn-${it.q}-${i}`;
      return h("tr", {}, h("td", { id: sid }, assessText(p.text)),
        p.options.map((o) => {
          const lid = `${stemId}-l${i}${o.id}`;
          return h("td", { class: "c" }, h("label", { class: "assess-yn-label" },
            h("input", { type: "radio", name, value: o.id, checked: mine[i] === o.id, disabled: locked, "aria-labelledby": `${sid} ${lid}`, "data-part": String(i),
              onchange: () => assessSetPart(v, it, i, o.id, `input[name="${name}"][value="${o.id}"]`) }),
            h("span", { id: lid }, o.text)));
        }));
    })));
  const hint = " Each statement is a radio group, and arrow keys choose Yes or No.";
  return h("div", { class: "assess-parts" }, table,
    assessPartStatus(it, locked, { empty: `Not answered yet. Answer all ${n} statements.${hint}`,
      some: (k) => `${k} of ${n} answered. The question counts as answered when all ${n} are.${hint}`, done: `Answered.${hint}` }));
}

// The review's score line: "3 of 4 matches right · 0.75 points".
function assessScoreLine(it, mine, score) {
  const [one, many] = ASSESS_PARTS[it.type];
  const n = score.part_count ?? it.parts.length;
  const pts = Number(score.points ?? 0);
  const done = Boolean(mine?.length) && mine.every(Boolean);
  return h("p", { class: "assess-score" }, done ? `${score.parts_right ?? 0} of ${n} ${n === 1 ? one : many} right` : "Not answered",
    h("span", { class: pts >= 1 ? "ok" : pts > 0 ? "part" : "bad" }, ` · ${assessPoints(pts)} point${pts === 1 ? "" : "s"}`));
}

// Each part right or wrong in words, with the correct answer beside a wrong one.
function assessPartsReview(it, mine, key) {
  const textOf = (i, id) => assessPlain(it.parts[i]?.options.find((o) => o.id === id)?.text ?? "");
  if (it.type === "matching") {
    return h("div", { class: "assess-targets" }, it.parts.map((p, i) => {
      const ok = mine[i] === key[i];
      return h("div", { class: `assess-target ${ok ? "r-ok" : "r-bad"}` }, h("span", {}, assessText(p.text)),
        h("span", { class: "assess-res" }, ok ? h("b", { class: "ok" }, `✓ ${textOf(i, mine[i])}`)
          : [h("b", { class: "bad" }, `✗ Yours: ${mine[i] ? textOf(i, mine[i]) : "not answered"}`), h("br"), h("b", { class: "ok" }, `✓ Correct: ${textOf(i, key[i])}`)]));
    }));
  }
  if (it.type === "yesno") {
    return h("table", { class: "assess-yn" },
      h("thead", {}, h("tr", {}, h("th", {}, "Statement"), h("th", { class: "c" }, "Yours"), h("th", { class: "c" }, "Correct"))),
      h("tbody", {}, it.parts.map((p, i) => {
        const ok = mine[i] === key[i];
        return h("tr", { class: ok ? "r-ok" : "r-bad" }, h("td", {}, assessText(p.text)),
          h("td", { class: "c" }, h("b", { class: ok ? "ok" : "bad" }, `${ok ? "✓" : "✗"} ${mine[i] ? textOf(i, mine[i]) : "Not answered"}`)),
          h("td", { class: "c" }, h("b", { class: "ok" }, textOf(i, key[i]))));
      })));
  }
  const word = it.type === "code" ? "Gap" : "Blank";
  const body = String(it.snippet).split(/\{\{(\d)\}\}/).map((piece, k) => {
    if (k % 2 === 0) return piece;
    const i = Number(piece) - 1;
    const ok = mine[i] === key[i];
    return [h("span", { class: "assess-gapn" }, h("span", { class: "sr-only" }, `${word} `), String(i + 1)),
      ok ? h("span", { class: "assess-fill ok" }, h("span", { class: "sr-only" }, "yours, right: "), `${textOf(i, mine[i])} ✓`)
        : [h("span", { class: "assess-fill bad" }, h("span", { class: "sr-only" }, "yours, wrong: "), mine[i] ? textOf(i, mine[i]) : "not answered"), " ",
          h("span", { class: "assess-fill key" }, h("span", { class: "sr-only" }, "correct: "), `${textOf(i, key[i])} ✓`)]];
  });
  return h("div", { class: "assess-snippet", role: "group", "aria-label": `${it.snippet_label} with your answers` }, body);
}

// ---------- review before finishing ----------

function assessReview() {
  const v = assess.view;
  const rows = v.items.map((it, i) => ({ it, i }));
  const flagged = rows.filter((r) => r.it.flagged);
  const attention = rows.filter((r) => r.it.flagged || !assessDone(r.it));
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
        h("td", {}, assessDone(it) ? null : h("span", { class: "pill-s fail" }, "Not answered"), it.flagged ? h("span", { class: "pill-s wrn" }, "Flagged") : null,
          assessDone(it) && !it.flagged ? h("span", { class: "pill-s neu" }, "Answered") : null)))))
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
        h("div", { class: "assess-big" }, `${assessPoints(r.correct)} of ${r.total} `, h("span", { class: "muted-text" }, `(${r.percent}%)`))),
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
        h("td", {}, `${assessPoints(d.correct)} of ${d.total}`),
        h("td", { "aria-hidden": "true" }, h("div", { class: "assess-bar" }, h("i", { style: `width:${d.percent}%` }))))))),
    h("h3", {}, "Weakest objectives"),
    weak.length ? h("table", { class: "assess-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Objective"), h("th", {}, "Correct"), h("th", {}, "Next step"))),
      h("tbody", {}, weak.map((o) => h("tr", {},
        h("td", {}, o.text ? `${o.objective} ${o.text}` : o.objective), h("td", {}, `${assessPoints(o.correct)} of ${o.total}`),
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
        h("span", {}, it.correct ? h("span", { class: "pill-s pass" }, "Correct") : it.points > 0 ? h("span", { class: "pill-s wrn" }, "Partly right")
          : h("span", { class: "pill-s fail" }, assessDone(it) ? "Wrong" : "Not answered"))),
      h("p", { class: "assess-stem", id: "assess-ans-stem" }, it.stem),
      it.changed_since ? h("p", { class: "muted-text small" }, "This question was edited after your exam. Your result stays as it was scored.") : null,
      assessAnswerList(it, it.answer, it.key, it.also_accept, it.correct, it.explanation, it.source, it),
      h("div", { class: "assess-qfoot" }, h("span", { class: "muted-text small" }, `${at + 1} of ${shown.length} shown`),
        h("div", { class: "row" },
          btn("◂ Previous", null, () => { assess.answerAt = at - 1; assessDraw(); }, "tool outline", { disabled: at === 0 }),
          btn("Next ▸", null, () => { assess.answerAt = at + 1; assessDraw(); }, "tool primary", { disabled: at >= shown.length - 1 }))))];
}

// The options with the learner's answer and the right one marked in words, then why and the source page.
function assessAnswerList(it, mine, key, also, correct, explanation, source, score = {}) {
  const text = Object.fromEntries(it.options.map((o) => [o.id, o.text]));
  let list;
  if (ASSESS_PARTS[it.type]) {
    list = [assessScoreLine(it, mine, score), assessPartsReview(it, mine || [], key)];
  } else if (it.type === "order") {
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
  return h("div", { class: "assess-review" }, list,
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
