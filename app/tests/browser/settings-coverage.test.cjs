// settings.js beyond the other Settings tests: Check for updates when the check fails, on a local build,
// when the channel can't be read or the download was refused, and an install that streams its progress,
// notes a build replaced on the channel and then fails; the new-build banner's failed restart; What's new
// with more than ten recent changes; the sign-in app ID notes and the admin link; what the organization's
// policy file says about itself; usage data that can't be read and what's sent; Settings switches that
// save one change each; Ready to work's fixes and its write test; and the command bar's Settings commands.
// app_settings and save_app_settings are the real engine's; the rest is mocked, and the shell's answers
// for the update check are set per case in the page.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, startupReads } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const RUNNING = "5d69f21aa1b2c3d4e5f60718293a4b5c6d7e8f90";
const NEWER = "a41c0e9aa1b2c3d4e5f60718293a4b5c6d7e8f90";
const PAGE = "https://github.com/KratosDataGod/KratosForge/releases/tag/preview";

let telemetry = null;  // telemetry_status's answer, or null to fail it
let readiness = [];
const mocks = {
  readiness_check: () => readiness,
  telemetry_status: () => { if (!telemetry) throw new Error("The usage store is locked."); return telemetry; },
  save_notebook: () => ({ saved: true }),
  list_workspaces: () => { throw new Error("Fabric didn't answer (503)."); },
  local_writes: () => ({ folder: "C:\\staging", files: 0, bytes: 0, uploading: { files: 0, bytes: 0 }, tpch: [] }),
};

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["app_settings", "save_app_settings"], mocks, storage: { "fd.fold.settings.advanced": "0" } });
  await startupReads(app);
  await page().evaluate(() => {
    // The shell's answers per case: each command maps to an answer, or a list of answers in turn. An answer can emit
    // component-log lines, wait for window.__release(), throw, or return a value.
    const real = window.__TAURI__.core.invoke;
    window.__TAURI__.core.invoke = async (cmd, args) => {
      // Settings as the engine has them, with this case's changes on top (window.__settingsPatch).
      if (cmd === "rpc" && args.method === "app_settings" && window.__settingsPatch) {
        return window.__settingsPatch(JSON.parse(JSON.stringify(await real(cmd, args))));
      }
      const spec = window.__shellSpec;
      if (!spec || !(cmd in spec)) return real(cmd, args);
      window.__fdShellCalls.push({ cmd, args });
      const s = spec[cmd];
      const one = Array.isArray(s) ? (s.length > 1 ? s.shift() : s[0]) : s;
      for (const line of one.emit || []) window.__fdEmit("component-log", line);
      if (one.hold) await new Promise((r) => { window.__release = r; });
      if (one.throw) throw one.throw;
      return one.value;
    };
  });
});
after(async () => { await app?.close(); });

beforeEach(async () => {
  if (skip) return;
  telemetry = null;
  readiness = [];
  await page().evaluate(() => {
    window.__shellSpec = { open_link: { value: null }, open_doc: { value: null } };
    window.__settingsPatch = null; window.__fdShellCalls.length = 0;
    document.getElementById("dialog-root").replaceChildren();
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    state.user = "chris@contoso.com"; state.notebook = null; state.commit = "5d69f21aa1b2c3d4e5f60718293a4b5c6d7e8f90";
    updateWatch.notice = null; renderUpdateBanner();
    try { localStorage.removeItem("fd.updatewatch.later"); localStorage.removeItem("fd.updatewatch.off"); } catch { /* */ }
    go("home");
  });
});

const dialog = () => page().locator("#dialog-root .dialog");
const close = () => page().locator("#dialog-root .dialog-actions").getByRole("button", { name: "Close" }).click();
const shellCalls = (cmd) => page().evaluate((cmd) => window.__fdShellCalls.filter((c) => c.cmd === cmd).map((c) => c.args), cmd);
const toastSays = (text) => page().waitForFunction((t) => document.getElementById("toast").textContent === t, text);
const saves = (from) => app.calls.slice(from).filter((c) => c.method === "save_app_settings").map((c) => c.params.changes);
const shell = (spec) => page().evaluate((spec) => Object.assign(window.__shellSpec, spec), spec);
const openSettings = async () => {
  await page().evaluate(() => go("settings"));
  await page().locator(".page h2", { hasText: "Updates" }).waitFor();
};
const checkUpdates = async () => {
  await openSettings();
  await page().getByRole("button", { name: "Check for updates" }).click();
};

