# Mutation testing, batch 44

Three modules not covered before, none touched by an open pull request, run with the tool's default `--kinds all`, as in batch 42. The table shows the point and boundary parts separately.

- The Delta read estimate before a load: rows and bytes counted from the log's add actions after a watermark, files without a row count, the column share of the bytes, the memory width per row, sizes in MB or GB, and the guard that stops a read that won't fit with its headroom (`meta/preflight.py`).
- One assessment of Fabric workloads: the opportunity that keeps each workload when two touch it, overlaps, the totals and the CU value only when the CU total is complete, the ranking, coverage and counts (`opportunities/assess.py`).
- Notes under a Query profile: a scan that read many rows and kept few through a wrapped column, and a cross join or join that multiplied rows, each at its exact threshold, with a join's inputs read from the step depths (`learn/evidence.py`).

The run was at `main` 8d6c0e7fb (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these runs reads `docs/` or a module's source.

The new tests write no files and use no network or timing. The preflight tests give `estimate` a stand-in table whose log holds chosen add actions, so the files without a row count, a log with no row counts at all and an empty read can be set up exactly; the assessment counts test replaces the analyzers and the inventory with stand-ins through `monkeypatch`.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/preflight.py` | test_preflight, test_batches, test_meta_batches_mutation (+ test_meta_preflight_mutation) | 81 of 81 (78 + 3) | 71 / 10 | **87.7%** | 70/78 → 78/78 | 1/3 → 3/3 | 81 / 0 | **100%** | none |
| `opportunities/assess.py` | test_opportunities, test_opportunities_share, test_opportunities_analyzers_mutation, test_opportunities_impact_mutation (+ test_opportunities_assess_mutation) | 50 of 50 (49 + 1) | 35 / 15 | **70.0%** | 35/49 → 49/49 | 0/1 → 1/1 | 50 / 0 | **100%** | none |
| `learn/evidence.py` | test_learn_evidence, test_learn_service (+ test_learn_evidence_mutation) | 80 of 80 (73 + 7) | 62 / 18 | **77.5%** | 62/73 → 71/73 | 0/7 → 7/7 | 78 / 2 | **97.5%** | 2 equivalent |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **meta/preflight.py**:
  - Sizes switching to GB at exactly 1 GB, and both units divided by powers of 1024.
  - An estimate exact unless told otherwise, and its text for one file and for a count that is "at least".
  - Files without a row count adding no rows and making the count inexact; a log with no row counts at all; nothing after the watermark reading no bytes, and a file at the watermark left out; an empty schema reading no columns.
  - Free memory as the platform reports it, and a read that fits exactly with its headroom allowed to run.
  - → `test_meta_preflight_mutation.py`.
- **opportunities/assess.py**:
  - Of two equally strong opportunities on one workload, the first keeps it.
  - Overlaps naming only the other opportunities that share a workload, never the opportunity itself or one that shares nothing.
  - The CU value given when the CU total is complete, and the reason when it isn't.
  - Coverage by type, analyzed, not read, types not analyzed and history not read, counted item by item; the counts of an assessment by category, confidence and action, and how many are counted.
  - → `test_opportunities_assess_mutation.py`.
- **learn/evidence.py**:
  - The thresholds at their exact edges: a scan of exactly 100,000 rows, a scan that kept exactly a quarter, a join of exactly 100,000 rows, a join that grew exactly ten times, a cross join that made only its larger input, and 1% shown as a percentage.
  - A join with three inputs gets no note.
  - A scan or join without a kept count, an input count or a share shows zero.
  - A join's inputs stopping at the next step at its level, and a step with no depth taken as the top.
  - → `test_learn_evidence_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `learn/evidence.py` | 70 | a scan's missing `rows_scanned` read as 1 instead of 0 | Equivalent: both are below the 100,000-row minimum, so the scan gets no note either way. |
| `learn/evidence.py` | 85 | a join's missing `rows` read as 1 instead of 0 | Equivalent: both are below the 100,000-row minimum, so the join gets no note either way. |

