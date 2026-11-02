// Source control when Git can't be read (found by the keyboard pass, #1690): a failed git_info shows why,
// with a Retry, in place of "Loading…", which otherwise stayed on the page for good with nothing to Tab to.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp, axeSource, axeFindings, settleForAxe } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
let app;
const page = () => app.page;
// git/cli.py's refusal when git.exe won't start (GitError, kind no_git); git_service.py git_info's shape (kinds from git/scopes.py KINDS), answered once the refusal is retried.
const KIND = (label) => ({ label, for: "One per team or project.", holds: "Its definitions." });
const INFO = { installed: true, version: "2.47.0", repos: [], current: null, notebooks_folder: "C:/Users/me/notebooks",
  clone_folder: "C:/Users/me/source/repos",
  kinds: { notebooks: KIND("Notebooks"), project: KIND("Data mesh project"), platform: KIND("Platform"), capacity: KIND("Capacity") } };
let failing = true;

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    git_info: () => { if (failing) throw new Error("GitError: Git couldn't start: [WinError 5] Access is denied"); return INFO; },
  } });
});
after(async () => { if (app) await app.close(); });

test("a failed git_info replaces Loading… with why, and Retry loads the page", { skip }, async () => {
  await page().evaluate(() => go("source"));
  const panel = page().locator("#view .error-panel");
  await panel.waitFor();
  assert.strictEqual(await page().locator("#view").getByText("Loading…").count(), 0, "no endless Loading…");
  const text = await panel.innerText();
  assert.match(text, /Couldn't read Git on this PC/);
  assert.match(text, /Git couldn't start: \[WinError 5\] Access is denied/);
  assert.doesNotMatch(text, /GitError/);
  const retry = panel.getByRole("button", { name: "Retry" });
  await retry.focus();
  assert.strictEqual(await page().evaluate(() => document.activeElement?.textContent.trim()), "Retry", "Retry is a Tab stop");
  if (axeSource) { await settleForAxe(page()); assert.deepStrictEqual(await axeFindings(page(), "#view"), []); }
  failing = false;
  await page().keyboard.press("Enter");
  await page().getByRole("heading", { name: "Start a repository for one kind of definition" }).waitFor();
  assert.strictEqual(await page().locator("#view .error-panel").count(), 0);
  assert.deepStrictEqual(app.errors, []);
});
