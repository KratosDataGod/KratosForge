# Mutation testing, batch 11

Three modules no open pull request is changing:
- The Snowflake SQL translator that runs Snowflake cells on this PC (`snowflake_sql.py`).
- The SQL compatibility rules behind the SQL editor's and the notebook's compatibility check (`sqlcompat/rules.py`).
- The ipywidgets stand-in that notebook cells import (`widgets.py`).

The run was at `main` 4d7f4b6bd (2026-11-14), with `sidecar/tools/mutate.py` and the same method as batches 1 to 10 (sample cap 150, seed 7), on DuckDB 1.5.6. All three modules have more points than the cap, so 150 of each were run.

Before each run, its tests were run in a copy of `sidecar/` on its own, the way the mutator runs them. This batch also ran them once against the module as `ast.unparse` writes it, because that is the text every mutant is written as. That check found a false 100% for `snowflake_sql.py`: `test_snowflake_sql.py::test_every_rule_the_code_uses_is_in_the_docs_table` reads the module's source for rule ids in double quotes, and `ast.unparse` writes single quotes, so the test failed for every mutant and each counted as killed. That run was thrown away, and the test is left out of the runs below.

Tests left out of every run, because they read pages under `docs/` or the module's own source:
- `test_snowflake_sql.py`: `test_the_docs_table_is_the_codes` and `test_every_rule_the_code_uses_is_in_the_docs_table`.
- `test_snowflake_sql_parity.py::test_the_docs_carry_the_table_and_say_no_account_was_used`.
- `test_snowflake_coverage.py`: `test_what_the_docs_say_runs_still_runs`, `test_the_docs_page_is_up_to_date` and `test_the_docs_link_the_list`.
- `test_learn_guides.py::test_the_shipped_guides_are_the_docs`.
- `test_practice_docs.py`: `test_every_rule_has_a_docs_section` and `test_no_docs_section_for_a_rule_that_doesnt_exist`.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `snowflake_sql.py` | test_local_sql_mutation, test_snowflake_coverage, test_snowflake_sql, test_snowflake_sql_edges, test_snowflake_sql_parity, test_snowflake_sql_variadic, test_sql_lambdas (+ test_snowflake_sql_mutation) | 150 of 732 | 132 / 18 | **88.0%** | 144 / 6 | **96.0%** | 6 equivalent |
| `sqlcompat/rules.py` | test_learn_fixes, test_learn_fixes_edges, test_learn_guides, test_localdata_engine_mutation, test_practice_docs, test_snowflake_sql_variadic, test_sql_compat, test_sql_practices, test_tsql_contract (+ test_sqlcompat_rules_mutation) | 150 of 1133 | 102 / 48 | **68.0%** | 137 / 13 | **91.3%** | 10 equivalent, 3 tied to #2623 and #2624 |
| `widgets.py` | test_ipywidgets, test_ipywidgets_parity, test_output_cap, test_outputs_hardening, test_widgets_more_edges (+ test_widgets_mutation) | 150 of 519 | 127 / 23 | **84.7%** | 146 / 4 | **97.3%** | 4 equivalent |

Existing test files the first sets left out were tried on the survivors first, and they killed none:
- `snowflake_sql.py`: test_snowflake_coverage_edges, test_local_sql_errors, test_local_sql_results, test_sql_portability.
- `sqlcompat/rules.py`: test_learn_labs, test_opportunities, test_risk, test_sql_portability.
- `widgets.py`: test_chart_libraries, test_dbutils, test_dbutils_parity, test_display_handles, test_notebook_inputs, test_notebook_inputs_edges, test_notebook_inputs_mutation, test_notebook_mutation, test_notebook_formats.

All the survivors that were killed, were killed by the new tests. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **snowflake_sql.py**:
  - A divisor written as text (`10 / '0'`) or as a column named like a number (`"2"`) still gets Snowflake's "Division by zero".
  - `OCTET_LENGTH` of BINARY counts bytes (`octet_length`), not the characters of its text.
  - `OBJECT_INSERT` with a `FALSE` update flag refuses a key that is already there.
  - `FLATTEN` with a positional input, and `OUTER => FALSE` against `OUTER => TRUE` on an empty array.
  - `ALTER ... SET TAG` refused as a Snowflake setting, while `ALTER TABLE ... RENAME` runs.
  - `AS_DECIMAL` refusing a precision of 39, and the reason each `AS_*` function gives when it has too many arguments.
  - The translation label carrying each note in full, and the report objects being frozen.
  - → `test_snowflake_sql_mutation.py`.
- **sqlcompat/rules.py**:
  - The exact text each finding points at, for 39 statements across the rules: types, `CREATE`/`ALTER` objects, constraints, hints, the endpoint's read-only writes, the clock words, date patterns, `ISNULL`, `GO` and CTEs.
  - `#temp` names: `#t` is a temp table and `# t` isn't, and `##g` is global.
  - Columns added by `ALTER TABLE`, with and without `COLUMN`, and after a comma.
  - A table made from a query, past `WITH (options)` and inside `CONVERT`.
  - An `UPDATE` whose `WHERE` comes after a subquery still has one.
  - A CTE with a column list that names itself, and the second of two CTEs.
  - Statements that end early (an unclosed column list, a lone `WITH`), read to their end without failing.
  - More than 1,024 columns, a bad name quoted to 64 characters, and the rules being frozen.
  - → `test_sqlcompat_rules_mutation.py`.
