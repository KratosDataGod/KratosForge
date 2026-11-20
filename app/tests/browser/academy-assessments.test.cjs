// Academy > Assessments (academy-assessments.js, ACD-013 Preview) with the real engine: start a practice
// exam, answer by keyboard, flag, move ordering steps with Alt+arrows, pause and resume, review, finish,
// results by domain and the answers with why. A drill checks each answer. The multi-part types (matching,
// hot area, yes or no, code completion) are answered with the keyboard alone, and matching also by drag;
// their review shows each part and the partial-credit line. Axe on each state. The Academy
// page (academy.js) draws the tab; here the test gives the module a container of its own.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const ENGINE = ["app_settings", "save_app_settings", "academy_exam_home", "academy_exam_start", "academy_exam_open",
  "academy_exam_answer", "academy_exam_flag", "academy_exam_check", "academy_exam_heartbeat", "academy_exam_pause",
  "academy_exam_finish", "academy_exam_review"];

let app;
const page = () => app.page;
const calls = (m) => app.calls.filter((c) => c.method === m);

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ENGINE, mocks: { readiness_check: () => [] } });
  await page().waitForFunction(() => state.settings && "show_preview" in state.settings);
});

after(async () => { await app?.close(); });

async function openTab() {
  await page().evaluate(() => {
    Object.assign(assess, { mode: "home", view: null, result: null, answers: null, pick: null });
    const box = h("div", { class: "page", id: "assess-test" });
    setView(box);
    renderAssessments(box);
  });
  await page().locator(".assess-cards").waitFor();
}

async function axe() {
  const found = await axeFindings(page(), "#assess-test");
  assert.deepStrictEqual(found, [], JSON.stringify(found, null, 1));
}

test("Home shows the notice, the three ways to start and no history yet", { skip }, async () => {
  await openTab();
  const text = await page().locator("#assess-test").textContent();
  assert.match(text, /not Microsoft exam questions, this is not Microsoft's exam interface/);
  assert.match(text, /DP-700 practice exam[\s\S]*50 questions[\s\S]*Domain diagnostic[\s\S]*Objective drill/);
  assert.match(text, /Nothing unfinished/);
  if (axeSource) await axe();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "assess-home.png") });
});

test("A practice exam: keyboard answers, a flag, the grid, pause and resume", { skip }, async () => {
  await page().locator(".assess-card .tool.primary", { hasText: "Start practice exam" }).click();
  await page().locator(".assess-qpanel").waitFor();
  assert.match(await page().locator(".assess-qhead").first().textContent(), /Question 1 of 50/);
  assert.match(await page().locator(".assess-clock-sub").textContent(), /used of 100:00/);
  // Find a single-answer question and answer it with "2", then flag it with F.
  const n = await page().evaluate(() => assess.view.items.findIndex((it) => it.type === "single"));
  await page().evaluate((i) => assessGo(i), n);
  await page().locator(".assess-stem").focus();
  await page().keyboard.press("2");
  await page().waitForFunction((i) => assess.view.items[i].answer?.length === 1, n);
  assert.ok(await page().locator('.assess-options input[data-n="2"]').evaluate((el) => el.checked && document.activeElement === el));
  await page().keyboard.press("f");
  await page().waitForFunction((i) => assess.view.items[i].flagged, n);
  const label = await page().locator(".assess-grid button").nth(n).getAttribute("aria-label");
  assert.match(label, /answered, flagged, current/);
  // Shortcuts off: number keys do nothing.
  await page().locator(".assess-keys input[type=checkbox]").uncheck();
  await page().evaluate(() => assessGo(assess.view.items.findIndex((it) => it.type === "single" && !it.answer)));
  await page().locator(".assess-stem").focus();
  await page().keyboard.press("1");
  await page().waitForTimeout(200);
  assert.strictEqual(await page().evaluate(() => assess.view.items[assess.cur].answer), null);
  await page().locator(".assess-keys input[type=checkbox]").check();
  // Pause stops the clock in the engine; Resume starts it again.
  await page().locator(".assess-side .tool", { hasText: "Pause" }).click();
  await page().locator(".assess-side .tool", { hasText: "Resume" }).waitFor();
  assert.ok(calls("academy_exam_pause").length >= 1);
  assert.match(await page().locator(".assess-qpanel").textContent(), /Paused\. Resume to answer\./);
  assert.ok(await page().locator(".assess-options input").first().isDisabled());
  await page().locator(".assess-side .tool", { hasText: "Resume" }).click();
  await page().locator(".assess-side .tool", { hasText: "Pause" }).waitFor();
  assert.ok(!(await page().locator(".assess-options input").first().isDisabled()));
  if (axeSource) await axe();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "assess-question.png") });
});

