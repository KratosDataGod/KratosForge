// The action-first framework (docs/ui-guidelines.md "Action-first standard"): one place where a
// screen's actions are declared with what they risk, where they run and when they can't be used, and
// where recommendations say, from evidence the app already holds, which action makes sense next.
// Loaded after features.js; the functions here run only after every script has loaded.
//
// It decides nothing on its own and runs nothing unasked: a recommendation is a button with a reason
// beside it. The engine stays the authority on what is allowed: an action that changes Fabric or data
// asks the engine first, as the Workload page does (workload_preflight), and available() below is a
// hint for the screen, never the check.

const ACTION_RISKS = ["low", "moderate", "high"];
const ACTION_PLACES = { local: "This PC", fabric: "Fabric", either: "This PC or Fabric" };
// Why a recommendation is made, most pressing first. The order is the whole ranking: there are no
// scores, and two candidates with the same reason are offered side by side, with neither called best.
const RECOMMENDATION_REASONS = ["failure", "safety", "validation", "unfinished", "local", "continue"];
// Verified: read from real state (a failed run, three validation warnings). Estimate: worked out, and labelled so.
const RECOMMENDATION_BASIS = { verified: "Verified", estimate: "Estimate" };

const actionRegistry = { actions: new Map(), recommendations: [], contexts: {} };

// a: {id, label, icon?, keywords?, contexts: [view ids], risk, where, writes?, reversible?, docs?, preview?,
//     primary?, available?(ctx) -> true | "why not", run(ctx), done?(result, ctx) -> {text, next: [ids]},
//     confirm?: {title, text, verb, typeToConfirm?}, ownConfirm?: "where it asks",
//     spendsCapacity?, riskCheck?: (ctx) -> riskGate target, ownRiskCheck?: "where it checks", safeToRepeat?}
// A high-risk action asks first (confirm, shown as a danger dialog, or ownConfirm naming the dialog that
// already does); one that spends capacity runs the risk check (riskCheck, or ownRiskCheck). Without
// them it isn't defined at all, so the mistake shows when the app loads, not when someone clicks.
function defineAction(a) {
  const problem = actionProblem(a);
  if (problem) throw new Error(`Action ${a?.id || "(no id)"}: ${problem}`);
  actionRegistry.actions.set(a.id, a);
  features.commands.push({ label: a.label, icon: a.icon || "flash", keywords: a.keywords || "", preview: !!a.preview,
    feature: `action:${a.id}`, when: () => actionApplies(a, actionContext()), run: () => runAction(a.id, actionContext()) });
  return a;
}

function actionProblem(a) {
  if (!a?.id || typeof a.id !== "string") return "needs an id";
  if (actionRegistry.actions.has(a.id)) return "is defined twice";
  if (!a.label) return "needs a label";
  if (!Array.isArray(a.contexts) || !a.contexts.length) return "needs the views it belongs to (contexts)";
  if (!ACTION_RISKS.includes(a.risk)) return `risk must be one of ${ACTION_RISKS.join(", ")}`;
  if (!ACTION_PLACES[a.where]) return `where must be one of ${Object.keys(ACTION_PLACES).join(", ")}`;
  if (typeof a.run !== "function") return "needs run(ctx)";
  if (a.risk === "high" && !a.confirm && !a.ownConfirm) return "is high risk, so it needs confirm or ownConfirm";
  if (a.spendsCapacity && !a.riskCheck && !a.ownRiskCheck) return "spends capacity, so it needs riskCheck or ownRiskCheck";
  return null;
}

// What a page knows about where the person is (the open notebook, the selected table). A page
// registers one provider for its view; without one the context is just the view.
function provideActionContext(view, fn) {
  actionRegistry.contexts[view] = fn;
}

function actionContext() {
  const view = state.view;
  let extra = {};
  try { extra = actionRegistry.contexts[view]?.() || {}; } catch { extra = {}; }  // a broken provider leaves the bare view
  return { view, ...extra };
}

function actionApplies(a, ctx) {
  return a.contexts.includes(ctx.view) && (!a.preview || previewShown());
}

// Whether an action can be used here and now, and if not, why, in words the person can be shown.
function actionState(id, ctx) {
  const a = actionRegistry.actions.get(id);
  if (!a) return null;
  let verdict = true;
  try { verdict = a.available ? a.available(ctx) : true; } catch { verdict = "It couldn't be checked just now."; }
  const available = verdict === true;
  return { action: a, available, reason: available ? "" : String(verdict || "It isn't available here."),
    destructive: a.risk === "high", local: a.where !== "fabric", needsFabric: a.where === "fabric", place: ACTION_PLACES[a.where] };
}

// Every action that belongs to this view, available or not, in the order they were defined.
function actionsFor(ctx) {
  return [...actionRegistry.actions.values()].filter((a) => actionApplies(a, ctx)).map((a) => actionState(a.id, ctx));
}

