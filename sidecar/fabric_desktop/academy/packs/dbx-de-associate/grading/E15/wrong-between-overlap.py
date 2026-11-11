# E15: land the orders table from the shop's database into bronze with partitioned reads. Run it with
# Run, check with Check my work. On this PC the database is a SQLite copy made from orders_source.csv:
# query() stands in for a JDBC connection and runs one SQL statement against it.
import csv
import json
import os
import sqlite3

import polars as pl

_db = sqlite3.connect(":memory:")
_db.execute("CREATE TABLE orders (order_id INTEGER, customer_id INTEGER, amount REAL, status TEXT, created_at TEXT)")
with open("orders_source.csv", encoding="utf-8", newline="") as f:
    _db.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?)",
                    [(int(r["order_id"]), int(r["customer_id"]), float(r["amount"]), r["status"], r["created_at"])
                     for r in csv.DictReader(f)])


def query(sql):
    """Run one SELECT against the source database, like one JDBC connection would. Returns a DataFrame."""
    cur = _db.execute(sql)
    names = [d[0] for d in cur.description]
    return pl.DataFrame([dict(zip(names, row)) for row in cur.fetchall()], schema=names, orient="row")


# The job's settings, as you would pass them to spark.read.jdbc / the JDBC data source options:
PARTITION_COLUMN = "order_id"
LOWER_BOUND = 1
UPPER_BOUND = 1000          # the DBA's estimate from last year; the table has grown since
NUM_PARTITIONS = 4

# Your work: read the table in NUM_PARTITIONS queries, each with its own WHERE on PARTITION_COLUMN,
# the way Spark splits a JDBC read. Every row must land exactly once, including rows outside the bounds.
# Record each partition's WHERE clause and row count in out/_partitions.json:
#   {"partitions": [{"where": "...", "rows": n}, ...]}
stride = (UPPER_BOUND - LOWER_BOUND) // NUM_PARTITIONS
partitions, frames = [], []
for i in range(NUM_PARTITIONS):   # BETWEEN includes both ends, so each boundary row is read twice
    lo, hi = LOWER_BOUND + i * stride, LOWER_BOUND + (i + 1) * stride
    if i == NUM_PARTITIONS - 1:
        hi = 10 ** 9
    where = f"{PARTITION_COLUMN} BETWEEN {lo} AND {hi}"
    part = query(f"SELECT * FROM orders WHERE {where}")
    partitions.append({"where": where, "rows": part.height})
    frames.append(part)

os.makedirs("out", exist_ok=True)
with open("out/_partitions.json", "w", encoding="utf-8") as f:
    json.dump({"partitions": partitions}, f, indent=2)
bronze = pl.concat(frames).sort("order_id")
bronze.write_parquet("out/bronze_orders.parquet")
print(f"{bronze.height} orders in {len(frames)} reads")