test("Review lists flagged and unanswered questions; Finish scores once and shows results by domain", { skip }, async () => {
  await page().locator(".assess-stem").focus();   // shortcuts work while focus is in the question
  await page().keyboard.press("r");
  await page().locator(".assess-head h2", { hasText: "Review before finishing" }).waitFor();
  assert.match(await page().locator(".assess-switch [aria-pressed=true]").textContent(), /Flagged or not answered/);
  await page().locator(".assess-switch button", { hasText: "Flagged (" }).click();
  assert.strictEqual(await page().locator(".assess-table tbody tr").count(), 1);
  if (axeSource) await axe();
  await page().locator(".assess-head .tool.primary", { hasText: "Finish exam" }).click();
  await page().locator(".dialog .tool.primary", { hasText: "Finish exam" }).click();
  await page().locator(".assess-headline").waitFor();
  const text = await page().locator("#assess-test").textContent();
  assert.match(text, /KratosForge practice result/);
  assert.match(text, /Not a Microsoft score, not a scaled score and not a prediction of passing/);
  assert.match(text, /1 Implement and manage an analytics solution\s*30–35%/);
  assert.match(text, /never recalculated/);
  assert.strictEqual(calls("academy_exam_finish").length, 1);
  if (axeSource) await axe();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "assess-results.png") });
});

test("Answers and why mark answers in words and link the source page", { skip }, async () => {
  await page().locator(".assess-headline .tool", { hasText: "See answers and why" }).click();
  await page().locator(".assess-why").waitFor();
  assert.match(await page().locator(".assess-switch [aria-pressed=true]").textContent(), /Wrong or not answered/);
  // Single and multiple mark the right option; ordering the right order; multi-part questions each part.
  assert.match(await page().locator(".assess-qpanel").textContent(), /Correct (answer|order) ✓|✓ Correct: |correct: |\d of \d+ \w+ right/);
  await page().locator(".assess-why a").click();
  const opened = await page().evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "open_link").map((c) => c.args.url));
  assert.ok(opened.length === 1 && opened[0].startsWith("https://learn.microsoft.com/"), opened.join());
  if (axeSource) await axe();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "assess-answers.png") });
});

test("A drill checks each answer, then locks it", { skip }, async () => {
  await openTab();
  assert.match(await page().locator("#assess-test").textContent(), /Finished[\s\S]*DP-700 practice exam/);
  await page().locator(".assess-card .tool", { hasText: "Pick objectives" }).click();
  await page().locator('.assess-objective input[value="1.1.1"]').check();
  await page().locator(".assess-objectives .tool.primary", { hasText: "Start drill" }).click();
  await page().locator(".assess-qpanel").waitFor();
  const check = page().locator(".assess-checkrow .tool", { hasText: "Check answer" });
  assert.ok(await check.isDisabled(), "nothing to check before answering");
  const isOrder = await page().evaluate(() => assess.view.items[0].type === "order");
  if (isOrder) await page().locator(".assess-qpanel .tool", { hasText: "Answer with this order" }).click();
  else await page().locator(".assess-options input").first().check();
  await page().waitForFunction(() => assess.view.items[0].answer);
  await check.click();
  await page().locator(".assess-why").waitFor();
  assert.ok(await page().evaluate(() => document.activeElement.classList.contains("assess-why")));
  assert.ok(calls("academy_exam_check").length === 1);
  if (!isOrder) assert.ok(await page().locator(".assess-options input").first().isDisabled());
  if (axeSource) await axe();
});

