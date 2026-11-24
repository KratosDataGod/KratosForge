// Optimize > Assessment > Excellence (optimize-excellence.js) on a real workspace rather than the
// demonstration fixture: Assess needs a workspace picked, sends the run history chosen, says why an
// assessment failed, and shows a scorecard with nothing to do next, dimensions that couldn't be checked
// or have no control, a control with no evidence cited, and Gaps with a failed read, a control from a
// dimension the app has no name for, and a workspace where everything was checked. excellence_assess is
// mocked, so each answer is exactly what the engine could send.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

const dim = (status, score, coverage, extra = {}) => ({ status, score, assured: score === null ? null : score - 10, coverage, weight: 10,
  rating: status === "rated" ? { label: "Good" } : null, ...extra });
const outcome = (control_id, dimension, outcome_, text, extra = {}) => ({ control_id, dimension, outcome: outcome_, severity: "medium",
  subject: { name: "Sales" }, text, evidence_ids: [], ...extra });

// A scorecard for workspace Sales: rated overall, nothing failed, two dimensions that couldn't be checked.
function result(overrides = {}) {
  return {
    fixture: false, assessed_at: "2026-11-19T09:30:00Z", policy_version: "2026.11",
    profile: { id: "baseline", title: "Baseline", version: "1.2" }, rule_versions: { "ARC-001": "1", "SEC-002": "1", "GOV-001": "1", "SUS-001": "1" },
    scores: {
      overall: { status: "rated", score: 81.4, assured: 70.2, coverage: 74, rating: { label: "Good" } },
      confidence: "medium", critical_risk: false,
      counts: { pass: 1, fail: 0, not_applicable: 0, not_evaluated: 2, errors: 1 },
      dimensions: {
        architecture: dim("rated", 81.4, 100),
        security: dim("not_evaluated", null, 0),
        governance: dim("rated", 0, 0),
        sustainability: dim("not_evaluated", null, 0, { label: "Sustainability" }),
      },
    },
    findings: [], next_actions: [], evidence: [],
    outcomes: [
      outcome("ARC-001", "architecture", "pass", "Bronze, silver and gold are separate lakehouses.", { evidence_ids: ["ev-gone"] }),
      outcome("SEC-002", "security", "not_evaluated", "The role assignments couldn't be read.", { needs: "workspace.roles, an admin's consent" }),
      outcome("SUS-001", "sustainability", "error", "The check stopped.", { subject: { id: "ws-1" } }),
    ],
    reads: [{ read: "role assignments", ok: false, problem: "" }, { read: "workspace settings", ok: true }],
    item_coverage: [{ item: { name: "nb_load", item_type: "Notebook" }, state: "unsupported", reason: "Its definition is encrypted." },
      { item: { name: "pl_main", item_type: "DataPipeline" }, state: "inspected" }],
    ...overrides,
  };
}

let answer;
before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    optimize_status: () => ({ allowed: true, active: null, connections: [], default_name: "FabricDesktop_Central" }),
    list_workspaces: () => [{ id: "w1", displayName: "Sales" }, { id: "w2", displayName: "Ops" }],
    excellence_assess: () => answer(),
  } });
  await page().waitForFunction(() => typeof optimizeExcellenceTabs === "function");
});
after(async () => { await app?.close(); });

// Each test starts on an empty Excellence view with Sales picked and 90 days of run history.
beforeEach(async () => {
  if (skip) return;
  await page().evaluate(() => {
    state.user = "chris@contoso.com"; state.workspaces = null;
    Object.assign(optimizeExcellence, { view: "excellence", workspaceId: "w1", days: 90, result: null, running: false, error: "" });
    optimizePage.tab = "assessment"; go("home"); go("optimize");
  });
  await page().locator(".exc-empty").waitFor();
  await page().getByLabel("Workspace", { exact: true }).locator("option", { hasText: "Ops" }).waitFor({ state: "attached" });
});
const assess = async (label = "Assess") => {
  await page().locator(".exc-scope").getByRole("button", { name: label, exact: true }).click();
  await page().waitForFunction(() => !optimizeExcellence.running && (optimizeExcellence.result || optimizeExcellence.error));
};

const pick = () => page().getByLabel("Workspace", { exact: true });
const toastSays = (text) => page().waitForFunction((t) => document.getElementById("toast").textContent === t, text);
const assessCalls = () => app.calls.filter((c) => c.method === "excellence_assess").map((c) => c.params);

test("Assess needs a workspace, sends the run history chosen, and says why an assessment failed", { skip }, async () => {
  assert.deepStrictEqual(await pick().locator("option").allInnerTexts(), ["Pick a workspace", "Sales", "Ops"]);
  await pick().selectOption("");
  await page().locator(".exc-scope").getByRole("button", { name: "Assess", exact: true }).click();
  await toastSays("Pick a workspace first");
  assert.deepStrictEqual(assessCalls(), []);

  answer = () => { throw new Error("The workspace's items couldn't be listed."); };
  await pick().selectOption("w1");
  await page().getByLabel("Run history").selectOption("7");
  await page().locator(".exc-scope").getByRole("button", { name: "Assess", exact: true }).click();
  await page().locator(".exc-view [role=alert]").getByText("The workspace's items couldn't be listed.").waitFor();
  assert.deepStrictEqual(assessCalls(), [{ workspace_id: "w1", days: 7 }]);
});

