import os
import deltalake
import polars as pl

TABLE = 'tables/silver_orders'

if not os.path.exists(TABLE):
    deltalake.write_deltalake(TABLE, pl.read_csv('orders_day1.csv', schema_overrides={'amount': pl.Float64}).to_arrow())

for _ in range(2):
    deltalake.write_deltalake(TABLE, pl.read_csv('orders_day2.csv', schema_overrides={'amount': pl.Float64}).to_arrow(), mode='overwrite')

snap = pl.DataFrame(deltalake.QueryBuilder().register('t', deltalake.DeltaTable(TABLE))
                    .execute('SELECT * FROM t ORDER BY order_id').read_all())
os.makedirs('out', exist_ok=True)
snap.write_parquet('out/silver_orders.parquet')
