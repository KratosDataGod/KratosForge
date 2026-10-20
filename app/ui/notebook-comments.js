// Comments on notebook cells, as in Fabric and Databricks: threads on a cell, optionally on lines
// of its code, with replies, edit and delete of your own, and resolve/reopen. A badge on the cell
// counts its open threads; the Comments panel lists every thread with Go to cell.
// The engine keeps them in the notebook file (comments_service.py): every change is its own call
// that re-reads the file, so the notebook's Save never sends or overwrites them. "@name" is only
// highlighted; there is no notification service. A thread on a deleted cell stays in the panel,
// marked so, until it is resolved or deleted.

// The text of a comment in parts, so a mention can be highlighted. Every part becomes a text node.
function commentParts(text) {
  return String(text).split(/(@[\w.-]*\w)/).filter(Boolean).map((t) => ({ text: t, mention: /^@[\w.-]*\w$/.test(t) }));
}

// The lines [first, last] a selection covers in `source`, or null when nothing is selected. A
// selection that ends at the start of a line doesn't include that line.
function commentLines(source, from, to) {
  if (from === to) return null;
  [from, to] = [Math.min(from, to), Math.max(from, to)];
  const lineAt = (pos) => source.slice(0, pos).split("\n").length;
  const first = lineAt(from);
  let last = lineAt(to);
  if (last > first && source[to - 1] === "\n") last -= 1;
  return [first, last];
}

// The threads to list: `filter` is "open" or "resolved"; `cellId` narrows to one cell. Each has
// its cell's number, or null when the cell has been deleted; in cell order, deleted cells last.
function commentRows(threads, cells, filter, cellId = null) {
  const at = new Map(cells.map((c, i) => [c.cell_id, i + 1]));
  return threads.filter((t) => (filter === "resolved") === !!t.resolved && (!cellId || t.cell_id === cellId))
    .map((t) => ({ thread: t, n: at.get(t.cell_id) ?? null }))
    .sort((a, b) => (a.n ?? Infinity) - (b.n ?? Infinity) || String(a.thread.comments[0]?.at).localeCompare(String(b.thread.comments[0]?.at)));
}

// Who wrote something, as shown: "You" only when its author id is the current account's (or, signed
// out, this PC's); otherwise the stored name, or "Someone on this PC" when none was stored.
function commentAuthor(name, id, me) {
  if (id && id === me?.id) return "You";
  return name || "Someone on this PC";
}

// Your own comment: the one you can edit or delete.
function ownComment(c, me) {
  return !!c.author_id && c.author_id === me?.id;
}

// The notebook's comments, loaded from the engine the first time they're needed.
function commentsOf(nb) {
  if (!nb.comments) {
    nb.comments = { open: false, filter: "open", cellId: null, threads: null, me: { name: "", id: "" } };
    rpc("notebook_comments_list", { name: nb.name })
      .then((out) => { nb.comments.threads = out.threads || []; nb.comments.me = out.me; drawComments(nb); })
      .catch(() => { nb.comments.threads = []; drawComments(nb); });  // a notebook not saved yet has none
  }
  return nb.comments;
}

function commentsOpen(nb) {
  return !!nb?.comments?.open;
}

function openComments(nb, cellId = null) {
  const c = commentsOf(nb);
  Object.assign(c, { open: true, cellId, filter: "open" });
  if (nb.prove?.open) nb.prove.open = false;  // one panel beside the cells at a time
  if (nb.validate?.open) nb.validate.open = false;
  renderNotebook();
}

function closeComments(nb) {
  nb.comments.open = false;
  renderNotebook();
}

function commentsToggle(nb) {
  const on = commentsOpen(nb);
  return btn("Comments", "comment", () => (on ? closeComments(nb) : openComments(nb)), `tool${on ? " toggled" : ""}`,
    { "aria-pressed": String(on), title: "Comment threads on this notebook's cells" });
}

// The cell head's badge: how many open threads the cell has. Empty when it has none.
function commentBadge(cell) {
  const slot = h("span", { class: "comment-slot" });
  paintBadge(slot, state.notebook, cell);
  return slot;
}

function paintBadge(slot, nb, cell) {
  const threads = commentsOf(nb).threads || [];
  const open = threads.filter((t) => t.cell_id === cell.cell_id && !t.resolved).length;
  slot.replaceChildren(...(open ? [h("button", { class: "tool small comment-badge", title: `${open} open comment thread${open === 1 ? "" : "s"}`,
    "aria-label": `Comments on this cell: ${open} open`, onclick: () => openComments(state.notebook, cell.cell_id) }, icon("comment", 14), String(open))] : []));
}

function commentMenuItems(cell) {
  return [{ label: "Add comment", icon: "comment", action: () => addComment(state.notebook, cell) }];
}

