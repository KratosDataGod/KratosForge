# Windows over a stream of events

Streaming questions are about time: how many events in each block, in the last ten minutes, in each
burst of activity. Three kinds of window answer them.

| Window | Size | Overlap | Each event counts | Good for |
| --- | --- | --- | --- | --- |
| Tumbling | Fixed (5 min) | None | Once | Shift reports, per-period totals |
| Hopping | Fixed (10 min), starts every hop (5 min) | Yes | Size ÷ hop times | Rolling counts for alerts |
| Session | Grows while events keep coming within a timeout | None | Once | Bursts of activity, user sessions |

On this PC you'll write them as a batch query over stored events with DuckDB:

- tumbling: `time_bucket(INTERVAL '5 minutes', event_ts)`
- hopping: put each event in every window that covers it
- session: compare each event with the one before (`lag`) and start a new session after a gap

The same ideas appear in Fabric as Eventstream's windowed aggregations, `bin()` in KQL, and
`window()` and `session_window()` in Spark structured streaming. A streaming engine adds what a batch
query can't show: windows that close as time passes, and watermarks for events that arrive late.
That part needs a running stream, which this exercise doesn't have.
