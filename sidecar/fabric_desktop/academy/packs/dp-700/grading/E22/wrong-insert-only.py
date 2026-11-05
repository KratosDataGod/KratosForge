import os
import deltalake
import polars as pl

TABLE = 'tables/silver_orders'

if not os.path.exists(TABLE):
    deltalake.write_deltalake(TABLE, pl.read_csv('orders_day1.csv', schema_overrides={'amount': pl.Float64}).to_arrow())


def load(path):
    src = pl.read_csv(path, schema_overrides={'amount': pl.Float64}).to_arrow()
    (deltalake.DeltaTable(TABLE)
        .merge(src, predicate='t.order_id = s.order_id', source_alias='s', target_alias='t')
        .when_not_matched_insert_all()
        .execute())


load('orders_day2.csv')
load('orders_day2.csv')

snap = pl.DataFrame(deltalake.QueryBuilder().register('t', deltalake.DeltaTable(TABLE))
                    .execute('SELECT * FROM t ORDER BY order_id').read_all())
os.makedirs('out', exist_ok=True)
snap.write_parquet('out/silver_orders.parquet')
