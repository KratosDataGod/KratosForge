// Learn (learn.js, LX-05): the page at the bottom of the rail with the real engine, so the lessons,
// the lab on this PC, Run, Check my answer and Reset are the ones that ship. Also axe on Home and on
// an open lab.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const ENGINE = ["app_settings", "save_app_settings", "learn_catalog", "learn_module", "learn_lab_start", "learn_lab_save", "learn_lab_run",
  "learn_lab_check", "learn_lab_hint", "learn_lab_reset", "notebook_practice_rules", "sql_practice_rules"];
const GOOD = "SELECT order_id, customer, amount\nFROM orders\nWHERE region = 'North' AND amount > 100\nORDER BY amount DESC;";

let app;
const page = () => app.page;
const engineCalls = () => app.calls.map((c) => c.method).filter((m) => m.startsWith("learn_"));

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ENGINE });
  await page().waitForFunction(() => state.settings && "show_preview" in state.settings);
});

after(async () => { await app?.close(); });

async function axe(scope) {
  return axeFindings(page(), scope);
}

test("Learn sits at the bottom of the rail, before Source control and Settings, and keeps the main rail at 8", { skip }, async () => {
  const bottom = await page().locator("#rail-bottom .rail-item").allTextContents();
  assert.deepStrictEqual(bottom.map((t) => t.trim()), ["Learn", "Source control", "Settings"]);
  assert.ok(await page().locator("#rail-main .rail-item").count() <= 8);
  await page().locator("#rail-bottom .rail-item", { hasText: "Learn" }).click();
  await page().locator(".learn-continue h2").waitFor();
  assert.match(await page().locator(".page.learn h1").textContent(), /Learn\s*Preview/);
  assert.strictEqual(await page().locator(".head-actions button").count(), 0, "Home's card is the one Start");
  assert.match(await page().locator(".learn-body").textContent(), /never read or write OneLake/);
  const labs = await page().locator(".learn-grid .learn-card b").allTextContents();
  assert.ok(labs.includes("LAB-02 · Optimize a slow query"), labs.join());
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "learn-home.png") });
});

test("Paths lists each path's lessons, and Best practices links every rule to its explanation", { skip }, async () => {
  await page().locator(".subtabs [role=tab]", { hasText: "Paths" }).click();
  await page().locator(".learn-paths section").first().waitFor();
  const text = await page().locator(".learn-paths").textContent();
  assert.match(text, /Data Fundamentals[\s\S]*LM-01 · Your first query on this PC/);
  assert.match(text, /Lessons for this path come in a later update/);
  await page().locator(".subtabs [role=tab]", { hasText: "Best practices" }).click();
  await page().locator(".learn-rules").first().waitFor();
  await page().locator(".learn-rules a", { hasText: "About SQ002" }).click();
  const opened = await page().evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "open_doc").map((c) => c.args.page));
  assert.deepStrictEqual(opened, ["sql-best-practices.md#sq002"]);
});

test("Learn Home has no serious axe findings", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  await page().evaluate(() => { learnPage.tab = "home"; learnPage.open = null; go("learn"); });
  await page().locator(".learn-continue h2").waitFor();
  assert.deepStrictEqual(await axe("#view"), []);
});

