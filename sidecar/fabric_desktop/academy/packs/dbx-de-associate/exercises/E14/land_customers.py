# E14: land the CRM's paged customers API into a bronze Delta table. Run it with Run, check with
# Check my work. On this PC the API is recorded: get() reads the recorded response for a page
# instead of calling the network, which a learner script can't reach here.
import json
import os

import deltalake
import pyarrow as pa
import pyarrow.parquet as pq

TABLE = "tables/bronze_customers"
FIRST_PAGE = "customers_page_1.json"


def get(page):
    """The API's response for one page: {"data": [...], "page": n, "next_page": the next page or None}."""
    with open(page, encoding="utf-8") as f:
        return json.load(f)


def land():
    # 1. Read every page: follow next_page until it is None.
    # 2. Write all the customers (id, name, country, updated_at) to TABLE so that a rerun of this job
    #    leaves one row per customer.
    # 3. Write out/_watermark.json with {"max_updated_at": <the latest updated_at>, "pages": <pages read>}
    #    so the next scheduled run knows where this one stopped.
    response = get(FIRST_PAGE)
    rows = response["data"]
    deltalake.write_deltalake(TABLE, pa.Table.from_pylist(rows), mode="append")


land()
land()   # the job's scheduled rerun: the table must still hold each customer once


# The checks read this snapshot of the table after both runs.
os.makedirs("out", exist_ok=True)
snap = deltalake.QueryBuilder().register("t", deltalake.DeltaTable(TABLE)).execute("SELECT * FROM t ORDER BY id").read_all()
pq.write_table(pa.table(snap), "out/bronze_customers.parquet")
