# Mutation testing, batch 49

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Reliability objectives: the success objective a table declares or gets from its criticality, the checks on `[objectives]`, and how a table stands against its objective (`meta/objectives.py`).
- Credential facts: what an agent publishes about its own credential, never the credential itself (`credential_facts.py`).
- Engine registry: every engine KratosForge knows, its adapter, and whether Apache Spark could run on this PC (`engines/registry.py`).

The run was at `main` 621707162 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test run reads `docs/` or `examples/`, and no test file was filtered.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/objectives.py` | test_incidents (+ test_meta_objectives_mutation) | 62 of 62 (57 + 5) | 52 / 10 | **83.9%** | 51/57 → 57/57 | 1/5 → 5/5 | 62 / 0 | **100%** | none |
| `credential_facts.py` | test_run_as (+ test_credential_facts_mutation) | 28 of 28 (28 + 0) | 26 / 2 | **92.9%** | 26/28 → 28/28 | none | 28 / 0 | **100%** | none |
| `engines/registry.py` | test_engines, test_engine_switch_failures, test_engine_conformance (+ test_engine_registry_mutation) | 22 of 22 (22 + 0) | 20 / 2 | **90.9%** | 20/22 → 22/22 | none | 22 / 0 | **100%** | none |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **meta/objectives.py**:
  - Five runs are the fewest that say anything: four counted runs are "not enough", five are judged, and `runs = 5` is accepted while 4 is refused.
  - `[objectives]` that isn't a table gets exactly one message.
  - The success rate is rounded to one place (4 of 6 is 66.7).
  - A success rate is a percent above 0, up to 100: 100 and 0.5 are accepted, and 0, 101, 100.5, -1, true and text are refused.
  - → `test_meta_objectives_mutation.py`.
- **credential_facts.py**:
  - A status record reads a password-protected certificate with the password its auth names.
  - A token error is kept to 200 characters.
  - → `test_credential_facts_mutation.py`.
- **engines/registry.py**:
  - An unknown engine is a `ValueError` that lists the known engines.
  - Apache Spark is ready only with both PySpark and Java, and the message names what is missing.
  - → `test_engine_registry_mutation.py`.