// ---------- Check for updates ----------

test("a check that fails says why and Try again checks again; a local build doesn't update itself", { skip }, async () => {
  await shell({ check_for_update: [{ throw: "Python isn't installed" }, { value: { commit: "", latest: { channel: "stable", commit: NEWER } } }] });
  await checkUpdates();
  await dialog().locator(".error-text", { hasText: "Python isn't installed" }).waitFor();
  await dialog().getByRole("button", { name: "Try again" }).click();
  await dialog().getByText("This is a local build, so it doesn't update itself.").waitFor();
  assert.match(await dialog().locator("dl.about").innerText(), /This build\s+.+\s+Latest on Stable\s+a41c0e9/);
  await close();
  await dialog().waitFor({ state: "detached" });
  assert.strictEqual((await shellCalls("check_for_update")).length, 2);
});

test("a channel that can't be read, or a refused download, is an error with the release page offered", { skip }, async () => {
  await shell({ check_for_update: [
    { value: { commit: RUNNING, latest: { channel: "preview", commit: null, error: "the version file timed out", page: PAGE } } },
    { value: { commit: RUNNING, latest: { channel: "stable", commit: NEWER, refused: "The new build's signature didn't match, so it wasn't offered.", page: PAGE } } },
  ] });
  await checkUpdates();
  await dialog().locator(".error-text", { hasText: "Couldn't check the Preview channel: the version file timed out" }).waitFor();
  assert.match(await dialog().innerText(), /You can download kratosforge\.exe from the channel's release page/);
  await dialog().getByRole("button", { name: "Download manually" }).click();
  assert.deepStrictEqual(await shellCalls("open_link"), [{ url: PAGE }]);
  await dialog().getByRole("button", { name: "Try again" }).click();
  await dialog().locator(".error-text", { hasText: "The new build's signature didn't match, so it wasn't offered." }).waitFor();
  await close();
});

test("Install and restart saves the notebook, shows the download and a replaced build, and a failed update says which build you're on", { skip }, async () => {
  await page().evaluate(() => { loadIntoState({ name: "Unsaved work", cells: [] }); state.notebook.dirty = true; go("home"); });
  await shell({
    check_for_update: { value: { commit: RUNNING, auto_update: true, latest: { channel: "stable", commit: NEWER, page: PAGE, note: "A big one." } } },
    update_app: { emit: ["Build a41c0e9 was replaced on the update channel by build b77d3f1", "progress 40"], hold: true, throw: "the disk is full" },
  });
  await checkUpdates();
  await dialog().getByText(/^A big one\. Running cells stop\./).waitFor();
  const from = app.calls.length;
  await dialog().getByRole("button", { name: "Install and restart" }).click();
  await dialog().getByRole("button", { name: "Downloading… 40%" }).waitFor();
  assert.ok(await dialog().getByRole("button", { name: "Later" }).isDisabled());
  await dialog().getByText("Installing build b77d3f1, which replaced build a41c0e9 on the channel.").waitFor();
  assert.deepStrictEqual(app.calls.slice(from).map((c) => c.method).filter((m) => m === "save_notebook"), ["save_notebook"], "the open notebook is saved first");
  await page().evaluate(() => window.__release());
  await dialog().locator(".error-text", { hasText: "Update to build a41c0e9 failed: the disk is full You are still on 5d69f21." }).waitFor();
  await dialog().getByRole("button", { name: "Try again" }).waitFor();
  assert.ok(await dialog().getByRole("button", { name: "Later" }).isEnabled());
  assert.deepStrictEqual(await shellCalls("update_app"), [{ commit: NEWER }]);
  await dialog().getByRole("button", { name: "Later" }).click();
});

test("the new-build banner links the release notes, and a failed restart says so and offers Try again", { skip }, async () => {
  await shell({
    check_for_update: { value: { commit: RUNNING, auto_update: true, latest: { channel: "preview", commit: NEWER, page: PAGE } } },
    update_app: { throw: "the download was cut off" },
  });
  await page().evaluate(() => watchForUpdates());
  const banner = page().locator("#update-root .update-banner");
  await banner.getByText("A new KratosForge build is ready.").waitFor();
  assert.match(await banner.innerText(), /Build a41c0e9 is out on Preview \(you're on 5d69f21\)/);
  await banner.getByRole("button", { name: "Release notes" }).click();
  assert.deepStrictEqual(await shellCalls("open_link"), [{ url: PAGE }]);
  await banner.getByRole("button", { name: "Restart to update" }).click();
  await banner.locator(".error-text", { hasText: "Update to build a41c0e9 failed: the download was cut off" }).waitFor();
  assert.match(await banner.innerText(), /You are still on this build\. Check for updates in the account menu can try again\./);
  await banner.getByRole("button", { name: "Try again" }).waitFor();
  await banner.getByRole("button", { name: "Later" }).click();
  await banner.waitFor({ state: "detached" });
});

// ---------- What's new ----------

test("What's new shows the ten newest changes, Show more the rest, and a change's number opens its pull request", { skip }, async () => {
  await page().evaluate((commit) => {
    const history = Array.from({ length: 12 }, (_, i) => ({ title: `Change ${i + 1}`, date: "2026-11-1" + (i % 9), pr: 4000 + i }));
    state.release = { commit, notes: "- Faster SQL pages", history };
  }, RUNNING);
  await page().keyboard.press("Control+K");
  await page().getByRole("combobox", { name: "Command bar" }).fill("What's new");
  await page().locator("#palette-list").getByRole("option", { name: "What's new", exact: true }).click();
  await dialog().getByText("Faster SQL pages").waitFor();
  const list = dialog().locator(".whatsnew-history > li");
  assert.strictEqual(await list.count(), 10);
  await dialog().getByRole("button", { name: "Show 2 more" }).click();
  assert.strictEqual(await list.count(), 12);
  assert.strictEqual(await dialog().getByRole("button", { name: "Show 2 more" }).count(), 0);
  await list.nth(11).getByRole("link", { name: "#4011" }).click();
  assert.deepStrictEqual(await shellCalls("open_link"), [{ url: "https://github.com/KratosDataGod/KratosForge/pull/4011" }]);
  await page().evaluate(() => { state.release = null; closeDialog(); });
});

// ---------- Settings page ----------

test("a sign-in app ID set by you says how to go back; the admin link is shown when it can't be copied", { skip }, async () => {
  await page().evaluate(() => {
    window.__settingsPatch = (s) => { s.client_id = { value: "11111111-2222-3333-4444-555555555555", source: "user" }; return s; };
    navigator.clipboard.writeText = () => Promise.reject(new Error("no clipboard"));
  });
  await openSettings();
  await page().getByText("Sign-in isn't using Microsoft's Fabric sign-in.", { exact: false }).first().waitFor();
  assert.match(await page().locator(".callout[role=note]").first().innerText(), /To go back, clear the ID and sign in again\./);
  await page().getByRole("button", { name: "Copy the link" }).click();
  await toastSays("https://login.microsoftonline.com/organizations/adminconsent?client_id=11111111-2222-3333-4444-555555555555");
  const from = app.calls.length;
  await page().getByRole("button", { name: "Use Microsoft's sign-in" }).click();
  await toastSays("Saved. Sign in again to use it.");
  assert.deepStrictEqual(saves(from), [{ client_id: "" }]);

  for (const [source, says] of [["policy", /Your organization set it, so only they can change it\./],
    ["environment", /setx FABRIC_DESKTOP_CLIENT_ID ""/], ["built_in", /It came with this build\./]]) {
    await page().evaluate((source) => {
      window.__settingsPatch = (s) => { s.client_id = { value: "11111111-2222-3333-4444-555555555555", source }; return s; };
      go("home");
    }, source);
    await openSettings();
    assert.match(await page().locator(".callout[role=note]").first().innerText(), says);
  }
});

test("Help and about shows the policy file's hash and how it changed, and what the organization turned off", { skip }, async () => {
  await page().evaluate(() => {
    window.__settingsPatch = (s) => Object.assign(s, {
      policy_record: { sha256: "ab".repeat(32), changed_at: "2026-11-18T08:00:00Z", managed: false, first: false },
      policy_problems: ["allowed_tenants: not a list"],
      allowed_tenants: { value: ["00000000-0000-0000-0000-000000000000"], source: "policy" },
      allow_fabric_runs: { value: false, source: "policy" },
      allow_signed_out_schedules: { value: false, source: "policy" },
    });
  });
  await openSettings();
  const about = page().locator("dl.about").last();
  await about.getByText("ab".repeat(32)).waitFor();
  const text = await about.innerText();
  assert.match(text, /Last changed .*, while the account signed in to this PC could change the file: not the way IT pushes it\./);
  assert.match(text, /Runs in Fabric\s+Off: your organization keeps work on this PC/);
  assert.match(text, /Schedules on this PC\s+Only while you're signed in to Windows/);
  assert.match(text, /Sign-in\s+No account, until the problems below are fixed/);
  assert.match(text, /Problems in your organization's settings\s+allowed_tenants: not a list/);
});

test("usage data that's on but can't be read says so; when read, See what's sent shows the waiting days or an example", { skip }, async () => {
  await page().evaluate(() => { window.__settingsPatch = (s) => { s.telemetry = { value: true, source: "user" }; return s; }; });
  await openSettings();
  await page().getByText("Usage data is on, but its status couldn't be read. Open Settings again to retry.").waitFor();

  const day = { day: "2026-11-20", features: { run_sql: { count: 3, errors: 1, seconds: 2 }, open_page: { count: 2, errors: 0, seconds: 1 } } };
  telemetry = { on: true, available: true, destination: "organization", waiting: [day], today: "2026-11-20", install_id: null, last_sent: null };
  await page().evaluate(() => go("home"));
  await openSettings();
  const card = page().locator("section.card", { has: page().locator("h2", { hasText: "Usage data" }) });
  assert.match(await card.innerText(), /Share usage data with your organization/);
  assert.match(await card.innerText(), /Waiting to send\s+1 day; today so far 5 actions, 1 failed/);
  await card.getByRole("button", { name: "See what's sent" }).click();
  await dialog().getByText("This is the exact text waiting to go to your organization's Application Insights.", { exact: false }).waitFor();
  assert.deepStrictEqual(JSON.parse(await dialog().getByLabel("Usage data").inputValue()), [day]);
  await close();

  telemetry = { ...telemetry, waiting: [], destination: "kratosbi", on: true };
  await page().evaluate(() => go("home"));
  await openSettings();
  await card.getByRole("button", { name: "See what's sent" }).click();
  await dialog().getByText("Nothing is waiting. This is an example of one day, in the exact shape that's sent.").waitFor();
  assert.strictEqual(JSON.parse(await dialog().getByLabel("Usage data").inputValue())[0].name, "daily_usage");
  await close();
});

test("each switch saves only its own change, and the update switches are kept on this PC", { skip }, async () => {
  await openSettings();
  const row = (title) => page().locator(".setting", { hasText: title });
  const flip = async (title) => {
    const from = app.calls.length;
    const box = row(title).locator("input[type=checkbox]").first();
    const was = await box.isChecked();
    await box.click();
    await page().waitForFunction(([title, was]) => {
      const r = [...document.querySelectorAll(".setting")].find((x) => x.textContent.includes(title));
      return r && r.querySelector("input[type=checkbox]").checked === !was;
    }, [title, was]);
    return { was, saved: saves(from) };
  };
  let r = await flip("Run notebooks in their own process");
  assert.deepStrictEqual(r.saved, [{ kernel_process: !r.was }]);
  r = await flip("Skip unchanged tables");
  assert.deepStrictEqual(r.saved, [{ onelake_skip_unchanged: !r.was }]);
  r = await flip("Warehouse changes");
  assert.deepStrictEqual(r.saved, [{ sql_warehouse_writes: !r.was }]);

  r = await flip("Show the forge after an update");
  assert.deepStrictEqual(r.saved, [], "kept on this PC, not in Settings");
  assert.strictEqual(await page().evaluate(() => localStorage.getItem(UPDATE_SPLASH_OFF)), r.was ? "1" : "0");
  r = await flip("Tell me when a new build is out");
  assert.deepStrictEqual(r.saved, []);
  assert.strictEqual(await page().evaluate(() => localStorage.getItem("fd.updatewatch.off")), r.was ? "1" : "0");
  await flip("Tell me when a new build is out");

  await page().locator(".advanced-group", { hasText: "SQL queries" }).getByRole("link", { name: "Learn more" }).click();
  assert.deepStrictEqual(await shellCalls("open_doc"), [{ page: "sql-execution.md" }]);

  r = await flip("Show features in preview");
  assert.deepStrictEqual(r.saved, [{ show_preview: !r.was }]);
  await flip("Show features in preview");
  assert.deepStrictEqual(app.errors, []);
});

// ---------- Ready to work and the command bar ----------

test("Ready to work offers each row's fix, and the write test says why it can't list workspaces", { skip }, async () => {
  readiness = [
    { key: "signin", title: "Signed in", status: "fail", text: "Not signed in.", fix: "sign_in" },
    { key: "workspaces", title: "Workspaces", status: "warn", text: "None you can write to.", fix: "workspaces" },
    { key: "capacity", title: "Capacity", status: "ok", text: "On F2.", fix: "capacity", doc: "capacity.md" },
    { key: "policy", title: "Policy", status: "warn", text: "Read with problems.", fix: "settings" },
    { key: "disk", title: "Disk", status: "warn", text: "Little room.", fix: "capacity", doc: "capacity.md" },
  ];
  await page().evaluate(() => { try { localStorage.removeItem("fd.ready.hidden"); localStorage.removeItem("fd.ready.write"); } catch { /* */ } go("workspaces"); go("home"); });
  const card = page().locator("section.ready");
  // Home may still be drawing from before this test's rows were set: Check again reads them for certain.
  await card.getByRole("button", { name: "Check again" }).first().click();
  await card.getByText("Little room.").waitFor();
  assert.strictEqual(await card.getByRole("button", { name: "Sign in" }).getAttribute("class"), "tool primary small", "a failing row's fix is the main button");
  await card.getByRole("button", { name: "How to get capacity" }).click();
  assert.deepStrictEqual(await shellCalls("open_doc"), [{ page: "capacity.md" }]);
  await card.getByRole("button", { name: "Open Workspaces" }).click();
  await page().waitForFunction(() => state.view === "workspaces");
  await page().evaluate(() => go("home"));
  await card.getByRole("button", { name: "Open Settings" }).click();
  await page().waitForFunction(() => state.view === "settings");

  readiness = [{ key: "capacity", title: "Capacity", status: "ok", text: "On F2." }];
  await page().evaluate(() => go("home"));
  await card.getByRole("button", { name: "Check again" }).first().click();
  await card.getByText("1 of 2 ready").waitFor();
  await card.getByRole("button", { name: "Test a write" }).click();
  await dialog().getByText("Fabric didn't answer (503).").waitFor();
  assert.ok(await dialog().getByRole("button", { name: "Test", exact: false }).last().isDisabled(), "nothing to test without a lakehouse");
  await dialog().getByRole("button", { name: "Cancel" }).click();
});

test("the command bar opens the new-name splash and its guide, the user guide and the keyboard shortcuts", { skip }, async () => {
  const run = async (label) => {
    await page().keyboard.press("Control+K");
    await page().getByRole("combobox", { name: "Command bar" }).fill(label);
    await page().locator("#palette-list").getByRole("option", { name: label, exact: true }).click();
  };
  await run("About the new name: KratosForge");
  await page().getByRole("link", { name: "What changed and what didn't" }).click();
  await page().waitForFunction(() => window.__fdShellCalls.some((c) => c.cmd === "open_doc"));
  assert.deepStrictEqual(await shellCalls("open_doc"), [{ page: "kratosforge.md" }]);
  await page().evaluate(() => closeDialog());
  await run("Open the user guide");
  await page().waitForFunction(() => window.__fdShellCalls.filter((c) => c.cmd === "open_doc").length === 2);
  assert.deepStrictEqual((await shellCalls("open_doc")).at(-1), { page: "user-guide.md" });
  await run("Keyboard shortcuts");
  await dialog().waitFor();
  assert.match(await dialog().getAttribute("aria-label"), /Keyboard shortcuts/i);
  await page().evaluate(() => closeDialog());
  await run("Open Settings");
  await page().waitForFunction(() => state.view === "settings");
});