test("An ordering question moves steps with Alt+arrows, keeps focus on the step and announces it", { skip }, async () => {
  // A random practice exam may hold no ordering question, so drill an objective that has one (2.1.1).
  await openTab();
  await page().locator(".assess-card .tool", { hasText: "Pick objectives" }).click();
  await page().locator('.assess-objective input[value="2.1.1"]').check();
  await page().locator(".assess-objectives .tool.primary", { hasText: "Start drill" }).click();
  await page().locator(".assess-qpanel").waitFor();
  const n = await page().evaluate(() => assess.view.items.findIndex((it) => it.type === "order"));
  assert.ok(n >= 0, "objective 2.1.1 has an ordering question");
  await page().evaluate((i) => assessGo(i), n);
  const first = await page().locator(".assess-order li .assess-step").first().textContent();
  await page().locator(".assess-order li").first().locator('button[data-dir="down"]').focus();
  await page().keyboard.press("Alt+ArrowDown");
  await page().waitForFunction((i) => assess.view.items[i].answer?.length > 1, n);
  assert.strictEqual(await page().locator(".assess-order li .assess-step").nth(1).textContent(), first);
  const focused = await page().evaluate(() => document.activeElement.closest("li")?.querySelector(".assess-step")?.textContent);
  assert.strictEqual(focused, first);
  await page().waitForFunction(() => /Moved to position 2 of/.test(document.getElementById("announce")?.textContent || ""));
  const up = await page().locator(".assess-order li").first().locator('button[data-dir="up"]');
  assert.ok(await up.isDisabled(), "the first step can't move up");
  assert.match(await up.getAttribute("aria-label"), /^Move '.+' up$/);
  if (axeSource) await axe();
});

// ---------- multi-part questions ----------

async function drillTo(objective, type) {
  await openTab();
  await page().locator(".assess-card .tool", { hasText: "Pick objectives" }).click();
  await page().locator(`.assess-objective input[value="${objective}"]`).check();
  await page().locator(".assess-objectives .tool.primary", { hasText: "Start drill" }).click();
  await page().locator(".assess-qpanel").waitFor();
  const n = await page().evaluate((t) => assess.view.items.findIndex((it) => it.type === t), type);
  assert.ok(n >= 0, `objective ${objective} has a ${type} question`);
  await page().evaluate((i) => assessGo(i), n);
  await page().locator(".assess-qpanel .assess-parts").waitFor();
  return n;
}

const item = (n) => page().evaluate((i) => assess.view.items[i], n);
const focused = () => page().evaluate(() => {
  const el = document.activeElement;
  return { tag: el.tagName, part: el.dataset.part, label: el.getAttribute("aria-label"), name: el.name, value: el.value, checked: el.checked };
});
// The accessible name a screen reader gets for a control labelled by other elements.
const nameOf = (sel) => page().locator(sel).first().evaluate((el) => (el.getAttribute("aria-labelledby") || "").split(" ")
  .map((id) => document.getElementById(id)?.textContent || "").join(" ").trim() || el.getAttribute("aria-label"));

// Choose the next option in a native list with the keyboard, as a keyboard user does, and wait for the engine.
async function arrowDown(n, sel) {
  const before = JSON.stringify((await item(n)).answer);
  await page().locator(sel).focus();
  await page().keyboard.press("ArrowDown");
  await page().waitForFunction(([i, b]) => JSON.stringify(assess.view.items[i].answer) !== b, [n, before]);
}

