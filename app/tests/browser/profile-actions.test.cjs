// #1460: the write actions on the Profile page (app/ui/profile.js), driven against the real engine (see
// harness.cjs for how to run it). Every profile_* call goes to python -m fabric_desktop.server, so what is
// sent, what the engine refuses and what it writes on disk are the engine's own (progress_service.py,
// progress.py). Before each test the engine's folder is reset to one profile with two validated notebooks
// (FabricDesktop/profile/signed-out/profile.json) and one older validation it hasn't recorded
// (FabricDesktop/validations.json, validate_service.py), so Add them has one to add.
// Covered: Edit profile > Save sends exactly the fields entered; Exclude and Include send that row's id
// and the new state; Save summary and Save Markdown send exactly the summary, period and achievements
// choice shown, and the file holds the report shown; Add them sends profile_backfill once and shows the
// row added; Export sends profile_export and says where the file went; Delete my profile data asks in a
// danger confirm (Cancel sends nothing, confirming sends profile_delete once); the engine's refusals
// read as plain sentences; opening every tab sends no write; axe finds nothing serious; no page errors.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { playwright, openApp, startupReads, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
const READS = ["profile_get", "profile_report"];
const WRITES = ["profile_save", "profile_exclude", "profile_report_save", "profile_backfill", "profile_export", "profile_delete"];
const SIDECAR = path.join(__dirname, "..", "..", "..", "sidecar");
const python = process.env.FABRIC_DESKTOP_PYTHON || (process.platform === "win32" ? "python" : "python3");

// The engine's folder (server.py: %LOCALAPPDATA%\FabricDesktop) and the signed-out profile in it
// (progress.path_for: no account signs in here, so the profile is "signed-out").
const root = () => path.join(app.engineHome, "FabricDesktop");
const profileFile = () => path.join(root(), "profile", "signed-out", "profile.json");
const downloads = () => path.join(app.engineHome, "Downloads");  // progress_service._save_new: Path.home() / "Downloads"
const onDisk = () => JSON.parse(fs.readFileSync(profileFile(), "utf8"));

// Builds the profile with the engine's own code, as validations that finished Ready record it.
const SEED = `
import json, sys
from pathlib import Path
from fabric_desktop.service import Service
from fabric_desktop import progress
root = Path(sys.argv[1])
s = Service(client_id="x", root=root)
s._profile_user = lambda: None
s.profile_save(settings={"record": True}, identity={"display_name": "Chris", "role": "Data engineer", "technologies": "Python, SQL"})
def run(id, name, rev, at):
    return {"id": id, "name": name, "verdict": "ready", "run": {"status": "ok"}, "finished_at": at,
            "code": {"revision": rev * 64}, "tables": {"reads": ["a", "b"], "writes": ["c"]}}
recorded = [run("validate-20261020-104212-a1b2c3", "sales_daily_load", "3", "2026-10-20T10:42:12+00:00"),
            run("validate-20261021-091500-b2c3d4", "customers_merge", "4", "2026-10-21T09:15:00+00:00")]
for r in recorded:
    s._progress_observe(r)
older = run("validate-20260901-080000-c3d4e5", "orders_hourly", "5", "2026-09-01T08:00:00+00:00")
(root / "validations.json").write_text(json.dumps(recorded + [older]), encoding="utf-8")
json.dump([r["id"] for r in progress.load(progress.path_for(root, None))["evidence"]], sys.stdout)
`;

function reseed() {
  fs.rmSync(path.join(root(), "profile"), { recursive: true, force: true });
  fs.rmSync(path.join(root(), "validations.json"), { force: true });
  fs.rmSync(downloads(), { recursive: true, force: true });
  const out = spawnSync(python, ["-c", SEED, root()], { cwd: SIDECAR, encoding: "utf8",
    env: { ...process.env, LOCALAPPDATA: app.engineHome, HOME: app.engineHome, USERPROFILE: app.engineHome } });
  assert.strictEqual(out.status, 0, out.stderr);
  return JSON.parse(out.stdout);
}

let baseline;
before(async () => {
  if (skip) return;
  app = await openApp({ engine: [...READS, ...WRITES] });
  // The app's startup reads, and its first approval polls about 5 s in, so neither is counted below.
  await startupReads(app);
  for (const end = Date.now() + 15000; !["integrations_pending", "editor_pending"].every((m) => app.calls.some((c) => c.method === m));
    await new Promise((r) => setTimeout(r, 50))) {
    if (Date.now() > end) throw new Error("the app's startup polls never came");
  }
  baseline = new Set(app.calls.map((c) => c.method));
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  reseed();
  await page().evaluate(() => {
    closeMenu(); closeDialog(); const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
    Object.assign(profilePage, { tab: "overview", days: 90, achievements: false, report: null }); go("home");
  });
});

