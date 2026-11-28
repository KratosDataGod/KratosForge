# Mutation testing, batch 476

One module not scored before, run with `--kinds all`:
- Unity Catalog volumes on this PC (Preview): a volume is a folder of the app's data folder, named as `/Volumes/main/<schema>/<volume>/<path>`. The module checks names and paths before anything touches the disk, keeps every path inside its volume's folder, writes CREATE, DROP, ALTER, COMMENT, SHOW, DESCRIBE and LIST VOLUME as DuckDB SQL that calls it, and reads `read_files` and `csv.`/Volumes/...`` from a volume (`databricks_volumes.py`).

The run was at `main` c9716c45 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 708 mutants, so a sample of 150 was run.

The test set was test_databricks_volumes and test_databricks_volumes_service.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. One survivor (the raise for a clause CREATE VOLUME doesn't know) makes the statement reader loop for ever; its test reads the statement on a thread and fails when the thread hasn't finished in 20 seconds, so it is a kill, not a timeout.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `databricks_volumes.py` | test_databricks_volumes, test_databricks_volumes_service (+ test_databricks_volumes_mutation_b) | 150 of 708 (143 + 7) | 119 / 31 | **79.3%** | 118/143 → 137/143 | 1/7 → 4/7 | 141 / 9 | **94.0%** | 9 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **databricks_volumes.py**:
  - A name with DEL (U+007F) is refused, and the next character (U+0080) is kept. Another catalog's refusal names workspace, hive_metastore and spark_catalog, not main.
  - An unknown clause after CREATE VOLUME is a syntax error and the reader stops. A missing word is named, and the error shows the token it stopped at, or "the end of the statement".
  - SET TAGS and OWNER TO are refused, each with its own reason.
  - An unterminated `name` or string leaves the cell to sqlglot, and an empty string `''` is read as one. LIST on its own isn't a volume statement, and LIST with a path is. CREATE TABLE volume isn't a volume statement.
  - A statement is shown whole up to 80 characters, then cut to 77 and dots.
  - Only a placeholder string is a placeholder; `SELECT 1` and `SELECT 'other'` aren't.
  - A volume in another schema renames inside that schema.
  - A change runs once per token, and the answers kept are cleared once there are more than 1,000.
  - No volume root lists no volumes. A stored creation time is kept.
  - A folder of CSV files is read by column name, and multiLine JSON is read when asked.
  - → `test_databricks_volumes_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `databricks_volumes.py` | 68 | the fallback when `os.path.isjunction` is missing gives `True` | Equivalent: the app needs Python 3.12 or later, where `os.path.isjunction` always exists, so the fallback never runs. |
| `databricks_volumes.py` | 452 | an unterminated backtick name: `t[-1] != "`"` to `t[0] != "`"` | Equivalent: the lexer's pattern only gives a backtick token of two or more characters when it ends in a backtick; a lone one is caught by `len(t) < 2`. |
| `databricks_volumes.py` | 517 | `take` returns None instead of False | Equivalent: every caller only tests whether it is true. |
| `databricks_volumes.py` | 626 | the schema check also for a one-part name (`len(parts) >= 1`) | Equivalent: a one-part name uses the session's current schema, which always exists, so the check gives no rows and stops nothing. |
| `databricks_volumes.py` | 754 | `tokens[:3]` to `tokens[:4]` | Equivalent: only the first three words are read. |
| `databricks_volumes.py` | 762 | `words[1:3]` to `words[1:4]` for COMMENT ON VOLUME | Equivalent: `words` has three entries, so both slices are the same. |
| `databricks_volumes.py` | 985 | one file passed as a one-item list (`len(files_) >= 1`) | Equivalent: DuckDB reads a one-item list as it reads the path. |
| `databricks_volumes.py` | 996 | `chr(34)` to `chr(35)` when CSV columns are renamed `_c0`, `_c1` | Equivalent: without a header DuckDB names the columns column0, column1..., which hold no `"` or `#`. |
| `databricks_volumes.py` | 1002 | Parquet `union_by_name` for one file (`>= 1`) | Equivalent: with one file there is nothing to match by name. |
