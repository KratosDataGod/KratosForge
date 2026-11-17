# Optimization engine (OE-001, OE-003 and OE-004, Preview)

The optimization engine looks at a Fabric workspace, or every workspace on a capacity that you can
see, and finds work that doesn't need to run, runs twice, or could run better. Every finding is an
**opportunity** with its evidence, how sure the engine is, what it is worth (measured, estimated or
Unknown), what to do next, how to check it and how to undo it.

Use it in **Optimize > Assessment** (Preview): pick a workspace or a capacity and the run history period, then choose **Assess**, or try the demonstration fixture first. The engine call behind it is `optimization_assess`. The inventory, gap analysis, backlog and architecture are in [engine-plan.md](engine-plan.md).

## What it reads

Reads only. No Fabric job starts, and nothing is written to Fabric or OneLake.

| Read | Fabric API | Why |
| --- | --- | --- |
| The workspace, or the workspaces on a capacity | `GET /workspaces/{id}`, `GET /workspaces` | What to assess. A capacity assessment covers only the workspaces you can see, up to 25. |
| Its items | `GET /workspaces/{id}/items` | The workloads. |
| Notebook and pipeline definitions | `POST /workspaces/{id}/items/{id}/getDefinition` | The code and activities to analyze. Up to 300 per workspace. |
| Their job history | `GET /workspaces/{id}/items/{id}/jobs/instances` | How often they run, on a schedule or not, and for how long. |

One assessment makes at most 1,500 Fabric reads and stops reading after 4 minutes. Anything past that budget is listed as not read, and `scope.budget` says the budget was reached.

On this PC it also reads the usage files you imported (Optimize > Cost and savings), which are the only
source of measured CU, and the price per CU hour.

No new permission is needed: getDefinition uses the Item.ReadWrite.All scope sign-in already asks for
(see docs/permissions.md), and the call never writes. A definition or history you can't read is listed in
the result's `coverage`, never guessed.

