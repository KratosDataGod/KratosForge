import json
import os

import deltalake
import pyarrow as pa
import pyarrow.parquet as pq

TABLE = "tables/bronze_customers"
FIRST_PAGE = "customers_page_1.json"


def get(page):
    with open(page, encoding="utf-8") as f:
        return json.load(f)


def land():
    rows, pages, page = [], 0, FIRST_PAGE
    response = get(page)
    rows.extend(response["data"])
    pages += 1
    deltalake.write_deltalake(TABLE, pa.Table.from_pylist(rows), mode="overwrite")
    os.makedirs("out", exist_ok=True)
    with open("out/_watermark.json", "w", encoding="utf-8") as f:
        json.dump({"max_updated_at": max(r["updated_at"] for r in rows), "pages": pages}, f)


land()
land()


# The checks read this snapshot of the table after both runs.
os.makedirs("out", exist_ok=True)
snap = deltalake.QueryBuilder().register("t", deltalake.DeltaTable(TABLE)).execute("SELECT * FROM t ORDER BY id").read_all()
pq.write_table(pa.table(snap), "out/bronze_customers.parquet")
