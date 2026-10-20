// Cell comments (notebook-comments.js) against the real engine: a thread on selected lines, reply,
// edit, resolve and reopen, delete with a red confirmation, and a thread whose cell was deleted.
// Saves go to the real engine too, so the test shows a Save from the notebook keeps the comments.
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const path = require("node:path");
const { playwright, openApp } = require("./harness.cjs");

const SHOTS = process.env.SCREENSHOT_DIR;
const skip = !playwright && "Playwright is not installed";

const CELLS = [
  { type: "code", cell_id: "c1", source: "a = 1\nb = 2\nc = 3", meta: {} },
  { type: "code", cell_id: "c2", source: "print(a + b)", meta: {} },
];

let app;
const page = () => app.page;
const pane = () => page().locator(".commentspane");

async function writeInDialog(text, ok) {
  const box = page().locator(".dialog textarea");
  await box.fill(text);
  await page().locator(".dialog-actions").getByRole("button", { name: ok }).click();
}

before(async () => {
  if (skip) return;
  app = await openApp({
    engine: ["save_notebook", "notebook_comments_list", "notebook_comment_add", "notebook_comment_reply",
      "notebook_comment_edit", "notebook_comment_delete", "notebook_comment_resolve"],
  });
  await page().evaluate(async (cells) => {
    await rpc("save_notebook", { name: "Review", cells });
    loadIntoState({ name: "Review", cells });
    go("notebook");
  }, CELLS);
});

after(async () => { if (app) await app.close(); });

test("a comment on selected lines shows in the panel, with a badge on the cell", { skip }, async () => {
  await page().evaluate(() => {
    const cell = state.notebook.cells[0];
    editors.get(cell.id).dispatch({ selection: { anchor: 6, head: 11 } });  // "b = 2"
  });
  await page().locator("[data-cell]").first().getByRole("button", { name: "More cell actions" }).click();
  await page().getByRole("menuitem", { name: "Add comment" }).click();
  assert.match(await page().locator(".dialog").innerText(), /On line 2\. .*isn't published to Fabric/);
  await writeInDialog("Why 2? @bob <b>check</b>", "Comment");
  await pane().getByText("Cell 1, line 2").waitFor();
  assert.strictEqual(await pane().locator(".comment-quote").innerText(), "b = 2");
  assert.strictEqual(await pane().locator(".mention").innerText(), "@bob");
  assert.match(await pane().locator(".comment-text").innerText(), /<b>check<\/b>/, "text is shown as typed, not as HTML");
  assert.match(await pane().locator(".comment-meta").innerText(), /^You/);
  assert.strictEqual(await page().locator("[data-cell] .comment-badge").first().innerText(), "1");
  if (SHOTS) await page().screenshot({ path: path.join(SHOTS, "notebook-comments.png") });
});

test("reply, edit your own, and a Save from the notebook keeps the thread", { skip }, async () => {
  await pane().getByRole("button", { name: "Reply" }).click();
  await writeInDialog("It's the default.", "Reply");
  await pane().getByText("It's the default.").waitFor();
  await pane().getByRole("button", { name: "Edit" }).first().click();
  await writeInDialog("Why 2? @bob", "Save");
  await pane().getByText("(edited)").waitFor();
  await page().evaluate(async () => {
    state.notebook.cells[0].source = "a = 1\nb = 20\nc = 3";
    markDirty();
    await saveNotebook();
  });
  const listed = await page().evaluate(() => rpc("notebook_comments_list", { name: "Review" }));
  assert.deepStrictEqual(listed.threads[0].comments.map((c) => c.text), ["Why 2? @bob", "It's the default."]);
});

test("resolve moves the thread to Resolved and clears the badge; Reopen brings it back", { skip }, async () => {
  await pane().getByRole("button", { name: "Resolve" }).click();
  await pane().getByRole("tab", { name: "Open 0" }).waitFor();
  assert.strictEqual(await page().locator("[data-cell] .comment-badge").count(), 0);
  await pane().getByRole("tab", { name: "Resolved 1" }).click();
  await pane().getByText("Resolved by You").waitFor();
  await pane().getByRole("button", { name: "Reopen" }).click();
  await pane().getByRole("tab", { name: "Resolved 0" }).waitFor();
  await pane().getByRole("tab", { name: "Open 1" }).click();
});

test("deleting a reply asks first, in red", { skip }, async () => {
  await pane().getByRole("button", { name: "Delete" }).nth(1).click();
  await page().locator(".dialog").getByText("Delete this comment?").waitFor();
  assert.ok(await page().$(".dialog-actions .danger-solid"));
  await page().locator(".dialog-actions").getByRole("button", { name: "Delete" }).click();
  await page().waitForFunction(() => document.querySelectorAll(".commentspane .comment").length === 1);
});

test("a thread on a deleted cell stays in the panel, marked so", { skip }, async () => {
  await page().evaluate(() => selectCell(state.notebook.cells[1]));
  await pane().getByRole("button", { name: "Comment on the selected cell" }).click();
  await writeInDialog("Prints the sum", "Comment");
  await pane().getByText("Prints the sum").waitFor();
  await page().locator("[data-cell]").nth(1).getByRole("button", { name: "Delete cell" }).click();
  await pane().getByText("On a deleted cell").waitFor();
  assert.strictEqual(await pane().getByText("Cell deleted").count(), 1);
  await pane().getByRole("button", { name: "Go to cell 1" }).click();
  assert.strictEqual(await page().evaluate(() => state.notebook.cells.indexOf(selectedCell())), 0);
  assert.deepStrictEqual(app.errors, []);
});
