# The Variables pane

**View > Variables** lists what the notebook's Python session holds on this PC: each variable's
name, type, size and a short value. It's the same explorer Fabric and Databricks notebooks have. Reading
it runs no cells and no queries; the only code of yours it can touch is a class's own `repr`,
used for the short value, as any Python explorer does.

## Filter and sort

- Type in **Filter by name or type** to narrow the list, for example `DataFrame` or `sales`.
- Click **Name**, **Type** or **Size** to sort; click again to reverse. **Size** sorts by the memory a
  variable holds, biggest first; variables whose memory can't be told cheaply go last.
- The line under the table counts the variables and adds up their memory.

Memory is an estimate: a Polars or Arrow frame's buffers, a NumPy array's bytes, or a built-in
list, dict or set with its first 10,000 items, scaled up (an object inside counts its base size).
A Spark frame, a Polars LazyFrame and your own classes have no figure, because measuring them
would mean running their query or their code. Hover a size to see the estimate.

## Open a variable

The arrow next to a data frame, list, tuple, set or dict opens it in place:

| Variable | What you see |
| --- | --- |
| Polars, Arrow or Spark data frame | Its columns and their types, read from the schema (a Spark frame's query isn't run) |
| List or tuple | The first 100 items with their index, type and value |
| Set | 100 of its items, sorted |
| Dict | The first 100 keys with their type and value |

Values whose name or content looks like a secret (`password`, `token`, `key` and the like, or a
value read with `getSecret`) show as **hidden**, at the top level and inside an opened dict, and a
hidden variable can't be opened. Opened variables close when the pane refreshes after a run, and
reopen with the new values when you open them again.

Clicking a variable's row still adds a cell that displays it.

When the debugging Preview features are on, the same table is the pane's **Now** tab, beside watches and history.
