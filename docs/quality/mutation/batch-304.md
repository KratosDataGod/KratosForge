# Mutation testing, batch 304

One module that had never been scored before: `databricks_volumes.py`. It ran with `--kinds all`, so the score counts the boundary mutants (`<` to `<=`, `>` to `>=`) as well as the earlier kinds, and the table shows each part.
- Unity Catalog volumes on this PC (Preview). The module covers the names a schema or volume may take, the volume statements a Databricks SQL cell runs (CREATE, DROP, ALTER, COMMENT ON, SHOW, DESCRIBE and LIST), how they are read and refused by name, the changes that run once however often DuckDB calls them, and `read_files` on a volume path.

The run was at `main` c9716c45d (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The module has 708 mutants, and 150 were sampled. Test files were given to the tool as paths relative to `sidecar/`, and they passed once on the unmutated copy first. Each pytest session got a new temporary folder, removed when the session ended, and no folder 40 or more levels deep was left. No `-k` filter was used. One test in `test_databricks_notebook_helpers.py`, `test_the_docs_page_is_up_to_date`, was deselected because it reads `docs/databricks-notebook-helpers.md`, which the tool's copy of `sidecar/` doesn't hold.

Before the "before" run, the tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, so the run can't score a false 100%. The new tests pass on DuckDB 2.0.0 and on sqlglot 30.23.0 and 30.27.0 too.

One mutant timed out in the "after" run and counts as killed. It removes the syntax error for an unknown clause in CREATE VOLUME (line 623), and then the loop that reads the clauses never moves past that word, so the statement is read forever. The new test that gives CREATE VOLUME an unknown clause is what reaches it.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `databricks_volumes.py` | test_databricks_volumes, test_databricks_volumes_service, test_databricks_magics, test_dbutils, test_databricks_notebook_helpers (one test deselected) (+ test_databricks_volumes_mut304) | 150 of 708 (143 + 7) | 119 / 31 | **79.3%** | 119/143 → 137/143 | 0/7 → 5/7 | 142 / 8 | **94.7%** | 8 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **databricks_volumes.py**:
  - A name holding DEL (U+007F) is refused by name, and the character after it is not.
  - The catalog refusal names every other catalog that also means main: workspace, hive_metastore and spark_catalog.
  - The creation time kept beside a volume is the one DESCRIBE reports.
  - With no volume folder at all, there are no volumes, in any schema.
  - The statement reader reads one-character words and punctuation, and an empty string `''`. A lone quote or backtick is unterminated.
  - A missing word is a syntax error that names the word found instead, such as `DROP VOLUME IF landing`, which expected EXISTS. CREATE VOLUME with an unknown clause is a syntax error too.
  - A volume named on its own needs no schema check. One named with its schema gets the SCHEMA_NOT_FOUND check first.
  - A rename to a bare name isn't checked for a move to another schema.
  - SET TAGS and OWNER TO are each refused with their own reason.
  - `LIST` on its own and a table named `volume` are not volume statements.
  - A statement is quoted whole in a message up to 80 characters. A longer one is cut to 77 characters and three dots.
  - Only a placeholder string is read as a placeholder.
  - A change with a token runs once however often it's asked. A change with no token is never kept. The answers kept are cleared once there are more than 1,000.
  - CSV files whose columns are in a different order are read by name, and multi-line JSON is read when `multiLine` is true.
  - These tests are in `test_databricks_volumes_mut304.py`.

### Mutants left, and why

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `databricks_volumes.py` | 68 | the stand-in for a missing `os.path.isjunction` answers `True` | Equivalent: Python 3.12, the oldest this app runs on, always has `os.path.isjunction`, so the stand-in is never called. |
| `databricks_volumes.py` | 454 | `t[-1]` to `t[-0]` in the check for an unterminated backtick | Equivalent: the only backtick token that doesn't end in a backtick is a lone backtick, which `len(t) < 2` already refuses. |
| `databricks_volumes.py` | 517 | `take` returns `None` instead of `False` | Equivalent: every caller reads the answer as a condition. |
| `databricks_volumes.py` | 754 | the first 3 tokens to the first 4 | Equivalent: only `words[0]`, `words[1:2]` and `words[1:3]` are read, so a fourth word is never looked at. |
| `databricks_volumes.py` | 762 | `words[1:3]` to `words[1:4]` | Equivalent: `words` holds at most 3 items, so both slices are the same. |
| `databricks_volumes.py` | 985 | `len(files_) > 1` to `>= 1` | Equivalent: DuckDB reads a list holding one file just as it reads that file. |
| `databricks_volumes.py` | 996 | `chr(34)` to `chr(35)` in the column rename for a CSV read without a header | Equivalent: without a header DuckDB names the columns `column0`, `column1` and so on, which never hold `"` or `#`. |
| `databricks_volumes.py` | 1002 | `len(files_) > 1` to `>= 1` for parquet | Equivalent: with one file, matching columns by name or by position gives the same table. |
