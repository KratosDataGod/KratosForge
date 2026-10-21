# Notebook comments

Comments let you discuss a notebook's code where it is, as cell comments do in Fabric and Databricks notebooks. A comment starts a **thread** on a cell, or on some lines of its code, and others can reply. Threads are kept in the notebook file, so they go with the notebook when you share it through Git.

## Add a comment

1. To comment on particular lines, select them in the cell's code first. With nothing selected, the comment is on the whole cell.
2. Open the cell's **...** menu and select **Add comment**. You can also select a cell and use **Comment on the selected cell** in the Comments panel, or the command bar (Ctrl+K).
3. Write the comment and select **Comment** (or press Ctrl+Enter).

If the notebook has unsaved changes, it is saved first, so the comment is attached to the cell as it is now. A comment on lines keeps a copy of those lines (up to 500 characters), so a reader sees what was commented on even after the code changes.

Each comment keeps its author: the email of the account you're signed in with, and that account's id. **They are saved in the notebook file, so they go into Git when the notebook is committed**, and anyone who can read the repository sees who wrote each comment. When you're signed out, a comment has no name, only an id kept on this PC: you see it as **You**, and others see **Someone on this PC**. The panel shows **You** for comments whose author id is the account you're signed in with now, and the stored email for everyone else's. A comment can be up to 4,000 characters. It is shown exactly as typed: nothing in it is run or formatted.

**@name** highlights a name in the comment, so the person can find it. There is no notification service: nobody is emailed or told. Let them know another way.

## The Comments panel

Select **Comments** on the notebook's **View** tab, or a cell's comment badge, to open the panel beside the cells. A badge on a cell's header counts its open threads; a badge opens the panel with that cell's threads only, and **Show all** lists every thread again.

- **Open** and **Resolved** filter the list. Threads are in cell order.
- Each thread shows its cell and lines, the code it was on, and every comment with its author and time.
- **Go to cell** selects the cell and scrolls to it.
- **Reply** adds a comment to the thread.
- **Edit** and **Delete** appear on your own comments only (the ones whose author is the account you're signed in with now). Edited comments are marked **(edited)**. Delete asks first. Deleting a thread's first comment deletes the whole thread, which needs every reply in it to be yours; when others have replied, resolve it instead.
- **Resolve** marks the thread done and records who resolved it; **Reopen** puts it back. Anyone can resolve or reopen a thread.

Only one panel shows beside the cells at a time: opening Comments closes Prove or Validate, and opening either of those closes Comments.

## Comments on a deleted cell

When a cell with comments is deleted, its threads are kept. The panel lists them last, marked **Cell deleted** and **On a deleted cell**, until you resolve them or delete their comments. **Undo delete** brings the cell back with its threads.

## What is written, and where

| Action | What it writes |
| --- | --- |
| Add, Reply, Edit, Delete, Resolve, Reopen | The notebook file on this PC (`<name>.ipynb`), under `metadata.fabric_desktop.comments`, including each author's email and account id. Nothing else in the file changes. |
| A comment while signed out | Also, the first time: `comment-author-id` in the app's data folder, a random id that marks this PC's signed-out comments as yours. |
| Save (the notebook's own) | Never sends or overwrites comments: it keeps them as they are in the file. |
| Publish to OneLake | Comments are not sent to Fabric. The Fabric notebook item gets the cells only. Fabric's own comments are separate and aren't brought here. |
| Git | Comments are part of the notebook file, so a commit includes them. The notebook comparison in Source control shows cell code only, so a comment doesn't show as a cell change. |

Every comment change is its own call to the engine, which reads the notebook file as it is now, makes the change and replaces the file in one step. A save and a comment can't undo each other, and two windows commenting on the same notebook don't lose each other's comments. Each change is recorded in the [audit trail](user-guide.md#audit-trail) with the notebook's name; the comment text isn't recorded there.

## Limits

- Up to 500 threads in a notebook and 200 comments in a thread.
- Comments made while signed out belong to this PC, not to an account. After you sign in, they're no longer yours to edit or delete (and on another PC they never are); resolve them instead.
- Comments written before authors had an account id (an early preview of this feature) have no `author_id`, so they're locked: anyone can resolve or reopen their threads, but nobody can edit or delete them. They show their stored name.
- Line ranges are kept as the line numbers they had. When code is added above them, the panel still shows the original lines' code, but the numbers can point at different lines.
