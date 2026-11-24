# Mutation testing, batch 32

Three modules, none touched by an open pull request:
- The SQL compatibility check's reader: the 500,000-character limit, the report listing the first 1,000 findings while counting every one, SQL that can't be tokenized, T-SQL's `GO` alone on its line (and not in Spark SQL), each word's place in a two-word token such as `ORDER BY`, sqlglot's error text without its token dump, and the exported JSON's indent and characters (`sqlcompat/analyzer.py`).
- Validate notebook's code checks: a finding's cell id, markdown and blank cells not counted as code, a reason with no cell said about the notebook, the missing lakehouse said once, package severity by where the notebook runs, an import used only inside a function, the second parameter cell, frozen cells, the lakehouse's name, a lakehouse file read with no lakehouse, the first Spark cell stopping a run without Spark, a run failure making its warning an error, sort order, and the saved report (`validate.py`).
- Checks on a pipeline definition: duplicate JSON keys (up to ten listed), a `Pipeline` passed in directly, the severity order, how an inactive activity is marked, a missing type, which activity a dependency loop is reported on, an upstream walk past a dependency that doesn't exist, Set variable and the pipeline's return value, the activity's own JSON, `item()` outside a ForEach, and the summary's counts (`orchestration/validate.py`).

Batch 31's modules (`warehouse_compare.py`, `orchestration/expressions.py` and `localdata/tsql_format.py`) were left to batch 31, which ran at the same time.

The run was at `main` ed38d4cf5 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 31 (sample cap 150, seed 7), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. `test_practice_docs.py` and `test_the_shipped_guides_are_the_docs` read `docs/`, so they were left out of the `sqlcompat/analyzer.py` run.

None of these modules writes files, and the new tests use no network and no timing.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `sqlcompat/analyzer.py` | the 12 other test files that use `sqlcompat` (+ test_sqlcompat_analyzer_mutation) | 136 of 136 | 110 / 26 | **80.9%** | 129 / 7 | **94.9%** | 6 equivalent, 1 unreachable (#2674) |
| `validate.py` | test_validate, test_validate_service_edges, test_compute_check_ast, test_spark_refusals (+ test_validate_mutation) | 150 of 163 | 115 / 35 | **76.7%** | 149 / 1 | **99.3%** | 1 equivalent |
| `orchestration/validate.py` | test_opportunities_analyzers_mutation, test_orchestration_definition_edges, test_pipeline_definitions (+ test_orchestration_validate_mutation) | 133 of 133 | 102 / 31 | **76.7%** | 128 / 5 | **96.2%** | 5 equivalent |

Two real bugs were found, both in `sqlcompat/analyzer.py`. Each is filed with a strict xfail in `test_sqlcompat_analyzer_mutation.py`, so a fix flips it:
- #2673: in T-SQL, a name spelled `go` splits the statement (`SELECT x AS go FROM t` is read as two statements).
- #2674: a statement that doesn't parse is reported at its first token, not at the token where parsing failed. The supported sqlglot versions don't put `start` in their error dicts, so `error_at` is never set.

### What the survivors were, and the tests that now kill them

- **sqlcompat/analyzer.py**:
  - SQL of 500,001 characters not checked (with the limit in the text, and `truncated` false), and 500,000 checked.
  - 1,001 findings listing 1,000 with `truncated` true while the counts say 1,001, and 1,000 not truncated.
  - SQL that can't be tokenized saying why, with its finding at offset 0 to 1 in statement 0.
  - `GO` alone on its line, in either case and with spaces around it, splitting T-SQL. Not splitting Spark SQL. Not alone when other text is on its line, at the start or at the end of the query.
  - `ORDER` and `BY` keeping their own offsets.
  - sqlglot's description kept as the error, with the token dump dropped.
  - The exported JSON indented by two and keeping `é` as written.
  - → `test_sqlcompat_analyzer_mutation.py`.
- **validate.py**:
  - A finding's cell id taken only when it has a cell.
  - A notebook of markdown and blank cells being empty.
  - The missing lakehouse not repeated as a Fabric reason, a reason with a cell offering the fix, and a reason with no cell titled "The notebook" with no fix.
  - `package_missing_pc` and `package_unknown_fabric` severities for each Runs on, the PC's version said when it's installed, and an import used only inside a function giving no finding.
  - The second parameter cell named.
  - "cell" and "cells" for frozen cells.
  - The lakehouse's name, or "attached".
  - `Files/` read with no lakehouse being an error.
  - The first Spark cell, by number and id, stopping a run without Spark.
  - A NameError in the clean run making the matching warning an error found by both.
  - Findings with no cell first, six names then "and 1 more", an unknown effect cut at its first `;`.
  - The saved report: the revision cut to 16 characters, only sections with findings, a fix only when there is one, and an empty cell for unknown seconds.
  - → `test_validate_mutation.py`.
- **orchestration/validate.py**:
  - Eleven duplicate keys listing ten, no warning without duplicates, and a `Pipeline` from `build` validated directly.
  - Errors before warnings before info when they're found in another order.
  - An inactive activity marked Succeeded, or as `onInactiveMarkAs` says.
  - "A has no type." with its activity, or "An activity has no type." with none.
  - A loop reported on its first activity, by path and message, and a loop reached through another activity.
  - The upstream walk not failing on a dependency that doesn't exist.
  - A variable name that isn't text being missing, and `pipelineReturnValue` needing `setSystemVariable`.
  - The activity's own JSON keeping its other keys and a Switch's cases without their activities. Expressions outside `typeProperties` checked.
  - `item()` on a ForEach's own items, and inside an If that isn't in a ForEach.
  - The summary counting each severity, an unknown one included.
  - → `test_orchestration_validate_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `sqlcompat/analyzer.py` | 92 (2) | `t.token_type == VAR` or `t.text.upper() == "GO"` swapped | Equivalent: sqlglot's T-SQL tokenizer makes every `go` a `COMMAND` token, so the `VAR` branch never sees one, and a `go` alone on its line is split by the first branch anyway. |
| `sqlcompat/analyzer.py` | 124 | `rfind` from 1 instead of 0 | Equivalent: it only misses a newline at offset 0, and the line is stripped before it's compared. |
| `sqlcompat/analyzer.py` | 125 (2) | `find` from `t.end - 1` or `t.end` | Equivalent: both offsets are inside the `GO` token, which has no newline. |
| `sqlcompat/analyzer.py` | 163 | `stmt.start + start` → `-` | Unreachable: the supported sqlglot versions' errors have no `start` (#2674). |
| `sqlcompat/analyzer.py` | 184 | the statement count 0 → 1 in the too-long outcome | Equivalent: with a reason, `_outcome` returns it without reading the count. |
| `validate.py` | 71 | `parsed` defaulting to False | Equivalent: depgraph always sets `parsed`. |
| `orchestration/validate.py` | 63 | info's rank 2 → 3 | Equivalent: it stays after warning's 1, and no other severity is used. |
| `orchestration/validate.py` | 64 | the default rank 3 → 4 | Equivalent: every diagnostic has a known severity. |
| `orchestration/validate.py` | 168 | a finished node's state 2 → 3 | Equivalent: only state 1 is compared. |
| `orchestration/validate.py` | 264, 270 (2) | `return False` → `return None` in `_in_foreach` | Equivalent: the only caller uses it in `not ...`. |

### Not in this batch

Batch 31's modules.

