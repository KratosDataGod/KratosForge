# Mutation testing, batch 39: boundary mutants, older modules (2)

A second sweep of older modules with `--kinds boundary`, the same way as batch 37. It takes seven modules that batches 36 and 37 didn't re-run, chosen for their comparisons and limits: SQL scanning with token indexes, size and age caps, deadlines and grace periods.

The run was at `main` 1dab3b76 (2026-11-15) with `--kinds boundary` (every boundary point, no sampling), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Each module ran with its own mutation test file and the test files that cover it most closely, all of which run in seconds:

- `risk.py`: test_risk, test_risk_service_edges, test_compute_check_ast, test_model_bpa, test_risk_mutation.
- `appstudio/spec.py`: test_appstudio, test_appstudio_spec_edges, test_appstudio_generate(_edges), test_appstudio_edit(_edges), test_appstudio_pbir(_edges), test_appstudio_spec_mutation.
- `sqlcompat/practices.py`: test_sql_practices, test_learn_fixes, test_learn_guides, test_sqlcompat_practices_mutation.
- `frames.py`: test_frames, test_frames_more_edges, test_notebook_display_edges, test_outputs_hardening, test_query_profile, test_spark_summary_schema, test_wrangler, test_write_types, test_frames_mutation.
- `spark_number_format.py`: test_spark_number_format_contract, test_spark_builtins_sql(_edges, _mutation), test_snowflake_sql, test_spark_number_format_mutation, limited to `-k "number or to_char or to_varchar"`.
- `sqlcompat/rules.py`: test_learn_fixes(_edges), test_learn_guides, test_localdata_engine_mutation, test_snowflake_sql_variadic, test_sql_compat, test_sql_practices, test_tsql_contract, test_sqlcompat_rules_mutation.
- `mailbox.py`: test_mailbox(_edges, _more_edges), the test_agent files, test_paging, test_lease_clock_skew, test_shared_schedules, test_schedule_overlap, test_sqlite_connections_closed, test_not_set_up, test_cli_local_commands, test_contract_onelake, test_mailbox_mutation.

The tool runs pytest with `-x` in a copy of `sidecar/` only. A test that reads `docs/` or `examples/` fails there, and then every mutant reads as killed. The first runs of `practices.py`, `rules.py` and `mailbox.py` hit this, so they were thrown away and run again with those tests left out: `test_learn_guides.py::test_the_shipped_guides_are_the_docs` and `test_practice_docs.py` for the two SQL modules, and seven tests in `test_mailbox.py` plus one in `test_agent_queue_bound.py` that run the shipped example notebook. Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. The `mailbox.py` "after" run used only `test_mailbox_mutation.py` and `test_boundary_mutation_3.py`. A wider set can only kill more mutants, so 16 / 16 holds.

## Results (boundary mutants only)

| Module | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- |
| `risk.py` | 10 | 6 / 4 | **60.0%** | 10 / 0 | **100%** | none |
| `appstudio/spec.py` | 11 | 11 / 0 | **100%** | no change needed | **100%** | none |
| `sqlcompat/practices.py` | 40 | 15 / 25 | **37.5%** | 30 / 10 | **75.0%** | 10 equivalent |
| `frames.py` | 17 | 9 / 8 | **52.9%** | 17 / 0 | **100%** | none |
| `spark_number_format.py` | 12 | 8 / 4 | **66.7%** | no change possible | **66.7%** | 4 equivalent |
| `sqlcompat/rules.py` | 71 | 25 / 46 | **35.2%** | 62 / 9 | **87.3%** | 6 equivalent, 3 with no difference found |
| `mailbox.py` | 16 | 2 / 14 | **12.5%** | 16 / 0 | **100%** | none |
| **All seven** | 177 | 76 / 101 | **42.9%** | 154 / 23 | **87.0%** | 20 equivalent, 3 with no difference found |

The new tests are in `sidecar/tests/test_boundary_mutation_3.py`, added to each module's set for the "after" runs. No real bugs were found.

### What the survivors were, and the tests that now kill them

- **risk.py**: a run that ended the moment it started took zero seconds; a run at exactly the CU-hour limit doesn't warn; a schedule due now, or at the window's edge, warns.
- **frames.py**:
  - The twentieth result still keeps the first, and the twenty-first evicts the least recently read.
  - A sort column or chart column one past the last is ignored or refused plainly.
  - Exactly sixty categories aren't truncated, and exactly seven series have no "other" slot.
  - A result of exactly a sheet's rows still goes to Excel.
  - Excel's first day (1900-01-01) is written as a date, and the minute before it as text.
