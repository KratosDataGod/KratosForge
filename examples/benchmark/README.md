# TPC-H benchmark notebooks

Two notebooks for testing a load on your PC and in Fabric with a standard workload, side by side:

| Notebook | What it does |
| --- | --- |
| [tpch_data.ipynb](tpch_data.ipynb) | Generates the eight TPC-H tables at the scale factor you pick (1 is about 1 GB) with DuckDB's tpch extension and writes them to the attached lakehouse in the `tpch` schema (as `tpch_region` and so on in a lakehouse without schemas). Run it once. |
| [tpch_benchmark.ipynb](tpch_benchmark.ipynb) | Loads those tables, runs the 22 TPC-H queries, times each step and writes each answer to `bench.q01` ... `bench.q22` (`bench_q01` ... without schemas). |

No data is stored here: it is generated when you run the first notebook, at any scale.

**In the app:** open the command bar (Ctrl+K) and choose **Add the TPC-H benchmark notebooks**, or put these files in `%LOCALAPPDATA%\FabricDesktop\notebooks`. Attach a lakehouse, run **TPC-H data**, then open **TPC-H benchmark**, turn on **Side by side** and select **Run all**.

How it works, what the result shows and what to expect: [docs/side-by-side.md](../../docs/side-by-side.md).

These files are generated from `sidecar/fabric_desktop/benchmark.py`. After changing it, run `python -m fabric_desktop.benchmark ../examples/benchmark` from `sidecar/`; a test checks they match.

## Large tables

[large_tables.py](large_tables.py) measures an incremental metadata run on a large local Delta table, once with Delta MERGE writes and once the way runs worked before 0.20 (read the whole table and overwrite it), and checks both end with the same rows. Run it from `sidecar/`: `python ../examples/benchmark/large_tables.py --rows 5000000` (add `--kind scd2` for a Type 2 dimension). Results and what they mean: [Large tables](../../docs/metadata-guide.md#large-tables).

[batched_loads.py](batched_loads.py) measures a full load of a lineitem-shaped source table (TPC-H's columns and row count per scale factor, generated with NumPy), once reading the whole source and once with `[load] batch_by`, and checks both end with the same rows. Run it from `sidecar/`: `python ../examples/benchmark/batched_loads.py --scale 1` (`--only batched` for a scale whose single read wouldn't fit in memory). Results: [Large tables](../../docs/metadata-guide.md#large-tables).

## Start-up, notebook start and SQL

[startup_and_sql.py](startup_and_sql.py) times the engine starting, a notebook starting and local SQL queries over a generated table, with median and p95 over several runs. Run it from `sidecar/`: `python ../examples/benchmark/startup_and_sql.py` (`--runs 10 --json bench.json` for more runs and a file). What it measures and the recorded baseline: [Performance benchmark](../../docs/performance.md).

### In CI, and refreshing the baseline

The **Performance** workflow runs this on each runner image in its matrix and compares it with that
image's baseline in [baselines/](baselines) using [compare.py](compare.py). It fails on two runs in a row
past the margin; the rules are in [Performance benchmark](../../docs/performance.md#in-ci).

Refresh a baseline when a change makes things faster or slower on purpose, or when GitHub changes the
runner image. Do it from a run on that image, never from your own PC, and only in a pull request (the
workflow never writes one). For a new image, add it to the workflow's matrix with its baseline file.

1. Run the **Performance** workflow on main (Actions, then **Run workflow**), or open the latest run.
2. Download its `benchmark-<commit>` artifact (`bench.json`), or copy the JSON from the **Show the result** step.
3. From `sidecar/`: `python ../examples/benchmark/compare.py bench.json --write-baseline ../examples/benchmark/baselines/<image>.json --runner "GitHub Actions <image>" --commit <commit>`
4. Commit the new file in a pull request that says why the numbers moved.

## Memory soak

[memory_soak.py](memory_soak.py) loops notebook cells, kernel restarts, local SQL and Delta writes, and checks that memory, handles, threads, child processes and temp files level off. Run it from `sidecar/`: `python ../examples/benchmark/memory_soak.py --minutes 20`. The **Memory soak** workflow runs it nightly on Linux and Windows and only warns for now. What counts as growth: [Memory soak](../../docs/performance.md#memory-soak).

These runs follow TPC-H's data and queries but are not audited TPC-H results. TPC-H is a trademark of the Transaction Processing Performance Council.
