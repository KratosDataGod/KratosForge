# Mutation testing, batch 169

A boundary re-score, as in batch 163, of six more modules first scored before batch 36. Only the boundary mutants were run (`--kinds boundary`); each module's point score from its first batch stands. The modules, with the batch that first scored each:
- The job queue (`jobqueue.py`, batch 22).
- What an external tool's scope lets it reach (`integrations/scope.py`, batch 27).
- SQL portability grades (`sql_portability.py`, batch 28).
- Pipeline gotchas (`orchestration/gotchas.py`, batch 30).
- Spark's `hash` and `xxhash64` in DuckDB SQL (`spark_hash.py`, batch 30).
- Git scopes (`git/scopes.py`, batch 33).

The run was at `main` 4ddd3662 (2026-11-18) with batches 163 to 168's tests added, and the same method as batch 163. Tests that read files outside `sidecar/` were left out: eight `test_mailbox.py` and `test_agent_queue_bound.py` tests that read the sample notebook for `jobqueue.py`, `test_learn_guides.py::test_the_shipped_guides_are_the_docs` for `orchestration/gotchas.py`, and `test_spark_hashing_values.py::test_spark_hashing_functions_answer_as_spark_3_5_or_are_refused_by_name` for `spark_hash.py`.

No new tests were needed: every survivor is equivalent. No mutant's run timed out.

## Results

| Module | Test files run | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `jobqueue.py` | the 21 test files that use it | 2 of 2 | 2 / 0 | **100%** | 2 / 0 | **100%** | none |
| `integrations/scope.py` | the 5 test files that use it | 2 of 2 | 2 / 0 | **100%** | 2 / 0 | **100%** | none |
| `sql_portability.py` | test_kudp_guardrails, test_snowflake_sql_variadic, test_sql_portability, test_sql_portability_mutation | 2 of 2 | 1 / 1 | **50.0%** | 1 / 1 | **50.0%** | 1 equivalent |
| `orchestration/gotchas.py` | test_learn_guides, test_pipeline_gotchas, test_pipeline_gotchas_mutation | 2 of 2 | 2 / 0 | **100%** | 2 / 0 | **100%** | none |
| `spark_hash.py` | test_spark_hash_mutation, test_spark_hashing_values | 2 of 2 | 0 / 2 | **0.0%** | 0 / 2 | **0.0%** | 2 equivalent |
| `git/scopes.py` | the 5 test files that use it | 2 of 2 | 2 / 0 | **100%** | 2 / 0 | **100%** | none |

No bugs were found.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `sql_portability.py` | 244 | `_RANK[found] > _RANK[grade]` to `>=` | Equivalent: at equal rank `found` is `grade`, so the grade doesn't change. |
| `spark_hash.py` | 77 | `len(run) >= 3` to `>` | Equivalent: a run of three steps is written inline instead of as one split string; the SQL computes the same list. |
| `spark_hash.py` | 304 | `count > 1` to `>=` | Equivalent: DuckDB's `LIST_CONCAT` of one list is that list (1.5.6 and 2.0). |

