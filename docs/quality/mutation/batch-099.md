# Mutation testing, batch 99

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Semantic models to and from Apache Ossie: the missing package's name, the alias and size refusals, the converter's report, dataset metrics lifted to the model, the merge of a new table's Direct Lake source, and the plan's details (`model_ossie.py`).
- Pipelines on this PC: a refusal's counts, the inactive flag, which runs get notebook and lakehouse helpers, starting, saving, auditing and cancelling a run, the run list's order, and how far down a repository pipeline folders are found (`pipeline_service.py`).
- The predeployment risk check: thresholds at zero, the capacity reading's fallbacks, the facts gathered for each kind of workload, which runs count, the unknown result when the check itself fails, and the acknowledgements file (`risk_service.py`).

The run was at `main` ea8c2942c (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902: each mutant ran without a bytecode cache, and the tests were first run once on the unmutated copy of `sidecar/`, where they passed. The method is the same as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `model_ossie.py` has 207 mutants, `pipeline_service.py` 187 and `risk_service.py` 168, so 150 of each were sampled.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. `test_activity_registry.py::test_example_report_in_docs_is_current` reads `docs/`, so it was deselected. `test_pipeline_runs.py` and `test_pipeline_data.py` also call `pipeline_service.py`; they take about 25 seconds, so they were left out of its set to keep each mutant's run short. `test_optional_packages.py` starts Python processes and was left out of the `model_ossie.py` set for the same reason.

No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `model_ossie.py` | test_model_ossie, test_model_ossie_edges, test_model_ossie_service_edges, test_tmdl, test_tmdl_write, test_safe_parse_readers, test_deep_nesting (+ test_model_ossie_mutation) | 150 of 207 (149 + 1) | 117 / 33 | **78.0%** | 117/149 → 148/149 | 0/1 → 1/1 | 149 / 1 | **99.3%** | 1 equivalent |
| `pipeline_service.py` | test_pipeline_service_edges, test_pipeline_definitions, test_pipeline_gotchas, test_activity_registry (+ test_pipeline_service_mutation) | 150 of 187 (147 + 3) | 109 / 41 | **72.7%** | 107/147 → 143/147 | 2/3 → 3/3 | 146 / 4 | **97.3%** | 4 equivalent |
| `risk_service.py` | test_risk_service_edges, test_risk_mutation, test_boundary_mutation_3, test_risk (+ test_risk_service_mutation) | 150 of 168 (148 + 2) | 111 / 39 | **74.0%** | 110/148 → 148/148 | 1/2 → 2/2 | 150 / 0 | **100.0%** | none |

One defect was found and is recorded as a strict `xfail` in `test_risk_service_mutation.py`: a CU hours threshold of 0 given as text ("0") is kept as 0.0, so the check warns on every run, although the setting's description says 0 turns the warning off. A number 0 does turn it off.

### What the survivors were, and the tests that now kill them

- **model_ossie.py**:
  - A missing PyYAML is named as the package people install, and a missing vendored module by its full name.
  - The loading hook doesn't answer for a name that is already loaded.
  - An alias is refused with its own line number.
  - The largest file is 20 million characters, a file of exactly the limit is read, and the size message gives whole megabytes.
  - The converter's report splits each warning into its object and message, and a warning without a closing bracket keeps its whole text.
  - Only SQL dialects of a dataset metric are qualified with the dataset, and an entry without text stays without it.
  - A metric without an expression or extensions is still lifted, and its Power BI stash gains its table with sorted keys while other vendors' extensions stay.
  - Names, placeholders and the shared Direct Lake source are read only from entries that have them.
  - A new table's partitions read through the model's own source, a model without one keeps the file's, and a table in both keeps the model's partitions.
  - A new table's detail counts what it has, and a changed column without an expression has no detail.
  - The round trip sends DAX as written, and TMDL to Ossie writes simple measures as SQL unless asked not to.
  - → `test_model_ossie_mutation.py`.
- **pipeline_service.py**:
  - A definition that can't be read counts one error only, only an inactive activity is marked, and import lists no expressions.
  - Notebook steps get the notebook runner only from the engine, contained and with their outputs removed by default.
  - Lakehouse reads and SQL projects need the engine and a request.
  - A run runs four activities at once unless told, and one whose notebooks read a lakehouse signs in first.
  - A run that can't start says why and saves nothing, and one with no notebooks or data needs no sign-in.
  - A run's thread is a daemon, and a finished run is audited with how it ended and how long it took.
  - A running run is saved at most twice a second, but its end at once.
  - Cancelling a run that isn't going says its state.
  - Runs are listed newest first, pipelines are found four folders down and no further, and a linked folder isn't followed.
  - A repository without a name is named by its folder, and the list reads 200 runs.
  - → `test_pipeline_service_mutation.py`.
- **risk_service.py**:
  - A zero threshold written as text, and zero failed runs, are accepted.
  - A failed capacity reading and unreadable usage are cut to 200 characters, and only this workspace's environment is its reading.
  - The CU index reads 500 runs without a refresh and 90 days of usage.
  - A Fabric step without a workspace name reads as blank, and plain facts have blank defaults.
  - A Data mesh entity needs a sign-in unless its tables are on this PC, and its input is read for a run only, in dev by default, with an error cut to 300 characters.
  - A Fabric run reads its workspace's capacity, and only runs where and in the environment the action runs count.
  - A notebook without cells is checked as empty.
  - The check refuses actions that start no work and reads without a refresh unless asked.
  - A check that fails is unknown and lets the run go.
  - An acknowledgement without an env says blank and cuts a long note, acknowledgements go into a new folder, and 50 are read back.
  - Placement advice reads 20 receipts and 10 side-by-side runs.
  - → `test_risk_service_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `model_ossie.py` | 223 | `sort_keys=False` to `True` when the document is written for the converter | Equivalent: the converter reads the document's mappings by key, and lists keep their order either way. |
| `pipeline_service.py` | 164 | `allow_uncontained` default `False` to `True` in `_prepare` | Equivalent: every caller passes it. |
| `pipeline_service.py` | 165 | `read_lakehouses` default `False` to `True` in `_prepare` | Equivalent: every caller passes it. |
| `pipeline_service.py` | 330 | `or` to `and` for a run record without a pipeline name | Equivalent: no listed pipeline is named "" or None, so the key is never looked up. |
| `pipeline_service.py` | 356 | `values=False` to `True` for a live run in the list | Equivalent: the list keeps only fields that carry no values. |

`model_ossie.py` has changed since this run: it now checks the file's Ossie version before anything else. On 2026-11-20 one test was updated for that change, the one that reads a file of exactly the size limit. It now expects the unknown-version message instead of the old one. The scores above are from the run as described.

