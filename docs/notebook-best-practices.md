# Notebook best practices (Preview)

KratosForge checks a notebook's code against best-practice rules on this PC. It reads the cells as
text and, for Python cells, with Python's own parser. **Nothing runs**, nothing is read from Fabric,
and it uses no capacity.

## In the notebook editor

**Best practices** on the View tab opens a panel beside the cells. It checks the code cells as you
type (when the code changes, every second or so) and lists each finding with its severity, what to
do, and **Go to cell n, line m**, which selects the cell and puts the cursor on that line. The button
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