- **widgets.py**:
  - How deep a nested view is drawn: down to `MAX_DEPTH`, then a placeholder.
  - The controls `interact` picks: the range of an int or float slider around its value and the float step in hundredths, a two- or three-number tuple as a slider with a step of 1, and anything else listed as a dropdown.
  - A range slider starting at a quarter and three quarters of its bounds.
  - `Text` turning `None` into an empty value, and a dropdown's description being settable.
  - `Output` without and with a kernel bound: `with out as o` gives the widget, an error inside isn't swallowed, `capture()` keeps what was there, and an output that exactly fills the rich cap is shown, also after `clear_output()`.
  - `interact` as a decorator and through `interact.options()` giving back the function.
  - Installing twice keeping this module.
  - → `test_widgets_mutation.py`.

### Equivalent mutants (left on purpose)

| Module | Line | Mutant | Why it changes nothing |
| --- | --- | --- | --- |
| `snowflake_sql.py` | 1363 (×2), 1376 | `_div0_branch` misreading DIV0's or DIV0NULL's division | A division it misses gets the zero check too, inside the `ELSE` branch DIV0 never reaches when the divisor is 0. DuckDB's `CASE` doesn't evaluate a branch it doesn't take, so the results are the same. |
| `snowflake_sql.py` | 1377, 1160 | `return False` → `return None` | Both callers only test the result for truth. |
| `snowflake_sql.py` | 953 | `COALESCE(running, 0)` → `1` for `CONDITIONAL_TRUE_EVENT` | The running sum of 0s and 1s up to the current row is never NULL. |
| `sqlcompat/rules.py` | 366 | `_where_follows`'s `return False` → `None` | Its caller only tests `not _where_follows(...)`. |
| `sqlcompat/rules.py` | 338 | `_args` counting commas from the `(` instead of after it | The `(` isn't a comma. |
| `sqlcompat/rules.py` | 605 | `ISNULL`'s argument count from the name instead of the `(` | A bracket is at its call's own depth, so the same `)` and the same commas are found. |
| `sqlcompat/rules.py` | 646 | `_regex_pattern` looking for commas from before the `(` | The two extra tokens are the name and the `(`, never a comma at the argument depth. |
| `sqlcompat/rules.py` | 764 | `_close(s, k + 1)` → `_close(s, k)` past `WITH (options)` | From `WITH`, the first `)` at the same depth is the same one. |
| `sqlcompat/rules.py` | 908 | the `NOT NULL` search starting two tokens earlier | Those tokens are the table name and `ADD`. |
| `sqlcompat/rules.py` | 977, 978 | the `CREATE`/`ALTER`/`DROP` guard and its look-ahead | The slice is safe without the guard, and only the first three words are read. |
| `sqlcompat/rules.py` | 1048 | the self-reference search starting at `AS` | `AS` and `(` are never a CTE's name. |
| `sqlcompat/rules.py` | 1087 | `FROM (` read at `FROM` | A `(` isn't a name either way, so the token is skipped. |
| `widgets.py` | 153 | `_view(depth=0)` default | Every caller passes the depth. |
| `widgets.py` | 230 | `IntSlider` reading a value from the app as `float` | Setting `value` casts it to `int` anyway. |
| `widgets.py` | 618 | `Literal[False]` → `Literal[True]` in `__exit__`'s annotation | An annotation. `test_widgets_mutation.py` checks that the error isn't swallowed. |
| `widgets.py` | 636 | `capture(wait=True)` default | `clear_output` doesn't read `wait`. |

Left without a test, tied to a bug filed in this batch:
- `sqlcompat/rules.py` lines 294 and 299, `_name_at`: the `db..table` branch can't be reached, so neither mutant changes anything until #2624 is fixed.
- `sqlcompat/rules.py` line 985, `_writes`' `k + 1 < len(toks)` guard for `SELECT ... INTO` at the end: for the T-SQL targets, sqlglot fails on that statement before the rules run (#2623).

### Real bugs found

- #2623: the SQL compatibility check fails with `AttributeError` on a T-SQL `SELECT ... INTO` with no table name, because sqlglot's T-SQL parser throws one and `analyzer.parse()` doesn't catch it.
- #2624: a `db..table` name isn't read past the first dot, so `CREATE TABLE db..t (a nvarchar(10))` gets no findings.
- #2625: `widgets.install()` keeps a real ipywidgets that a library imported first, because only KratosForge's module has `__kratosforge__` and the check defaults to `True`.

None of them is security-relevant.

### Not in this batch

- `snowflake_sql.py`, `sqlcompat/rules.py` and `widgets.py` past the 150 sampled mutants.