// Repaint the badges and the panel in place, so editors keep focus.
function drawComments(nb) {
  if (state.view !== "notebook" || state.notebook !== nb) return;
  for (const cell of nb.cells) {
    const slot = document.querySelector(`[data-cell="${cell.id}"] .comment-slot`);
    if (slot) paintBadge(slot, nb, cell);
  }
  document.querySelector(".commentspane")?.replaceChildren(...commentsContent(nb));
}

// A box for writing a comment. Ctrl+Enter sends it. Resolves to the text, or null.
function commentDialog(title, initial = "", okLabel = "Comment", note = null) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (v) => { if (settled) return; settled = true; closeDialog(); resolve(v); };
    const box = h("textarea", { class: "comment-input", rows: "5", maxlength: "4000", "aria-label": "Comment",
      placeholder: "Write a comment. @name highlights a name; nobody is notified." });
    box.value = initial;
    const ok = btn(okLabel, null, () => { if (box.value.trim()) done(box.value.trim()); }, "tool primary");
    box.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); ok.click(); } });
    showDialog(title, h("div", { class: "comment-form" }, note, box), [btn("Cancel", null, () => done(null), "tool outline"), ok],
      () => { if (!settled) { settled = true; resolve(null); } });
    box.focus();
  });
}

// The engine's answer to a change: the notebook's threads as they are in the file now.
function commentsChanged(nb, out) {
  if (!out) return;
  nb.comments.threads = out.threads || [];
  nb.comments.me = out.me;
  drawComments(nb);
}

async function addComment(nb, cell) {
  const view = editors.get(cell.id);
  const sel = view?.state.selection.main;
  const lines = sel && cell.type === "code" ? commentLines(view.state.doc.toString(), sel.from, sel.to) : null;
  const where = lines ? (lines[0] === lines[1] ? `line ${lines[0]}` : `lines ${lines[0]}-${lines[1]}`) : "the cell";
  const n = nb.cells.indexOf(cell) + 1;
  const text = await commentDialog(`Comment on cell ${n}`, "", "Comment",
    h("p", { class: "muted-text" }, `On ${where}. Saved in the notebook file, so it goes with the notebook through Git. It isn't published to Fabric.`));
  if (!text) return;
  // The engine checks the cell is in the file, so a new or changed cell is saved first.
  if (nb.dirty && !(await saveNotebook())) return;
  const c = commentsOf(nb);
  const out = await attempt(() => rpc("notebook_comment_add", { name: nb.name, cell_id: cell.cell_id, text, lines }), "Could not add the comment");
  if (!out) return;
  // Show the new thread: open threads, and not narrowed to another cell.
  c.filter = "open";
  if (c.cellId && c.cellId !== cell.cell_id) c.cellId = null;
  commentsChanged(nb, out);
  if (!c.open) openComments(nb, cell.cell_id);
}

async function replyComment(nb, t) {
  const text = await commentDialog("Reply", "", "Reply");
  if (!text) return;
  commentsChanged(nb, await attempt(() => rpc("notebook_comment_reply", { name: nb.name, thread_id: t.id, text }), "Could not reply"));
}

async function editComment(nb, t, c) {
  const text = await commentDialog("Edit comment", c.text, "Save");
  if (!text || text === c.text) return;
  commentsChanged(nb, await attempt(() => rpc("notebook_comment_edit", { name: nb.name, thread_id: t.id, comment_id: c.id, text }), "Could not edit the comment"));
}

async function deleteComment(nb, t, c) {
  const whole = c === t.comments[0];
  const ok = await confirmDialog(whole ? "Delete this thread?" : "Delete this comment?",
    whole ? `The thread and its ${t.comments.length} comment${t.comments.length === 1 ? "" : "s"} are removed from the notebook file. This can't be undone here; Git keeps earlier commits.`
      : "The comment is removed from the notebook file. This can't be undone here; Git keeps earlier commits.",
    "Delete", { danger: true });
  if (!ok) return;
  commentsChanged(nb, await attempt(() => rpc("notebook_comment_delete", { name: nb.name, thread_id: t.id, comment_id: c.id }), "Could not delete the comment"));
}

async function resolveComment(nb, t, resolved) {
  commentsChanged(nb, await attempt(() => rpc("notebook_comment_resolve", { name: nb.name, thread_id: t.id, resolved }),
    resolved ? "Could not resolve the thread" : "Could not reopen the thread"));
}

function commentsPanel(nb) {
  if (!commentsOpen(nb)) return null;
  if (nb.prove?.open || validateOpen(nb)) { nb.comments.open = false; return null; }  // the one opened last shows
  return h("aside", { class: "commentspane", "aria-label": "Comments" }, commentsContent(nb));
}

