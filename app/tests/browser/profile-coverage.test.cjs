// Profile (profile.js), the paths profile, profile-actions and profile-impact-goals leave out: a notice
// and its Dismiss, recording off with nothing recorded, progression off on a progression tab, the
// picture (choose one, a wrong type, too large, unreadable, remove), the top rank and nothing left to
// earn, goals at the limit, a roadmap milestone made a goal, Copy summary that can't copy, turning
// progression off in Settings, and the palette's three Profile commands. The profile comes from the
// real engine code (progress_service.py) run once in a temp folder, then changed per test.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const BUILD = `
import json, sys, tempfile
from pathlib import Path
from fabric_desktop.service import Service
class Auth:
    def signed_in_user(self): return "dev@contoso.com"
s = Service(client_id="x", root=Path(tempfile.mkdtemp()) / "app")
s._auth = Auth()
s.profile_save(settings={"record": True}, identity={"display_name": "Chris", "role": "Data engineer"})
s._progress_observe({"id": "validate-20261020-104212-a1b2c3", "name": "sales_daily_load", "verdict": "ready",
    "run": {"status": "ok"}, "finished_at": "2026-10-20T10:42:12+00:00",
    "code": {"revision": "3f9c1e0d2b7a" + "0" * 52}, "tables": {"reads": ["a", "b"], "writes": ["c"]}})
