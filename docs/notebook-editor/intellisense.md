# IntelliSense in notebook cells

Python cells explain the code you're writing, as Fabric and Databricks notebooks do. Everything
comes from the notebook's own kernel and the app on this PC: there's no AI service, nothing is sent
anywhere, and looking at a name never runs code.

## Hover a name

Rest the mouse on a function, class, module or variable in a Python cell. A card shows:

- for a function or class: its signature (`read_csv(source, *, has_header=True, ...)`) and the
  start of its documentation;
- for a variable: its type and a short preview of its value. Values of names that look like
  secrets (`password`, `token`, `key`...) or that came from `notebookutils.credentials` show as
  **hidden**.

The card comes from objects the kernel already has, so run the cell that defines a name before
hovering it. Attributes that are computed when read (properties) are never read for a hover.

## Signature help

While you type a call's arguments, a card above the cursor shows the parameters with the one you're
typing in bold, and the first paragraph of its docs. It follows commas and `name=` keywords, and
works across lines. Press **Esc** to close it.

After a call's result (`df.write.mode("overwrite").saveAsTable(`) the kernel can't know the object
without running the code, so there's no signature help there.

## Docs in the autocomplete list

**Ctrl+Space** (or typing) lists names from the kernel; the highlighted one shows its signature and
docs beside the list.

## Syntax errors as you type

A Python syntax error gets a red underline a moment after you stop typing; hover it for Python's
message. Cells in another language (`%%sql`, `%%html`) aren't checked, and notebook syntax such as
`%run`, `%pip`, `!command` and `x = %sql ...` isn't an error. **Validate** still checks the whole
notebook, including names used before they're defined.

## When it isn't there

- With **Runs on: Fabric**, the kernel on this PC isn't used, so hover, signature help and docs in
  the list are off; the syntax check still works.
- With the notebook running in its own process (Settings), hovering before you've run any cell
  shows nothing, because no process has started yet.
