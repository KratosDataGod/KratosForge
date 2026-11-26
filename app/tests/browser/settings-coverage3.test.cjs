// settings.js beyond settings-coverage and support-bundle: Save support bundle's Choose folder (the
// folder picked, a picker that fails), a file the bundle doesn't have on this PC, a notebook limit set
// to a value off the list (shown, and changed with one save), Help and about and the account menu's guides,
// updates rolled out by the organization, Delete left-overs with a file in use, Ready to work's remembered write test and
// its Hide. app_settings and save_app_settings are the real engine's; the support bundle preview and
// readiness are mocked with their own reply shapes, and the shell's folder picker is set per case.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;

// support_bundle.preview: the files it would hold, the ones missing on this PC, and CONTENTS.txt.
const PREVIEW = {
  folder: "C:\\Users\\pat\\Downloads",
  files: [{ name: "about.json", bytes: 2048 }, { name: "CONTENTS.txt", bytes: 900 }, { name: "sidecar.log", bytes: 4096 }],
  missing: ["agent.log", "extra.log"],
  contents: "What is in the zip.",
};

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["app_settings", "save_app_settings"], mocks: {
    support_bundle_preview: () => JSON.parse(JSON.stringify(PREVIEW)),
    readiness_check: () => [{ key: "capacity", title: "Capacity", status: "ok", text: "On F2." }],
    list_workspaces: () => [],
    // local_writes_service: what's kept on this PC, and what a clear deleted and left.
    local_writes: () => ({ folder: "C:\\staging", files: 3, bytes: 3072, uploading: { files: 0, bytes: 0 }, tpch: [{ folder: "C:\\tpch", bytes: 1024 }] }),
    local_writes_clear: () => ({ bytes: 3072, in_use: ["C:\\staging\\sales.parquet"] }),
  }, storage: { "fd.fold.settings.advanced": "0" } });
  await startupReads(app);
  await page().evaluate(() => {
    const real = window.__TAURI__.core.invoke;
    window.__TAURI__.core.invoke = async (cmd, args) => {
      if (cmd === "rpc" && args.method === "app_settings" && window.__settingsPatch) {
        return window.__settingsPatch(JSON.parse(JSON.stringify(await real(cmd, args))));
      }
      const spec = window.__shellSpec;
      if (!spec || !(cmd in spec)) return real(cmd, args);
      window.__fdShellCalls.push({ cmd, args });
      const one = spec[cmd];
      if (one.throw) throw one.throw;
      return one.value;
    };
  });
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  await page().evaluate(() => {
    window.__shellSpec = {}; window.__settingsPatch = null; window.__fdShellCalls.length = 0;
    document.getElementById("dialog-root").replaceChildren();
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    state.user = "pat@contoso.com"; state.tenant = "contoso"; state.notebook = null;
    go("home");
  });
});

const dialog = () => page().locator("#dialog-root .dialog");
const shell = (spec) => page().evaluate((spec) => Object.assign(window.__shellSpec, spec), spec);
const shellCalls = (cmd) => page().evaluate((cmd) => window.__fdShellCalls.filter((c) => c.cmd === cmd).map((c) => c.args), cmd);
const saves = (from) => app.calls.slice(from).filter((c) => c.method === "save_app_settings").map((c) => c.params.changes);

