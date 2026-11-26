// The risk check before a run (risk.js) beyond risk.test.cjs and xray-risk-ack: a finding of an outcome the app
// has no icon for and with a measurement, a pass whose checks carry a measurement, one other check folded away,
// a result with no label, a check that failed without a reason, Check again from a warning, Run anyway once the
// box is ticked, and the "Before a run" settings left empty. The engine's risk_check is mocked in risk.analyze's
// shape; nothing runs.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const NOTE = "Read only: nothing was started, changed or paused to work this out.";
const WARNING = { id: "r-w", result: "warning", label: "Warning", summary: "1 thing to check before you run", needs_acknowledgement: true, note: NOTE,
  findings: [{ check: "history", outcome: "warning", title: "The last run failed" }, { check: "policy", outcome: "pass", title: "Allowed" }] };
const PASS = { id: "r-p", result: "pass", label: "Pass", summary: "Nothing found", findings: [], note: NOTE };

let answers;
before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    risk_check: () => answers.shift(),
    risk_acknowledge: () => ({ recorded: true }),
  } });
});
after(async () => { await app?.close(); });
beforeEach(async () => {
  if (skip) return;
  answers = [];
  await page().evaluate(() => { closeDialog(); toastClose(); });
  app.errors.length = 0;
});

test("findings, a pass with a measurement, one other check, a result with no label, and a failure with no reason", { skip }, async () => {
  const out = await page().evaluate(() => {
    const m = { value: 1.3, unit: "cu_hours", classification: "estimated" };
    const odd = riskFinding({ outcome: "strange", title: "Something odd", measurement: m });
    const pass = riskPanel({ result: "pass", summary: "Fine", findings: [{ title: "Used 1.3 CU hours", measurement: m }] });
    const warn = riskPanel({ result: "warning", summary: "Look", findings: [{ outcome: "warning", title: "A" }, { outcome: "pass", title: "B" }] });
    const nolabel = riskPanel({ result: "mystery", summary: "?" });
    return {
      odd: [odd.className, odd.textContent], pass: pass.querySelector("li").textContent,
      rest: warn.querySelector(".risk-rest summary").textContent, nolabel: nolabel.querySelector(".risk-pills .pill-s").outerHTML,
      alt: riskLocalAlternative({}), none: riskPanel(null),
      failed: riskCheckFailed("SignInCancelled: closed").findings[0].title,
    };
  });
  assert.match(out.odd[0], /risk-finding strange/);
  assert.match(out.odd[1], /^Something odd /);
  assert.match(out.pass, /^Used 1\.3 CU hours /);
  assert.strictEqual(out.rest, "1 other check passed or isn't known");
  assert.match(out.nolabel, /class="pill-s neu">mystery</);
  assert.strictEqual(out.alt, false);
  assert.strictEqual(out.none, null);
  assert.strictEqual(out.failed, "no reason given.");
  assert.deepStrictEqual(app.errors, []);
});

test("Check again from a warning asks again; Run anyway runs once the box is ticked and records it", { skip }, async () => {
  answers = [WARNING, PASS];
  const gate = page().evaluate(() => riskGate({ id: "notebook:nb", action: "run", title: "Run nb in Fabric?" }));
  const d = page().getByRole("dialog", { name: "Run nb in Fabric?" });
  await d.waitFor();
  await d.getByRole("link", { name: "Check again" }).click();
  assert.strictEqual(await gate, true, "the second check passed");
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "risk_check").map((c) => c.params.refresh), [false, true]);

  answers = [WARNING];
  const gate2 = page().evaluate(() => riskGate({ id: "notebook:nb", action: "run", title: "Run nb in Fabric?" }));
  await d.waitFor();
  await d.getByRole("button", { name: "Run anyway" }).click();
  await page().waitForFunction(() => /^Tick "I've looked at these" first, or cancel\.$/.test(document.getElementById("toast").textContent));
  await d.getByRole("checkbox").check();
  await d.getByRole("button", { name: "Run anyway" }).click();
  assert.strictEqual(await gate2, true);
  assert.deepStrictEqual(app.calls.filter((c) => c.method === "risk_acknowledge").map((c) => c.params),
    [{ id: "notebook:nb", action: "run", env: null, risk_id: "r-w" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("the Before a run settings left empty give off and zeros", { skip }, async () => {
  const values = await page().evaluate(() => riskSettingsFields({}).values());
  assert.deepStrictEqual(values, { warn_run_cu_hours: "", warn_failed_runs: 0, schedule_window_minutes: 0, require_acknowledgement: false });
  assert.deepStrictEqual(app.errors, []);
});
