import os
import deltalake
import polars as pl

TABLE = 'tables/sales'
sales = pl.read_csv('sales.csv', try_parse_dates=True, schema_overrides={'amount': pl.Float64})
sales = sales.with_columns(pl.col('sale_date').dt.strftime('%Y-%m').alias('sale_month'))
# One partition per sale
deltalake.write_deltalake(TABLE, sales.to_arrow(), partition_by=['sale_id'])

os.makedirs('out', exist_ok=True)
pl.DataFrame(deltalake.QueryBuilder().register('t', deltalake.DeltaTable(TABLE))
             .execute('SELECT * FROM t').read_all()).write_parquet('out/sales.parquet')