const writes = (from) => app.calls.slice(from).filter((c) => WRITES.includes(c.method)).map((c) => ({ method: c.method, params: c.params }));
const pause = (ms) => new Promise((r) => setTimeout(r, ms));

// Opens Profile on a tab and waits for it to draw from the engine's answer.
async function openProfile(tab, ready) {
  await page().evaluate((tab) => { profilePage.tab = tab; profilePage.report = null; go("home"); go("profile"); }, tab);
  await page().locator(".page.profile").waitFor();
  if (ready) await page().getByText(ready, { exact: false }).first().waitFor();
}
const toastNow = async (want) => (await page().waitForFunction((want) => {
  const t = document.getElementById("toast");
  return !t.hidden && (!want || t.textContent.includes(want)) && t.textContent;
}, want)).jsonValue();
// A row by its notebook name, in the bold title (no handler of its own).
const row = (name) => page().locator(".profile-row", { has: page().locator("b", { hasText: `Validated notebook ${name}` }) });

test("Edit profile > Save sends exactly the fields entered, and the page and the file show them", { skip }, async () => {
  await openProfile("overview", "Validated notebook sales_daily_load");
  const from = app.calls.length;
  await page().getByRole("button", { name: "Edit profile" }).click();
  const dialog = page().getByRole("dialog", { name: "Edit profile" });
  await dialog.getByRole("textbox", { name: "Display name" }).fill("  Chris Moreno ");
  await dialog.getByRole("textbox", { name: "Role or specialization" }).fill("Analytics engineer");
  await dialog.getByRole("textbox", { name: "Preferred technologies" }).fill("");
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await dialog.waitFor({ state: "detached" });
  await page().locator(".page.profile h1", { hasText: "Chris Moreno" }).waitFor();
  assert.deepStrictEqual(writes(from), [{ method: "profile_save",
    params: { identity: { display_name: "Chris Moreno", role: "Analytics engineer", technologies: "" } } }]);
  assert.match(await page().locator(".page.profile .profile-id .sub").innerText(), /^Analytics engineer · Not signed in/);
  assert.deepStrictEqual(onDisk().identity, { display_name: "Chris Moreno", role: "Analytics engineer", technologies: "" });
  assert.strictEqual(await toastNow("Saved"), "Saved");
});

test("Edit profile > Cancel sends nothing", { skip }, async () => {
  await openProfile("overview", "Validated notebook sales_daily_load");
  const from = app.calls.length;
  await page().getByRole("button", { name: "Edit profile" }).click();
  const dialog = page().getByRole("dialog", { name: "Edit profile" });
  await dialog.getByRole("textbox", { name: "Display name" }).fill("Someone else");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await dialog.waitFor({ state: "detached" });
  await pause(300);
  assert.deepStrictEqual(writes(from), []);
  assert.strictEqual(onDisk().identity.display_name, "Chris");
});

test("Exclude sends that row's id with excluded true, Include sends it with false", { skip }, async () => {
  const ids = reseed();  // in the order recorded: sales_daily_load, then customers_merge
  await openProfile("overview", "Validated notebook sales_daily_load");
  const from = app.calls.length;
  await row("sales_daily_load").getByRole("button", { name: "Exclude" }).click();
  await row("sales_daily_load").locator(".pill-s", { hasText: "Excluded" }).waitFor();
  assert.deepStrictEqual(writes(from), [{ method: "profile_exclude", params: { id: ids[0], excluded: true } }]);
  assert.strictEqual(await row("customers_merge").locator(".pill-s", { hasText: "Verified" }).count(), 1, "the other row is untouched");
  assert.match(await page().locator(".page.profile h2", { hasText: "Recorded accomplishments" }).innerText(), /1 verified/);
  assert.deepStrictEqual(onDisk().excluded, [ids[0]]);
  const mid = app.calls.length;
  await row("sales_daily_load").getByRole("button", { name: "Include" }).click();
  await row("sales_daily_load").locator(".pill-s", { hasText: "Verified" }).waitFor();
  assert.deepStrictEqual(writes(mid), [{ method: "profile_exclude", params: { id: ids[0], excluded: false } }]);
  assert.deepStrictEqual(onDisk().excluded, []);
});

