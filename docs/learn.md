# Learn (Preview)

**Learn** is at the bottom of the left rail, above Source control and Settings. It has short
lessons, practice labs and the best-practice rules the app checks. Everything runs on this PC with
data generated here. A lesson or lab never reads or writes OneLake, never starts a Fabric job and
never publishes anything to Fabric.

## The tabs

| Tab | What it shows |
| --- | --- |
| Home | The lesson to continue (the one you opened last, or the first one) and the practice labs |
| Paths | Lessons grouped by subject: Data Fundamentals, SQL Development, Python & Data Engineering, Microsoft Fabric, Databricks, Snowflake and Enterprise Architecture |
| Labs | The practice labs: a task, a lab with generated data, and checks that say when it's done |
| Best practices | Every notebook and SQL rule, each with **About** to its explanation, example and when it's safe to leave |

A lesson marked **Draft** is still being written and may change.

## What's in it

| Id | Lesson or lab | Path | Teaches the rule |
| --- | --- | --- | --- |
| LM-01 | Your first query on this PC | Data Fundamentals | SQ002 |
| LM-02 | Data types, and why decimals beat floats for money | Data Fundamentals | SM003 |
| LM-03 | Joins without surprise row counts | Data Fundamentals | SQ006 |
| LM-04 | Grouping and aggregation | Data Fundamentals | SQ009 |
| LM-05 | Keys: primary, foreign, business and surrogate | Data Fundamentals | |
| LAB-01 | From raw data to a checked answer: find six planted problems, then a clean answer | Data Fundamentals | |
| LAB-02 | Optimize a slow query: the same answer, at least twice as fast | SQL Development | SQ002 |
| LM-06 | Lakehouses and the medallion layers | Microsoft Fabric | |
| LM-07 | OneLake and Delta tables: files and a log | Microsoft Fabric | NB020 |
| LM-08 | Notebooks: let the engine do the work | Microsoft Fabric | NB003 |
| LM-09 | Direct Lake: tables a Power BI model reads straight from OneLake | Microsoft Fabric | |
| LAB-03 | Build a star schema for a report: dimensions, a fact, and keys that all match | Microsoft Fabric | |
| LAB-04 | Fix a broken load: find the doubled rows, rebuild, then a load that's safe to run again | Microsoft Fabric | |
| LM-10 | QUALIFY: filter on a window function | Snowflake | |
| LM-11 | MERGE: update, insert and delete in one step | Databricks | |
| LM-12 | Incremental loads: a watermark that misses nothing | Python & Data Engineering | |

The Microsoft Fabric lessons and labs also run on this PC, in DuckDB, with data generated here. They
teach how lakehouses, OneLake, notebooks and Direct Lake work, and most of the SQL carries over to Fabric:
where a lesson uses DuckDB's own SQL, it says so.
Like every lab, they never read or write OneLake and never publish to Fabric.

The Snowflake, Databricks and Python & Data Engineering paths start with one lesson each. They run on this
PC in DuckDB too, and say where the SQL differs on the other platform: QUALIFY isn't in Fabric Warehouse,
and the MERGE lesson checks the table a MERGE would leave with a read-only SELECT. More lessons, and the
Enterprise Architecture path, come in later updates.

## A lesson or lab

Opening one shows its lesson on the left and **Checks** on the right.

1. **Start the lab** makes the lab's data on this PC: generated tables, the same every time, with
   known problems planted for some labs to find. Nothing is downloaded.
2. Write SQL in the box. **Run** (or Ctrl+Enter) shows the first 200 rows. Every run is undone
   afterwards, so the lab's data stays as it was whatever you run.
3. **Check my answer** runs the exercise's checks and says what each one found: the same result as
   the reference, no best-practice findings, faster than the starting query, and so on.
4. **Show a hint** gives one hint at a time, from a nudge to nearly the answer.

A run or a check stops after 30 seconds, and a lab can use up to 1 GB of memory, so a runaway
query can't hold up the app or the PC.

Speed checks are measured on this PC only. A Fabric Warehouse or SQL analytics endpoint plans and
runs the same query differently.

**Reset lab** deletes the lab's folder on this PC, your answers included, and makes it again. It asks
first. **Leave** goes back to Learn and keeps your answers.

## Guidance

**Learning guidance**, at the top right of Learn and in **Settings > Appearance > Layout > Learning
guidance**, sets how much explanation shows beside your work. You can change it at any time; an
organization policy can set it, and then it can't be changed here.

| Level | What it adds |
| --- | --- |
| Expert | No explanations: a best-practice finding shows its rule, **About** link and **Dismiss** |
| Professional (the default) | A line on why the finding matters, from the rule's guide, with **Explain** and **Show example** under each notebook or SQL finding |
| Guided | **Explain** open to start with, **Lesson: <title>** where a published lesson covers the rule, and **Suggested next** on Learn Home: the next lesson you haven't started, in path order. Draft lessons are linked only from Learn |

