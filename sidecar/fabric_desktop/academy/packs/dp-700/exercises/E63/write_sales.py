import os
import deltalake
import polars as pl

TABLE = 'tables/sales'
sales = pl.read_csv('sales.csv', try_parse_dates=True, schema_overrides={'amount': pl.Float64})

# Reports read one month at a time. Write tables/sales partitioned so a month's query reads only
# that month's files: add a sale_month column ('2026-01') and partition by it. Then write the snapshot.

deltalake.write_deltalake(TABLE, sales.to_arrow())

os.makedirs('out', exist_ok=True)
pl.DataFrame(deltalake.QueryBuilder().register('t', deltalake.DeltaTable(TABLE))
             .execute('SELECT * FROM t').read_all()).write_parquet('out/sales.parquet')
