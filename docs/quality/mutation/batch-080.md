# Mutation testing, batch 80

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds.
- Connections for the Data Factory page: the cloud and gateway connections the person can use, and what uses each one in each environment's workspace (`fabric_connections_service.py`).
- Pipeline, copy job and dataflow runs for Monitor > Runs, and one run's activities (`fabric_pipeline_runs_service.py`).
- The Direct Lake check's service half: a Delta table's numbers from its log and Parquet footers, relationship keys, and the check itself (`model_directlake_service.py`).

The run was at `main` 1b1e47552 (2026-11-17), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), so every run was made with `PYTHONDONTWRITEBYTECODE=1`. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against each module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/`.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `fabric_connections_service.py` | test_fabric_connections, test_fabric_connections_service_edges, test_workspace_dataflows (+ test_fabric_connections_mutation) | 79 of 79 (79 + 0) | 70 / 9 | **88.6%** | 70/79 → 78/79 | none | 78 / 1 | **98.7%** | 1 equivalent |
| `fabric_pipeline_runs_service.py` | test_fabric_pipeline_runs, test_fabric_pipeline_runs_service_edges, test_remote_text_lines (+ test_fabric_pipeline_runs_mutation) | 105 of 105 (104 + 1) | 84 / 21 | **80.0%** | 84/104 → 100/104 | 0/1 → 1/1 | 101 / 4 | **96.2%** | 4 equivalent |
| `model_directlake_service.py` | test_model_directlake, test_model_directlake_service_edges (+ test_model_directlake_service_mutation) | 104 of 104 (101 + 3) | 92 / 12 | **88.5%** | 91/101 → 100/101 | 1/3 → 2/3 | 102 / 2 | **98.1%** | 2 equivalent |

No bugs were found in these modules.

### What the survivors were, and the tests that now kill them

- **fabric_connections_service.py**:
  - A connection row keeps its path and privacy level as Fabric gives them, and a connection without details has empty fields.
  - Environments come in the order dev, test, prod, then any other environment by name.
  - At most 500 definitions are read in one scan, and the rest are counted as skipped.
  - The scan runs on a daemon thread with the three tokens.
  - A long workspace or item id in what was missed is cut to 100 characters.
  - → `test_fabric_connections_mutation.py`.
- **fabric_pipeline_runs_service.py**:
  - Times are parsed to the exact microsecond, and a value that isn't text is no time.
  - A duration is rounded to the millisecond.
  - Activity text is capped at 16,000 characters: exactly 16,000 isn't marked as cut, and an empty value isn't either.
  - Activity JSON is indented by two spaces and keeps letters such as ü as they are.
  - An activity row keeps its start and end times, and an error with only a code is still shown.
  - A run with no times is looked up over the last day, with five minutes either side.
  - At most 20 runs per item and 300 in all are listed, newest first.
  - The scan runs on a daemon thread, and a long id in what was missed is cut to 100 characters.
  - → `test_fabric_pipeline_runs_mutation.py`.
- **model_directlake_service.py**:
  - Row groups are sampled across the table, every second file when half the footers are read, and the total is scaled up.
  - Row groups are exact when every footer is read.
  - At most 200 footers are read by default.
  - A Delta log without record counts leaves the row count unknown.
  - A relationship whose key types differ isn't matched row by row.
  - A model with no Direct Lake tables reports no findings and zero counts, and a model that isn't there is named with its workspace.
  - → `test_model_directlake_service_mutation.py`.

### Left

- **fabric_connections_service.py**, 1 equivalent: the thread pool's 8 workers becoming 9 changes how many definitions are read at once, not what the scan finds.
- **fabric_pipeline_runs_service.py**, 4 equivalent:
  - The thread pool's 8 workers becoming 9, as above.
  - The fraction rewrite's `count=1` becoming `0` (every match): a time has only one fraction, and an offset has no dot.
  - Keeping 7 fractional digits instead of 6, or padding to 7: on Python 3.11 and later, `fromisoformat` takes any number of digits and drops those past the sixth, so the time is the same.
- **model_directlake_service.py**, 2 equivalent:
  - `get_add_actions(flatten=False)`: in deltalake 1.6.6 the path, size and record count are top-level columns either way.
  - `len(sample) < len(paths)` becoming `<=`: when every file is read, the count scaled by the same number of files is the count itself.

