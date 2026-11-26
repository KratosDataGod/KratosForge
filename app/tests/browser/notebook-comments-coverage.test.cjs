// Cell comments (notebook-comments.js), the paths notebook-comments leaves out: the ribbon's
// Comments button opening and closing the panel, "Loading comments..." while the file is read, a
// cell's badge opening the panel on that cell only, Ctrl+Enter sending a reply, deleting a whole
// thread naming how many comments go with it, and the palette's Comment on the selected cell.
// The engine's replies are mocked; what the person sees and what was sent is checked.
const { test, before, after, beforeEach } = require("node:test");
const assert = require("node:assert");
const { playwright, openApp } = require("./harness.cjs");

const skip = !playwright && "Playwright is not installed";
const CELLS = [
  { type: "code", cell_id: "c1", source: "a = 1", meta: {} },
  { type: "code", cell_id: "c2", source: "b = 2", meta: {} },
];
const ME = { name: "Chris", id: "u1" };
const THREADS = [
  { id: "t1", cell_id: "c1", resolved: false, comments: [
    { id: "k1", text: "Why 1?", author: "Chris", author_id: "u1", at: "2026-11-01T09:00:00Z" },
    { id: "k2", text: "Because.", author: "Ana", author_id: "u2", at: "2026-11-01T09:05:00Z" }] },
  { id: "t2", cell_id: "c2", resolved: false, comments: [{ id: "k3", text: "Rename b", author: "Ana", author_id: "u2", at: "2026-11-01T10:00:00Z" }] },
];

// Set before the app opens; a test holds the list back by putting a promise in `hold`.
const db = { hold: null };
let app;
const page = () => app.page;
const pane = () => page().locator(".commentspane");
const sent = (m) => app.calls.filter((c) => c.method === m).map((c) => c.params);

before(async () => {
  if (skip) return;
  app = await openApp({ mocks: {
    save_notebook: () => ({ saved: true }),
    notebook_comments_list: async () => { if (db.hold) await db.hold; return { threads: structuredClone(THREADS), me: ME }; },
    notebook_comment_reply: (p) => ({ threads: THREADS.map((t) => (t.id === p.thread_id ? { ...t, comments: [...t.comments,
      { id: "k9", text: p.text, author: "Chris", author_id: "u1", at: "2026-11-02T09:00:00Z" }] } : t)), me: ME }),
    notebook_comment_delete: () => ({ threads: [], me: ME }),
  } });
  app.errors.length = 0;
});
after(async () => { if (app) await app.close(); });

beforeEach(async () => {
  if (skip) return;
  db.hold = null;
  await page().evaluate((cells) => {
    closeDialog(); closeDialog();
    loadIntoState({ name: "Review", cells: structuredClone(cells) });
    state.ribbonTab = "view";
    go("notebook");
  }, CELLS);
  await page().locator("[data-cell]").nth(1).waitFor();
  app.calls.length = 0;
  app.errors.length = 0;
});

test("the Comments button opens the panel, which says it's loading until the file is read, and closes it", { skip }, async () => {
  let release;
  db.hold = new Promise((r) => { release = r; });
  await page().evaluate(() => { delete state.notebook.comments; renderNotebook(); });
  const toggle = page().getByRole("button", { name: "Comments", exact: true });
  await toggle.click();
  await pane().getByText("Loading comments...").waitFor();
  assert.strictEqual(await toggle.getAttribute("aria-pressed"), "true");
  release();
  await pane().locator(".comment-thread").first().waitFor();
  assert.strictEqual(await pane().locator(".comment-thread").count(), 2);
  await page().getByRole("button", { name: "Comments", exact: true }).click();
  await pane().waitFor({ state: "detached" });
  assert.deepStrictEqual(app.errors, []);
});

test("a cell's badge opens the panel on that cell only; Show all lists every thread", { skip }, async () => {
  const badge = page().locator("[data-cell]").nth(1).locator(".comment-badge");
  await badge.waitFor();
  assert.strictEqual(await badge.getAttribute("aria-label"), "Comments on this cell: 1 open");
  await badge.click();
  await pane().getByText("Cell 2 only").waitFor();
  assert.deepStrictEqual(await pane().locator(".comment-text").allInnerTexts(), ["Rename b"]);
  await pane().getByRole("button", { name: "Show all" }).click();
  await page().waitForFunction(() => document.querySelectorAll(".commentspane .comment-thread").length === 2);
  assert.deepStrictEqual(app.errors, []);
});

test("Ctrl+Enter sends a reply; deleting a whole thread says how many comments go with it", { skip }, async () => {
  await page().evaluate(() => openComments(state.notebook));
  const t1 = pane().locator('[data-thread="t1"]');
  await t1.getByRole("button", { name: "Reply" }).click();
  await page().locator(".dialog textarea").fill("Fixed it");
  await page().locator(".dialog textarea").press("Control+Enter");
  await t1.getByText("Fixed it").waitFor();
  assert.deepStrictEqual(sent("notebook_comment_reply"), [{ name: "Review", thread_id: "t1", text: "Fixed it" }]);
  await t1.locator(".comment").first().getByRole("button", { name: "Delete" }).click();
  const confirm = page().getByRole("dialog", { name: "Delete this thread?" });
  assert.match(await confirm.innerText(), /The thread and its 3 comments are removed from the notebook file\./);
  await confirm.getByRole("button", { name: "Delete" }).click();
  await pane().getByText("No open comments.", { exact: false }).waitFor();
  assert.deepStrictEqual(sent("notebook_comment_delete"), [{ name: "Review", thread_id: "t1", comment_id: "k1" }]);
  assert.deepStrictEqual(app.errors, []);
});

test("the palette's Comment on the selected cell asks for a comment on that cell", { skip }, async () => {
  await page().locator("[data-cell]").nth(1).locator(".gutter").click({ position: { x: 10, y: 40 } });
  await page().evaluate(() => { features.commands.find((c) => c.label === "Comment on the selected cell").run(); });
  const d = page().getByRole("dialog", { name: "Comment on cell 2" });
  await d.getByText("On the cell.", { exact: false }).waitFor();
  await d.getByRole("button", { name: "Cancel" }).click();
  assert.deepStrictEqual(sent("notebook_comment_add"), []);
  assert.deepStrictEqual(app.errors, []);
});
