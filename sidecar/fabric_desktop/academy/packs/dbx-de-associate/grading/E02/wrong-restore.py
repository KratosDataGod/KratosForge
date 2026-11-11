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

history = sorted(deltalake.DeltaTable(TABLE).history(), key=lambda h: h['version'])
report = {'bad_version': 1, 'operations': [h['operation'] for h in history], 'versions_before_fix': len(history)}
deltalake.DeltaTable(TABLE).restore(0)   # brings the Teapot back too

os.makedirs("out", exist_ok=True)
with open("out/report.json", "w") as f:
    json.dump(report, f)
snap = read(deltalake.DeltaTable(TABLE))
snap.write_parquet("out/products.parquet")
print(snap)
