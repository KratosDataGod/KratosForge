# Notebook best practices (Preview)

KratosForge checks a notebook's code against best-practice rules on this PC. It reads the cells as
text and, for Python cells, with Python's own parser. **Nothing runs**, nothing is read from Fabric,
and it uses no capacity.

## In the notebook editor

**Best practices** on the View tab opens a panel beside the cells. It checks the code cells as you
type (when the code changes, every second or so) and lists each finding with its severity, what to
do, and **Go to cell n, line m**, which selects the cell and puts the cursor on that line.
**About NB0nn** opens the rule's explanation in [Each rule explained](#each-rule-explained). The button
shows how many findings there are while the panel is open. Under each finding, **Dismiss** hides it
for you, and in Professional or Guided guidance **Explain** and **Show example** show its section of this
page in the panel ([Learn](learn.md#beside-a-finding)).

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
| NB018 | Info | Python UDF in Spark code (`udf`, `@udf`, `spark.udf.register`) | Use a built-in function, or a `pandas_udf` |
| NB019 | Warning | Spark reads straight from an `http(s)` address | Copy the file into the lakehouse's `Files` first |
| NB020 | Warning | `VACUUM` keeps less than 7 days (168 hours) of history | Keep the default retention |

NB013 to NB017 are kept for rules the Learn plan names next. NB018 to NB020 are based on guidance in
Microsoft's [skills-for-fabric](https://github.com/microsoft/skills-for-fabric) (MIT), written in our own words.

Secrets are found with the same patterns the Git page uses before a commit. Rules NB004 and NB008
apply only when the check is told the notebook runs on a schedule. A cell that doesn't parse is
still checked for secrets, ids in paths and `%pip` lines; the editor already underlines its syntax
error. Scala, R and other `%%` cells are skipped, except `%%sql`.

## Each rule explained

Each finding in the panel links to its rule here with **About NB0nn**: why the rule exists, an
example and, where it applies, when the finding is safe to leave. Each rule also has the principle
behind it, its impact, and how to check the change worked. Under the title are the rule's severity,
its area and how important it is:

- **Critical**: it can leak a secret, lose data or put wrong data in place. Fix it before the code
  is shared or scheduled.
- **Recommended**: sound practice that most code should follow.
- **Educational**: a habit worth knowing, with little risk when it's skipped.
- **Advanced**: a tradeoff that needs judgment, where the exceptions matter.

This only explains the rule. The severity, and any policy your organization sets, decide
what the panel shows and requires.

### NB001

**Secret written in a cell** · Error · Security · Critical

Why: a key, password or connection string in code is copied into Git, notebook versions, cell
outputs and anything else that keeps the notebook. Anyone who can read the notebook can use it.

```python
# Before
conn = "Server=...;Password=Sup3rS3cret!"
# After
conn = notebookutils.credentials.getSecret("https://my-vault.vault.azure.net/", "sales-db")
```

When it's fine: never in a notebook that is saved or shared. Rotate a secret that was committed.

Principle: Keep secrets out of code, and read them from a vault when the code runs.

Impact: Security. Anyone who can read the notebook, its versions or its Git history can use the secret.

How to check: Best practices shows no NB001, and the Git page's secret check finds nothing before you commit. A secret that was ever committed stays in history, so rotate it.

### NB002

**Workspace or lakehouse id written in a path** · Warning · Maintenance · Recommended

Why: an `abfss://<workspace id>@...` path points at one workspace. When the notebook is promoted to
Test or Prod, it still reads and writes the Dev lakehouse.

```python
# Before
df = spark.read.load("abfss://3f2a...@onelake.dfs.fabric.microsoft.com/9c1d.../Tables/sales")
# After
df = spark.read.table("sales")          # or the default lakehouse's "Tables/sales"
```

When it's fine: a one-off notebook that is never promoted. A parameter is still easier to change.

Principle: Let the environment decide where data lives. Code names tables, not workspaces.

Impact: Reliability. After promotion, Test or Prod reads and writes the Dev lakehouse.

How to check: No path in the notebook holds a workspace or lakehouse id. After you promote it and run it once, the write shows in the Test lakehouse's table history, not Dev's.

### NB003

**`collect()` or `toPandas()` without a limit** · Warning · Performance · Recommended

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

Principle: Move the work to the data, not all the data to one machine.

Impact: Reliability. The driver can run out of memory as the table grows, and the run fails.

How to check: The finding is gone, and the rows brought back are capped by the limit or by the number of groups.

### NB004

**`display()` of a whole table in a scheduled notebook** · Info · Performance · Educational

Why: nobody reads a scheduled run's output, and displaying a whole table makes the run slower and
its output large. Checked only for notebooks that run on a schedule.

```python
# Before
display(df)
# After
print(f"{df.count():,} rows written")
```

Principle: A scheduled run's output is a log. Keep it short.

Impact: Performance. The run is slower and its saved output is large.

How to check: The scheduled run's output shows a count or a sample, not the whole table.

### NB005

**CSV or JSON read without a schema** · Warning · Performance · Recommended

Why: inferring a schema reads the files an extra time, and the inferred types change when the files
do, so a column can turn from a number into a string without warning.

```python
# Before
df = spark.read.option("header", True).csv("Files/orders/")
# After
schema = "order_id INT, order_date DATE, amount DECIMAL(18,2)"
df = spark.read.option("header", True).schema(schema).csv("Files/orders/")
```

Principle: Say what shape you expect the data to have, instead of guessing it from the files.

Impact: Reliability and performance. The files are read an extra time, and a column's type can change without warning.

How to check: `df.printSchema()` shows the types you wrote, on every run.

### NB006

**`SELECT *` in SQL** · Info · Maintenance · Educational

Why: it reads every column, which costs time on wide tables, and code that depends on column order
breaks when a column is added or renamed.

```sql
-- Before
SELECT * FROM sales
-- After
SELECT order_id, order_date, amount FROM sales
```

When it's fine: a quick look at a table while you explore.

Principle: Ask only for the columns you use.

Impact: Performance on wide tables, and code that breaks when a column is added or renamed.

How to check: The query names its columns, and the result has the columns your code uses, in the order you named them.

### NB007

**Whole table overwritten** · Info · Performance · Advanced

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

Principle: Rewrite only the data that changed.

Impact: Performance and capacity. Every file is rewritten on each run, even when one day changed.

How to check: `DESCRIBE HISTORY` shows the write removed and added only the files of the range you replaced, and a row count outside that range is the same before and after.

### NB008

**`%pip install` in a scheduled notebook** · Warning · Reliability · Recommended

Why: the package is installed again on every run. That is slow, and a run fails when the package
index can't be reached. Checked only for notebooks that run on a schedule.

Do this instead: put the package in a Fabric environment and attach it to the notebook.

Principle: A scheduled run uses the same prepared environment every time.

Impact: Reliability. Each run installs the package again, and fails when the package index can't be reached.

How to check: The notebook has no `%pip` lines, and its attached environment lists the package and its version.

### NB009

**Package installed without a version** · Warning · Reliability · Recommended

Why: a new release of the package can change results or break the notebook, and two runs a week
apart may not use the same code.

```python
# Before
%pip install great-expectations
# After
%pip install great-expectations==1.2.3
```

Principle: Pin what you depend on, so a run today and a run next month use the same code.

Impact: Reliability. A new release can change results or break the notebook between two runs.

How to check: Every install line names a version (`==1.2.3`), and `%pip show` reports that version.

### NB010

**Table written inside a loop** · Warning · Performance · Recommended

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

Principle: Write once per run, not once per item.

Impact: Performance. Each write makes small files and a new table version.

How to check: `DESCRIBE HISTORY` shows one write for the run, and the table gained the same number of rows as before the change.

### NB011

**`count()` called again and again on one DataFrame** · Info · Performance · Educational

Why: every `count()` runs the whole query again. Three counts read the data three times.

```python
# Before
if df.count() > 0: print(df.count())
# After
n = df.count()
if n > 0: print(n)
```

Principle: Work a value out once and keep it.

Impact: Performance. Each `count()` reads the data again.

How to check: The cell starts one count job for the DataFrame instead of several (the Spark jobs listed under the cell).

### NB012

**Error caught and ignored** · Warning · Reliability · Recommended

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

Principle: Fail loudly, or handle only the errors you expect and say that you did.

Impact: Reliability. The run reports success while a table is wrong or missing.

How to check: Make the expected error happen once (a missing file, for example) and see the message. Any other error still fails the run.

### NB018

**Python UDF in Spark code** · Info · Performance · Advanced

Why: a Python UDF sends every row from Spark to a Python process and back, one row at a time. A
built-in function runs inside Spark and is usually many times faster. When nothing built in fits, a
`pandas_udf` works on whole batches. `pandas_udf` and Fabric User Data Functions (`@udf.function()`)
aren't flagged.

```python
# Before
upper = F.udf(lambda s: s.upper())
df = df.withColumn("name", upper("name"))
# After
df = df.withColumn("name", F.upper("name"))
```

Safe to leave: logic no built-in function can express, on a small table.
Principle: Use the engine's own functions before writing your own.

Impact: Performance. Every row goes to Python and back.

How to check: Run the cell before and after on the same data (Prove, or Side by side) and check the rows match. Compare the measured times of the two runs.

Source: [Spark data engineering patterns](https://github.com/microsoft/skills-for-fabric/blob/bc42aaf/skills/spark-cli/references/authoring/resources/data-engineering-patterns.md).

### NB019

**Spark reads straight from a web address** · Warning · Reliability · Recommended

Why: Spark's readers expect storage paths, so an `http(s)` address fails or reads differently from
run to run, and the file behind it can change or disappear. A copy in the lakehouse gives every run
the same input.

```python
# Before
df = spark.read.option("header", True).csv("https://example.com/sales.csv")
# After: copy it once (a pipeline Copy activity, for example), then read the copy
df = spark.read.option("header", True).csv("Files/landing/sales.csv")
```

Reading a web address with pandas (`pd.read_csv(url)`) isn't flagged.
Principle: Land outside data first, then read your own copy.

Impact: Reliability. A read from the web can fail, or read a different file, from one run to the next.

How to check: The read path is under `Files/`, and two runs read the same file.

Source: [Spark data engineering patterns](https://github.com/microsoft/skills-for-fabric/blob/bc42aaf/skills/spark-cli/references/authoring/resources/data-engineering-patterns.md).

### NB020

**`VACUUM` keeps less than 7 days of history** · Warning · Reliability · Critical

Why: `VACUUM` deletes files that older table versions use. With less than the default 168 hours,
it can delete files a running query or write still needs, and time travel to those versions stops
working. Found in `%%sql` cells, `spark.sql(...)`, `DeltaTable.vacuum(hours)` and delta-rs
`vacuum(retention_hours=...)`.

```sql
-- Before
VACUUM sales RETAIN 0 HOURS
-- After
VACUUM sales RETAIN 168 HOURS
```

Safe to leave: a table nothing else reads or writes, such as a scratch table in a test.
Principle: Keep enough history for anything that may still read it.

Impact: Data loss. A running read or write can fail, and time travel to older versions stops working.

How to check: `VACUUM` keeps 168 hours or more, and `DESCRIBE HISTORY` still lists the versions you need.

Source: [Spark data engineering patterns](https://github.com/microsoft/skills-for-fabric/blob/bc42aaf/skills/spark-cli/references/authoring/resources/data-engineering-patterns.md).

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