test("Save support bundle's Choose folder puts the picked folder in the box, and names a file this PC doesn't have", { skip }, async () => {
  await shell({ pick_folder: { value: "D:\\Support" } });
  await page().evaluate(() => saveSupportBundle());
  await dialog().waitFor();
  const rows = await dialog().locator(".bundle-files tbody tr").allInnerTexts();
  assert.strictEqual(rows.length, 5);
  assert.match(rows.at(-1), /^CONTENTS\.txt/, "CONTENTS.txt is drawn last");
  assert.match(rows.find((r) => r.startsWith("extra.log")), /Not on this PC\s+Not on this PC\. CONTENTS\.txt says so\./);
  assert.match(await dialog().innerText(), /Save still works: about\.json and CONTENTS\.txt are always there\./);

  await dialog().getByRole("button", { name: "Choose folder…" }).click();
  await page().waitForFunction(() => document.getElementById("sb-folder").value === "D:\\Support");
  assert.deepStrictEqual(await shellCalls("pick_folder"), [{ start: "C:\\Users\\pat\\Downloads" }], "the picker starts at the folder shown");
  assert.ok(!(await dialog().getByRole("button", { name: "Choose folder…" }).isDisabled()), "enabled again once it answers");
  assert.ok(await dialog().locator("[role=alert]").isHidden());
  assert.deepStrictEqual(app.errors, []);
});

test("a folder picker that fails says why and keeps the folder", { skip }, async () => {
  await shell({ pick_folder: { throw: "The folder dialog is already open." } });
  await page().evaluate(() => saveSupportBundle());
  await dialog().getByRole("button", { name: "Choose folder…" }).click();
  await page().locator("#toast", { hasText: "The folder dialog is already open." }).waitFor();
  assert.strictEqual(await page().evaluate(() => document.getElementById("sb-folder").value), "C:\\Users\\pat\\Downloads");
  assert.ok(!(await dialog().getByRole("button", { name: "Choose folder…" }).isDisabled()));
  assert.deepStrictEqual(app.errors, []);
});

test("a cell time limit off the list is shown as it is, and choosing another saves just that", { skip }, async () => {
  await page().evaluate(() => {
    window.__settingsPatch = (s) => {
      s.kernel_process = { ...s.kernel_process, value: true };
      s.cell_time_limit_minutes = { ...(s.cell_time_limit_minutes || {}), value: 45, source: "user" };
      return s;
    };
    go("settings");
  });
  const select = page().getByRole("combobox", { name: "Cell time limit" });
  await select.waitFor();
  assert.strictEqual(await select.locator("option:checked").innerText(), "45 minutes");
  assert.deepStrictEqual(await select.locator("option").allInnerTexts(), ["Off", "15 minutes", "1 hour", "4 hours", "12 hours", "45 minutes"]);
  const from = app.calls.length;
  await page().evaluate(() => { window.__settingsPatch = null; });
  await select.selectOption("60");
  await page().waitForFunction(() => document.querySelector("select[aria-label='Cell time limit']")?.value === "60"
    && state.settings.cell_time_limit_minutes.value === 60);
  assert.deepStrictEqual(saves(from), [{ cell_time_limit_minutes: 60 }]);
  assert.deepStrictEqual(app.errors, []);
});

test("a passed write test is remembered for this account and the card says so; a failed one forgets it", { skip }, async () => {
  const stored = await page().evaluate(() => {
    localStorage.removeItem("fd.ready.hidden");
    rememberWrite({ status: "ok" }, { workspace_id: "w1", lakehouse_id: "lh1", workspace: "Sales" });
    return JSON.parse(localStorage.getItem("fd.ready.write"));
  });
  assert.strictEqual(stored.user, "pat@contoso.com");
  assert.strictEqual(stored.tenant, "contoso");
  assert.strictEqual(stored.lakehouse_id, "lh1");
  assert.ok(Date.now() - Date.parse(stored.at) < 60000);

  // A card drawn now reads it: Capacity is ok and the remembered write passed, so it's down to one line.
  await page().evaluate(() => { const card = readinessCard(); card.id = "kf-ready-test"; document.querySelector(".page").prepend(card); });
  await page().waitForFunction(() => /Ready to work\.[\s\S]*Checked /.test(document.getElementById("kf-ready-test")?.innerText || ""));
  assert.strictEqual(await page().locator("#kf-ready-test").getByRole("button", { name: "Test again" }).count(), 1);
  await page().evaluate(() => document.getElementById("kf-ready-test").remove());

  assert.strictEqual(await page().evaluate(() => {
    rememberWrite({ status: "fail" }, { workspace_id: "w1", lakehouse_id: "lh1" });
    return localStorage.getItem("fd.ready.write");
  }), null);
  assert.deepStrictEqual(app.errors, []);
});

