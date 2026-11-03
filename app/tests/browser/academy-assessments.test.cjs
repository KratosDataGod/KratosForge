// Academy > Assessments (academy-assessments.js, ACD-013 Preview) with the real engine: start a practice
// exam, answer by keyboard, flag, move ordering steps with Alt+arrows, pause and resume, review, finish,
// results by domain and the answers with why. A drill checks each answer. Axe on each state. The Academy
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
  app = await openApp({ engine: ENGINE });
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
  assert.match(await page().locator(".assess-clock-sub").textContent(), /used of 1:40:00/);
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

test("An ordering question moves steps with Alt+arrows, keeps focus on the step and announces it", { skip }, async () => {
  const n = await page().evaluate(() => assess.view.items.findIndex((it) => it.type === "order"));
  assert.ok(n >= 0, "a 50-question exam has an ordering question");
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

test("Review lists flagged and unanswered questions; Finish scores once and shows results by domain", { skip }, async () => {
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
  assert.match(await page().locator(".assess-qpanel").textContent(), /Correct (answer|order) ✓/);
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
