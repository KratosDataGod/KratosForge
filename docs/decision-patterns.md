# Decision patterns (Preview)

A decision pattern explains a choice that has no single right answer: when each option works, when
it fails, and what it does to cost and reliability. Each one ends with a small example that runs
offline in a notebook on this PC (Python or DuckDB), so you can see the tradeoff instead of taking
it on trust. Nothing here is measured on your data: to measure, use the tools each pattern names.

Patterns don't decide for you, and they don't change what a policy or a safety check requires.

- [DP-01: Incremental or full refresh](#dp-01)
- [DP-02: DuckDB on this PC or Spark in Fabric](#dp-02)

## Each pattern

### DP-01

**Incremental or full refresh**

A full refresh rebuilds a table from all of its source on every run. An incremental refresh reads
only what changed since the last run, usually rows with an `updated_at` (a watermark) later than the
last one it saw, and adds or merges them into the table.

Full refresh works when:

- The source is small enough to read in the time and capacity you have for each run.
- The source can change or delete old rows, and you have no reliable record of what changed.
- You want each run to correct whatever an earlier run got wrong.

Incremental refresh works when:

- The source is large and most of it doesn't change between runs.
- Every change has a time or a sequence you can trust, such as a Delta table's change data feed.
- A run can be repeated safely: the same rows applied twice give the same table (a `MERGE` on a key,
  not an `INSERT`).

Incremental refresh fails when:

- Rows arrive late, with an `updated_at` earlier than the watermark. They are skipped, and nothing
  says so. The example below shows this.
- The source deletes rows. A watermark only sees rows that still exist.
- The watermark is saved before the write finishes, and the write fails. The next run starts after
  rows that were never written.

Cost: an incremental run reads and writes less, so it uses less time and capacity than a full one on
a large table. A full refresh's cost grows with the table, every run.

Reliability: a full refresh corrects itself on the next run. An incremental table drifts from its
source until something compares the two. Plan a full rebuild, or a comparison, on a schedule.

How to decide: start with full refresh while it fits the run's time. Move to incremental when it
doesn't, with a key to merge on, a lookback window for late rows, and a regular check against a full
rebuild ([Prove](prove/how-proofs-work.md) compares two results row by row).

```python
# Runs on this PC with DuckDB; nothing is read from or written to Fabric.
import duckdb
con = duckdb.connect()
con.execute("CREATE TABLE source (id INT, amount INT, updated_at TIMESTAMP)")
con.execute("INSERT INTO source VALUES (1, 10, '2026-10-01 09:00'), (2, 20, '2026-10-01 10:00')")
con.execute("CREATE TABLE target AS SELECT * FROM source")            # the first load
watermark = con.execute("SELECT max(updated_at) FROM target").fetchone()[0]

# A new row, and a late one: written now, but stamped before the watermark.
con.execute("INSERT INTO source VALUES (3, 30, '2026-10-01 11:00'), (4, 40, '2026-10-01 08:00')")
con.execute("INSERT INTO target SELECT * FROM source WHERE updated_at > ?", [watermark])

missing = con.execute("SELECT id FROM source EXCEPT SELECT id FROM target").fetchall()
print("Rows the incremental run missed:", missing)                   # [(4,)]
```

### DP-02

**DuckDB on this PC or Spark in Fabric**

KratosForge runs `spark` and `%%sql` cells on this PC with DuckDB, through DuckDB's PySpark API. In
Fabric the same notebook runs on Spark, spread across the nodes of a Spark pool.

DuckDB on this PC works when:

- The data fits on this PC's disk, and the work fits its memory with room to spill to disk.
- You are developing or testing: a run starts in seconds, with no session to wait for and no Fabric
  job.
- The notebook uses the parts of the Spark API that DuckDB's covers. The [engine picker](engines.md#engine-picker-preview)
  and [Validate](validate-notebook.md) say what doesn't.

Spark in Fabric works when:

- The data is bigger than one machine handles well, or the job needs more cores than this PC has.
- The job is scheduled, shared, or must run where the data is governed.
- The notebook uses Spark features DuckDB's API doesn't have, or Fabric-only libraries.

Each one fails when:

- On this PC: the data outgrows the disk or the memory, or the results must be uploaded to OneLake
  over a slow connection. The upload, not the query, then sets the run's time.
- In Fabric: small, quick jobs spend most of their time starting a session, and every run uses
  capacity.
- Both: the two engines can give different answers for the same code, for example in decimal
  division or time zones. Check before trusting a result from the other side.

Cost: a run on this PC starts no Fabric job, so the compute uses no Fabric capacity. Reading or
writing OneLake from this PC still uses some. A run in Fabric uses capacity for the whole session.

Reliability: a run on this PC depends on this PC being on and connected. A scheduled run in Fabric
doesn't.

How to decide: develop on this PC, then measure the real notebook both ways with
[Side by side](side-by-side.md), which times each run and checks both wrote the same tables (see
[Comparing fairly](side-by-side.md#comparing-fairly)). Schedule where the measurement and the
reliability needs point.

```python
# Runs on this PC with DuckDB: how long a group-by over 10,000,000 rows takes here. It's a
# measurement of this PC only; Side by side measures your notebook in both places.
import time, duckdb
start = time.perf_counter()
rows = duckdb.sql("SELECT range % 1000 AS k, sum(range) AS s FROM range(10000000) GROUP BY k").fetchall()
print(f"{len(rows):,} groups in {time.perf_counter() - start:.2f} s on this PC (measured)")
```