json.dump({"profile": s.profile_get(), "report": s.profile_report()}, sys.stdout)
`;
// A 1x1 PNG.
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");
const AVATAR = `data:image/png;base64,${PNG.toString("base64")}`;

let app;
let built;
const db = { profile: null };
const page = () => app.page;
const sent = (m) => app.calls.filter((c) => c.method === m).map((c) => c.params);
const toastSays = (re) => page().waitForFunction((src) => new RegExp(src).test(document.getElementById("toast")?.textContent || ""), re.source);
const tab = (name) => page().getByRole("tab", { name, exact: true });

before(async () => {
  if (skip) return;
  const python = process.env.FABRIC_DESKTOP_PYTHON || (process.platform === "win32" ? "python" : "python3");
  const out = spawnSync(python, ["-c", BUILD], { cwd: path.join(__dirname, "..", "..", "..", "sidecar"), encoding: "utf8" });
  assert.strictEqual(out.status, 0, out.stderr);
  built = JSON.parse(out.stdout);
  db.profile = structuredClone(built.profile);
  app = await openApp({ mocks: {
    profile_get: () => db.profile,
    profile_report: () => built.report,
    profile_avatar: () => ({ avatar: db.profile.avatar }),
    profile_save: (p) => {
      if (p.dismiss_notice) db.profile.notice = null;
      Object.assign(db.profile.settings, p.settings || {});
      return db.profile;
    },
    profile_avatar_set: (p) => { db.profile.avatar = `data:image/png;base64,${p.png_base64}`; return db.profile; },
    profile_avatar_remove: () => { db.profile.avatar = null; return db.profile; },
    profile_goal_add: (p) => { db.profile.roadmap.goals.push({ id: "g1", title: p.milestone, source: "roadmap", met: false, checks: [], track: "" }); return db.profile; },
  } });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  db.profile = structuredClone(built.profile);
  await page().evaluate(() => {
    closeDialog(); closeDialog();
    profilePage.report = null;
    const t = document.getElementById("toast"); t.hidden = true; t.textContent = "";
  });
  app.calls.length = 0;
  app.errors.length = 0;
});

const open = async (tabId) => {
  await page().evaluate((t) => { profilePage.tab = t; go("profile"); }, tabId);
  await page().locator(".page.profile").waitFor();
};

test("a notice shows until Dismiss; with recording off and nothing recorded, Turn on starts it again", { skip }, async () => {
  db.profile.notice = "Your profile file couldn't be read, so a new one was started.";
  db.profile.settings.record = false;
  db.profile.evidence = [];
  await open("overview");
  const notice = page().getByRole("alert").filter({ hasText: "couldn't be read, so a new one was started" });
  await notice.waitFor();
  await page().getByRole("heading", { name: "Recording is off" }).waitFor();
  await notice.getByRole("button", { name: "Dismiss" }).click();
  await notice.waitFor({ state: "detached" });
  await page().getByRole("button", { name: "Turn on" }).click();
  await toastSays(/^Recording is on$/);
  assert.deepStrictEqual(sent("profile_save"), [{ dismiss_notice: true }, { settings: { record: true } }]);
  assert.deepStrictEqual(app.errors, []);
});

test("with progression off, a progression tab opens on Overview and its tabs are gone", { skip }, async () => {
  db.profile.settings.progression = false;
  await open("ranks");
  await page().getByText("Validated notebook sales_daily_load").waitFor();
  assert.strictEqual(await tab("Overview").getAttribute("aria-selected"), "true");
  for (const name of ["Journey", "Ranks", "Achievements"]) assert.strictEqual(await tab(name).count(), 0, name);
  assert.deepStrictEqual(app.errors, []);
});

test("Choose picture saves a 256 px square; a wrong type, a large file and an unreadable one are refused", { skip }, async () => {
  await open("overview");
  await page().getByRole("button", { name: "Edit profile" }).first().click();
  const chooser = page().waitForEvent("filechooser");
  await page().getByRole("dialog", { name: "Edit profile" }).getByRole("button", { name: "Choose picture" }).click();
  await (await chooser).setFiles({ name: "me.png", mimeType: "image/png", buffer: PNG });
  await toastSays(/^Picture saved$/);
  const saved = sent("profile_avatar_set")[0].png_base64;
  const size = Buffer.from(saved, "base64");
  assert.deepStrictEqual([size.readUInt32BE(16), size.readUInt32BE(20)], [256, 256]);
  assert.strictEqual(await page().evaluate(() => state.avatar), `data:image/png;base64,${saved}`);
  const choose = async (file) => {
    const c = page().waitForEvent("filechooser");
    await page().evaluate(() => { profileChoosePicture(); });
    await (await c).setFiles(file);
  };
  await choose({ name: "me.gif", mimeType: "image/gif", buffer: PNG });
  await toastSays(/^Choose a PNG, JPEG or WebP picture\.$/);
  await choose({ name: "big.png", mimeType: "image/png", buffer: Buffer.alloc(5 * 1024 * 1024 + 1) });
  await toastSays(/^Choose a picture of 5 MB or less\.$/);
  await choose({ name: "broken.png", mimeType: "image/png", buffer: Buffer.from("not a picture") });
  await toastSays(/^That picture couldn't be read\. Try a PNG or JPEG\.$/);
  assert.strictEqual(sent("profile_avatar_set").length, 1);
  assert.deepStrictEqual(app.errors, []);
});

test("Remove picture removes it", { skip }, async () => {
  db.profile.avatar = AVATAR;
  await open("overview");
  await page().locator(".profile-id img.profile-avatar").waitFor();
  await page().getByRole("button", { name: "Edit profile" }).first().click();
  await page().getByRole("dialog", { name: "Edit profile" }).getByRole("button", { name: "Remove picture" }).click();
  await toastSays(/^Picture removed$/);
  await page().locator(".profile-id img.profile-avatar").waitFor({ state: "detached" });
  assert.strictEqual(await page().evaluate(() => state.avatar), null);
  assert.strictEqual(sent("profile_avatar_remove").length, 1);
  assert.deepStrictEqual(app.errors, []);
});

test("at the top rank with nothing left to earn, Overview and Journey say so", { skip }, async () => {
  db.profile.ranks.next = null;
  db.profile.progression.next = [];
  db.profile.progression.milestones = [];
  await open("overview");
  await page().getByText("You hold every rank there is.").waitFor();
  await tab("Journey").click();
  await page().getByText("You've earned every achievement there is so far.").waitFor();
  await page().getByText("None yet. Your first validated notebook earns First Flame.").waitFor();
  assert.deepStrictEqual(app.errors, []);
});

test("at the goal limit Add goal is off and says why; below it a roadmap milestone becomes a goal", { skip }, async () => {
  const max = db.profile.roadmap.max_goals;
  db.profile.roadmap.goals = Array.from({ length: max }, (_, i) => ({ id: `e${i}`, title: `Goal ${i}`, source: "entered", met: false, checks: [] }));
  await open("goals");
  const add = page().getByRole("button", { name: "Add goal" });
  await add.waitFor();
  assert.ok(await add.isDisabled());
  assert.strictEqual(await add.getAttribute("title"), `Up to ${max} goals: remove one first`);
  db.profile.roadmap.goals = [];
  await open("goals");
  const row = page().locator(".profile-rank:not(.held)").filter({ has: page().getByRole("button", { name: "Make it a goal" }) }).first();
  const title = await row.locator("b").first().innerText();
  const id = db.profile.roadmap.tracks.flatMap((t) => t.milestones).find((m) => m.title === title).id;
  await row.getByRole("button", { name: "Make it a goal" }).click();
  await toastSays(/^Goal added$/);
  assert.deepStrictEqual(sent("profile_goal_add"), [{ milestone: id }]);
  assert.deepStrictEqual(app.errors, []);
});

test("Copy summary that the clipboard refuses says so", { skip }, async () => {
  await page().evaluate(() => {
    Object.defineProperty(navigator, "clipboard", { value: { writeText: async () => { throw new Error("denied"); } }, configurable: true });
  });
  await open("report");
  await page().locator(".profile-report h2").first().waitFor();
  await page().getByRole("button", { name: "Copy summary" }).click();
  await toastSays(/^Couldn't copy the summary\.$/);
  assert.deepStrictEqual(app.errors, []);
});

test("turning Developer progression off in Settings takes its tabs away", { skip }, async () => {
  await open("settings");
  await tab("Journey").waitFor();
  const row = page().locator(".setting", { hasText: "Developer progression" });
  await row.locator("input[type=checkbox]").uncheck();
  await tab("Journey").waitFor({ state: "detached" });
  assert.deepStrictEqual(sent("profile_save"), [{ settings: { progression: false } }]);
  assert.deepStrictEqual(app.errors, []);
});

test("the palette's Profile, Development goals and Professional report open those tabs", { skip }, async () => {
  for (const [label, name] of [["Development goals", "Goals"], ["Professional report", "Professional report"], ["Profile", "Overview"]]) {
    await page().evaluate((l) => features.commands.find((c) => c.label === l).run(), label);
    await page().waitForFunction((n) => document.querySelector('.profile [role=tab][aria-selected="true"]')?.textContent === n, name);
  }
  assert.deepStrictEqual(app.errors, []);
});
