# Mutation testing, batch 38

Three modules not covered before, none touched by an open pull request, each with tests that run in under 30 seconds. This is the first batch of new modules run with the tool's default `--kinds all`, so each score counts the boundary mutants that batch 36 added (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Fabric CU avoided by a run on this PC, worked out from Fabric runs of the same workload: which baselines are recent enough, the five newest, scaling by rows read, and how the estimate is worded (`calibration.py`).
- Reading a usage file a person exported: the size and row limits, the separator, the decimal mark, day-first or month-first dates, and time zone offsets (`usage/reader.py`).
- Freshness promises for tables: checking a `[freshness]` table, when a table is late against `within_hours` or `by`, when it is next due, the order of the report, and rewriting the entity file (`meta/freshness.py`).

`databricks_bytes.py` (Databricks SQL's byte-level string functions) was run too. It scored 50 of 50 with its current tests and has no boundary points, so it needs no new tests.

The run was at `main` 811af46d0 (2026-11-15; the branch was then moved to 1dab3b763, which adds batch 37 and doesn't change these modules), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. Three tests in `test_databricks_coverage.py` read `docs/`, so they were left out of the `databricks_bytes.py` run, as in batch 35.

The new tests are pure: they write no files and use no network or timing. The usage reader tests use the module's real limits (a 64 MB file and a 250,000-row file are built in memory), not monkeypatched ones, so a changed limit fails them.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `calibration.py` | test_calibration (+ test_calibration_mutation) | 65 of 65 (60 + 5) | 45 / 20 | **69.2%** | 44/60 → 59/60 | 1/5 → 5/5 | 64 / 1 | **98.5%** | 1 equivalent |
| `usage/reader.py` | test_usage_dates, test_usage_finite, test_usage_import, test_usage_reader_store_edges (+ test_usage_reader_mutation) | 150 of 194 (140 + 10) | 125 / 25 | **83.3%** | 120/140 → 139/140 | 5/10 → 10/10 | 149 / 1 | **99.3%** | 1 equivalent |
| `meta/freshness.py` | test_dataops (+ test_meta_freshness_mutation) | 108 of 108 (103 + 5) | 76 / 32 | **70.4%** | 76/103 → 102/103 | 0/5 → 5/5 | 107 / 1 | **99.1%** | 1 equivalent |
| `databricks_bytes.py` | test_databricks_ip_bytes_edges, test_fips, test_databricks_coverage | 50 of 50 (50 + 0) | 50 / 0 | **100%** | 50/50 | none | no change needed | **100%** | none |

Every boundary mutant in the three modules is now killed: 6 of 20 were killed before. No real bugs were found.

### What the survivors were, and the tests that now kill them

- **calibration.py**:
  - A Fabric run at this very moment, and one exactly at the age limit, used; one half a second past the limit not.
  - A measured baseline without a value left out.
  - A version with no baselines at all saying there is nothing to compare with, not that the versions differ.
  - Only the five newest runs used, with their date range named.
  - One run named in the singular.
  - Rows scaling only when both sides read more than none, and one row on each side still scaling.
  - The baselines' sources named, or "Fabric runs of this workload" when none is recorded.
  - Three measured runs firm, while three estimated or two measured runs rough.
  - → `test_calibration_mutation.py`.
- **usage/reader.py**:
  - A file of exactly 64 MB not refused for its size, one byte more refused with the limit named in MB, and the limit itself pinned.
  - Exactly 250,000 rows read and one more refused, with the message naming the limit.
  - An Excel workbook named as one.
  - The separator taken from the header line only.
  - Exponent notation such as `1e5` not read as a number.
  - `UTC` giving the UTC zone.
  - Offsets up to 14 hours and 59 minutes read (including `+02:30`), and `+15:00`, `+01:60` and `-15` refused.
  - `01/12/2026` still reading both ways, since 12 can be a month.
  - A short row giving empty cells for its missing columns.
  - → `test_usage_reader_mutation.py`.
- **meta/freshness.py**:
  - A promise described in words, for hours, a time of day or both.
  - A `[freshness]` that isn't a table saying how to write one.
  - Either half of a promise enough, any hours above 0 allowed (0.5 and 1), and 0 refused.
  - A run ending its seconds after it started, keeping its own offset; a start without one read as UTC; no seconds recorded meaning it ended when it started.
  - Age in hours to two places.
  - The due time given; exactly at the promised hours still fresh and a second later late.
  - By a time of day: late from that very minute, fresh a minute before, and a run at midnight counting as today, both for lateness and for the next due time.
  - The latest run's failure shown only when it didn't succeed.
  - The report listing late tables first, then never loaded, then fresh.
  - Removing a promise leaving no blank lines at the end.
  - → `test_meta_freshness_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `calibration.py` | 26 | `return False` → `return None` | Equivalent: `_usable` is only used as a filter, where None is as false as False. |
| `usage/reader.py` | 165 | `int(m.group(3) or 0) > 59` → `or 1` | Equivalent: with no minutes written the value is 0 or 1, and neither is over 59. |
| `meta/freshness.py` | 129 | `"fresh": 2` → `3` in the report order | Equivalent: fresh still sorts after late (0) and never loaded (1). |