Code is parsed, never run. The result keeps no notebook code or pipeline JSON beyond the evidence each
opportunity cites (a Copy activity's source settings, a cell and line number).

## What it finds (this slice)

| Rule | Finds | Action | Confidence |
| --- | --- | --- | --- |
| OE-DUP-NB | Notebooks on the same default lakehouse with the same code once comments, docstrings, spacing, quote style and SQL keyword case are set aside (string literals keep their case), compared by Python syntax tree, cell by cell | Eliminate the copies' runs | Proven; High when run history is missing |
| OE-DUP-NB-SUSPECT | The same code with no default lakehouse in the definitions: which data each reads isn't known | Check, then consolidate; nothing is marked to remove and no saving is claimed | Medium |
| OE-DUP-NB-PARAM | The same code on different default lakehouses: not duplicate processing, but duplicate maintenance | Consolidate into one parameterized notebook | Proven (no capacity is claimed) |
| OE-DUP-COPY | Copy activities in different pipelines with the same source and the same sink | Eliminate one | High; Medium when the source uses expressions |
| OE-REPEAT-EXTRACT | Copy activities with the same source and different sinks | Consolidate: land once, share (shortcut) | Medium |
| OE-FULL-REFRESH | Workloads that run on a schedule and rewrite whole tables: an overwrite with no replaceWhere (rule NB007), INSERT OVERWRITE without a partition, CREATE OR REPLACE TABLE ... AS SELECT, a Copy whose table action is Overwrite | Optimize in place (incremental) | Medium |

Names are never compared. Two notebooks called the same thing with different code are not duplicates.

## What an opportunity is worth

Impact uses the measurement model (docs/finops/plan.md): measured, estimated, scenario or Unknown.

- **Run time** is measured from Fabric's job history (end minus start of each finished run).
- **CU** is measured only from an imported Capacity Metrics or Cost Management export, matched to the item
  by workspace and item name. Without one, CU is **Unknown**, never 0.
- **Per month** figures are estimates: the measured period scaled to 30 days.
- When an opportunity removes only part of a workload (an incremental load instead of a full one), how
  much is Unknown until it's measured with a before and after run. No percentage is assumed.
- **CU value** is the CU at your price per CU hour (or the default list price, and the result says which).
  It is never called a saving: `realizable` is false, because money is saved only when a capacity is
  resized, paused or retired, or on pay-as-you-go. **Verified** is Unknown until a change is implemented
  and measured.

## Totals never count twice

Totals add up per workload, not per opportunity. When two opportunities touch the same workload, the one
earlier in the six-question order counts (eliminate, consolidate, optimize in place, redesign, reschedule,
run locally, hybrid, keep in Fabric), then the surer one; the other is marked `counted: false` with
`overlaps` naming it. A workload whose saving isn't measured yet adds an Unknown part, which makes the
total incomplete (`complete: false`, `unknown_parts`), never bigger.

## The Assessment tab

- **Highest-value next action**: the top counted opportunity, what's there now and what to do.
- **Totals per month**: Fabric run time freed, capacity freed, CU value and Verified, each labelled Measured, Estimated or Unknown, with **Incomplete** when part is Unknown, and the reminder that freed capacity is not money saved.
- **What was read**: items, what was analyzed, what couldn't be read and why, item types not analyzed yet, and every workload's runs.
- **Opportunities**: ranked; rows not counted in the totals are dimmed and say why.
- Choosing an opportunity opens its evidence (with sources), what it's worth and how that was worked out, the checks before changing anything, how to undo it, other ways, and buttons that open its items in Fabric. A demonstration item has nothing to open.

## The call

`optimization_assess(workspace_id="", capacity_id="", days=30, demo=False)`

Returns `workloads`, `opportunities` (ranked: counted first, then by confidence, known size, effort, risk),
`totals`, `counts`, `coverage` (what was and wasn't read, which item types aren't analyzed yet), `price`,
and `next_action` (the top opportunity's next step, or what to fix when nothing could be read). When
nothing is found it says that isn't proof there is nothing.

`demo=True` runs a **demonstration fixture**: a made-up workspace, runs and usage file, labelled as a
fixture on the result and on every opportunity. It makes no Fabric call. It shows how the engine works;
nothing in it is a finding or a saving.

## Sharing an assessment (OE-004)

`optimization_share(share_id, approve=False, confirm="")` adds an assessment made on this PC to the
central Optimize lakehouse, so a team sees one list. An assessment returns its `share_id`. This PC keeps
its last 5 assessments in memory. After a restart, or once 5 newer ones are made, assess again. The rows
are rebuilt from what this PC kept, never taken from the app.

- Optimize's own writer and ask do the writing (the Opportunities tab reads the same tables). Without
  `approve`, nothing is written: the call says what it would add and gives a `confirm`. It writes only
  with `approve` and that `confirm`. If the assessment changed since, it asks again. The call is
  audited fail-closed: its audit line is written first, and it is refused if that line can't be written.
- Each opportunity becomes one finding and one recommendation in `fdopt_findings` and
  `fdopt_recommendations`:
  - rule `oe.<rule>` (for example `oe.dup-nb`);
  - the item to keep, or else the first by id, as its item;
  - the opportunity id as its target.
  The same rule on the same items is the same finding on every PC. If one is **dismissed** in Optimize,
  it stays dismissed when it's found again. If the set of items changes, it is a new opportunity.
- Severity: High for proven or high confidence, then Medium and Low. Info is for an opportunity not
  counted in the totals.
- The evidence and impact are kept as JSON. Anything that looks like a secret is removed from them
  and from all text, both before the rows reach the writer and again in the writer. No code or data
  is shared.
- A demonstration fixture is never shared.

The Share button on the Assessment tab comes next, after its mockup.

## Not done yet

A background job with progress and Stop for large capacities, the Share button on the Assessment tab, near-duplicate notebooks, duplicate tables and semantic models, SQL and Spark analyzers,
local execution classes and the network model, the capacity planner and the savings ledger. See the
backlog in [engine-plan.md](engine-plan.md).
