# Mutation testing, batch 326

One module, run uncapped: every mutant, not a sample. This is the first mutation run of this module.
- Spark's timestamps in the session time zone on this PC (`spark_time_zone.py`, WR-120).

The run was at `main` ea988e99e (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 159 of 159 mutants ran (152 point + 7 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed).

The set is test_spark_time_zone_cov2, test_spark_timezone_state and test_spark_timezone_values, about 15 s a session. One test was left out with `--deselect`: test_spark_timezone_values' `test_spark_time_zone_answers_as_spark_3_5_or_is_refused_by_name` reads `docs/sql-compatibility.md`, outside `sidecar/`, so it fails in mutate.py's copy of `sidecar/` before any mutant is made. Its Spark 3.5.3 value cases therefore count for nothing here. The score counts only the tests run. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_time_zone.py` | test_spark_time_zone_cov2, test_spark_timezone_state, test_spark_timezone_values (one test deselected) (+ test_spark_time_zone_mutation) | 159 of 159 (152 + 7) | 103 / 56 | **64.8%** | 100/152 → 135/152 | 3/7 → 6/7 | 141 / 18 | **88.7%** | 18 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_time_zone.py**:
  - zone_id refuses an offset past Spark's range (minutes or seconds of 60, past 18:00 by a minute or a second, +19) with "isn't a zone Spark reads", and one with minutes or seconds inside it (+05:59, +05:00:59, +17:59:59) with "can't be used on this PC"; +18 and -18 are read, and are DuckDB's to refuse.
  - gaps: () for UTC and Etc/GMT+8, (3600,) for America/Chicago and Australia/Eucla (+08:45 and +09:45, whole hours apart), (3600, 1800) for Australia/Lord_Howe and Africa/Cairo (whose local mean time ended in 1900).
  - A day of each unit (24 HOUR, 1440 MINUTES, 86400 SECOND, and the plural forms) is a calendar day; 25 hours is a day and 3600 seconds; an interval not added or subtracted is left.
  - One gap writes one earlier instant to try.
  - A number and text with its own offset are instants and not wrapped, nor is a cast of one; a cast of text is wrapped, once, however many times fix runs.
  - On DuckDB in America/Chicago, 2024-11-03 01:30 read as text, through nested casts, is 06:30 UTC, the earlier instant; earlier_in gives the earlier of two instants and leaves a time shown once.
  - → `test_spark_time_zone_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_time_zone.py` | 60 (2) | `minutes or 0` or `seconds or 0` → `or 1` in the `> 59` tests | Equivalent: 1 is not more than 59 either. |
| `spark_time_zone.py` | 86 | `lru_cache(maxsize=64)` → 65 | Equivalent: only how many zones are kept. |
| `spark_time_zone.py` | 99 (4) | years to 2101 → 2102; months 4, 7 or 10 → 5, 8 or 11 | Equivalent on this tz database: every zone it has gives the same gaps either way (checked for each). |
| `spark_time_zone.py` | 140 (2) | the default gaps `[3600, 1800]` → 3601 or 1801 | Equivalent: fix runs the passes only when the session has gaps, so the default is never used. |
| `spark_time_zone.py` | 140 | gaps tried shortest first | Equivalent: an instant one gap earlier with that gap's offset change is the only earlier one showing the same wall clock, so at most one WHEN holds. |
| `spark_time_zone.py` | 162 | `_put` returns None | Equivalent: its callers here don't read what it returns. |
| `spark_time_zone.py` | 176 (4) | `_depth` returns None, `1 - …`, `0 + …`, `default=1` | Equivalent: a wrapper's operand goes in the list outside its lambdas, and the lambdas' bodies hold no other wrapper, so two wrappers' names are never in scope together (nested casts give the right instant with the names the same). |
| `spark_time_zone.py` | 242 (3) | `* (sign)` → `// (sign)`, `seconds >= 0` → `>= 1` or `> 0` | Equivalent: `n // -1` is `-n`, and at 0 seconds there are 0 days either way. |
