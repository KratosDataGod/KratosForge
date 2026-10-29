# Notebook best practices (Preview)

KratosForge checks a notebook's code against best-practice rules on this PC. It reads the cells as
text and, for Python cells, with Python's own parser. **Nothing runs**, nothing is read from Fabric,
and it uses no capacity.

## In the notebook editor

**Best practices** on the View tab opens a panel beside the cells. It checks the code cells as you
type (when the code changes, every second or so) and lists each finding with its severity, what to
do, and **Go to cell n, line m**, which selects the cell and puts the cursor on that line.
**About NB0nn** opens the rule's explanation in [Each rule explained](#each-rule-explained). The button
shows how many findings there are while the panel is open.

The panel reads the governance policies in force for the open project (`governance_practice_settings`).
A rule a policy turns off (`enabled = false`) isn't shown, and a policy's severity, or its `severity`
parameter, changes a rule's severity. When two policies name one rule, it's on if either turns it on,
at the worse severity. With no project open, or when the policies can't be read, every rule is on as
built, and the panel says so. The panel doesn't check NB004 and NB008, which are for scheduled runs.

## Rules

| Id | Severity | Rule | What to do |
| --- | --- | --- | --- |
| NB001 | Error | Secret written in a cell | Read it from Key Vault with `notebookutils.credentials.getSecret` |
| NB002 | Warning | Workspace or lakehouse id written in a OneLake path | Use the default lakehouse's `Tables/...` or `Files/...` path, or a parameter |
| NB003 | Warning | `collect()` or `toPandas()` without a limit | Add `.limit(n)`, or aggregate first |
| NB004 | Info | `display()` of a whole table in a scheduled notebook | Display a sample or a count |
| NB005 | Warning | CSV or JSON read without a schema | Pass `.schema(...)` |
| NB006 | Info | `SELECT *` in `%%sql` or `spark.sql` | Name the columns |
| NB007 | Info | Whole table overwritten | Use `replaceWhere`, a partition overwrite, or MERGE |
| NB008 | Warning | `%pip install` in a scheduled notebook | Put the package in an environment |
| NB009 | Warning | Package installed without a version | Pin it: `package==1.2.3` |
| NB010 | Warning | Table written inside a loop | Collect the rows and write once |
| NB011 | Info | `count()` called three or more times on one DataFrame | Keep the count in a variable, or cache |
| NB012 | Warning | Error caught and ignored (`except ...: pass`) | Log the error, or let it fail |

Secrets are found with the same patterns the Git page uses before a commit. Rules NB004 and NB008
apply only when the check is told the notebook runs on a schedule. A cell that doesn't parse is
still checked for secrets, ids in paths and `%pip` lines; the editor already underlines its syntax
error. Scala, R and other `%%` cells are skipped, except `%%sql`.

## Each rule explained

Each finding in the panel links to its rule here with **About NB0nn**: why the rule exists, an
example, and when the finding is safe to leave.

### NB001

**Secret written in a cell** · Error · Security

Why: a key, password or connection string in code is copied into Git, notebook versions, cell
outputs and anything else that keeps the notebook. Anyone who can read the notebook can use it.

```python
# Before
conn = "Server=...;Password=Sup3rS3cret!"
# After
conn = notebookutils.credentials.getSecret("https://my-vault.vault.azure.net/", "sales-db")
```

When it's fine: never in a notebook that is saved or shared. Rotate a secret that was committed.

### NB002

**Workspace or lakehouse id written in a path** · Warning · Maintenance

Why: an `abfss://<workspace id>@...` path points at one workspace. When the notebook is promoted to
Test or Prod, it still reads and writes the Dev lakehouse.

```python
# Before
df = spark.read.load("abfss://3f2a...@onelake.dfs.fabric.microsoft.com/9c1d.../Tables/sales")
# After
df = spark.read.table("sales")          # or the default lakehouse's "Tables/sales"
```

When it's fine: a one-off notebook that is never promoted. A parameter is still easier to change.

### NB003

**`collect()` or `toPandas()` without a limit** · Warning · Performance

Why: both bring every row to the driver, one machine. A table that fits today can run the driver out
of memory next month.