test("Matching: a list per requirement answers with the keyboard, drag is a shortcut, an item can be used twice", { skip }, async () => {
  const n = await drillTo("2.2.1", "matching");
  const it = await item(n);
  const parts = it.parts.length;
  assert.match(await page().locator(".assess-qhead").textContent(), /Matching/);
  assert.match(await page().locator(".assess-qpanel").textContent(), /Drag an item onto a requirement, or choose it in the requirement's list\. The lists work with the keyboard and a screen reader\./);
  assert.match(await page().locator(".assess-qpanel").textContent(), /Not answered yet\. Fill every requirement to answer\./);
  assert.strictEqual(await nameOf('.assess-targets select[data-part="0"]'), it.parts[0].text);
  // Keyboard: the first requirement's list, ArrowDown chooses the first item; focus stays on the list.
  await arrowDown(n, '.assess-targets select[data-part="0"]');
  assert.deepStrictEqual((await item(n)).answer, [it.options[0].id, ...Array(parts - 1).fill("")]);
  assert.deepStrictEqual(await focused().then((f) => [f.tag, f.part]), ["SELECT", "0"]);
  assert.match(await page().locator(".assess-qpanel").textContent(), new RegExp(`1 of ${parts} filled\\. Not answered until every requirement has an item\\.`));
  assert.strictEqual((await item(n)).answered, false);
  // A letter in a focused list is the list's type-ahead, never the N (next question) shortcut.
  const cur = await page().evaluate(() => assess.cur);
  await page().keyboard.press("n");
  await page().waitForTimeout(200);
  assert.strictEqual(await page().evaluate(() => assess.cur), cur);
  // Drag is a mouse shortcut: the same item onto the second requirement; the pool says it's used twice.
  const chip = page().locator(`.assess-chip[data-item="${it.options[0].id}"]`);
  await chip.dragTo(page().locator('.assess-target[data-part="1"]'));
  await page().waitForFunction(([i, id]) => assess.view.items[i].answer?.[1] === id, [n, it.options[0].id]);
  assert.match(await page().locator(`.assess-chip[data-item="${it.options[0].id}"] .assess-uses`).textContent(), /used 2×/);
  // Clear empties one requirement.
  await page().locator('.assess-target[data-part="1"] .tool', { hasText: "Clear" }).click();
  await page().waitForFunction((i) => assess.view.items[i].answer?.[1] === "", n);
  for (let i = 1; i < parts; i++) await arrowDown(n, `.assess-targets select[data-part="${i}"]`);
  assert.strictEqual((await item(n)).answered, true);
  assert.match(await page().locator(".assess-qpanel").textContent(), /Answered\. You can change any match until you finish\./);
  assert.match(await page().locator(".assess-grid button").nth(n).getAttribute("aria-label"), /, answered/);
  if (axeSource) await axe();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "assess-matching.png") });
  // Check: each requirement right or wrong with the correct item beside it, and the partial-credit line.
  await page().locator(".assess-checkrow .tool", { hasText: "Check answer" }).click();
  await page().locator(".assess-why").waitFor();
  const text = await page().locator(".assess-qpanel").textContent();
  assert.match(text, new RegExp(`\\d of ${parts} matches right · [\\d.]+ points?`));
  const check = await page().evaluate((q) => assess.checks[q], it.q);
  assert.strictEqual(await page().locator(".assess-review .assess-target.r-ok").count(), check.parts_right);
  assert.strictEqual(await page().locator(".assess-review .assess-target.r-bad").count(), parts - check.parts_right);
  assert.strictEqual(await page().locator(".assess-review .assess-target.r-bad").filter({ hasText: "✓ Correct: " }).count(), parts - check.parts_right);
  assert.ok(await page().locator(".assess-targets select").first().isDisabled(), "a checked answer can't change");
  if (axeSource) await axe();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "assess-matching-review.png") });
});

