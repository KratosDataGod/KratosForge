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

# Your maintenance goes here:
#   1. Compact the table so each day's partition is one data file, keeping its partitioning and its history.
#   2. Clean up files the table no longer uses, keeping 7 days of history for time travel.
#   3. Then write the two snapshots below. The second reads the table as it was at version 0.

os.makedirs('out', exist_ok=True)


def snapshot(table, path):
    rows = deltalake.QueryBuilder().register('t', table).execute('SELECT * FROM t').read_all()
    pl.DataFrame(rows).write_parquet(path)


snapshot(deltalake.DeltaTable(TABLE), 'out/readings.parquet')
snapshot(deltalake.DeltaTable(TABLE, version=0), 'out/first_batch.parquet')