test("Save summary sends exactly the summary typed; Save Markdown sends the period and choice shown and saves the report shown", { skip }, async () => {
  await openProfile("report", "Validated notebook sales_daily_load");
  const from = app.calls.length;
  await page().getByRole("textbox", { name: "Executive summary (optional)" }).fill("Moved the sales loads to Fabric.");
  await page().getByRole("button", { name: "Save summary" }).click();
  await page().locator(".profile-report", { hasText: "Moved the sales loads to Fabric." }).waitFor();
  assert.deepStrictEqual(writes(from), [{ method: "profile_save", params: { report_summary: "Moved the sales loads to Fabric." } }]);
  assert.strictEqual(onDisk().report.summary, "Moved the sales loads to Fabric.");
  // A different period and the achievements box, as shown.
  await page().getByRole("combobox", { name: "Reporting period" }).selectOption("365");
  await page().getByRole("checkbox", { name: "Include KratosForge achievements" }).check();
  await page().waitForFunction(() => profilePage.report && profilePage.days === 365 && profilePage.achievements);
  const shown = await page().evaluate(() => profilePage.report.markdown);
  assert.match(shown, /Moved the sales loads to Fabric\./);
  assert.match(await page().locator(".profile-report").innerText(), /First Flame/);
  const mid = app.calls.length;
  await page().getByRole("button", { name: "Save Markdown" }).click();
  const said = await toastNow("Saved ");
  assert.deepStrictEqual(writes(mid), [{ method: "profile_report_save", params: { days: 365, include_achievements: true } }]);
  const file = path.join(downloads(), "accomplishments.md");
  assert.strictEqual(said, `Saved ${file}`);
  assert.strictEqual(fs.readFileSync(file, "utf8"), shown);
});

test("Add them sends profile_backfill once and shows the validation it added", { skip }, async () => {
  await openProfile("settings", "Include past validations");
  const from = app.calls.length;
  await page().getByRole("button", { name: "Add them" }).click();
  assert.strictEqual(await toastNow("Added"), "Added 1 past validation");
  await page().getByText("Include past validations").waitFor({ state: "detached" });
  assert.deepStrictEqual(writes(from), [{ method: "profile_backfill", params: {} }]);
  assert.strictEqual(onDisk().settings.backfilled, true);
  await page().getByRole("tab", { name: "Overview" }).click();
  const added = row("orders_hourly");
  await added.waitFor();
  assert.strictEqual(await added.locator(".pill-s", { hasText: "Added from history" }).count(), 1);
  assert.strictEqual(await page().locator(".profile-row .pill-s", { hasText: "Added from history" }).count(), 1);
  assert.deepStrictEqual(writes(from), [{ method: "profile_backfill", params: {} }], "still once");
});

test("Export sends profile_export and says where the file went", { skip }, async () => {
  await openProfile("settings", "Your data");
  const from = app.calls.length;
  await page().getByRole("button", { name: "Export my profile" }).click();
  const file = path.join(downloads(), "kratosforge-profile.json");
  assert.strictEqual(await toastNow("Saved "), `Saved ${file}`);
  assert.deepStrictEqual(writes(from), [{ method: "profile_export", params: {} }]);
  assert.deepStrictEqual(JSON.parse(fs.readFileSync(file, "utf8")), onDisk());
});

