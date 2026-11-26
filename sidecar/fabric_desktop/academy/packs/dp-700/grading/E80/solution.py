import os
import deltalake
import polars as pl

TABLE = 'tables/readings'
readings = pl.read_csv('readings.csv')

# The ingestion job. Every day, 12 batches of 25 readings, each appended to that day's partition.
for day in sorted(readings['reading_date'].unique()):
    for b in range(1, 13):
        batch = readings.filter((pl.col('reading_date') == day) & (pl.col('batch') == b))
        deltalake.write_deltalake(TABLE, batch.to_arrow(), mode='append', partition_by=['reading_date'])

# Compact every partition: one OPTIMIZE commit, the rows don't change and the history keeps every write.
deltalake.DeltaTable(TABLE).optimize.compact()
# Remove unused files older than the default 7 days. Today's replaced files stay, so time travel still works.
deltalake.DeltaTable(TABLE).vacuum(dry_run=False)

os.makedirs('out', exist_ok=True)


def snapshot(table, path):
    rows = deltalake.QueryBuilder().register('t', table).execute('SELECT * FROM t').read_all()
    pl.DataFrame(rows).write_parquet(path)


snapshot(deltalake.DeltaTable(TABLE), 'out/readings.parquet')
snapshot(deltalake.DeltaTable(TABLE, version=0), 'out/first_batch.parquet')
