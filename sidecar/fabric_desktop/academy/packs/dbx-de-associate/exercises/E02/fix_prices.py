import json
import os
import deltalake
import polars as pl

TABLE = 'tables/products'


def read(dt):
    """All rows of a DeltaTable (any version) as a polars DataFrame."""
    return pl.DataFrame(deltalake.QueryBuilder().register('t', dt).execute('SELECT * FROM t ORDER BY id').read_all())

# What happened to this table (it runs once, the first time):
#   version 0  the products were loaded
#   version 1  a bulk UPDATE meant for one product ran on every row: price * 100
#   version 2  the discontinued Teapot was deleted, on purpose
if not os.path.exists(TABLE):
    deltalake.write_deltalake(TABLE, pl.read_csv('products.csv').to_arrow())
    deltalake.DeltaTable(TABLE).update(updates={'price': 'price * 100'})
    deltalake.DeltaTable(TABLE).delete('discontinued = true')

# Your work:
# 1. Read the table's history (or its _delta_log folder) and find the bad version.
# 2. Read the version just before it, and put those prices back on the products the table has now.
#    The Teapot stays deleted.
# 3. Fill in the report below from the history, not by hand.

report = {'bad_version': None, 'operations': [], 'versions_before_fix': None}

os.makedirs('out', exist_ok=True)
with open('out/report.json', 'w') as f:
    json.dump(report, f)
snap = read(deltalake.DeltaTable(TABLE))
snap.write_parquet('out/products.parquet')
print(snap)
