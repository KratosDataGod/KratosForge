# Mutation testing, batch 33

Three modules, none touched by an open pull request:
- Line magics and Spark SQL in notebook cells: a triple-quoted string with text after it on its line, a bracket or escaped quote inside a string on the line before a magic, extra spaces between a magic's words, which statement decides whether SQL returns rows, a quoted name before the verb, a backslash inside quotes and backticks, and what follows a block comment (`magics.py`).
- Cost and capacity figures: each F and P SKU's size, which saved settings are taken, a time with an offset, a run under a second, the CUs of each engine, the reasons given when a figure isn't known, the price note, the report's rounding and order, and the share of a capacity (`costs.py`).
- Kinds of repository and where notebooks find their data: the checked kind, the message when a stated kind would change, an unreadable or readable `locations.toml`, `runs_on = "this_pc"` winning over a workspace, and the 256-character limit on a location's fields (`git/scopes.py`).

The run was at `main` b76c42d29 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 32 (sample cap 150, seed 7), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways (509 passed, and the tests added after that passed the same way), so no run below can score a false 100%. `test_databricks_notebook_helpers.py` reads `docs/`, so it was left out of the `magics.py` run.

The new tests point `LOCALAPPDATA` and `HOME` at the test's own folder, and use no network and no timing.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `magics.py` | test_notebook_magics, test_notebook_magics_windows, test_notebook_formats_edges, test_opportunities_analyzers_mutation (+ test_notebook_magics_mutation) | 150 of 221 | 130 / 20 | **86.7%** | 147 / 3 | **98.0%** | 3 equivalent |
| `costs.py` | test_capacity*, test_estate*, test_measurement, test_risk_service_edges, test_usage_* (12 files, + test_costs_mutation) | 150 of 189 | 107 / 43 | **71.3%** | 150 / 0 | **100%** | none |
| `git/scopes.py` | test_git_scopes, test_git_cli_and_scopes_edges, test_git_service_edges, test_git_service_more_edges, test_git_service_mutation, test_git_service_platform_mutation, test_notebook_places (+ test_git_scopes_mutation) | 77 of 77 | 69 / 8 | **89.6%** | 76 / 1 | **98.7%** | 1 equivalent |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **magics.py**:
  - A triple quote with text after it on its line still opening a string, so a magic inside it is left alone.
  - A string's own end: a bracket inside a string not counting, `\"` not ending a string, and the scan not running past a string's end.
  - Extra spaces between a magic's words making no empty words.
  - `returns_rows` being `False` (not just falsy) for empty SQL, and the last statement deciding, not the first.
  - A backticked name holding a verb before a `WITH`'s own verb not counting as the verb.
  - A backslash escaping inside quotes but not inside backticks, a backslash at the very end of an open quote kept, and a semicolon after a closed string splitting.
  - What follows `*/` kept in full.
  - → `test_notebook_magics_mutation.py`.
- **costs.py**:
  - Every F, P and trial SKU's size, and SKUs that have none.
  - Settings taking only known keys, and only when they have a value.
  - A time with an offset keeping it and one without being UTC.
  - Runs under a second counted, and negative or boolean times not.
  - The rough scenario being time here times the CUs of a Python notebook.
  - A metadata run keeping its name and environment when its time isn't known.
  - A Fabric run's workload needing both its workspace and item, its plain kind and name, and why its time isn't known.
  - The CUs and the wording for each engine, Table maintenance counted as Spark.
  - An unknown cost keeping its reason or saying "CU use isn't known", and the price note and method added only when there's CU to price.
  - "1 of 1 run ... has" and "2 of 2 runs ... have".
  - The report's rounding (seconds to 1 place, CU hours to 3, money to 2), items ranked by their cost plus what they avoided, then longest first, and each item's kind, name and environment.
  - A capacity's share over its whole period, and why it isn't known.
  - → `test_costs_mutation.py`.
- **git/scopes.py**:
  - `check_kind` returning the kind.
  - Changing a stated kind naming both kinds.
  - An unreadable `locations.toml` existing and saying why, and a readable one existing with no problem.
  - `runs_on = "this_pc"` winning over a workspace, and a workspace with no lakehouse attaching none.
  - A location field of 256 characters kept and one of 257 refused.
  - → `test_git_scopes_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `magics.py` | 189 | the bracket depth's sign flipped | Equivalent: only `depth == 0` is compared, and flipping every step's sign keeps that the same. |
| `magics.py` | 213 | `i < 0` → `i < 1` after finding the end of a `--` comment | Equivalent: the newline found is at least two characters past the `--`, so it is never at 0. |
| `magics.py` | 218 | `end < 0` → `end < 1` after finding `*/` | Equivalent: `*/` is searched for from two past the `/*`, so it is never found at 0. |
| `git/scopes.py` | 156 | `parents=True` → `False` when writing a new repository's files | Equivalent: every starting file is at most one folder deep, and the repository's own folder already exists. |

### Not in this batch

Batch 34's modules, which ran at the same time.

