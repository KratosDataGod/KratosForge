# Mutation testing, batch 40

Three modules not covered before, none touched by an open pull request, run with the tool's default `--kinds all`, so each score counts the boundary mutants that batch 36 added (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- A run's scorecard: which side was faster (a tie counts for this PC), the estimated Fabric cost with its assumptions and rounding, this PC's measured zero job CU, the correctness counts, and the notes (`scorecard.py`).
- What an optimization opportunity is worth: imported CU per workload (a row counted once, rows without a value or time skipped), measured run time, the monthly figure scaled from the measured period (never below one day), Unknown when any part isn't measured, and a CU value that needs a price and a currency (`opportunities/impact.py`).
- `mapInPandas`, `mapInArrow` and `groupBy().applyInPandas` on this PC's Spark: the batch size setting, the schema check, empty results, null group keys, key order, a clean index in each group, and a function whose signature can't be read (`spark_map.py`).

The run was at `main` a13d0a0fc (2026-11-15; the branch was then moved to a1e0f60c9, which only adds Windows shell tests and doesn't change these modules), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these runs reads `docs/` or a module's source.

`test_prove.py` also reaches `scorecard.py`, through a whole proof run, but takes 46 seconds a run, so it was left out of the scorecard runs. That makes the scorecard's "before" lower than what the whole suite pins; the "after" needs only the two files listed.

The new tests write no files and use no network or timing. The Spark tests run code through a notebook kernel, as `test_spark_map.py` does.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `scorecard.py` | test_scorecard_cost (+ test_scorecard_mutation) | 50 of 50 (49 + 1) | 20 / 30 | **40.0%** | 20/49 → 49/49 | 0/1 → 1/1 | 50 / 0 | **100%** | none |
| `opportunities/impact.py` | test_opportunities, test_opportunities_share, test_opportunities_analyzers_mutation, test_risk, test_workload_health (+ test_opportunities_impact_mutation) | 68 of 68 (68 + 0) | 48 / 20 | **70.6%** | 48/68 → 65/68 | none | 65 / 3 | **95.6%** | 3 equivalent |
| `spark_map.py` | test_spark_map (+ test_spark_map_mutation) | 74 of 74 (72 + 2) | 51 / 23 | **68.9%** | 50/72 → 65/72 | 1/2 → 2/2 | 67 / 7 | **90.5%** | 7 equivalent |

`opportunities/impact.py` has no `<` or `>` comparisons (its floors use `max`), so it has no boundary points. No real bugs were found.

### What the survivors were, and the tests that now kill them

- **scorecard.py**:
  - No comparison without both times (None or 0 on either side); this PC faster, Fabric faster, and a tie reported for this PC.
  - The Python and Spark CU rates, the CU-seconds rounded to one place and the amount to four, an amount that is exactly CU hours times the price, the three assumptions word for word, and the price kept out of the CU measurement's assumptions.
  - Empty and None ids dropped from the measurement.
  - No job time (None or 0) giving "not available" with its reason and no figures.
  - This PC's side: a measured 0 at the finish time, the start time when there is no finish, and Unknown without either.
  - Correctness counts of tables, matches and levels; time and resources; the cost text for an estimate and for a run that never reached Fabric; the engine defaulting to Python.
  - The notes: Fabric's total only with both its times, the kernel process or the engine process (a CPU time of 0 still counts), and Fabric's own note.
  - → `test_scorecard_mutation.py`.
- **opportunities/impact.py**:
  - Imported rows summed per workspace and item (names trimmed and case-folded), their period spanned, a row with no end ending at its start, and the files named (or the import's id, or "an imported file").
  - A row counted once across imports, while a row skipped for having no value doesn't use up its key; a measured 0 still counted.
  - Run time and CU measured with their sources and methods, and each Unknown reason; a zero run total measured, not Unknown.
  - The monthly figure: each part scaled to 30 days, a period under a day counted as one, Unknown when any part is (with its reason, or "Not measured.").
  - The days of a measured period, and the fallback without both ends.
  - Avoidable impact for one workload, for two (no single workload id; Unknown CU when one has no usage) and for part of a workload; per-workload figures.
  - A CU value needing a known CU, a price above 0 and a currency, with the price's source in its assumptions.
  - → `test_opportunities_impact_mutation.py`.
- **spark_map.py**:
  - The default batch of 10,000 rows (10,001 rows come as 10,000 and 1), a setting of one row used, and a bad, zero or negative setting falling back to 10,000.
  - A schema that is neither DDL nor a StructType refused.
  - `mapInArrow` refusing a non-batch, and an empty result keeping the schema.
  - `applyInPandas` refusing a non-frame, keeping null keys as a group, calling groups in key order, giving each group a clean index with only the frame's columns (with and without the key), and passing only the frame to a function whose signature can't be read.
  - Grouping names from strings, other values and the `cols` attribute, and no columns refused.
  - → `test_spark_map_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `opportunities/impact.py` | 49 | `start is None or end is None` → `and` | Equivalent: `end` falls back to `start`, so it is None only when `start` is. |
| `opportunities/impact.py` | 89, 114 | `zip(..., strict=True)` → `strict=False` | Equivalent: both lists are built from the same workloads, so they are always the same length. |
| `spark_map.py` | 62, 73 | `barrier=False` → `True` | Equivalent: `barrier` is accepted for Spark's signature and not used on one PC. |
| `spark_map.py` | 70, 132 | `ignore_index=True` → `False` | Equivalent: the joined frame's index isn't used; `createDataFrame` reads only the columns. |
| `spark_map.py` | 90, 91 | `zip(..., strict=True)` → `strict=False` | Equivalent: a batch's columns have the same length, and the names match the columns. |
| `spark_map.py` | 124 | the early return for an empty frame dropped | Equivalent: an empty frame has no groups, so the loop adds nothing and the same empty result is returned. |

