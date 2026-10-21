# Notebook magics and cell languages

Magic commands are lines that start with `%` (one line) or `%%` (a whole cell). KratosForge
runs the ones Fabric and Databricks notebooks have where they make sense on your PC, and says so
plainly where they don't. This page grows as more of them land; [Notebooks and SQL](notebooks.md)
covers the rest of the notebook.

## SQL results in Python

A `%%sql` cell shows its result as a table, as in Fabric. It also keeps that result in a variable
called `_sqldf`, as Databricks does, so the next Python cell can carry on from it:

```sql
%%sql
SELECT region, sum(amount) AS total FROM dbo.sales GROUP BY region
```

```python
top = _sqldf.orderBy("total", ascending=False).limit(3)
display(top)
```

- `_sqldf` is a `spark` DataFrame. It shows in the **Variables** pane.
- Only a query sets it (`SELECT`, `WITH`, `SHOW`, `DESCRIBE`...). A cell that creates a view or
  writes a table leaves the previous `_sqldf` as it was.
- To run one query inside a Python cell, put `%sql` in front of it. Assign it to keep the result,
  or put it on the cell's last line to show it:

```python
df = %sql SELECT * FROM dbo.sales WHERE region = 'West'
print(df.count())
%sql SELECT max(amount) AS largest FROM dbo.sales
```

`MERGE`, `UPDATE` and `DELETE` change rows in place, which runs in Fabric only, so `%sql` stops
with an error for them and **Runs on** lists the cell as Fabric only. Put them in a `%%sql` cell to
run them once the notebook is published.

**In Fabric.** Fabric notebooks have `%%sql` cells but neither `%sql` lines nor `_sqldf`. When
you publish, KratosForge writes those as calls Fabric has: `df = %sql ...` becomes
`df = spark.sql("...")` (`lakehouse.sql` in a Python notebook), and a `%%sql` cell whose `_sqldf`
a later cell uses becomes `_sqldf = spark.sql("...")` followed by `display(_sqldf)`. Your notebook
on this PC keeps the magics.

Dependencies, stale cells, Validate and Prove read `%sql` lines too: a cell that uses `_sqldf`
depends on the `%%sql` cell before it, the tables a `%sql` line reads count as reads, and a `%sql`
line that writes a table is refused in a proof, like a `%%sql` cell.

## Cell languages

A cell's first line can say what language the rest of it is in. Fabric writes `%%name`;
Databricks writes `%name`. Both work, so a notebook brought over from Databricks runs without
editing its headers.

| First line | Fabric | Databricks | On this PC |
| --- | --- | --- | --- |
| Spark SQL | `%%sql` | `%sql` (alone on the line) | Runs through the notebook's `spark`; the result is kept in `_sqldf` |
| Python | `%%pyspark`, `%%python` | `%python` | Runs as Python; line numbers in errors match the cell |
| Markdown | `%%markdown` | `%md` | Shows the text as Markdown under the cell |
| HTML | `%%html` | | Shows the HTML in a sandboxed frame |
| Scala | `%%spark` | `%scala` | Needs Fabric's Spark: switch **Runs on** to Fabric |
| R | `%%sparkr` | `%r` | Needs Fabric's Spark: switch **Runs on** to Fabric |
| .NET | `%%csharp` | | Needs Fabric's Spark: switch **Runs on** to Fabric |

`%sql` followed by a query on the same line is the one-line `%sql` above, not a header.

The editor highlights a `%sql` cell as SQL and a `%md` cell as Markdown, and the cell's language
picker shows Spark SQL for both `%%sql` and `%sql`. Dependencies, Validate, Prove and the
**Runs on** check read the header too, so a `%sql` cell's tables count as reads and a `%scala`
cell is listed as Fabric only.

**In Fabric.** Publishing writes Databricks headers the way Fabric reads them: `%sql` becomes
`%%sql`, `%scala` becomes `%%spark`, `%r` becomes `%%sparkr`, `%python` is dropped, and a `%md` or
`%%markdown` cell becomes a Markdown cell. Your notebook on this PC keeps what you wrote.

## Supported magics

| Magic | On this PC |
| --- | --- |
| `%%sql` | Spark SQL through the notebook's `spark` session; the result is shown and kept in `_sqldf` |
| `%sql` | One Spark SQL query inside Python; returns a DataFrame |
| `%%html` | Shows the HTML in a sandboxed frame |
| `%run Other notebook` | Runs another notebook in this one's namespace |
| `%time expression` | Runs the expression and prints the wall time |
| `%%pyspark`, `%%python`, `%python` | A Python cell |
| `%md`, `%%markdown` | A Markdown cell's text, shown under the cell |
| `%pip`, `%conda` | Skipped with a note: the engine's packages are fixed |
| `%%configure` | Does nothing here; it sets Spark session options in Fabric |
| `%%spark`, `%%sparkr`, `%%csharp`, `%scala`, `%r` | Need Fabric's Spark; switch **Runs on** to Fabric |
