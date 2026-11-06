import os
import deltalake
import polars as pl

TABLE = 'tables/silver_orders'

# Day 1 makes the table. Day 2 must update the orders that changed and add the new ones,
# and running day 2 again (a retry) must change nothing. Finish by writing the snapshot below.

if not os.path.exists(TABLE):
    deltalake.write_deltalake(TABLE, pl.read_csv('orders_day1.csv', schema_overrides={'amount': pl.Float64}).to_arrow())

# day 2 here, twice

snap = pl.DataFrame(deltalake.QueryBuilder().register('t', deltalake.DeltaTable(TABLE))
                    .execute('SELECT * FROM t ORDER BY order_id').read_all())
os.makedirs('out', exist_ok=True)
snap.write_parquet('out/silver_orders.parquet')
