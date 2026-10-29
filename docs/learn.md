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

## A lesson or lab

Opening one shows its lesson on the left and **Checks** on the right.

1. **Start the lab** makes the lab's data on this PC: generated tables, the same every time, with
   known problems planted for some labs to find. Nothing is downloaded.
2. Write SQL in the box. **Run** (or Ctrl+Enter) shows the first 200 rows. Every run is undone
   afterwards, so the lab's data stays as it was whatever you run.
3. **Check my answer** runs the exercise's checks and says what each one found: the same result as
   the reference, no best-practice findings, faster than the starting query, and so on.
4. **Show a hint** gives one hint at a time, from a nudge to nearly the answer.

Speed checks are measured on this PC only. A Fabric Warehouse or SQL analytics endpoint plans and
runs the same query differently.

**Reset lab** deletes the lab's folder on this PC, your answers included, and makes it again. It asks
first. **Leave** goes back to Learn and keeps your answers.

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