function commentsContent(nb) {
  const c = commentsOf(nb);
  const threads = c.threads || [];
  const head = h("div", { class: "explorer-head" }, "Comments",
    h("button", { class: "tool small", "aria-label": "Close Comments", onclick: () => closeComments(nb) }, icon("dismiss", 14)));
  const count = (f) => commentRows(threads, nb.cells, f, c.cellId).length;
  const tab = (id, label) => h("button", { class: `tab${c.filter === id ? " active" : ""}`, role: "tab", "aria-selected": String(c.filter === id),
    onclick: () => { c.filter = id; drawComments(nb); } }, `${label} ${count(id)}`);
  const tabs = h("div", { class: "ribbon-tabs validate-tabs", role: "tablist", "aria-label": "Show" }, tab("open", "Open"), tab("resolved", "Resolved"));
  const body = h("div", { class: "prove-body" });
  if (c.cellId) {
    const n = nb.cells.findIndex((x) => x.cell_id === c.cellId) + 1;
    body.append(h("div", { class: "row" }, h("span", {}, n ? `Cell ${n} only` : "A deleted cell only"),
      h("button", { class: "link-btn", onclick: () => { c.cellId = null; drawComments(nb); } }, "Show all")));
  }
  body.append(btn("Comment on the selected cell", "add", () => addComment(nb, selectedCell()), "tool outline"));
  const rows = commentRows(threads, nb.cells, c.filter, c.cellId);
  if (c.threads === null) body.append(h("p", { class: "muted-text" }, "Loading comments..."));
  else if (!rows.length) body.append(h("p", { class: "muted-text" }, c.filter === "open"
    ? "No open comments. Select a cell and use Comment, or Add comment in the cell's ... menu. Select lines of code first to comment on them."
    : "No resolved comments."));
  rows.forEach((r) => body.append(commentThread(nb, r)));
  return [head, tabs, body, h("div", { class: "validate-how" }, howItWorks("comments", {
    reads: "The comments saved in this notebook's file, and who is signed in (your comments are the ones you can edit or delete).",
    more: [["Who wrote it", "Each comment keeps its author's account email (and account id) in the notebook file, so it goes into Git when the notebook is committed. Signed out, a comment has no name and shows as \"Someone on this PC\" to others."]],
    changes: "Adding, replying, editing, deleting, resolving and reopening write the comments into the notebook file on this PC (metadata.fabric_desktop.comments). Nothing else in the notebook changes.",
    results: "The notebook file, so comments go with it through Git. Publish to OneLake doesn't send them to Fabric. @name only highlights the name: nobody is notified.",
    anchor: "notebook-comments" }))];
}

function commentThread(nb, { thread: t, n }) {
  const me = nb.comments.me;
  const where = n ? `Cell ${n}${t.lines ? `, ${t.lines[0] === t.lines[1] ? `line ${t.lines[0]}` : `lines ${t.lines[0]}-${t.lines[1]}`}` : ""}` : "On a deleted cell";
  const goTo = n ? h("button", { class: "link-btn", onclick: () => depsGo(nb, n) }, `Go to cell ${n}`) : null;
  return h("div", { class: `comment-thread${t.resolved ? " resolved" : ""}`, "data-thread": t.id },
    h("div", { class: "row" }, h("b", {}, where), n ? null : h("span", { class: "pill-s wrn" }, "Cell deleted"),
      t.resolved ? h("span", { class: "pill-s pass" }, "Resolved") : null),
    t.quote ? h("pre", { class: "comment-quote" }, t.quote) : null,
    t.comments.map((c) => h("div", { class: "comment" },
      h("div", { class: "comment-meta" }, h("b", {}, commentAuthor(c.author, c.author_id, me)), when(c.at), c.edited_at ? h("span", { class: "muted-text" }, "(edited)") : null),
      h("p", { class: "comment-text" }, commentParts(c.text).map((p) => (p.mention ? h("span", { class: "mention" }, p.text) : p.text))),
      ownComment(c, me) ? h("div", { class: "row comment-actions" },
        h("button", { class: "link-btn", onclick: () => editComment(nb, t, c) }, "Edit"),
        h("button", { class: "link-btn", onclick: () => deleteComment(nb, t, c) }, "Delete")) : null)),
    t.resolved ? h("p", { class: "muted-text" }, `Resolved by ${commentAuthor(t.resolved_by, t.resolved_by_id, me)}`) : null,
    h("div", { class: "row comment-actions" },
      t.resolved ? null : btn("Reply", null, () => replyComment(nb, t), "tool outline small"),
      btn(t.resolved ? "Reopen" : "Resolve", null, () => resolveComment(nb, t, !t.resolved), "tool outline small"), goTo));
}

registerFeature({
  id: "comments",
  commands: [
    { label: "Show comments on this notebook", icon: "comment", keywords: "comments review discuss threads notes",
      when: () => state.view === "notebook" && !!state.notebook, run: () => openComments(state.notebook) },
    { label: "Comment on the selected cell", icon: "comment", keywords: "add comment review note",
      when: () => state.view === "notebook" && !!state.notebook, run: () => addComment(state.notebook, selectedCell()) },
  ],
});