```python
# Before
rows = df.toPandas()
# After
rows = df.groupBy("region").agg(F.sum("amount")).toPandas()   # aggregate first
sample = df.limit(1000).toPandas()                              # or take a sample
```

When it's fine: the DataFrame is already small by construction, such as the result of an aggregation.

### NB004

**`display()` of a whole table in a scheduled notebook** · Info · Performance

Why: nobody reads a scheduled run's output, and displaying a whole table makes the run slower and
its output large. Checked only for notebooks that run on a schedule.

```python
# Before
display(df)
# After
print(f"{df.count():,} rows written")
```

### NB005

**CSV or JSON read without a schema** · Warning · Performance

Why: inferring a schema reads the files an extra time, and the inferred types change when the files
do, so a column can turn from a number into a string without warning.

```python
# Before
df = spark.read.option("header", True).csv("Files/orders/")
# After
schema = "order_id INT, order_date DATE, amount DECIMAL(18,2)"
df = spark.read.option("header", True).schema(schema).csv("Files/orders/")
```

### NB006

**`SELECT *` in SQL** · Info · Maintenance

Why: it reads every column, which costs time on wide tables, and code that depends on column order
breaks when a column is added or renamed.

```sql
-- Before
SELECT * FROM sales
-- After
SELECT order_id, order_date, amount FROM sales
```

When it's fine: a quick look at a table while you explore.

### NB007

**Whole table overwritten** · Info · Performance

Why: an overwrite without `replaceWhere` rewrites every file and makes a full new table version,
even when only one day changed.

```python
# Before
df.write.mode("overwrite").saveAsTable("sales")
# After
(df.write.mode("overwrite")
   .option("replaceWhere", "order_date >= '2026-10-01'")
   .saveAsTable("sales"))
```

When it's fine: small tables, and full reloads that are meant to replace everything.

### NB008

**`%pip install` in a scheduled notebook** · Warning · Reliability

Why: the package is installed again on every run. That is slow, and a run fails when the package
index can't be reached. Checked only for notebooks that run on a schedule.

Do this instead: put the package in a Fabric environment and attach it to the notebook.

### NB009

**Package installed without a version** · Warning · Reliability

Why: a new release of the package can change results or break the notebook, and two runs a week
apart may not use the same code.

```python
# Before
%pip install great-expectations
# After
%pip install great-expectations==1.2.3
```

### NB010

**Table written inside a loop** · Warning · Performance

Why: each write makes small files and a new table version. A thousand passes make a thousand
versions and a slow table.

```python
# Before
for region in regions:
    load(region).write.mode("append").saveAsTable("sales")
# After
from functools import reduce
from pyspark.sql import DataFrame
reduce(DataFrame.unionByName, [load(r) for r in regions]).write.mode("append").saveAsTable("sales")
```

### NB011

**`count()` called again and again on one DataFrame** · Info · Performance

Why: every `count()` runs the whole query again. Three counts read the data three times.

```python
# Before
if df.count() > 0: print(df.count())
# After
n = df.count()
if n > 0: print(n)
```

### NB012

**Error caught and ignored** · Warning · Reliability

Why: `except ...: pass` hides a failure, so the run reports success while the table is wrong or
missing.

```python
# Before
try:
    load_day(day)
except Exception:
    pass
# After
try:
    load_day(day)
except FileNotFoundError:
    print(f"No file for {day}; skipped")   # catch only what you expect, and say so
```

## Settings

A governance policy can name these rules in **Checked by**, like `NB003, NB006` ([governance rules](governance.md#the-rules)). Each rule can be turned off or given another severity, which is how governance templates tune them:
`{"NB003": {"severity": "Error"}, "NB007": {"enabled": false}}`.

## Engine methods

| Method | What it does | Changes |
| --- | --- | --- |
| `notebook_practices(sources, scheduled?, settings?)` | Findings for the code cells' sources, worst first, with each finding's cell and line | Nothing |
| `notebook_practice_rules()` | The rules above, for a template to list | Nothing |
| `governance_practice_settings(kind)` | How the open project's policies set the `notebook` or `sql` rules: `{settings, policies, note}` | Nothing |

Findings have the same shape as the semantic model best practices check:
severity, rule, category, object, description and URL, plus `rule_id`, `cell` and `line`.
