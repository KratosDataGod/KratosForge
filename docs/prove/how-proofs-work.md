# How a proof works

A proof runs one notebook on your PC and in Fabric as a controlled experiment, checks whether both produced the same results under rules you can see, and keeps a receipt anyone can check. Open it with **Prove** in the notebook ribbon ([Notebooks and SQL](../notebooks.md#prove-this-notebook-preview) describes the panel). This page describes what happens underneath; the [program plan](plan.md) lists what comes next.

!!! note "Preview"
    The Fabric half has been tested by running the exact notebook Fabric would get, in a separate Python process, against Delta tables on disk. It hasn't run on a live Fabric tenant yet. [Live check](#live-check) lists what to try first.

## What happens

1. **Check.** The notebook is read before anything runs. A proof starts only if every read and write goes through `from fabric_desktop import lakehouse`, because that is what a proof can pin and contain. It doesn't start when a cell uses Spark (in Fabric, Spark reads and writes the lakehouse directly), opens a Delta table with `deltalake` itself, changes files with `notebookutils.fs`, or runs SQL that creates or changes tables. Each reason names the cell and line. Network calls, files written on the machine, other notebooks run with `%run`, and use of the clock or random values don't block a proof; they are listed as warnings and kept in the receipt.
2. **Pin the inputs.** The Delta version of every table in the attached lakehouse is recorded. Both runs read every table at that version, even if someone writes to it meanwhile. A table created after the proof starts doesn't exist for either run.
3. **Run on this PC.** The kernel restarts and runs every code cell in order, with the notebook's Parameters bar values set right after the parameter cell, as a run on this PC does, and with the random seed fixed (Python's `random`, and NumPy's when it is installed) and, on Linux and macOS, the time zone set to UTC. Writes go to the proof's own folder in the lakehouse: `Files/_fabric_desktop/proofs/<proof>/pc/tables/<schema>/<table>`. A cell that reads a table it wrote earlier reads that copy. The first append to an existing table starts from a copy of its pinned version, so both runs change the same starting state.
4. **Run in Fabric.** A copy of the notebook is published as **<notebook>_proof** in the notebook's workspace and run with Fabric's job scheduler. Your own published notebook, and any schedule on it, isn't touched. The copy has two extra cells: the first loads the same proof code used on your PC (copied word for word, so both sides follow the same rules) and the last saves what the run read, wrote and ran on to `Files/_fabric_desktop/proofs/<proof>/fabric/log.json`. Writes go to `.../<proof>/fabric/tables/...`. The Parameters bar's values go to the job as its parameters, which Fabric sets right after the parameter cell. A value the bar shows in red stops the proof before anything is published or run.
5. **Verify.** For every table either run wrote, the two outputs are compared under the rules below. The lakehouse's own tables are checked again: if any changed during the proof, something else wrote them, and the result is **inconclusive**. Files both runs read are compared by SHA-256; different content also makes the result inconclusive.
6. **Score and record.** A scorecard and an immutable receipt are saved (see below).

Only one proof runs at a time, and the notebook kernel can't run other cells while it does. Cancel stops whichever run is going; nothing is written to the lakehouse's tables either way.

## Results

| Verdict | Meaning |
| --- | --- |
| Same | Every table both runs wrote matched under the rules. The text says whether every row was compared or some tables only by checksum. |
| Differs | At least one table didn't match. Each table says how. |
| Inconclusive | The comparison can't be trusted: the lakehouse's tables changed during the proof, or a file had different content for each run. |
| Failed | A run failed; the cell that stopped it is named. |
| Nothing to compare | Neither run wrote a table. |

Each table's result has a **level**:

- **Complete**: every row was compared. With key columns, rows are matched by key, and the result counts rows only on this PC, rows only in Fabric and changed rows, names each changed column with the largest absolute and relative difference, and lists the first 50 differing rows with both values. Without keys, rows are matched as whole rows (order doesn't matter; duplicates count). If the key isn't unique, rows are matched as whole rows and the result says so.
- **Fingerprint**: the table has more rows than the limit (5,000,000 by default), so only row counts and an order-independent checksum of every row were compared. A matching checksum makes a difference very unlikely, not impossible; the result says so. Before hashing, decimals are rounded to 10 significant digits, so the checksum ignores differences smaller than that instead of applying the column tolerances.

## Rules

| Rule | Default | Meaning |
| --- | --- | --- |
| `keys` | none | Columns that identify a row. |
| `ignore` | none | Columns left out, such as a load timestamp. |
| `abs`, `rel` | 0, 1e-9 | Numbers match when they differ by at most `abs`, or by at most `rel` times the larger value. Two engines adding the same numbers in a different order differ around 1e-15. With both at 0, whole numbers and decimals are compared exactly, digit for digit (1.50 equals 1.5); a difference too small to show as a floating-point number is counted but its size isn't shown. |
| `columns` | none | Tolerances for one column: `{"amount": {"abs": 0.01}}`. |
| `timestamps` | `us` | Compare timestamps to the microsecond, millisecond (`ms`) or second (`s`). Timestamps are compared in UTC. |
| `limit_rows` | 5,000,000 | Above this, compare fingerprints only. |

A column of whole numbers on one side and decimals on the other (for example an average one engine returns as an integer type) is compared as numbers under these tolerances, and the result notes it. Any other type difference makes the table differ.

A key column that is text on one side and numbers on the other is matched as text, so rows still pair up; the type difference already makes the table differ. A rule that isn't valid (a negative or non-numeric tolerance, a row limit that isn't a whole number, `keys` that aren't column names) is refused with a message saying what it should be, and nothing is compared.

Rules can be set for all tables and per table. A finished proof can be verified again under other rules without running anything (`prove_verify`), for example to see whether a difference is within a tolerance; that check can be saved as its own receipt pointing at the proof's.

## Scorecard

Every number says what it is:

| Part | Kind | Where it comes from |
| --- | --- | --- |
| Time on this PC | Measured | First cell to last |
| CPU time and peak memory on this PC | Measured | The engine process while the notebook ran in it (sampled four times a second) |
| Fabric total, job, run and session start | Measured | Publish to finish; the job's start and end from Fabric's job API; the notebook's own run time from its log |
| Fabric CPU and memory | Not available | Fabric doesn't report them for a notebook run. A Python notebook starts with 2 vCores and 16 GB unless `%%configure` changes it. |
| Fabric capacity and cost | Estimate | Job time x CUs for a Python notebook x price per CU hour, from **Optimize > Cost and savings**. The assumptions are listed. On a capacity you already pay for, this is a share of it, not an added charge. |
| This PC's cost | Not available | No Fabric job starts, so its Fabric job CU is 0 (measured). Its OneLake reads and writes still use capacity and aren't measured, so the total isn't known. Power and hardware aren't counted. |

The two cost rows also carry each side's Fabric job CU as a classified measurement (Measured, Estimated, Scenario or Unknown, as in **Optimize > Cost and savings**), so a receipt's cost reads the same way as that tab. Neither side counts OneLake reads and writes. When Fabric doesn't report the job's start and end, Fabric's side is **Unknown** with no value, never 0. See [Capacity and cost evidence](../finops/cost-evidence.md).

## Receipts

A receipt is a JSON document whose id is the SHA-256 of its content (keys sorted, no spaces). Changing any part of it gives a different id, so anyone with the file can check it. It holds:

- the notebook's code: each cell's stable id and source hash, and one revision hash over all of them;
- the inputs: tables and the versions read on each side, files and their SHA-256;
- the parameters both runs used (the Parameters bar's values when the proof started);
- the environment on each side: Python and the versions of the packages the notebook imports;
- who ran it (your user and tenant) and where (workspace and lakehouse);
- each run's status and measurements, each table's verification with its level and rules, the scorecard, and the limitations found;
- the cost of each side: its Fabric job CU as a classified measurement.

Receipts are kept in `%LOCALAPPDATA%\FabricDesktop\receipts`, written once and never replaced. A receipt can be exported as an HTML or Markdown report, leaving out your identity, workspace and lakehouse names, example row values, or this PC's details.

The packages a Fabric run reports are remembered, so the notebook's dependency check can say "seen in Fabric on <date>, version x" instead of "unknown".

## Cleaning up

A proof's outputs stay in `Files/_fabric_desktop/proofs/<proof>/` so they can be checked again. Deleting them (`prove_delete_outputs`) removes that folder only; the receipt stays.

## Limits

- Proofs cover notebooks that read and write through the lakehouse module, published to Fabric as Python notebooks. Spark notebooks need a Spark-side adapter (plan, stage 6).
- The check reads the code; it can't see calls built at run time. Notebook code runs as you on both sides and could write elsewhere with your access; a proof contains what goes through supported calls and reports what it can see.
- A pinned version that is vacuumed away during the proof can't be read; the run fails and says so.
- The clock differs between runs; outputs built from it differ too. The warning names the cell.
- The time zone is fixed on Linux and macOS; Windows has no per-process time zone switch, so the receipt records "not changed" there.
- A run in Fabric that fails at a cell doesn't save its log, so its inputs and environment aren't known.

## Live check

Nothing here has run on a tenant. On a workspace with a small lakehouse nobody else writes to:

1. Make a notebook that reads one table and one CSV under `Files/` with the lakehouse module and writes one table.
2. Run a proof. Confirm a notebook named `<notebook>_proof` appears in the workspace, runs once in the Monitoring hub, and that your own published notebook is unchanged.
3. In the lakehouse, confirm no table under `Tables/` got a new version, and `Files/_fabric_desktop/proofs/<proof>/` holds `pc`, `fabric` and `fabric/log.json`.
4. Confirm the result shows both Python versions and the Fabric package versions.
5. Run a second proof and append a row to the input table from Fabric while it shows "Run in Fabric": the result must be inconclusive and name the table.
