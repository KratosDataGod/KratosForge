# Performance benchmark

A small, repeatable benchmark for three things a person waits on: the engine starting, a notebook
starting, and local SQL queries. It is not part of the default CI run: run it by hand, on the PC
you want numbers for, and compare runs on the same PC.

```
cd sidecar
python ../examples/benchmark/startup_and_sql.py
python ../examples/benchmark/startup_and_sql.py --runs 10 --rows 1000000 --json bench.json
```

| Option | Default | What it does |
| --- | --- | --- |
| `--runs` | 5 | Times each measurement is repeated. |
| `--rows` | 1,000,000 | Rows in the generated table the SQL queries read. |
| `--warmup` | 1 | Uncounted engine and notebook starts first: the very first one compiles bytecode and fills the file cache. |
| `--json` | none | Also writes the full result to this file. |

Each line is reported as median, p95 (nearest rank, so with 5 runs it is the slowest run) and min,
in milliseconds. Nothing touches OneLake, Fabric or the network, and no Fabric capacity is used:
everything runs in a temporary folder that is removed at the end, with `LOCALAPPDATA` and `HOME`
pointed there so your own settings and notebooks are not read or changed.

## What is measured

| Measurement | From | To |
| --- | --- | --- |
| startup | starting `python -m fabric_desktop.server`, as the app does | its first answer on the RPC pipe (`app_settings`) |
| notebook first cell | starting a fresh Python process | `Kernel()` made and a first cell (`1 + 1`) finished |
| notebook first spark cell | the same start | a first `spark.range(1000).count()` finished |
| SQL first query on a new project | a local data project over the generated Parquet file, just added | its first `COUNT(*)` finished (a fresh project each run) |
| SQL count, group by, filter | the open project | each DuckDB query finished |
| SQL t-sql top | the open project | a `SELECT TOP 10 ... GROUP BY` in T-SQL, translated and run |

The notebook start is the in-process kernel, timed in a fresh process; when Settings runs notebooks
in their own process the app adds that process's start. `Kernel()` already makes the local spark
session, so the first spark cell adds little on top of the first cell.

## Baseline

Recorded on 2026-11-02 at main `59a61423`, with the defaults (5 runs, 1 warm-up, 1,000,000 rows),
on a Linux cloud build machine (Python 3.13, 4 CPUs). It is a reference for comparing changes on
that kind of machine, not a figure for a person's Windows PC: a Windows baseline has not been
recorded yet.

| Measurement | Median | p95 | Min |
| --- | ---: | ---: | ---: |
| startup | 1,065.5 | 1,080.6 | 938.8 |
| notebook first cell | 756.7 | 819.4 | 714.6 |
| notebook first spark cell | 759.3 | 822.7 | 717.0 |
| SQL first query on a new project | 1.7 | 2.2 | 1.5 |
| SQL count | 1.4 | 1.5 | 1.2 |
| SQL group by | 5.6 | 7.5 | 5.5 |
| SQL filter | 3.4 | 4.3 | 3.1 |
| SQL t-sql top | 10.6 | 27.7 | 10.1 |

On that machine the T-SQL query's p95 was about 2.6 times its median, and the other lines' p95 was
within about a third of their median. Treat a change smaller than that spread as noise, and re-run
with more `--runs` before reading anything into it.

## The app window

`app/tests/browser/ui-benchmark.cjs` times the window itself in headless Chromium, with every engine call
answered by a fake, so it needs no engine, Fabric or network. It was added before platform skins, to have a
baseline to compare them with.

```
NODE_PATH="$(npm root -g)" node app/tests/browser/ui-benchmark.cjs --runs 3 --json ui.json
```

| Measurement | What it times |
| --- | --- |
| startup | page load until the left rail is drawn and every script has loaded |
| navigation | opening each left-rail page until it is styled and laid out (first visit and later visits) |
| token switch | new values for the color tokens on the page root, as a skin change sets them, until restyled |
| density switch | Comfortable to Compact and back, until restyled |
| large list | a workspace of 5,000 items: Show items until the first screen of rows is drawn, and the elements added |
| editor | 200 characters typed into the SQL editor, and the longest task while typing |
| memory | JS heap, elements in the page, and elements still held anywhere, after visiting every page |
| background | main-thread time used in 10 seconds of doing nothing |

Baseline, 2026-11-03 at main `00298ad`, median of 3 runs, on a Linux cloud build machine (4 CPUs, Chromium
from Playwright 1.63). Times in milliseconds.

| Measurement | Baseline | Budget |
| --- | ---: | ---: |
| startup | 349 | 1,200 |
| navigation, slowest page | 4.7 | 25 |
| navigation, median | 2.2 | |
| token switch, slowest | 5.2 | 25 |
| density switch, slowest | 2.7 | 15 |
| large list, 5,000 items | 2,959 | 2,100 |
| large list, elements added | 76,673 | 9,000 |
| editor, 200 characters | 724 | 2,500 |
| editor, longest task | 159 | 500 |
| JS heap | 10.3 MB | 32 MB |
| elements in the page, on Home | 356 | 1,500 |
| background, per 10 s | 11.1 | 60 |

`app/tests/browser/ui-budgets.test.cjs` runs it in CI, one uncounted warm-up run and then one
counted run, and fails when a number goes over its budget in
`ui-budgets.json`. Each budget is about three times the baseline, so a slow CI machine doesn't fail it and a
real regression does. Lower a budget when a change makes the app faster; raising one needs the reason in the
pull request.

What the baseline shows:

- **Switching appearance is cheap.** Restyling the whole window for new tokens takes about 5 ms, so a skin
  can switch at once without reloading the page (which would also lose open work).
- **Large workspaces were slow.** At the baseline, 5,000 items took about 3 seconds and added about 77,000
  elements, because every row was drawn. Now the first 200 rows are drawn and 200 more each time you scroll
  near the end: 706 ms and 3,007 elements (median of 3 runs, same machine, 2026-11-03), and the budgets are
  about three times that. The browser's find-in-page only sees rows already drawn, so in a big workspace
  scroll to the end first.
- **Pages already left are kept in memory.** After visiting every page and returning to Home, about 94,000
  elements are still held, against 356 in the page, even after a garbage collection. In a separate check the
  held count grew after the first and second visit to a large workspace and then stopped, so it looks like one
  earlier copy of a page is kept, not a leak that grows without end. What holds it is not known yet (UNVERIFIED).
- The editor's 200 characters include Playwright's own time per key, so read it as a comparison between runs,
  not as typing latency.

## What it is not

The engine benchmark (`startup_and_sql.py`) is not run in CI and nothing fails on its numbers yet; the
[quality gates](audits/QUALITY_GATES.md) propose recording it per build, warning-only at first. It does not time WebView2 or the Tauri shell
(see [The app window](#the-app-window) for the page itself), a notebook on Fabric, or OneLake reads and
writes. For TPC-H on your PC and in Fabric side by side, see [the TPC-H benchmark notebooks](https://github.com/KratosDataGod/KratosForge/tree/main/examples/benchmark).