test("Hot area: labelled lists at numbered blanks, Tab moves from blank to blank", { skip }, async () => {
  const n = await drillTo("3.2.1", "hotarea");
  const it = await item(n);
  assert.match(await page().locator(".assess-qhead").textContent(), /Hot area/);
  assert.strictEqual(await page().locator(".assess-snippet").getAttribute("aria-label"), `${it.snippet_label} with ${it.parts.length} blanks`);
  assert.strictEqual(await page().locator('.assess-snippet select[data-part="0"]').getAttribute("aria-label"), `Blank 1, ${it.parts[0].label}`);
  assert.match(await page().locator(".assess-qpanel").textContent(), new RegExp(`Not answered yet\\. Choose all ${it.parts.length} blanks to answer\\.`));
  await arrowDown(n, '.assess-snippet select[data-part="0"]');
  assert.strictEqual((await focused()).label, `Blank 1, ${it.parts[0].label}`);
  await page().keyboard.press("Tab");
  assert.strictEqual((await focused()).label, `Blank 2, ${it.parts[1].label}`);
  for (let i = 1; i < it.parts.length; i++) await arrowDown(n, `.assess-snippet select[data-part="${i}"]`);
  assert.strictEqual((await item(n)).answered, true);
  assert.match(await page().locator(".assess-qpanel").textContent(), /Answered\. You can change any blank until you finish\./);
  if (axeSource) await axe();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "assess-hotarea.png") });
  await page().locator(".assess-checkrow .tool", { hasText: "Check answer" }).click();
  await page().locator(".assess-why").waitFor();
  assert.match(await page().locator(".assess-qpanel").textContent(), new RegExp(`\\d of ${it.parts.length} blanks right · [\\d.]+ points?`));
  if (axeSource) await axe();
});

test("Yes or no: one radio group per statement, arrow keys choose Yes or No", { skip }, async () => {
  const n = await drillTo("2.2.3", "yesno");
  const it = await item(n);
  const rows = it.parts.length;
  assert.match(await page().locator(".assess-qhead").textContent(), /Yes or no/);
  const first = '.assess-yn input[data-part="0"]';
  assert.strictEqual(await nameOf(first), `${it.parts[0].text} Yes`);
  assert.strictEqual(await page().locator(first).count(), 2, "Yes and No for the first statement");
  await page().locator(first).first().focus();
  await page().keyboard.press("Space");
  await page().waitForFunction((i) => assess.view.items[i].answer?.[0] === "yes", n);
  assert.match(await page().locator(".assess-qpanel").textContent(), new RegExp(`1 of ${rows} answered\\. The question counts as answered when all ${rows} are\\.`));
  assert.deepStrictEqual(await focused().then((f) => [f.value, f.checked]), ["yes", true]);
  await page().keyboard.press("ArrowRight");                 // the radio group moves to No and chooses it
  await page().waitForFunction((i) => assess.view.items[i].answer?.[0] === "no", n);
  assert.deepStrictEqual(await focused().then((f) => [f.value, f.checked]), ["no", true]);
  for (let i = 1; i < rows; i++) {
    await page().locator(`.assess-yn input[data-part="${i}"]`).first().focus();
    await page().keyboard.press("Space");
    await page().waitForFunction(([j, k]) => assess.view.items[j].answer?.[k] === "yes", [n, i]);
  }
  assert.strictEqual((await item(n)).answered, true);
  assert.match(await page().locator(".assess-qpanel").textContent(), /Answered\. Each statement is a radio group: arrow keys choose Yes or No\./);
  assert.match(await page().locator(".assess-keys").textContent(), /Number keys choose only in single and multiple answer questions\./);
  if (axeSource) await axe();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "assess-yesno.png") });
  await page().locator(".assess-checkrow .tool", { hasText: "Check answer" }).click();
  await page().locator(".assess-why").waitFor();
  assert.match(await page().locator(".assess-qpanel").textContent(), new RegExp(`\\d of ${rows} statements right · [\\d.]+ points?`));
  if (axeSource) await axe();
});