// Runs an action the way every screen should: say why when it can't run, ask first when it is high
// risk, run the risk check when it spends capacity, show a failure with Retry only when repeating
// is safe, and end with what changed and the next step when the action says so.
async function runAction(id, ctx = actionContext()) {
  const s = actionState(id, ctx);
  if (!s) return undefined;
  const a = s.action;
  if (!s.available) { toast(`${a.label}: ${s.reason}`, true, { quiet: true }); return undefined; }
  if (a.confirm) {
    const c = typeof a.confirm === "function" ? a.confirm(ctx) : a.confirm;
    if (!(await confirmDialog(c.title, c.text, c.verb || a.label, { danger: a.risk === "high", typeToConfirm: c.typeToConfirm }))) return undefined;
  }
  if (a.riskCheck && !(await riskGate(a.riskCheck(ctx)))) return undefined;
  const result = await attempt(() => a.run(ctx), `${a.label} didn't work`, { retry: !!a.safeToRepeat });
  if (result !== undefined && a.done) {
    const d = a.done(result, ctx);
    if (d?.text) doneToast(d.text, d.next || [], ctx);
  }
  return result;
}

// A message that an action finished, with the next step as its button when one is available.
function doneToast(text, nextIds = [], ctx = actionContext()) {
  const next = nextIds.map((id) => actionState(id, ctx)).find((s) => s?.available);
  toast(text, false, next ? { action: { label: next.action.label, run: () => runAction(next.action.id, ctx) } } : {});
}

// ---------- recommendations ----------

// r: {id, contexts, reason, basis, action, when(ctx) -> evidence or null, why(evidence, ctx) -> text}
// when() looks only at what the app already holds; it returns what it found (so why() can say it),
// or nothing when there is nothing to recommend.
function defineRecommendation(r) {
  const problem = !r?.id ? "needs an id"
    : actionRegistry.recommendations.some((x) => x.id === r.id) ? "is defined twice"
    : !Array.isArray(r.contexts) || !r.contexts.length ? "needs the views it belongs to (contexts)"
    : !RECOMMENDATION_REASONS.includes(r.reason) ? `reason must be one of ${RECOMMENDATION_REASONS.join(", ")}`
    : !RECOMMENDATION_BASIS[r.basis] ? "basis must be verified or estimate"
    : !r.action ? "needs the action it leads to"
    : typeof r.when !== "function" || typeof r.why !== "function" ? "needs when(ctx) and why(evidence)" : null;
  if (problem) throw new Error(`Recommendation ${r?.id || "(no id)"}: ${problem}`);
  actionRegistry.recommendations.push(r);
  return r;
}

// The recommendations for this context, at most limit, most pressing reason first. ranked is true
// only when the first one's reason is more pressing than the next one's; otherwise they are choices
// of equal standing, and the screen says "You could" rather than "Recommended".
function recommend(ctx = actionContext(), limit = 3) {
  const found = [];
  for (const r of actionRegistry.recommendations) {
    if (!r.contexts.includes(ctx.view)) continue;
    const s = actionState(r.action, ctx);
    if (!s?.available || !actionApplies(s.action, ctx)) continue;
    let evidence = null;
    try { evidence = r.when(ctx); } catch { evidence = null; }  // a rule that can't read its evidence recommends nothing
    if (!evidence) continue;
    found.push({ id: r.id, reason: r.reason, basis: r.basis, why: r.why(evidence, ctx), evidence, state: s });
  }
  const rank = (x) => RECOMMENDATION_REASONS.indexOf(x.reason);
  found.sort((x, y) => rank(x) - rank(y));  // stable: definition order within a reason
  const items = found.slice(0, limit);
  const ranked = items.length === 1 || (items.length > 1 && rank(items[0]) < rank(items[1]));
  return { items, ranked };
}

// ---------- shared pieces for screens ----------

// "Recommended next" (or "You could"): each recommendation with its basis, its reason in words and
// one button. Nothing at all when there is nothing to recommend.
function nextSteps(ctx = actionContext(), limit = 3) {
  const { items, ranked } = recommend(ctx, limit);
  if (!items.length) return null;
  return h("section", { class: "next-steps", "aria-label": ranked ? "Recommended next" : "You could" },
    h("span", { class: "next-steps-head" }, ranked ? "Recommended next" : "You could"),
    ...items.map((it) => h("div", { class: "next-step", "data-recommendation": it.id },
      h("span", { class: `pill-s ${it.basis === "verified" ? "info" : "neu"}` }, RECOMMENDATION_BASIS[it.basis]),
      h("span", { class: "grow" }, it.why),
      btn(it.state.action.label, it.state.action.icon || null, () => runAction(it.state.action.id, ctx),
        ranked && it === items[0] ? "tool primary small" : "tool outline small"))));
}

// A screen's primary action and a More menu with every other action that belongs here. The primary
// is the top recommendation when one is ranked first, otherwise the action marked primary for this
// view. Actions that can't run stay, marked unavailable (still focusable, so a screen reader reaches
// them) with the reason as their tooltip; choosing one says the reason (runAction).
function actionBar(ctx = actionContext()) {
  const all = actionsFor(ctx);
  const top = recommend(ctx, 1);
  const lead = (top.ranked && top.items[0]?.state) || all.find((s) => s.action.primary) || null;
  const button = (s, cls) => btn(s.action.label, s.action.icon || null, () => runAction(s.action.id, ctx), cls,
    { "data-action": s.action.id, "aria-disabled": s.available ? null : "true", title: s.available ? null : s.reason,
      "aria-description": s.available ? null : s.reason });
  const rest = all.filter((s) => s !== lead && s.action.id !== lead?.action.id);
  return h("div", { class: "action-bar row" },
    lead ? button(lead, "tool primary") : null,
    rest.length ? btn("More", "chevronDown", (e) => showMenu(e.currentTarget,
      rest.map((s) => button(s, "tool"))), "tool outline", { "aria-haspopup": "menu" }) : null);
}
