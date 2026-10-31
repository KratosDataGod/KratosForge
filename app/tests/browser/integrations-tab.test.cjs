// Settings > Integrations against the real engine (#1197): a request waiting for the person shows in
// full and Deny reaches the engine; Recent activity shows one row per call with what was decided and
// how it ended, approved calls that never finished included; Check the trail says when a line was
// changed; and axe finds nothing serious on the tab. The calls are written by the engine's own
// audit code, so the rows have the shape the tab really gets.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { playwright, openApp, settleForAxe } = require("./harness.cjs");

let axeSource = null;
try { axeSource = fs.readFileSync(require.resolve("axe-core/axe.min.js"), "utf8"); } catch { /* not installed: the axe test skips */ }
const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";
const ENGINE = ["integrations_status", "integrations_activity", "integrations_pending", "integrations_decide",
  "integrations_audit_check", "editor_status", "app_settings"];

// One line per decision and outcome the tab shows, and one request waiting for the person.
const SEED = String.raw`
import time
from fabric_desktop.integrations.audit import IntegrationAudit
from fabric_desktop.integrations.core import CallContext, Caller, IntegrationError
from fabric_desktop.integrations.permissions import summary
from fabric_desktop.integrations.registry import default_registry
from fabric_desktop.integrations.store import IntegrationStore, args_hash
from fabric_desktop.service import Service

store = Service().integration_store
store.set_setting("enabled", True)
client, _ = store.add_client("Codex", "codex", grants=["READ_METADATA", "READ_DATA", "EXECUTE_LOCAL_CODE"])
audit = IntegrationAudit(store)
t = time.time() - 600

def line(cap, permission, decision, error=None, stage="", approval=""):
    global t
    t += 30
    ctx = CallContext(Caller(client["id"], "mcp-stdio"))
    ctx.decision, ctx.approval_id = decision, approval
    audit.record(ctx, cap, permission, {"workspace_id": "ws1", "code": "print(1)"}, t, 0.4, error, stage=stage)

line("fabric_desktop_list_workspaces", "READ_METADATA", "allowed")
line("fabric_desktop_list_workspaces", "READ_METADATA", "", IntegrationError("permission_denied", "Not allowed."))
line("fabric_desktop_execute_python", "EXECUTE_LOCAL_CODE", "approval_denied", IntegrationError("approval_denied", "No."))
line("fabric_desktop_execute_python", "EXECUTE_LOCAL_CODE", "approval_pending", IntegrationError("approval_required", "Waiting."))
line("nope_nothing", "", "", IntegrationError("not_found", "No such capability."))
line("fabric_desktop_execute_python", "EXECUTE_LOCAL_CODE", "approved", stage="started", approval="ap_done")
line("fabric_desktop_execute_python", "EXECUTE_LOCAL_CODE", "approved", approval="ap_done")
line("fabric_desktop_execute_python", "EXECUTE_LOCAL_CODE", "approved", stage="started", approval="ap_killed")

# An approval used with no line at all: the process was stopped between using it and writing.
cap = default_registry().get("fabric_desktop_execute_python")
lost = {"code": "print(2)"}
asked = store.request_approval(client, cap.id, cap.title, cap.permission, summary(cap, client, lost), lost)
store.decide(asked["id"], True)
store.consume(asked["id"], client["id"], args_hash(cap.id, lost))

# And one waiting for the person.
waiting = {"code": "import pandas as pd\nprint(pd.__version__)"}
store.request_approval(client, cap.id, cap.title, cap.permission, summary(cap, client, waiting), waiting)
`;

let app;
const page = () => app.page;

function seed() {
  execFileSync(process.env.FABRIC_DESKTOP_PYTHON || (process.platform === "win32" ? "python" : "python3"), ["-c", SEED], {
    cwd: path.join(__dirname, "..", "..", "..", "sidecar"),
    env: { ...process.env, LOCALAPPDATA: app.engineHome, HOME: app.engineHome, USERPROFILE: app.engineHome },
  });
}

// Settings can render again while its data loads, so wait for the tab to finish loading, with its
// activity rows in, rather than for a heading that a re-render may replace a moment later.
async function openTab() {
  await page().evaluate(() => { go("home"); state.settingsTab = "integrations"; go("settings"); });
  await page().waitForFunction(() => {
    const box = document.querySelector(".page.settings .integrations");
    return box && !box.querySelector(".loading") && box.querySelectorAll("tbody tr").length > 0;
  }, null, { timeout: 15000 });
}

