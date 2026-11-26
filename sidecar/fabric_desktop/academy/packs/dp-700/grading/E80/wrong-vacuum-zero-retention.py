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

# Compact, then clear out every replaced file at once to save storage. Time travel to version 0 now fails.
deltalake.DeltaTable(TABLE).optimize.compact()
deltalake.DeltaTable(TABLE).vacuum(retention_hours=0, enforce_retention_duration=False, dry_run=False)

os.makedirs('out', exist_ok=True)


def snapshot(table, path):
    rows = deltalake.QueryBuilder().register('t', table).execute('SELECT * FROM t').read_all()
    pl.DataFrame(rows).write_parquet(path)


snapshot(deltalake.DeltaTable(TABLE), 'out/readings.parquet')
snapshot(deltalake.DeltaTable(TABLE, version=0), 'out/first_batch.parquet')
