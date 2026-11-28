# Mutation testing, batch 261: the Fabric Excellence Assessment controls (every mutant)

Six modules not covered before, the controls and inspections the excellence assessment added for DevOps, notebooks and pipelines, capacity and semantic models, run uncapped (`--cap 100000`), so every mutant ran:
- Whether a workspace is connected to Git and in step with its branch, from the Git reads the app already makes (`excellence/catalog_devops.py`).
- The notebook and pipeline controls, among them a pipeline activity with no retry, no timeout under 12 hours, or no failure path (FEA-REL-004, 005 and 006) (`excellence/catalog_artifacts.py`).
- What a notebook's code or a pipeline's activities show, without keeping the code: rule matches, activity names, and the retry, timeout and failure-path settings read from each activity (`excellence/inspect_items.py`).
- Capacity controls from imported Capacity Metrics exports: failed operations, refreshes a day and utilization (`excellence/catalog_capacity.py`).
- Semantic model controls from the model's definition (`excellence/catalog_models.py`).
- What a semantic model's definition shows, as names and counts only (`excellence/inspect_models.py`).

The run was at `main` 3424d8f33 (2026-11-23), with `sidecar/tools/mutate.py` and `--cap 100000 --seed 7 --kinds all`. Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each module's set is its own test file and test_excellence_catalog, which registers every control; the notebook and pipeline modules also have test_catalog_artifacts_cov3. test_excellence_ui_names was left out: it reads `app/`, which isn't in the copy. No `-k` filter or `--deselect` was used. No mutant's run timed out. The "after" numbers come from re-running each module's survivors with `--only` once its new tests were in.

Later batches (356 to 361, 368 to 374 and 406 to 411) added their own `test_excellence_catalog_artifacts_mutation.py`, `test_excellence_catalog_capacity_mutation.py` and `test_excellence_catalog_models_mutation.py`. Those files are kept as they are. This batch's tests for the same three modules are in the files ending `_mutation_fea.py`, so neither set replaces the other. The numbers above are this batch's, measured before those files existed.

Before the "after" runs, the new tests passed in a copy of `sidecar/` on their own and against each module as `ast.unparse` writes it.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/catalog_devops.py` | test_excellence_devops, test_excellence_catalog (+ test_excellence_catalog_devops_mutation) | 97 of 97 (96 + 1) | 87 / 10 | **89.7%** | 87/96 → 95/96 | 0/1 → 1/1 | 96 / 1 | **99.0%** | 1 equivalent |
| `excellence/catalog_artifacts.py` | test_excellence_artifacts, test_catalog_artifacts_cov3, test_excellence_catalog (+ test_excellence_catalog_artifacts_mutation_fea) | 128 of 128 (126 + 2) | 87 / 41 | **68.0%** | 87/126 → 124/126 | 0/2 → 2/2 | 126 / 2 | **98.4%** | 2 equivalent |
| `excellence/inspect_items.py` | test_excellence_artifacts, test_catalog_artifacts_cov3, test_excellence_catalog (+ test_excellence_inspect_items_mutation) | 130 of 130 (124 + 6) | 114 / 16 | **87.7%** | 108/124 → 122/124 | 6/6 → 6/6 | 128 / 2 | **98.5%** | 2 equivalent |
| `excellence/catalog_capacity.py` | test_excellence_capacity, test_excellence_catalog (+ test_excellence_catalog_capacity_mutation_fea) | 179 of 179 (167 + 12) | 128 / 51 | **71.5%** | 127/167 → 162/167 | 1/12 → 12/12 | 174 / 5 | **97.2%** | 5 equivalent |
| `excellence/catalog_models.py` | test_excellence_models, test_excellence_catalog (+ test_excellence_catalog_models_mutation_fea) | 79 of 79 (77 + 2) | 54 / 25 | **68.4%** | 53/77 → 75/77 | 1/2 → 2/2 | 77 / 2 | **97.5%** | 2 equivalent |
| `excellence/inspect_models.py` | test_excellence_models, test_excellence_catalog (+ test_excellence_inspect_models_mutation) | 63 of 63 (61 + 2) | 52 / 11 | **82.5%** | 52/61 → 57/61 | 0/2 → 2/2 | 59 / 4 | **93.7%** | 4 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/catalog_devops.py**:
  - A Git value with a space (32) is kept; one with a control character (31) or DEL (127) is emptied, and one with character 128 is kept. A value is cut to 256 characters.
  - A workspace without a display name is named by its `name`.
  - The record's source names Get Status only when the status was read and kept.
  - A provider the app has no display name for is shown as Fabric gives it.
  - A workspace behind its branch names how many of the incoming items are in conflict.
  - → `test_excellence_catalog_devops_mutation.py`.
- **excellence/catalog_artifacts.py**:
  - FEA-REL-004 and 005: an inspection made before the retry or timeout settings were kept (no `retry_unknown` or `timeout_unknown`) is not evaluated. With `and` for `or` in `_callouts`, such a pipeline passed: a pass on settings nobody had read.
  - FEA-REL-006: an inspection made before the dependencies were kept (no `failure_path`) is not evaluated; with `and` for `or` the control broke on the missing key instead.
  - The wording of every finding: "1 place" and "3 places", "1 activity is" and "2 activities are", "has" and "have", the first five places or names and "and N more" (none at exactly five), and the five names of activities that couldn't be read.
  - A notebook finding's count, and the activity lists kept in a pipeline finding's detail.
  - The VACUUM control names 7 days.
  - → `test_excellence_catalog_artifacts_mutation_fea.py`.
- **excellence/inspect_items.py**:
  - The exact answers for a definition not read (`{"read": False}`), a notebook without cells, a pipeline without activities, and an inspection that breaks (read, with the error's type only).
  - A workspace id inside a list in an activity's settings is found.
  - A rule keeps 20 places and counts every match; a list keeps 50 names and its count has them all.
  - `_timeout_set`: seconds and minutes each count, a timeout of one second is set, 12 hours exactly is not under the default, and a day and a second is over it (dividing the days by 24 read that as one second). `_retry_set` is pinned for 0, text, expressions, a flag and unreadable text.
  - → `test_excellence_inspect_items_mutation.py`.
- **excellence/catalog_capacity.py**:
  - The thresholds at their exact values: 10% of CU failed fails, 24 refreshes on a day aren't counted (more than 24 are), a window of one day is enough to pass, 7 days of Compute-page periods are enough, and 10% utilization passes.
  - What counts: a CU of 0 is kept, a negative CU, a flag or text isn't; a row without a start is left out; a period that ends where it starts, or has no CU, is left out; two periods that only touch both count, in either order; operations that used no CU are not evaluated, and half a CU-second is a share.
  - An F0 SKU, a SKU not given and a state not given don't apply, with their wording.
  - No operations leave the refresh control not evaluated.
  - The evidence: CU kept to three places, imports named, counted and listed by id, the top 20 refresh days with ties by name and their workspace.
  - The wording: CU-hours, the window's dates, five items named and every day counted, the refreshes the benefit would leave out, the days covered, and the unused capacity in the benefit.
  - → `test_excellence_catalog_capacity_mutation_fea.py`.
- **excellence/catalog_models.py**:
  - A model not inspected (no kind, or no relationship count) is not evaluated by every control; dropping that answer made the control break instead. A rule control needs the rules the inspection kept.
  - A model set to DirectQuery only fails with its own reason, and the Direct Lake control applies from one Direct Lake table.
  - The wording: singular and plural, the first five names and "and N more", the roles and the tables they filter, and a model whose roles filter no rows.
  - → `test_excellence_catalog_models_mutation_fea.py`.
- **excellence/inspect_models.py**:
  - A name is kept up to 256 characters and cut with an ellipsis after; 50 names a list and 50 objects a rule are kept, and every one is counted.
  - A definition without a model gives exactly that error.
  - A relationship missing one of its tables is left out, and the rest of the model is still inspected.
  - → `test_excellence_inspect_models_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `excellence/catalog_devops.py` | 176 | `_uncommitted`'s `conflicts or 0` to `or 1` | Equivalent: `_counts` always writes `conflicts`, so the fallback is never used. |