// The activity rows in one read, once all `n` are there: a count and a read taken apart can straddle a re-render.
async function activityRows(n) {
  const handle = await page().waitForFunction((n) => {
    const card = [...document.querySelectorAll(".page.settings .integrations section.card")]
      .find((s) => s.querySelector("h2")?.textContent === "Recent activity");
    const trs = card ? [...card.querySelectorAll("tbody tr")] : [];
    return trs.length === n && trs.map((tr) => {
      const pill = tr.querySelector(".pill-s");
      return { decision: tr.children[3]?.textContent, result: pill?.textContent, cls: pill?.className, title: pill?.title };
    });
  }, n, { timeout: 15000 });
  return handle.jsonValue();
}

const activity = () => page().locator("section.card").filter({ has: page().getByRole("heading", { name: "Recent activity" }) });

async function axe(scope) {
  await settleForAxe(page());
  await page().addScriptTag({ content: axeSource });
  return page().evaluate(async (scope) => {
    const r = await window.axe.run(document.querySelector(scope), { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] });
    return r.violations.filter((v) => ["serious", "critical"].includes(v.impact)).map((v) => `${v.id} @ ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  }, scope);
}

before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1280, height: 1000 }, engine: ENGINE });
  seed();
});

after(async () => { if (app) await app.close(); });

test("Recent activity shows each call once, with what was decided and how it ended", { skip }, async () => {
  await openTab();
  // Eight lines and one used approval with none; the finished approved call shows once: eight rows.
  const cells = await activityRows(8);
  const results = cells.map((c) => c.result);
  for (const want of ["ok", "permission denied", "approval denied", "approval required", "not found", "started", "unfinished"]) {
    assert.ok(results.includes(want), `no ${want} row: ${results.join(", ")}`);
  }
  assert.strictEqual(results.filter((r) => r === "started").length, 1, "the finished approved call still shows its started line");
  const decisions = new Set(cells.map((c) => c.decision));
  for (const want of ["Allowed", "Refused", "Denied by you", "Not answered", "Not valid", "Approved by you"]) {
    assert.ok(decisions.has(want), `no "${want}": ${[...decisions].join(", ")}`);
  }
  const started = cells.find((c) => c.result === "started");
  assert.match(started.cls, /\bwrn\b/);
  assert.match(started.title, /No result yet/);
  const unfinished = cells.find((c) => c.result === "unfinished");
  assert.match(unfinished.cls, /\bwrn\b/);
  assert.match(unfinished.title, /no record of how it ended/);
  assert.match(cells.find((c) => c.result === "ok").cls, /\bpass\b/);
  assert.match(cells.find((c) => c.result === "not found").cls, /\bfail\b/);
  // Code is never shown: arguments that hold code read "(given)".
  assert.ok(!(await activity().textContent()).includes("print("));
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "integrations-activity.png"), fullPage: true });
  assert.deepStrictEqual(app.errors, []);
});

test("Settings > Integrations has no serious axe findings, with a request waiting and every kind of row", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  await openTab();
  assert.deepStrictEqual(await axe(".page.settings"), []);
});

test("A request waiting for the person shows in full, and Deny reaches the engine", { skip }, async () => {
  await openTab();
  const waiting = page().locator("section.card").filter({ has: page().getByRole("heading", { name: "Waiting for you" }) });
  await waiting.getByText(/Codex/).waitFor();
  await waiting.getByRole("button", { name: "Review" }).click();
  const dialog = page().getByRole("dialog", { name: /^Codex wants to: / });
  await dialog.waitFor();
  // The code is shown whole, so the person reads what will run.
  assert.match(await dialog.locator("pre").textContent(), /import pandas as pd\nprint\(pd.__version__\)/);
  await dialog.getByRole("button", { name: "Allow once" }).waitFor();
  await dialog.getByRole("button", { name: "Deny" }).click();
  await page().getByText("Denied Codex").waitFor();
  const decided = app.calls.filter((c) => c.method === "integrations_decide");
  assert.strictEqual(decided.length, 1);
  assert.strictEqual(decided[0].params.approve, false);
  await page().getByRole("heading", { name: "Waiting for you" }).waitFor({ state: "detached" });
});

test("Check the trail says the lines are whole, then names a changed one", { skip }, async () => {
  await openTab();
  await activity().getByRole("button", { name: "Check the trail" }).click();
  await page().getByText(/^All \d+ lines are as they were written$/).waitFor();
  const folder = path.join(app.engineHome, "FabricDesktop", "integrations", "audit");
  const [file] = fs.readdirSync(folder).filter((f) => f.endsWith(".jsonl"));
  const lines = fs.readFileSync(path.join(folder, file), "utf8").split("\n");
  lines[0] = lines[0].replace('"decision": "allowed"', '"decision": "approved"').replace('"decision":"allowed"', '"decision":"approved"');
  fs.writeFileSync(path.join(folder, file), lines.join("\n"));
  await activity().getByRole("button", { name: "Check the trail" }).click();
  await page().getByText(/^\d+ problems?: /).waitFor();
});
