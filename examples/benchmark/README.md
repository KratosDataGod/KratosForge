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

These runs follow TPC-H's data and queries but are not audited TPC-H results. TPC-H is a trademark of the Transaction Processing Performance Council.