| `excellence/catalog_artifacts.py` | 80 | `_nb_rule`'s `count or 0` to `or 1` | Equivalent: inspect_items always writes a rule's `count`, and it is at least 1. |
| `excellence/catalog_artifacts.py` | 84 | `p.get("cells", 0)` to `1` | Equivalent: inspect_items writes `cells` with every notebook's `rules`, and `_nb_rule` reaches this line only when `rules` is there. |
| `excellence/inspect_items.py` | 91 | `_names_fixed_workspace`'s last `return False` to `None` | Equivalent: every caller only tests whether the answer is true. |
| `excellence/inspect_items.py` | 129 | `days * 24` to `days * 25` | Equivalent: a timeout of a day or more is over the 12-hour default either way. |
| `excellence/catalog_capacity.py` | 188 | the failure detail's `round(failed, 3)` and `round(total, 3)` to 4 places (2 mutants) | Equivalent: both are sums of `cu_by_status`, which is already rounded to three places. |
| `excellence/catalog_capacity.py` | 251 | `int(m.group(1)) <= 0` to `<= 1` | Equivalent: there is no F1 SKU; F2 is the smallest. F0 is tested. |
| `excellence/catalog_capacity.py` | 282 | the utilization detail's `round(used, 3)` and `round(available, 3)` to 4 places (2 mutants) | Equivalent: `used` sums CU already rounded to three places, and `available` is a whole number of CU-seconds. |
| `excellence/catalog_models.py` | 125 | `_fallback`'s `direct_lake_tables or 0` to `or 1` | Equivalent: the control applies only when there is at least one Direct Lake table, so the fallback is never used. |
| `excellence/catalog_models.py` | 149 | `_rule`'s `count or 0` to `or 1` | Equivalent: inspect_models always writes a rule's `count`, and it is at least 1. |
| `excellence/inspect_models.py` | 56 | the table's `"dimension": True` to `False` | Equivalent: only SM006 reads it, and the inspection doesn't ask for SM006. |
| `excellence/inspect_models.py` | 68 | the relationship's `"active"` (`is not False` to `is False`, and its default, 3 mutants) | Equivalent: model_practices.check doesn't read `active`; inactive relationships are left out of the bidirectional list before this, and that is tested. |
