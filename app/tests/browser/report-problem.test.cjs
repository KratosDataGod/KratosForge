// Settings > Help and about > Report a problem (#1257): what the person types is redacted on Send and
// Copy only; Send goes to the support contact (mailto, https) or a new GitHub issue; Copy only opens
// nothing; the diagnostics box is read-only; and axe finds nothing serious in the dialog.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, axeSource, axeFindings } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const ID = "4b2f8c1e-9d3a-4e6f-8a7b-1c2d3e4f5a6b";
const TYPED = `Saving failed for raj@contoso.com in C:\\Users\\raj\\notebooks on workspace ${ID}, token=abc123secretvalue`;

let app;
const page = () => app.page;
const dialog = () => page().getByRole("dialog", { name: "Report a problem" });

before(async () => {
  if (skip) return;
  app = await openApp({ engine: ["app_settings"] });
  await page().waitForSelector("#rail-main .rail-item");
});

after(async () => { await app?.close(); });

// A clipboard the test can read, and no shell calls left from the test before.
beforeEach(async () => {
  if (skip) return;
  await page().evaluate(() => {
    window.__copied = [];
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (t) => { window.__copied.push(t); } } });
    window.__fdShellCalls.length = 0;
  });
});

async function open(support) {
  await page().evaluate(async (support) => {
    closeDialog();
    await loadSettings();
    state.settings = { ...(state.settings || {}), support_url: { value: support, source: "policy" } };
    reportProblem();
  }, support);
  await dialog().waitFor();
  await dialog().getByRole("textbox", { name: "What happened" }).fill(TYPED);
}

const copied = () => page().evaluate(() => window.__copied);
const links = () => page().evaluate(() => window.__fdShellCalls.filter((c) => c.cmd === "open_link").map((c) => c.args.url));

function assertRedacted(text) {
  assert.ok(text.startsWith("Saving failed for [email] in C:\\Users\\[user]\\notebooks on workspace [id], token=[removed]"), text);
  for (const leak of ["raj@contoso.com", "\\raj\\", ID, "abc123secretvalue"]) assert.ok(!text.includes(leak), `${leak} in ${text}`);
}

test("Send to a mailto contact copies the whole report and opens a short mail link, both redacted", { skip }, async () => {
  await open("mailto:help@contoso.com");
  await dialog().getByRole("button", { name: "Send" }).click();
  await dialog().waitFor({ state: "detached" });
  const [text] = await copied();
  assertRedacted(text);
  const [url] = await links();
  assert.ok(url.startsWith("mailto:help@contoso.com?subject=KratosForge%20problem&body="), url);
  const body = decodeURIComponent(url.split("&body=")[1]);
  assert.ok(body.length <= 1500 && text.startsWith(body), "the mail body is the start of the copied report");
  assertRedacted(body);
});

test("Send to an https contact opens it; with none it opens a new GitHub issue", { skip }, async () => {
  await open("https://support.contoso.com/new");
  await dialog().getByRole("button", { name: "Send" }).click();
  await dialog().waitFor({ state: "detached" });
  assertRedacted((await copied())[0]);
  assert.deepStrictEqual(await links(), ["https://support.contoso.com/new"]);
  await page().evaluate(() => { window.__fdShellCalls.length = 0; });
  await open("");
  assert.match(await dialog().textContent(), /opens a new issue on GitHub/);
  await dialog().getByRole("button", { name: "Send" }).click();
  await dialog().waitFor({ state: "detached" });
  assert.deepStrictEqual(await links(), ["https://github.com/KratosDataGod/KratosForge/issues/new"]);
});

test("Copy only copies the redacted report and opens nothing; the diagnostics can't be edited", { skip }, async () => {
  await open("mailto:help@contoso.com");
  const info = dialog().getByRole("textbox", { name: /^Sent with it/ });
  assert.strictEqual(await info.getAttribute("readonly"), "");
  await dialog().getByRole("button", { name: "Copy only" }).click();
  await page().waitForFunction(() => window.__copied.length === 1);
  assertRedacted((await copied())[0]);
  assert.deepStrictEqual(await links(), []);
  assert.strictEqual(await dialog().count(), 1, "Copy only leaves the dialog open");
  await dialog().getByRole("button", { name: "Cancel" }).click();
  await dialog().waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});

test("Report a problem has no serious axe findings", { skip: skip || (!axeSource && "axe-core is not installed") }, async () => {
  await page().emulateMedia({ reducedMotion: "reduce" });
  await open("mailto:help@contoso.com");
  const found = await axeFindings(page(), "#dialog-root .dialog");
  assert.deepStrictEqual(found, []);
});
