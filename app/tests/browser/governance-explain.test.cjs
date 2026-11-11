// Explain beside a semantic model finding in Governance > Check now (GD-07), and a team's note in
// Explain (GD-09, learn/team.py), from the mockup. The rule guides come from the real engine; the check
// result and the team notes are mocked. A note is the policy author's text, so it shows as plain text.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const HOSTILE = "Use decimal(19,4) for money. <img src=x onerror=\"window.__hacked=1\"> **not bold**";
let app;
let notes = {};
let pkg = null;
const page = () => app.page;
const finding = (rule_id, object, description) => ({ rule_id, severity: "Warning", rule: rule_id, category: "Performance",
  object_type: "Table", object, description, fix: { action: "open_entity", label: "Open the entity", entity: object } });
const result = () => ({ project: "crm", checked_at: new Date().toISOString(), where: "this PC", enforcement: "Check now stops nothing.",
  problems: [], inactive: [], counts: { to_look_at: 2, unknown: 0, excepted: 0, passing: 0, policies: 1 },
  policies: [{ id: "gold-model", name: "Gold model standards", enforcement: "warn", severity: "medium", status: "findings", checked: 7,
    passed: 5, unknown: [], not_run: [], missing: [], rules: ["SM003", "naming.table_name"],
    findings: [finding("SM003", "fact_sales", "amount is double."), finding("naming.table_name", "gold.Orders", "Orders doesn't match the naming pattern.")] }] });

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["app_settings", "save_app_settings", "learn_rule_guides", "learn_catalog", "learn_dismissals"],
    mocks: {
      platform_whoami: () => ({ admin: true, user: "ana@contoso.com", domains_owned: [] }),
      governance_overview: () => ({ me: { admin: true, domains_owned: [] }, options: {}, policies: [], counts: {}, domains: [], projects: ["crm"], exceptions: [], problems: [] }),
      governance_last_check: () => ({ project: "crm", result: result() }),
      learn_team_notes: () => ({ notes, problems: [], package: pkg, note: "" }),
    },
  });
  await page().waitForFunction(() => state.settings && "learn_mode" in state.settings);
});

after(async () => { await app?.close(); });

async function openCheck(mode, teamNotes = {}, teamPackage = null) {
  notes = teamNotes;
  pkg = teamPackage;
  await page().evaluate((mode) => saveSettings({ learn_mode: mode }), mode);
  await page().waitForFunction((mode) => state.settings.learn_mode?.value === mode, mode);
  await page().evaluate(() => { learnTeamNotesLoad = null; learnDetail.clear(); govPage.view = "check"; govPage.open = null; goPlatform("governance"); });
  await page().waitForSelector(".bp-policy [data-rule=SM003]");
}

const sm003 = () => page().locator(".bp-policy [data-rule=SM003]");

test("Professional: a model finding gets the guide's first sentence and Explain, never Dismiss", { skip }, async () => {
  await openCheck("professional");
  assert.match(await sm003().locator(".learn-why").innerText(), /^double and float store an approximation/i);
  await sm003().locator("button", { hasText: "Explain" }).click();
  const detail = sm003().locator(".learn-detail");
  const text = await detail.innerText();
  for (const part of ["Recommended · Performance", "Why it matters", "What to do", "When it's fine", "How to check"]) assert.ok(text.includes(part), part);
  assert.strictEqual(await sm003().locator("button", { hasText: "Dismiss" }).count(), 0, "Check now lists what policies require");
  assert.strictEqual(await sm003().locator(".learn-team-notes").count(), 0, "no team note, no box");
  assert.strictEqual(await page().locator(".bp-policy [data-rule='naming.table_name'] .learn-actions").count(), 0, "only model rules have guides here");
});

test("Expert shows no Explain under a model finding", { skip }, async () => {
  await openCheck("expert");
  assert.strictEqual(await sm003().locator(".learn-actions, .learn-why").count(), 0);
});

test("Guided opens Explain with the team's note as plain text, named with its policy", { skip }, async () => {
  await openCheck("guided", { SM003: [{ text: HOSTILE, policy: "Sales data standards", owner: "Data platform", version: 3, sha256: "3f9a" + "0".repeat(56) + "c21e" }] });
  const box = sm003().locator(".learn-detail .learn-team-notes");
  await box.waitFor();
  assert.strictEqual(await box.locator(".learn-team-text").innerText(), HOSTILE, "shown literally");
  assert.strictEqual(await box.locator("img, strong").count(), 0, "no markup from the note");
  assert.strictEqual(await page().evaluate(() => window.__hacked), undefined);
  assert.strictEqual(await box.locator(".muted-text").innerText(), "From the policy Sales data standards, version 3, owned by Data platform. SHA-256 3f9a…c21e.");
});

test("a note read from the policy package says how old the package is", { skip }, async () => {
  await openCheck("guided", { SM003: [{ text: "Use decimal.", policy: "Sales data standards", owner: "", version: 1, sha256: "" }] }, { age: "3 days", taken_at: "2026-11-08T09:00:00+00:00" });
  const box = sm003().locator(".learn-team-notes");
  await box.waitFor();
  const said = await box.innerText();
  assert.ok(said.includes("From the policy Sales data standards, version 1."), said);
  assert.ok(said.includes("From the project's policy package taken 3 days ago: the platform couldn't be reached."), said);
});