test("Code completion: a token used in another gap moves; the review shows each gap and partial credit", { skip }, async () => {
  const n = await drillTo("2.3.3", "code");
  const it = await item(n);
  const gaps = it.parts.length;
  assert.match(await page().locator(".assess-qhead").textContent(), /Code completion/);
  assert.strictEqual(await page().locator(".assess-snippet").getAttribute("aria-label"), `PySpark code with ${gaps} gaps`);
  assert.strictEqual(await page().locator('.assess-snippet select[data-part="1"]').getAttribute("aria-label"), "Gap 2");
  await arrowDown(n, '.assess-snippet select[data-part="0"]');
  const tok = (await item(n)).answer[0];
  assert.match(await page().locator(".assess-bank li.used").textContent(), /\(in gap 1\)/);
  // The same token in gap 2 moves it there and empties gap 1, and says so.
  await page().locator('.assess-snippet select[data-part="1"]').selectOption(tok);
  await page().waitForFunction(([i, t]) => assess.view.items[i].answer?.[1] === t && assess.view.items[i].answer[0] === "", [n, tok]);
  await page().waitForFunction(() => /moved from gap 1 to gap 2\. Gap 1 is empty\./.test(document.getElementById("announce")?.textContent || ""));
  assert.strictEqual((await focused()).label, "Gap 2");
  // Fill every gap with the keyboard, each with a token not used yet.
  for (let i = 0; i < gaps; i++) {
    if ((await item(n)).answer[i]) continue;
    const free = await page().evaluate(([j, k]) => { const a = assess.view.items[j]; return a.options.find((o) => !a.answer.includes(o.id)).id; }, [n, i]);
    await page().locator(`.assess-snippet select[data-part="${i}"]`).selectOption(free);
    await page().waitForFunction(([j, k]) => assess.view.items[j].answer?.[k], [n, i]);
  }
  assert.strictEqual((await item(n)).answered, true);
  assert.match(await page().locator(".assess-qpanel").textContent(), /Answered\. Choosing a token already in another gap moves it here and empties that gap\./);
  if (axeSource) await axe();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "assess-code.png") });
  await page().locator(".assess-checkrow .tool", { hasText: "Check answer" }).click();
  await page().locator(".assess-why").waitFor();
  const check = await page().evaluate((q) => assess.checks[q], it.q);
  assert.match(await page().locator(".assess-score").textContent(), new RegExp(`^(${check.parts_right} of ${gaps} gaps right) · ${check.points} points?$`));
  assert.strictEqual(await page().locator(".assess-review .assess-fill.ok, .assess-review .assess-fill.key").count(), gaps, "each gap shows the right token once");
  assert.strictEqual(await page().locator(".assess-review .assess-fill.bad").count(), gaps - check.parts_right);
  // Finish the drill: Answers and why calls it Correct, Partly right or Wrong, with the same line.
  await page().locator(".assess-side .tool", { hasText: "Review and finish" }).click();
  await page().locator(".assess-head .tool.primary", { hasText: "Finish exam" }).click();
  if (await page().locator(".dialog .tool.primary", { hasText: "Finish exam" }).count()) await page().locator(".dialog .tool.primary", { hasText: "Finish exam" }).click();
  await page().locator(".assess-headline").waitFor();
  await page().locator(".assess-headline .tool", { hasText: "See answers and why" }).click();
  await page().locator(".assess-switch button", { hasText: "All" }).click();
  for (let k = 0; k < 20 && !(await page().locator(".assess-score").count()); k++) await page().locator(".assess-qfoot .tool", { hasText: "Next" }).click();
  const pill = await page().locator(".assess-qhead .pill-s").textContent();
  const want = check.correct ? "Correct" : check.parts_right ? "Partly right" : "Wrong";
  assert.strictEqual(pill, want);
  if (axeSource) await axe();
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "assess-code-review.png") });
});