test("a lesson starts its lab on this PC, runs SQL, and checks the answer", { skip }, async () => {
  await page().evaluate(() => learnOpen("LM-01"));
  await page().locator(".learn-lesson h2").first().waitFor();
  assert.match(await page().locator(".learn-lesson").textContent(), /Pick the columns/);
  assert.ok(await page().locator(".learn-lesson pre.learn-code").count() >= 1, "fenced SQL is a code block");
  await page().locator("button", { hasText: "Start the lab" }).click();
  await page().locator("textarea.learn-editor").waitFor();
  await page().locator("textarea.learn-editor").fill("SELECT order_id, amount FROM orders ORDER BY order_id LIMIT 3");
  await page().locator(".learn-work button", { hasText: "Run" }).click();
  await page().locator(".learn-result table").waitFor();
  assert.deepStrictEqual(await page().locator(".learn-result th").allTextContents(), ["order_id", "amount"]);
  assert.strictEqual(await page().locator(".learn-result tbody tr").count(), 3);
  await page().locator("textarea.learn-editor").press("End");
  await page().locator("textarea.learn-editor").type(" ");
  assert.strictEqual(await page().locator(".learn-result table").count(), 0, "editing clears the last run's result");
  assert.match(await page().locator(".page-head h1").textContent(), /Preview/);
  assert.strictEqual(await page().locator("#view h1").count(), 1, "the lesson's own title is a level down");
  await page().locator(".learn-work button", { hasText: "Check my answer" }).click();
  await page().locator(".learn-checks li.no").waitFor();
  assert.match(await page().locator(".learn-aside").textContent(), /Not yet/);
  await page().locator("textarea.learn-editor").fill(GOOD);
  await page().locator(".learn-work button", { hasText: "Check my answer" }).click();
  await page().locator(".learn-pass").waitFor();
  assert.match(await page().locator(".learn-aside").textContent(), /Same result/);
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "learn-lab.png") });
});

test("an open lab has no serious axe findings", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  assert.deepStrictEqual(await axe("#view"), []);
});

test("a run that tries to write changes nothing, and BEGIN/COMMIT is refused with a message", { skip }, async () => {
  const editor = page().locator("textarea.learn-editor");
  await editor.fill("DELETE FROM orders");
  await page().locator(".learn-work button", { hasText: "Run" }).click();
  await page().locator(".learn-result p").waitFor();
  await editor.fill("SELECT count(*) AS n FROM orders");
  await editor.press("Control+Enter");
  await page().waitForFunction(() => document.querySelector(".learn-result td")?.textContent === "7");
  await editor.fill("BEGIN; DELETE FROM orders; COMMIT;");
  await page().locator(".learn-work button", { hasText: "Run" }).click();
  await page().locator(".learn-result .learn-error").waitFor();
  assert.match(await page().locator(".learn-result .learn-error").textContent(), /BEGIN, COMMIT or ROLLBACK/);
});

test("Reset lab asks first, and Leave goes back to Learn", { skip }, async () => {
  await page().locator(".head-actions button", { hasText: "Reset lab" }).click();
  await page().locator("#dialog-root .dialog").waitFor();
  assert.match(await page().locator("#dialog-root .dialog").textContent(), /your answers included/);
  await page().locator("#dialog-root button", { hasText: "Reset lab" }).click();
  for (const end = Date.now() + 10000; !engineCalls().includes("learn_lab_reset") && Date.now() < end; await new Promise((r) => setTimeout(r, 25)));
  assert.ok(engineCalls().includes("learn_lab_reset"));
  await page().locator("textarea.learn-editor").waitFor();
  assert.strictEqual(await page().locator(".learn-result table").count(), 0, "the old result is gone");
  await page().evaluate(() => { learnPage.tab = "home"; });  // not left to the axe tests, which skip without axe-core
  await page().locator(".head-actions button", { hasText: "Leave" }).click();
  await page().locator(".learn-continue").waitFor();
  await page().locator(".subtabs [role=tab]", { hasText: "Labs" }).click();
  await page().locator(".head-actions button").waitFor();
  assert.match(await page().locator(".head-actions").textContent(), /Continue: Your first query on this PC/);
});

