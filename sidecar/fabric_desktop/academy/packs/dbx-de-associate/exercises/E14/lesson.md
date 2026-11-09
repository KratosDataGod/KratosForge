# A paged REST API to bronze from a notebook

**Why pages.** An API that holds thousands of records returns a few at a time. Each response carries
the records and a way to ask for the next part: a `next_page` link, a cursor or an offset. A loader
that reads one response and stops has silently loaded only the first page.

**The loop.** Request the first page, keep its records, and follow the link until it is empty:

```python
rows, page = [], FIRST_PAGE
while page is not None:
    response = get(page)
    rows.extend(response["data"])
    page = response["next_page"]
```

Gather everything, then write once. Writing inside the loop with `append` makes a failure halfway
leave half a load behind.

**Ready to schedule.** A notebook becomes a Lakeflow job task that reruns on a schedule or after a
failure, so a rerun must leave the same table. A full extract replaces the table (`overwrite`). An
incremental extract keeps a watermark, such as the latest `updated_at` it loaded, and asks the API only
for records changed after it, then merges them into the table.

**On this PC.** A learner script can't reach the network here, so `get()` reads recorded responses
from files beside the script. A live API adds what this doesn't check: timeouts, rate limits, retries
with backoff, and a secret for the token, read with `dbutils.secrets.get` in Databricks rather than
written in the notebook.
