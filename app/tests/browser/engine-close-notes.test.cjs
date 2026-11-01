// A session the engine couldn't close comes back as notes, once (engines_service.py _with_notes, from
// SessionManager.notes): after a switch (engine_state), when going back to Python (this PC)
// (engine_select with the kernel) and from engine_close. The person sees each note as given, as a
// warning rather than an error, and it stays in Activity.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

// engines/sessions.py SessionManager.notes: a remote session's note.
const REMOTE = "Couldn't close the Fabric Spark session. It ends on its own after its idle timeout, and may use compute or capacity until then.";
const LOCAL = "Couldn't close the DuckDB session.";

// What the mocked engine_* calls answer next; each test sets what it needs.
const next = { state: null, select: null, close: null };

before(async () => {
  if (skip) return;
  app = await openApp({ viewport: { width: 1440, height: 900 },
    engine: ["list_notebooks", "open_notebook", "save_notebook", "notebook_places", "compute_check", "kernel_variables",
      "restart_kernel", "engine_catalog"],
    mocks: {
      notebook_runs: () => ({ runs: [] }),
      engine_prepare: (p) => ({ engine: p.engine, transition: { needed: false }, confirm: false, grant_minutes: 60, cells: [],
        risk: { outcome: "pass", lines: [] } }),
      // engines_service.py engine_select: the kernel answers at once, with notes; another engine starts in the background.
      engine_select: (p) => p.engine === "local-python" ? next.select || { engine: "local-python", status: "ready" } : { engine: p.engine, status: "starting" },
      // engine_state: the choice, its status, and the start job's notes.
      engine_state: () => next.state,
      // engine_close: back to the kernel, with any notes.
      engine_close: () => next.close || { engine: "local-python", status: "ready" },
    } });
  await page().evaluate(async () => {
    await rpc("save_notebook", { name: "Notes", cells: [{ type: "code", source: "%%sql\nselect 1" }] });
    await openNotebook("Notes");
  });
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  next.state = next.select = next.close = null;
  await page().evaluate(() => {
    closeMenu(); closeDialog();
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = ""; t.className = "toast";
    activity.items = [];
    engineStates.delete("Notes");  // each test starts on Python (this PC)
    renderNotebook();
  });
});

const toast = async () => {
  await page().waitForFunction(() => { const t = document.getElementById("toast"); return !t.hidden && t.textContent; });
  return page().evaluate(() => { const t = document.getElementById("toast"); return { text: t.textContent, error: t.classList.contains("error"), role: t.getAttribute("role") }; });
};
const logged = () => page().evaluate(() => activity.items.map((i) => ({ status: i.status, title: i.title, text: i.text })));

async function pickDuckDB() {
  await page().locator(".engine-btn").first().click();
  await page().locator("#menu [role^=menuitem]", { hasText: /^DuckDB/ }).click();
}

test("a switch whose old session couldn't close says so as a warning, and keeps it in Activity", { skip }, async () => {
  next.state = { engine: "local-duckdb", label: "DuckDB", status: "ready", notes: [REMOTE] };
  await pickDuckDB();
  const t = await toast();
  assert.match(t.text, /^Notes now runs on DuckDB \(this PC\)\./);
  assert.ok(t.text.endsWith(` ${REMOTE}`), `the note is shown as given: ${t.text}`);
  assert.strictEqual(t.error, false, "a warning, not an error");
  assert.strictEqual(t.role, "status");
  assert.deepStrictEqual(await logged(), [{ status: "info", title: "Engine session not closed", text: REMOTE }]);
});

test("a clean switch adds nothing", { skip }, async () => {
  next.state = { engine: "local-duckdb", label: "DuckDB", status: "ready" };
  await pickDuckDB();
  const t = await toast();
  assert.strictEqual(t.text, "Notes now runs on DuckDB (this PC).");
  assert.deepStrictEqual(await logged(), []);
});

test("going back to Python (this PC) shows the note when the session couldn't close", { skip }, async () => {
  next.state = { engine: "local-duckdb", label: "DuckDB", status: "ready" };
  await pickDuckDB();
  await page().waitForFunction(() => engineStates.get("Notes")?.status === "ready");
  await page().evaluate(() => { const t = document.getElementById("toast"); t.hidden = true; t.textContent = ""; });
  next.select = { engine: "local-python", status: "ready", notes: [LOCAL] };
  await page().locator(".engine-btn").first().click();
  await page().locator("#menu [role^=menuitem]", { hasText: /^Python \(this PC\)/ }).click();
  await page().getByRole("dialog").getByRole("button", { name: "Switch and close session" }).click();
  const t = await toast();
  assert.strictEqual(t.text, LOCAL);
  assert.strictEqual(t.error, false);
  assert.deepStrictEqual(await logged(), [{ status: "info", title: "Engine session not closed", text: LOCAL }]);
});

test("a start that failed adds engine_close's note to its error", { skip }, async () => {
  next.state = { engine: "local-duckdb", label: "DuckDB", status: "error", error: "DuckDB couldn't open the file" };
  next.close = { engine: "local-python", status: "ready", notes: [LOCAL] };
  await pickDuckDB();
  const t = await toast();
  assert.strictEqual(t.text, `DuckDB (this PC) didn't start: DuckDB couldn't open the file ${LOCAL}`);
  assert.ok(app.calls.some((c) => c.method === "engine_close"));
  assert.ok((await logged()).some((i) => i.status === "info" && i.text === LOCAL));
});