test("a scorecard with nothing failed says so, and marks what couldn't be checked or has no control", { skip }, async () => {
  answer = () => result();
  await assess();
  await page().locator(".exc-hero").waitFor();
  assert.deepStrictEqual(assessCalls().at(-1), { workspace_id: "w1", days: 90 });
  assert.match(await page().locator(".exc-scope").innerText(), /Last assessed/);
  await page().locator(".exc-scope").getByRole("button", { name: "Assess again" }).waitFor();
  assert.strictEqual(await page().getByLabel("Workspace", { exact: true }).inputValue(), "w1", "the workspace stays picked");
  const view = page().locator(".exc-view");
  await view.getByText("No control failed. That covers only what could be checked, so see the coverage above and Gaps.").waitFor();
  const card = (name) => view.locator(".exc-dim", { has: page().locator(".exc-dn", { hasText: new RegExp(`^${name}$`) }) });
  assert.strictEqual(await card("Security").locator(".exc-dm").innerText(),
    "Needs role assignments, an admin's consent. It counts against coverage, never as passed.");
  assert.strictEqual(await card("Governance").locator(".exc-dm").innerText(), "Nothing here could be checked. It counts against coverage, never as passed.");
  assert.strictEqual(await card("DevOps").locator(".exc-dr").innerText(), "Not applicable");
  assert.match(await view.locator(".exc-counts").innerText(), /3\s*Not evaluated/, "errors count as not evaluated");

  await card("DevOps").click();
  await page().locator("#dialog-root .dialog").getByText("No control in this version applies to this dimension yet, so it's left out of the overall score. It is never shown as passing.").waitFor();
  await page().locator("#dialog-root .dialog-actions").getByRole("button", { name: "Close" }).click();
  await card("Architecture").click();
  const sheet = page().locator("#dialog-root .dialog");
  await sheet.getByText("Score 81, assured 71, coverage 100%. Weight 10 of 100 in the Baseline profile.").waitFor();
  await sheet.locator("summary", { hasText: "Evidence" }).click();
  await sheet.getByText("No evidence is cited.").waitFor();
  await page().locator("#dialog-root .dialog-actions").getByRole("button", { name: "Close" }).click();
  await sheet.waitFor({ state: "detached" });
});

test("Gaps lists a failed read, controls not evaluated and items not inspected; a clean assessment says all was checked", { skip }, async () => {
  answer = () => result();
  await assess();
  await page().locator(".exc-views").getByRole("tab", { name: "Gaps" }).click();
  const inner = page().locator(".exc-inner");
  await inner.getByText("Reads that failed").waitFor();
  assert.strictEqual(await inner.locator("section", { hasText: "Reads that failed" }).locator("li").innerText(), "Role assignments: Couldn't be read.");
  const rows = await inner.locator("section", { hasText: "Controls not evaluated (2)" }).locator("tbody tr").allInnerTexts();
  assert.match(rows[0], /^SEC-002\s+Security\s+Sales\s+The role assignments couldn't be read\.\s+Needs: role assignments, an admin's consent$/);
  assert.match(rows[1], /^SUS-001\s+Sustainability\s+ws-1\s+The check stopped\.$/, "a dimension the app has no name for uses the engine's label");
  assert.deepStrictEqual(await inner.locator("section", { hasText: "Items not inspected (1)" }).locator("li").allInnerTexts(),
    ["nb_load (Notebook): not supported yet. Its definition is encrypted."]);

  // Assessed again with everything read and checked.
  await page().evaluate(() => { optimizeExcellence.view = "excellence"; render(); });
  answer = () => result({ reads: [{ read: "workspace settings", ok: true }], item_coverage: [],
    outcomes: [outcome("ARC-001", "architecture", "pass", "Separate lakehouses.")] });
  await page().locator(".exc-scope").getByRole("button", { name: "Assess again" }).click();
  await page().waitForFunction(() => !optimizeExcellence.running && optimizeExcellence.result?.item_coverage.length === 0);
  await page().locator(".exc-views").getByRole("tab", { name: "Gaps" }).click();
  await page().locator(".exc-inner").getByText("Everything that applies was read and checked.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("an overall score with no rating says its status", { skip }, async () => {
  answer = () => {
    const r = result();
    r.scores.overall = { status: "not_applicable", score: null, assured: null, coverage: null, rating: null };
    return r;
  };
  await assess();
  const hero = page().locator(".exc-hero .exc-stat").first();
  await hero.locator(".exc-rating.none", { hasText: "Not applicable" }).waitFor();
  assert.strictEqual(await hero.locator(".exc-v").innerText(), "—");
  assert.strictEqual(await page().locator(".exc-hero .exc-stat").nth(2).locator(".exc-v").innerText(), "—", "no coverage, and no % after it");
});
