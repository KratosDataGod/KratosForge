# Mutation testing, batch 27

Three modules, none touched by an open pull request:
- Workspace limits for connected tools: the re-check after an approval wait, how deep a record is read, how far a receipt chain is followed, and the resolvers that read a notebook, a proof, an execution and the metadata project (`integrations/scope.py`).
- Go to definition, find references and rename across a notebook: statements on a cell's first line, dotted imports, keyword-only defaults, except and case names, global statements, a method in a class inside a function, how often a name is written in any scope, and the length of a place's line text (`nbsymbols.py`).
- Spark's number formats for to_number and to_char: an empty format, a separator at the end of a digit run, the 38-digit limit, a format that starts with its decimal point, and a sign moved past more than one space (`spark_number_format.py`).

The run was at `main` da9c8c5ac (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 26 (sample cap 150, seed 7), on DuckDB 1.5.6.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. The `spark_number_format.py` run used `-k "number or to_char or to_varchar"` on the Spark SQL test files, which keeps their 279 number-format tests and leaves out `test_snowflake_sql.py::test_the_docs_table_is_the_codes`, which reads `docs/`. `test_integrations_executions.py` was left out of the `integrations/scope.py` run because it takes about 40 seconds and none of its tests limit a tool's workspaces.

None of these modules writes files, and the new tests use no network and no timing.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `integrations/scope.py` | test_integrations_scope_edges, test_integrations_workspace_scope, test_integrations_permissions, test_integrations_registry_edges, test_integrations_services_mutation (+ test_integrations_scope_mutation) | 150 of 161 | 132 / 18 | **88.0%** | 148 / 2 | **98.7%** | 2 equivalent |
| `nbsymbols.py` | test_nbsymbols (+ test_nbsymbols_mutation) | 150 of 268 | 117 / 33 | **78.0%** | 148 / 2 | **98.7%** | 2 equivalent |
| `spark_number_format.py` | test_spark_number_format_contract, test_spark_builtins_sql, test_spark_builtins_sql_edges, test_spark_builtins_sql_mutation, test_snowflake_sql, with the `-k` filter above (+ test_spark_number_format_mutation) | 150 of 272 | 139 / 11 | **92.7%** | 144 / 6 | **96.0%** | 6 equivalent |

Three `spark_number_format.py` mutants timed out before and after, and were counted as killed. The three new test files pass on DuckDB 1.5.6 and 2.0.0. The number-format tests go through `sql.run`, as the other Spark SQL tests do.

### What the survivors were, and the tests that now kill them

- **integrations/scope.py**:
  - The re-check after an approval wait: a call that reaches only the tool's workspaces passing, one that reaches another workspace or a place it can't tell being refused with the reason and where to change it, and a tool with no limit reaching anything.
  - A workspace id 12 levels deep being found, one 13 levels deep making the record impossible to check, counted the same through dictionaries and lists, and every part read from the top.
  - A receipt chain of three being followed to its proof, and a longer one being impossible to check.
  - A notebook reaching both its lakehouse's workspace and the one it publishes to, with the call's own workspace added.
  - A record whose lakehouse has no workspace id being impossible to check.
  - An execution with no call context being read for no client.
  - The metadata project reaching the workspaces its entities name.
  - → `test_integrations_scope_mutation.py`.
- **nbsymbols.py**:
  - A def or a global statement on a cell's first line.
  - `import os.path` binding `os`, and shadowing the module's `os` inside a function.
  - Keyword-only defaults of a function and of a lambda being read where they are written.
  - An except name at module level being a definition, with another except name left alone.
  - Case captures and stars being definitions, found where the pattern binds them.
  - A global statement finding the name after its keyword, with the name earlier on the line skipped.
  - A method in a class inside a function seeing the module's name, past its class's own.
  - How often a name is written in any scope counting only that name: arguments, classes, defs, except names and the rest of a mapping pattern.
  - A place's line text being cut at 200 characters.
  - → `test_nbsymbols_mutation.py`.
- **spark_number_format.py**:
  - An empty format saying it is empty.
  - A separator at the end of a digit run being refused.
  - 38 digits being the most a format can have, with or without a decimal point.
  - A format that starts with its decimal point reading the fraction.
  - A sign moving left past every space before it.
  - → `test_spark_number_format_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `integrations/scope.py` | 135, 141 | `return False` → `return None` | Equivalent: both are false inside `all()`. |
| `nbsymbols.py` | 27 | `[0, 0]` → `[1, 0]` | Equivalent: lines count from 1, so the first entry is never read. |
| `nbsymbols.py` | 221 | `+ 6` → `+ 7` after `global` | Equivalent: `global` is always followed by at least one space, so the name starts at 7 or later. |
| `spark_number_format.py` | 19 | `frozen=True` → `False` | Equivalent: a digit run is never changed or hashed. |
| `spark_number_format.py` | 90 | `-1` → `-0` for no dollar sign | Equivalent: the checks only compare it with positions, which are never negative. |
| `spark_number_format.py` | 99 | `seps[j - 1]` → `seps[j + 1]` | Equivalent: two separators side by side are still caught at the first one, and a leading one by `j == 0`. |
| `spark_number_format.py` | 233 | `else 0` → `else 1` digits after the point | Equivalent: with no decimal point, nothing reads the digits after it. |
| `spark_number_format.py` | 297, 298 | `or` → `and`, and the `"0"` fallback dropped | Not reached: every valid format of up to four parts gives text other than a lone sign. |

### Real bugs found

None.

### Not in this batch

Batch 26's modules.

