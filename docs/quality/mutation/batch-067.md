# Mutation testing, batch 67

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The access cache: snapshot ids, the rows a refresh writes, and the per-workspace view OneLake > Access shows (`access_cache.py`).
- Opt-in usage data: counting, how long unsent days are kept, and what is sent (`telemetry.py`).
- The Snowflake function list's status on this PC and the page made from it (`snowflake_coverage.py`).

The run was at `main` 9911af348 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. `test_snowflake_coverage.py` compares the committed `docs/snowflake-functions.md`, so it was left out for `snowflake_coverage.py`, which is why that module's "before" score is low; no other test in these sets reads `docs/`, `examples/` or `app/`.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `access_cache.py` | test_access_cache, test_access_cache_service_edges (+ test_access_cache_mutation) | 76 of 76 (76 + 0) | 65 / 11 | **85.5%** | 65/76 → 76/76 | none | 76 / 0 | **100%** | none |
| `telemetry.py` | test_telemetry, test_corrupt_state_reads (+ test_telemetry_mutation) | 81 of 81 (78 + 3) | 59 / 22 | **72.8%** | 58/78 → 77/78 | 1/3 → 3/3 | 80 / 1 | **98.8%** | 1 equivalent |
| `snowflake_coverage.py` | test_snowflake_coverage_edges (+ test_snowflake_coverage_mutation) | 85 of 85 (84 + 1) | 22 / 63 | **25.9%** | 22/84 → 84/84 | 0/1 → 1/1 | 85 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **access_cache.py**:
  - No scans means no snapshots.
  - A snapshot id is the first 24 hex characters of the rows' SHA-256, and the same rows give the same id whatever the order of keys or rows.
  - A scan keeps the workspace and lakehouse names, or blanks when there are none.
  - A workspace seen only through one of its lakehouses is neither stale nor known.
  - The workspace scan's name wins over a lakehouse scan's, unless it is blank.
  - The summary counts people, groups, apps and guests exactly.
  - A lakehouse role's paths, Entra ids and item members are read back from JSON, and blanks read as empty lists.
  - → `test_access_cache_mutation.py`.
- **telemetry.py**:
  - Unsent days are kept for exactly 30 days: on 2026-10-17, 2026-09-17 is still sent and the day before it is dropped.
  - The last day sent is the latest one, nothing is sent while usage data is off, and today waits until it's over.
  - The build is the commit's first 12 characters, or "dev".
  - The install id is made once and returned, and Settings shows the one on file.
  - A file that can't be read reads as empty, and the file is key-sorted JSON with one-space indents.
  - The Windows version is sent on Windows, and the platform elsewhere.
  - An envelope is one custom event, version 2, with key-sorted string properties.
  - A report is posted once, with a 15-second limit and the caller's wait. A status of 400 or more keeps the days queued, and 399 doesn't.
  - → `test_telemetry_mutation.py`.
- **snowflake_coverage.py**:
  - The list is read from the JSON beside the module, the page is the repository's `docs/snowflake-functions.md`, and importing the module writes nothing.
  - A case name drops the "(what)" and upper-cases a function. `parse_json path` becomes `Path (v:a)` and operators are left as written.
  - An error is noted by its first line, cut to 160 characters, or by its type when it has no message.
  - A check that differs gives its own note, or the value it got. Checks that all run are counted ("1 value check", "2 value checks"), and their labels are named once each.
  - A function with checks takes its status from them, and one without is probed.
  - A sample runs without the two small tables unless asked. One DuckDB can't run fails with its first line, and one sqlglot can't read is unread. A refused one gives the reason without the statement prefix, and a labelled one differs with every label.
  - A listed sample runs once, on the two tables. A probe tries 0 to 4 NULLs, puts a window function inside `OVER`, stops at the first arity that runs, and prefers a refusal to DuckDB's errors.
  - The sample is shown only for a function without checks.
  - The page counts each status and gives each group a table with a Sample column only when the group has a sample. Pipes are escaped and lines joined.
  - → `test_snowflake_coverage_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `telemetry.py` | 95 | `destination()[0]` to `destination()[1]` in `enabled()` | Equivalent: `destination()` returns a connection string and whose it is together, or two empty strings, so both parts are empty or both are set. |

It doesn't involve text input, so leading or trailing whitespace and regex edge cases can't change it.

