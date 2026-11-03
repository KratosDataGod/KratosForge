# Incremental loads with a watermark

A **full load** reads the whole source and replaces the target. It is simple and always right, and
it gets slower and more expensive as the source grows. An **incremental load** reads only what
changed since the last run. It needs two things:

1. **A watermark**: the last change the previous run took, kept in a small table. Each run reads
   rows changed after it (`modified_at > high_water`) and, when the load succeeds, moves it forward
   to the last change it loaded.
2. **An upsert**: a changed row has to update the row that's already there, not sit beside it.
   `MERGE` updates the rows that match on the key and inserts the rest.

Move the watermark only after the rows are written, and only as far as the rows you actually
loaded. Setting it to the current time skips any change that arrived late with an older timestamp
(that trap is exercise E57).

## Try it

Load the orders that changed since yesterday into `silver_orders`, then move the watermark.