test("Help and about opens the user guide and the IT admins' guide; the account menu has the guide", { skip }, async () => {
  await shell({ open_doc: { value: null } });
  await page().evaluate(() => go("settings"));
  await page().getByRole("button", { name: "User guide" }).click();
  await page().getByRole("button", { name: "Guide for IT admins" }).click();
  await page().waitForFunction(() => window.__fdShellCalls.filter((c) => c.cmd === "open_doc").length === 2);
  assert.deepStrictEqual(await shellCalls("open_doc"), [{ page: "user-guide.md" }, { page: "permissions.md" }]);

  await page().evaluate(() => accountMenu(document.getElementById("account")));
  const items = (await page().locator("#menu [role=menuitem]").allInnerTexts()).map((t) => t.trim());
  assert.ok(items.includes("Profile (Preview)") && items.includes("Help and user guide") && items.includes("Sign out"), items.join(" | "));
  await page().locator("#menu [role=menuitem]", { hasText: "Help and user guide" }).click();
  await page().waitForFunction(() => window.__fdShellCalls.filter((c) => c.cmd === "open_doc").length === 3);
  await page().evaluate(() => closeMenu());
  assert.deepStrictEqual(app.errors, []);
});

test("with updates rolled out by the organization, Updates says KratosForge doesn't update itself", { skip }, async () => {
  await page().evaluate(() => {
    window.__settingsPatch = (s) => { s.auto_update = { value: false, source: "policy" }; return s; };
    go("settings");
  });
  await page().locator(".page h2", { hasText: "Updates" }).waitFor();
  const card = page().locator("section.card", { has: page().locator("h2", { hasText: "Updates" }) });
  await page().waitForFunction(() => /Your organization rolls out new builds, so KratosForge doesn't update itself\./
    .test(document.querySelector(".page")?.innerText || ""));
  assert.match(await card.innerText(), /This is build /);
  await page().evaluate(() => { window.__settingsPatch = null; });
  assert.deepStrictEqual(app.errors, []);
});

test("Delete left-overs asks first, then says what it deleted and what was still in use", { skip }, async () => {
  await page().evaluate(() => { clearLocalWrites(); });  // it waits for the confirm: not awaited here
  await dialog().waitFor();
  assert.match(await dialog().innerText(), /This deletes 4\.0 KB from this PC: 3 files in C:\\staging; TPC-H data in C:\\tpch\./);
  await dialog().getByRole("button", { name: "Delete" }).click();
  await page().locator("#toast", { hasText: "Deleted 3.0 KB. 1 still in use, left: try again when it has finished." }).waitFor();
  assert.strictEqual(app.calls.filter((c) => c.method === "local_writes_clear").length, 1);
  assert.deepStrictEqual(app.errors, []);
});

test("Hide takes Ready to work off Home and keeps it off", { skip }, async () => {
  await page().evaluate(() => { localStorage.removeItem("fd.ready.hidden"); localStorage.removeItem("fd.ready.write"); go("workspaces"); go("home"); });
  const card = page().locator("section.ready");
  await card.getByRole("button", { name: "Hide" }).click();
  await card.waitFor({ state: "detached" });
  assert.strictEqual(await page().evaluate(() => localStorage.getItem("fd.ready.hidden")), "1");
  await page().evaluate(() => { go("workspaces"); go("home"); });
  await page().locator(".home-hero, .page").first().waitFor();
  assert.strictEqual(await page().locator("section.ready").count(), 0);
  await page().evaluate(() => localStorage.removeItem("fd.ready.hidden"));
  assert.deepStrictEqual(app.errors, []);
});