test("The first visit asks once how much guidance you'd like: Save keeps the choice, Skip keeps Professional, and policy hides it", { skip }, async () => {
  await page().evaluate(() => { learnPage.open = null; learnPage.tab = "home"; go("learn"); });
  await page().locator(".learn-ask").waitFor();
  assert.match(await page().locator(".learn-ask h2").textContent(), /Would you like KratosForge to help you improve your development skills\?/);
  assert.strictEqual(await page().locator(".learn-ask input:checked").inputValue(), "professional", "Professional is picked to start with");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "learn-ask.png") });
  await page().locator(".learn-ask input[value=guided]").check();
  await page().locator(".learn-ask button", { hasText: "Save" }).click();
  await page().waitForFunction(() => state.settings.learn_mode?.value === "guided" && state.settings.learn_mode.source === "user");
  await page().locator(".learn-ask").waitFor({ state: "detached" });
  assert.ok(await page().evaluate(() => document.activeElement?.matches(".learn-guidance select")), "keyboard focus moves to the picker");
  await page().evaluate(() => saveSettings({ learn_mode: null }).then(() => render()));  // as if never asked
  await page().locator(".learn-ask").waitFor();
  await page().locator(".learn-ask button", { hasText: "Skip" }).click();
  await page().waitForFunction(() => state.settings.learn_mode?.value === "professional" && state.settings.learn_mode.source === "user");
  await page().locator(".learn-ask").waitFor({ state: "detached" });
  const policy = await page().evaluate(() => {
    const keep = state.settings.learn_mode;
    state.settings.learn_mode = { value: "expert", source: "policy" };
    const shown = learnAsk() !== null;
    state.settings.learn_mode = { value: "professional", source: "default" };
    state.settings.show_preview = { ...state.settings.show_preview, value: false };
    const hidden = learnAsk() !== null;
    state.settings.show_preview = { ...state.settings.show_preview, value: true };
    state.settings.learn_mode = keep;
    return { shown, hidden };
  });
  assert.deepStrictEqual(policy, { shown: false, hidden: false }, "not asked when policy sets it or preview features are hidden");
});

test("Guidance is Professional by default: no Lesson link beside a finding, no suggestion on Home (LX-06)", { skip }, async () => {
  await page().evaluate(() => { learnPage.open = null; learnPage.tab = "home"; go("learn"); });
  await page().locator(".learn-continue").waitFor();
  assert.strictEqual(await page().locator(".learn-guidance select").inputValue(), "professional");
  assert.strictEqual(await page().evaluate(() => learnFindingLink("SQ002")), null);
  assert.strictEqual(await page().locator(".learn-suggest").count(), 0);
});

test("Guided adds the lesson that covers a finding's rule, and it opens the lesson", { skip }, async () => {
  await page().locator(".learn-guidance select").selectOption("guided");
  await page().waitForFunction(() => state.settings.learn_mode?.value === "guided");
  await page().evaluate(() => document.body.append(h("div", { id: "finding-test" }, learnFindingLink("SQ002"))));  // outside #view, which the mode change renders again
  await page().locator("#finding-test a", { hasText: "Lesson: Your first query on this PC" }).click();
  await page().locator(".page-head h1", { hasText: "Your first query" }).waitFor();
  await page().evaluate(() => document.querySelector("#finding-test").remove());
  const none = await page().evaluate(async () => { const el = learnFindingLink("NB001"); await learnCatalogCached(); return el.textContent; });
  assert.strictEqual(none, "", "no lesson covers NB001 yet");
  const draftOnly = await page().evaluate(async () => {
    const cat = await learnCatalogCached();
    learnCatalogLoad = Promise.resolve({ ...cat, modules: cat.modules.map((m) => ({ ...m, status: "draft" })) });
    const el = learnFindingLink("SQ002");
    await learnCatalogCached();
    learnCatalogLoad = null;
    return el.textContent;
  });
  assert.strictEqual(draftOnly, "", "a draft lesson isn't linked beside a finding");
});

test("Guided also suggests the next lesson on Home, and a policy-set level can't be changed here", { skip }, async () => {
  await page().evaluate(() => { learnPage.open = null; go("learn"); });
  await page().locator(".learn-suggest").waitFor();
  assert.match(await page().locator(".learn-suggest").textContent(), /Suggested next[\s\S]*LM-02 · Data types, and why decimals beat floats for money/);
  await page().evaluate(() => { state.settings.learn_mode = { value: "guided", source: "policy" }; render(); });
  await page().locator(".learn-guidance select").waitFor();
  assert.strictEqual(await page().locator(".learn-guidance select").isDisabled(), true);
});