A level is a preference, not a permission: the checks run the same at every level, and a rule your
organization requires is required at every level. Guidance only adds explanations and links.
The first names of the levels still work in a settings or policy file: Focus reads as Expert and
Learning as Guided.
**SQL guidance** and **Notebook guidance**, under Learning guidance in **Settings > Appearance >
Layout**, set a level for one subject only: for example Expert for SQL, which you know well, and
Guided for notebooks. **Same** (the default) follows Learning guidance. They change only the
guidance beside that subject's findings; checks and required rules are the same at every level, and
an organization policy can set them too.
Guidance shows only while **Show preview features** is on.

### Beside a finding

- **Explain** shows how important the rule is (Critical, Recommended, Educational or Advanced) and its
  area, why it matters, what to do, when it's fine to leave, and how to check a change worked, from
  the rule's section of the notebook or SQL best-practices guide. Importance only explains a rule: its
  severity and your organization's policies decide what is flagged and required. That text ships with
  the app, so it works offline and nothing is sent anywhere.
- **Show example** shows the guide's Before and After. **Copy example** copies the After; your code
  isn't changed for you.
- **Try the fix**, at every level, shows a rewrite as a preview, for the rules that have a safe one. It
  is offered only when KratosForge can show the rewrite returns exactly what the SQL returned before.
  Otherwise the button isn't there:
    - **SQ002**: `YEAR(order_date) = 2026` becomes `(order_date >= '2026-01-01' AND order_date < '2027-01-01')`,
      when `order_date` is a date or date-and-time column (not `datetimeoffset`) of the one table that
      has it. It needs the column types: on a warehouse, load its objects in the explorer first.
    - **SQ005**: `sales` becomes `dbo.sales`, when `dbo`, the default schema, is the only schema with
      that name.

  Nothing changes until **Apply**, which is one edit in the editor: **Ctrl+Z** undoes it. On a
  warehouse or SQL analytics endpoint, **Compare before applying** runs the statement as written, then
  as fixed. That works for a SELECT, or the query of CREATE or ALTER VIEW. It asks first each time:
  both versions only read, and both use capacity. It then says whether the rows are the same (every
  row compared, or a checksum above the comparison limit) and gives each run's measured time. The
  fixed version runs second, so data the first run cached can favor it. **Stop** ends a Compare that
  is still running. A lakehouse tab's T-SQL doesn't run on this PC, so it has no Compare. Each fix
  ends with the principle behind it.
- **Dismiss** hides the finding in your own Best practices panels on this PC: **Not on this line**
  (that line, and any line in the same file that reads exactly the same, until it changes), **Not in
  this file** (this notebook or saved SQL file, by its folder and name; an unsaved SQL tab by its
  lakehouse or warehouse and tab name) or **Never show** the rule. Only a fingerprint (SHA-256) of
  the line is kept, never your code. The check still runs, and governance checks and everything else
  that reads findings still see it. The panel's **N dismissed** list has **Show again** for each, and
  **Show all again**.
- A rule a governance policy for the project turns on can't be dismissed: the finding says **Your
  organization requires this rule**, and an earlier dismissal of it doesn't hide it. A policy that
  only changes a rule's severity doesn't stop you dismissing it.

The first time you open Learn, a card at the top of Home asks **Would you like KratosForge to help you
improve your development skills?** with the three levels and Professional picked. **Save** keeps the
level you picked; **Skip** keeps Professional. Either way it isn't asked again, and choosing another
level with **Learning guidance** answers it too. It isn't asked when an organization policy sets it.

### Under a Query profile

A notebook result's **Query profile** notes what the run measured that a best-practice rule
explains, such as a scan that read 3,000,000 rows to keep 369,600 because its filter wraps the column
in `year()`. Each note links **About** its rule, and appears only when the profile shows it
([Notebooks](notebooks.md)).

## In your profile

When every check of an exercise passes in a published lesson or lab, it is recorded in your
[Profile](profile.md) like a clean Validate run, unless recording is off there. The last exercise of a
lesson also records the lesson as finished. These can earn The First Spark, The Data Smith, The
Query Forger, The Architect's Eye and The Guardian. Your answer is never stored in the profile. Draft
lessons are for trying out and aren't recorded.

## What is stored

A started lab is a folder under this PC's KratosForge data: `learn/<id>/` holds the lab's DuckDB
database, `lab.json` (which data was generated, and the lesson's version) and `answers/`, your SQL
for each exercise. Nothing is sent anywhere. The lab's database is opened with file and network
access turned off.

Lessons ship with the app. [Writing a Learn lesson](learn/writing-lessons.md) is for people who
write or review them.

## Engine methods

| Method | What it does | Changes |
| --- | --- | --- |
| `learn_catalog()` | Every path and lesson, and whether its lab is started on this PC | Nothing |
| `learn_module(module_id)` | One lesson: its text, exercises (never the solution) and its lab's state | Nothing |
| `learn_lab_start(module_id)` | Makes the lab if it isn't there | The lab's folder |
| `learn_lab_save(module_id, exercise_id, text)` | Saves your SQL for an exercise | The answer file |
| `learn_lab_run(module_id, sql)` | The first 200 rows of your SQL, run and undone | Nothing |
| `learn_lab_check(module_id, exercise_id, answer?)` | The exercise's checks on your SQL | Nothing |
| `learn_lab_hint(module_id, exercise_id, level)` | One hint | Nothing |
| `learn_lab_reset(module_id)` | Deletes the lab and makes it again | The lab's folder |