test("Delete my profile data asks in a danger confirm; Cancel sends nothing", { skip }, async () => {
  await openProfile("settings", "Your data");
  const from = app.calls.length;
  await page().getByRole("button", { name: "Delete my profile data" }).click();
  const dialog = page().getByRole("dialog", { name: "Delete your profile data?" });
  await dialog.waitFor();
  assert.match(await dialog.innerText(), /It can't be undone\./);
  const ok = dialog.getByRole("button", { name: "Delete", exact: true });
  assert.match(await ok.getAttribute("class"), /danger-solid/);
  assert.ok(await ok.isDisabled(), "Delete waits for DELETE to be typed");
  await dialog.getByRole("textbox", { name: "Type DELETE to confirm" }).fill("DELETE");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await dialog.waitFor({ state: "detached" });
  await pause(300);
  assert.deepStrictEqual(writes(from), []);
  assert.ok(fs.existsSync(profileFile()));
});

test("confirming Delete sends profile_delete once and the profile starts empty", { skip }, async () => {
  await openProfile("settings", "Your data");
  const from = app.calls.length;
  await page().getByRole("button", { name: "Delete my profile data" }).click();
  const dialog = page().getByRole("dialog", { name: "Delete your profile data?" });
  await dialog.getByRole("textbox", { name: "Type DELETE to confirm" }).fill("DELETE");
  await dialog.getByRole("button", { name: "Delete", exact: true }).click();
  assert.strictEqual(await toastNow("deleted"), "Your profile data was deleted");
  await page().locator(".page.profile h1", { hasText: "Your profile" }).waitFor();
  await page().getByText("Nothing yet. Validate a notebook", { exact: false }).waitFor();
  assert.deepStrictEqual(writes(from), [{ method: "profile_delete", params: {} }]);
  assert.ok(!fs.existsSync(path.dirname(profileFile())), "the profile folder is gone");
  assert.ok(fs.existsSync(path.join(root(), "validations.json")), "Validate history stays");
});

// The engine's refusals, each made real on disk between drawing the page and the click. The engine
// writes the home folder as ~ in what it says (server.py), so Downloads reads as ~/Downloads.
const AT_DOWNLOADS = `Something already exists at ~${path.sep}Downloads on this PC. Pick another name or folder.`;
const REFUSALS = [
  { name: "an accomplishment gone from the profile", tab: "overview", ready: "Validated notebook sales_daily_load",
    break: () => { const p = onDisk(); p.evidence = p.evidence.filter((r) => r.artifact.name !== "sales_daily_load"); fs.writeFileSync(profileFile(), JSON.stringify(p)); },
    click: () => row("sales_daily_load").getByRole("button", { name: "Exclude" }).click(),
    method: "profile_exclude", says: "Could not change the accomplishment: That accomplishment isn't in your profile." },
  { name: "past validations with recording turned off", tab: "settings", ready: "Include past validations",
    break: () => { const p = onDisk(); p.settings.record = false; fs.writeFileSync(profileFile(), JSON.stringify(p)); },
    click: () => page().getByRole("button", { name: "Add them" }).click(),
    method: "profile_backfill", says: "Could not add past validations: Turn on Record my accomplishments first." },
  { name: "an export when Downloads is a file", tab: "settings", ready: "Your data",
    break: () => fs.writeFileSync(downloads(), "not a folder"),
    click: () => page().getByRole("button", { name: "Export my profile" }).click(),
    method: "profile_export", says: `Could not export your profile: ${AT_DOWNLOADS}` },
  { name: "a report saved when Downloads is a file", tab: "report", ready: "Validated notebook sales_daily_load",
    break: () => fs.writeFileSync(downloads(), "not a folder"),
    click: () => page().getByRole("button", { name: "Save Markdown" }).click(),
    method: "profile_report_save", says: `Could not save the report: ${AT_DOWNLOADS}` },
];
for (const r of REFUSALS) {
  test(`the engine refusing ${r.name} shows a plain message`, { skip }, async () => {
    await openProfile(r.tab, r.ready);
    r.break();
    const from = app.calls.length;
    await r.click();
    const said = await toastNow("Could not");
    assert.strictEqual(said.split("\n")[0], r.says);
    assert.doesNotMatch(said, /Error:|\[object Object\]|Errno|Traceback/);
    assert.deepStrictEqual(writes(from).map((c) => c.method), [r.method]);
  });
}

test("opening every tab of Profile sends no write", { skip }, async () => {
  const from = app.calls.length;
  await openProfile("overview", "Validated notebook sales_daily_load");
  for (const [tab, ready] of [["Journey", "Recent milestones"], ["Achievements", "How to earn it"],
    ["Professional report", "Executive summary"], ["Settings", "Your data"], ["Overview", "Recorded accomplishments"]]) {
    await page().getByRole("tab", { name: tab }).click();
    await page().getByText(ready, { exact: false }).first().waitFor();
  }
  await page().getByRole("tab", { name: "Professional report" }).click();
  await page().locator(".profile-report", { hasText: "sales_daily_load" }).waitFor();
  await pause(300);
  const made = app.calls.slice(from).map((c) => c.method);
  for (const m of READS) assert.ok(made.includes(m), `${m} was read`);
  assert.deepStrictEqual(made.filter((m) => WRITES.includes(m)), []);
  assert.deepStrictEqual([...new Set(made.filter((m) => !READS.includes(m) && !baseline.has(m)))], []);
});

test("axe finds nothing serious on Overview, the report, Settings and the delete confirm", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  const check = async (where, selector) => assert.deepStrictEqual(await axeFindings(page(), selector), [], where);
  await openProfile("overview", "Validated notebook sales_daily_load");
  await check("overview", ".page.profile");
  await openProfile("report", "Validated notebook sales_daily_load");
  await check("report", ".page.profile");
  await openProfile("settings", "Your data");
  await check("settings", ".page.profile");
  await page().getByRole("button", { name: "Delete my profile data" }).click();
  await page().getByRole("dialog", { name: "Delete your profile data?" }).waitFor();
  await check("delete confirm", "#dialog-root .dialog");
});

test("no page errors", { skip }, () => assert.deepStrictEqual(app.errors, []));