- **sqlcompat/practices.py**: statements that end exactly at a keyword (`SELECT TOP`, `INTO`, `LIKE`, `ROW_NUMBER() OVER`, an unclosed subquery) give the same findings as before and don't fail; a `LIKE` after an `IN` list is still in the WHERE clause; a wrapped column after `GROUP BY` isn't a WHERE finding.
- **sqlcompat/rules.py**: 27 statements cut off at a boundary (a trailing `TABLE`, `#`, `LIMIT`, `PRIMARY`, `WITH a AS`, `CONVERT(string`, `ON SCHEMA::`, an unclosed type size, two `REFERENCES` in one table) are pinned to the exact rules and spans they report now.
- **mailbox.py**:
  - A request of exactly the byte limit, and a submitter name of exactly 200 characters, are read.
  - An empty request is rejected once exactly the writing grace has passed, and a run lookup Fabric couldn't answer is given up on at exactly its grace.
  - A deadline of this very second hasn't passed: at the door, when the request is queued, and while it waits in the queue.
  - A pipeline run from exactly a day ago, or exactly five minutes ahead, still vouches; one that vouched exactly a day ago still counts and is still remembered.
  - An owner at exactly its fresh time plus the grace (on OneLake) or its stale time plus the grace (on this PC) isn't lost yet.
  - A finished request's files go at exactly two weeks.
- → `test_boundary_mutation_3.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `sqlcompat/practices.py` | 137 | `hit["line"] - 1 < len(starts)` → `<=` | Equivalent: a hit's line is never past the last line, so it can't equal `len(starts)`. |
| `sqlcompat/practices.py` | 138 | `at < s.end` → `<=` | Equivalent: a statement's end is trimmed, so no line start equals it. |
| `sqlcompat/practices.py` | 188 | `i > 0` → `>=` | Equivalent: at 0, `toks[-1]` is the last token, which can't end where the first starts. |
| `sqlcompat/practices.py` | 217 | `j > i` → `>=` | Equivalent: token `i` is DISTINCT, so it can't be GROUP. |
| `sqlcompat/practices.py` | 274 | `where < i < end` → `<=` (both) | Equivalent: at `where` the token is WHERE and at `end` it is a clause end, and neither is a wrapping function. |
| `sqlcompat/practices.py` | 326 | `after >= close` → `>` | Equivalent: at `close` the token is `)` or the last one, never FROM, so the result is the same. |
| `sqlcompat/practices.py` | 385, 387 | `after < close` → `<=` | Equivalent: at `close` the token is `)` or the last one, never AS or a name that isn't a stop word. |
| `sqlcompat/practices.py` | 392 | `j + 3 >= close` → `>` | Equivalent: at `close` the token is neither a dot nor `(`. |
| `spark_number_format.py` | 92 | `first_digit < dollar`, `point < dollar` → `<=` | Equivalent: a digit or the point can't be at the dollar sign's index. |
| `spark_number_format.py` | 98, 161 | `i < point`, `i > point_at` → `<=`, `>=` | Equivalent: a group separator can't be at the point's index. |
| `sqlcompat/rules.py` | 462 | `k > 0` → `>=` | Equivalent: at 0, `toks[-1]` would have to be TOP, which ends no statement that reaches here. |
| `sqlcompat/rules.py` | 736, 741 | `j < end` → `<=` | Equivalent: at `end` the token is `)` or the last one, and both give the same result. |
| `sqlcompat/rules.py` | 791 | `i >= 2` → `>` | Equivalent: at 2, `toks[0]` is CREATE in a CTAS, not CONVERT. |
| `sqlcompat/rules.py` | 882 | `k > i` → `>=` | Equivalent: token `i` is a keyword at depth `d`, neither shallower nor a comma. |
| `sqlcompat/rules.py` | 980 | `i + 1 < len(toks)` → `<=` | Equivalent: at the last token the rest is empty, which gives the same result. |
| `sqlcompat/rules.py` | 296, 301 | `i + 3 < len(toks)`, `i + 2 < len(toks)` → `<=` | No difference found: they differ only for a name ending in `..` or `.` at the very end of a statement, which is already reported as SYN-007 either way. |
| `sqlcompat/rules.py` | 410 | `h.start < b` → `<=` | No difference found: no FN-001 hit was found that starts exactly where an explained span ends. |

### Not in this batch

The other modules from batches 1 to 35, which later batches can re-run with `--kinds boundary`.

