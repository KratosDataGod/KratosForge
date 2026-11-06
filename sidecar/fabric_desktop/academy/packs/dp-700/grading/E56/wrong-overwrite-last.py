import os
import deltalake
import polars as pl

TABLE = 'tables/events'
events = pl.read_csv('events.csv', schema_overrides={'amount': pl.Float64})

# The ingestion job: one append per arriving batch (40 batches of 25 events).
for b in range(1, 41):
    deltalake.write_deltalake(TABLE, events.filter(pl.col('batch') == b).to_arrow(), mode='append')

# 'Fix': overwrite with the last batch
deltalake.write_deltalake(TABLE, events.filter(pl.col('batch') == 40).to_arrow(), mode='overwrite')
deltalake.DeltaTable(TABLE).optimize.compact()

os.makedirs('out', exist_ok=True)
pl.DataFrame(deltalake.QueryBuilder().register('t', deltalake.DeltaTable(TABLE))
             .execute('SELECT * FROM t').read_all()).write_parquet('out/events.parquet')
