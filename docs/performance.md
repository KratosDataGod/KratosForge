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

## In CI

The **Performance** workflow (`.github/workflows/perf.yml`) runs this benchmark on each runner image in its
matrix (now `windows-2025-vs2026`, Windows Server 2025) with the defaults above, after every merge to main,
on demand, and on a pull request that changes the benchmark. `examples/benchmark/compare.py` then compares
each median with that image's baseline, `examples/benchmark/baselines/<image>.json`, which records the
runner, commit and settings it came from. An image with no baseline compares nothing and can't fail. The job
summary shows each median next to the baseline, and the result file is kept as an artifact.

The Windows baseline was recorded on 2026-11-05 from the workflow's first run (Windows Server 2025, Python
3.12, 4 CPUs, the defaults above). Medians in milliseconds: startup 979.6, notebook first cell 753.5, notebook
first spark cell 754.4, SQL first query on a new project 1.5, count 1.5, group by 6.9, filter 4.6, t-sql top
28.3.

A measurement is past the margin when its median is more than 1.5 times the baseline's **and** more than
25 ms slower. The second rule keeps a 1 ms query that takes 3 ms on a busy runner from counting. The SQL
lines are compared only when the run used the baseline's table size.

**It is a gate on two runs in a row.** One run past the margin is a warning: GitHub's Windows runners differ
from one machine to the next by up to about 1.5x on unchanged code. The job fails when the same measurement
is past the margin in that run and in the previous run on the same image. Main's previous run is kept in
the Actions cache; a pull request starts from main's. Once a run is back within range, the count starts
again.

A baseline changes only in a reviewed pull request that changes its file; the workflow never writes one.
How to record one: [examples/benchmark/README.md](https://github.com/KratosDataGod/KratosForge/blob/main/examples/benchmark/README.md#in-ci-and-refreshing-the-baseline).

## Memory soak

A short soak checks that the engine gives back what it uses: memory, open handles, threads, child
processes and temp files. `examples/benchmark/memory_soak.py` stands in for the engine and loops its
everyday work. Every table stays the same size, so what the process holds should level off.

- **Notebook:** a cell in the notebook process overwrites a small local Delta table and reads it
  back. The kernel restarts every 25 cells, and the old process must have ended.
- **SQL page:** a query over a local Delta table, then a page of its result.
- **Writes:** an overwrite, a MERGE and a metadata-table append through the local store.

```
cd sidecar
python ../examples/benchmark/memory_soak.py --minutes 20
```

Every 15 seconds it records the process's memory (resident on Linux, private bytes on Windows), open
handles, OS threads (Linux only), Python threads, child processes (Linux only), the notebook process's
memory and the files in its temp and SQL results folders. After a warm-up (5 minutes by default), a
measurement counts as **growing** only when all three of these hold:

- its least-squares slope is past the limit per hour below;
- that slope adds up to at least the minimum rise over the run;
- the median of the last third of the samples is above the first third's by at least half that rise
  (a straight climb gives two thirds of it).

So one jump, or a notebook process that grows and is restarted, does not count. Only a steady climb does.

| Measurement | Growth per hour that counts | Minimum rise |
| --- | ---: | ---: |
| engine memory | 60 MB | 25 MB |
| open handles | 40 | 10 |
| OS threads | 40 | 10 |
| Python threads | 12 | 3 |
| child processes | 12 | 3 |
| temp and SQL result files | 40 | 10 |

The **Memory soak** workflow (`.github/workflows/soak.yml`) runs it for 20 minutes on Ubuntu 24.04 and
for 10 minutes (3-minute warm-up) on Windows Server 2025. It runs after each merge to main, on demand,
and on a pull request that changes the soak. **It is advisory:** a growing measurement is a warning in
the job summary, and the job stays green. A step that fails does fail the job, because the soak then
measured nothing. The result, with every sample, is kept as an artifact. `--gate` makes growth fail the
job (exit code 1); the workflow will use it once the limits have held on main for a while.

On a Linux cloud machine (4 CPUs), 20 minutes ran 1,439 loops with no failed step. After the warm-up,
memory went from 307 to 314 MB (14.6 MB per hour, under the 60 MB limit). Handles stayed at 19, OS
threads at 107, Python threads at 4, child processes at 1 and files at 20. A planted leak of 64 KB per
loop was reported as growing.

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
| large list, 5,000 items | 2,959 (706 now, see below) | 2,100 |
| large list, elements added | 76,673 (3,007 now) | 9,000 |
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
