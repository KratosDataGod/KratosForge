# Optimization engine (OE-001, Preview, engine only)

The optimization engine looks at a Fabric workspace, or every workspace on a capacity that you can
see, and finds work that doesn't need to run, runs twice, or could run better. Every finding is an
**opportunity** with its evidence, how sure the engine is, what it is worth (measured, estimated or
Unknown), what to do next, how to check it and how to undo it.

This first slice is the engine and one call, `optimization_assess`. The Optimize > Assessment screen
waits on a mockup. The inventory, gap analysis, backlog and architecture are in [engine-plan.md](engine-plan.md).

## What it reads

Reads only. No Fabric job starts, and nothing is written to Fabric or OneLake.

| Read | Fabric API | Why |
| --- | --- | --- |
| The workspace, or the workspaces on a capacity | `GET /workspaces/{id}`, `GET /workspaces` | What to assess. A capacity assessment covers only the workspaces you can see, up to 25. |
| Its items | `GET /workspaces/{id}/items` | The workloads. |
| Notebook and pipeline definitions | `POST /workspaces/{id}/items/{id}/getDefinition` | The code and activities to analyze. Up to 300 per workspace. |
| Their job history | `GET /workspaces/{id}/items/{id}/jobs/instances` | How often they run, on a schedule or not, and for how long. |

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
| OE-DUP-NB | Notebooks with the same code once comments, docstrings, spacing, quote style and SQL letter case are set aside, compared by Python syntax tree, cell by cell, with the same default lakehouse | Eliminate the copies' runs | Proven; High when a default lakehouse or run history is missing |
| OE-DUP-NB-PARAM | The same code with different default lakehouses: not duplicate processing, but duplicate maintenance | Consolidate into one parameterized notebook | Proven (no capacity is claimed) |
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

## The call

`optimization_assess(workspace_id="", capacity_id="", days=30, demo=False)`

Returns `workloads`, `opportunities` (ranked: counted first, then by confidence, known size, effort, risk),
`totals`, `counts`, `coverage` (what was and wasn't read, which item types aren't analyzed yet), `price`,
and `next_action` (the top opportunity's next step, or what to fix when nothing could be read). When
nothing is found it says that isn't proof there is nothing.

`demo=True` runs a **demonstration fixture**: a made-up workspace, runs and usage file, labelled as a
fixture on the result and on every opportunity. It makes no Fabric call. It shows how the engine works;
nothing in it is a finding or a saving.

## Not done yet

The screen (mockup first), sharing opportunities to the central lakehouse, rejecting one so it isn't
raised again, near-duplicate notebooks, duplicate tables and semantic models, SQL and Spark analyzers,
local execution classes and the network model, the capacity planner and the savings ledger. See the
backlog in [engine-plan.md](engine-plan.md).
