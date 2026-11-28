# Mutation testing, batch 461

One module not scored before, run with `--kinds all`:
- Cross-platform verification cases run on this PC: the case file's format checked, each case's dataset made in DuckDB, its statement run through the DuckDB engine session (or the local T-SQL translation), the answer compared with the documented one under the normalizations the case names, and a run record per case and dialect that can never mark a parity cell Verified (`parity_harness.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason (the seeded cases are `fabric_desktop/parity_cases.json`, inside `sidecar/`). The module has 281 mutants, so a sample of 150 was run. `main` has changed this module since (#3245); the new tests pass against today's version. One test changed for it: #3245 counts a statement that fails on its data as a difference, so the test of a T-SQL error now uses a column that isn't there, which fails to bind.

The test set was test_parity_harness.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. They open no network connection: the same fixture as test_parity_harness refuses sockets. The "after" run reran the 43 survivors with the new tests added.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `parity_harness.py` | test_parity_harness (+ test_parity_harness_mutation_b) | 150 of 281 (148 + 2) | 107 / 43 | **71.3%** | 106/148 → 143/148 | 1/2 → 2/2 | 145 / 5 | **96.7%** | 5 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **parity_harness.py**:
  - Each refusal of a case that breaks the format: a table that isn't a plain name or isn't text, a column that isn't a `[name, type]` list with a plain name, rows that aren't lists of the right length, a statement or source that isn't text or is blank, a dataset that isn't an object, a case that isn't an object.
  - Repeated case ids name only the ids that repeat.
  - `decimal(0,0)`, `decimal(39,0)` and a scale above the precision aren't types; `decimal(1,0)`, `decimal(38,2)` and `decimal(2,2)` are.
  - A Boolean isn't an int, a number isn't a Boolean, and a number isn't a string. Strings and dates read as their values.
  - A dataset writes Booleans as SQL keywords. A dataset row of the wrong length is refused.
  - Arrow timestamps of any unit, with or without a time zone, a large string and a time type have their logical names.
  - integer_width allows only whole-number types (a `decimal(p,0)` among them); decimal_scale_display allows only two decimals.
  - The words actual and expected replace only text.
  - A dataset DuckDB can't make is an error with DuckDB's own reason. A statement with no result says so. More than 10,000 rows is an error, and 10,000 are compared.
  - A T-SQL statement that fails on DuckDB is an error naming the exception, on one line. A T-SQL batch runs its earlier statements first.
  - A record names the translation it used for DuckDB, Spark and T-SQL.
  - Records are written into a folder that doesn't exist yet.
  - → `test_parity_harness_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `parity_harness.py` | 161 | `ex.get("unconfirmed", False)` to `True` in the check | Equivalent: the default is only checked for being a Boolean, and True and False both are. |
| `parity_harness.py` | 242 | `autoinstall_known_extensions` False to True | Equivalent: the connection also has `enable_external_access` off, which stops installing and loading extensions, so there is nothing to install. |
| `parity_harness.py` | 242 | `autoload_known_extensions` False to True | Equivalent, for the same reason. |
| `parity_harness.py` | 318 | `zip(want, got, strict=True)` to `strict=False` | Equivalent: the column names were compared equal just before, so both lists have the same length and strict never raises. |
| `parity_harness.py` | 330 | `zip(names, ..., strict=True)` to `strict=False` | Equivalent: `names` is made from `want`, one name per column, so the lengths always match. |
