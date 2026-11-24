# Direct Lake fallback risk

**Preview.** The Direct Lake fallback risk card (it was called the Direct Lake check) says
whether a semantic model will fall back to DirectQuery, or fail, before anyone queries it. It
lives on **Data mesh > Deploy > Semantic model**, in a card under the Direct Lake one, and shows
only while Settings shows preview features.

Pick a model in the environment's workspace (Test or Prod) and the capacity whose limits to
check against, then **Check now**. KratosForge reads the model's definition from Fabric
(getDefinition, read only) and each table's Delta log and Parquet footers from OneLake, then
checks on this PC. The check starts no Fabric job, runs no query and changes nothing; reading
from OneLake uses capacity.

Everything on the card is **predicted from metadata on this PC**: Fabric hasn't run a query. It
never says a model does fall back, only that it will, might, or is within the published limits.

## The model's verdict

The card starts with one verdict for the whole model, and names the table that decides it:

| Verdict | What it means |
| --- | --- |
| Will fall back | A table is past one of the capacity's limits, its source is a SQL view, or the SQL analytics endpoint has security on it. With **Direct Lake only**, or a model that reads OneLake directly, this reads **Will fail** |
| Might fall back | A table is within 10% of a limit, so a few more loads will pass it |
| Within limits | Every table is within the capacity's published limits |
| Not checked | A table couldn't be read, or nothing rules fallback out (see below) |

What happens past a limit depends on the model's **Fallback setting**, which the card reads from
the model:

- **Automatic**: one table past a limit sends every query on the model to DirectQuery, which is
  slower, even queries that don't read that table.
- **Direct Lake only**: nothing falls back; queries on the model fail.
- **DirectQuery only**: every query goes to DirectQuery, whatever the limits.
- A model that reads OneLake directly (Direct Lake on OneLake) doesn't fall back: refresh fails.

Below the verdict, tiles count the tables that will fall back, might, and are within limits,
and show the model's size on OneLake against the capacity's limit (F2 to F32 only). The table
lists each table's rows, Parquet files and row groups against the limits. Select a table to see
each check, its evidence, the fix and the forecast; tables past a limit start open.

### Fallback that doesn't depend on size

A model on the SQL analytics endpoint also falls back, on any capacity, when:

- **a table's source is a SQL view**, not a Delta table: every query that reads it goes to
  DirectQuery. The fix is to materialize the view as a Delta table and point the model's table
  at it.
- **the SQL analytics endpoint has row-level or object-level security** on a table: queries
  that read it go to DirectQuery.

This PC doesn't read the endpoint's security, so a model on the SQL analytics endpoint whose
tables are all within the limits reads **Not checked**, never **Within limits**. A model that
reads OneLake directly has neither cause.

### The forecast

For each table, the card reads its latest commits from the Delta log (up to 200 a table, and
2,000 for the whole model) and fits how fast its Parquet files, row groups and rows grow. It
then says, for example, "Crosses F32's Parquet file limit in about 11 days at the current rate
(16 commits over 15 days)".

- It always says how many commits it used, and uses only commits it actually read.
- A table with fewer than 3 commits, or less than 7 days of history, gets **Not enough
  history**, never a guess.
- Growth counts only since the last time the number went down: after OPTIMIZE merges files, the
  forecast starts again, and a table that hasn't grown since says **No growth seen**.
- Row groups aren't in the log, so they follow the Parquet files at today's row groups per file.

### Which capacity fits

Under the verdict, the card names the smallest capacity whose limits every table fits today,
and at the current rate in 90 days. Pick another capacity in **Limits for capacity** to judge
the same numbers against it; nothing is read again. A view or the endpoint's security doesn't
depend on the capacity, so the card names those tables instead. The card shows no cost or
capacity unit figure.

### What it can't see

**What this can't see from this PC** lists what the prediction leaves out: the endpoint's
security on a model on the SQL analytics endpoint, how busy the capacity's memory is when people
query (Fabric can page tables out under pressure), and changes Microsoft makes to the limits
after this app's table of them was last reviewed. Without a OneLake sign-in on this PC, no table
is read and each says **Not checked**, with the next step.

## Other checks

The card also lists what would make the model's queries or refresh fail, under **Other checks**.
The limits checks are shown table by table above; this is the full list.

| Severity | Check | What it means |
| --- | --- | --- |
| Would fail | Table missing | The Delta table the model reads isn't in the lakehouse. If it's a SQL view, Direct Lake on SQL always falls back to DirectQuery for it |
| Would fail | Column missing | The model reads a column the Delta table no longer has. Visuals that use it show an error |
| Would fail | Type Direct Lake can't read | The model reads a binary, array, map or struct column |
| Would fail | Relationship types differ | The two columns of a relationship have different types |
| Would fail | Key not unique | The one side of a relationship has values that appear more than once (only when you tick **Also check relationship keys**, which reads those columns from OneLake) |
| Would fail | Calculated column | Direct Lake on SQL doesn't support calculated columns |
| Falls back | Parquet files, Row groups, Rows | The table is past, or within 10% of, the capacity's Direct Lake limit |
| Falls back | Model size | On F2 to F32, the model's tables take more space on OneLake than the capacity allows |
| Falls back | Type changed | The model says one type and the Delta table now stores another |
| Info | New column | The Delta table has a column the model doesn't have yet |
| Info | Rows with no match | Rows of a fact whose key isn't in the dimension, or is empty. Power BI groups them under (Blank) when you slice by the dimension, instead of under the row they belong to (only when you tick **Also check relationship keys**) |
| Info | Type left out | The Delta table has a column Direct Lake can't read, so the model leaves it out |

Past a limit, what happens depends on the model. A model on the SQL analytics endpoint with
the default fallback setting (**Automatic**) sends those queries to DirectQuery, which is
slower. With fallback off (**Direct Lake only**) the queries fail, and a model that reads
OneLake directly fails to refresh. The card shows which one applies.

Row groups come from each file's Parquet footer, up to 200 files a table. A table with more
files is estimated from an even sample of 200 and marked "about".

## The limits

The limits are Microsoft's [Direct Lake guardrails](https://learn.microsoft.com/fabric/fundamentals/direct-lake-overview#fabric-capacity-guardrails-and-limitations)
for the capacity you pick. The card starts on the workspace's own capacity when the app can
read it (Monitor's capacity status). P and trial SKUs use the limits of the F SKU of the same
size.

| Capacity | Parquet files a table | Row groups a table | Rows a table | Model size on OneLake |
| --- | --- | --- | --- | --- |
| F2, F4, F8 | 1,000 | 1,000 | 300 million | 10 GB |
| F16 | 1,000 | 1,000 | 300 million | 20 GB |
| F32 | 1,000 | 1,000 | 300 million | 40 GB |
| F64 (P1, FT1) | 5,000 | 5,000 | 1.5 billion | No limit |
| F128 (P2) | 5,000 | 5,000 | 3 billion | No limit |
| F256 (P3) | 5,000 | 5,000 | 6 billion | No limit |
| F512 (P4) | 10,000 | 10,000 | 12 billion | No limit |
| F1024 (P5) and up | 10,000 | 10,000 | 24 billion | No limit |

## Fixing what it finds

- **Update model** (the layer's own model only) rebuilds it from the metadata, which adds new
  columns and drops ones the Delta table no longer has.
- **Compact in Table health** opens Table health, where Optimize (Test and Prod) or Compact
  (Dev) merges small files, which cuts Parquet files and row groups.
- **Open the table** opens the dimension whose key isn't unique, or the fact with rows that have
  no match, so you can fix its load. A `[[lookup]]` sends keys it can't find to the Unknown
  member (-1), so rows with no match usually mean the fact's key isn't looked up.

For any other model, make the change in Fabric or in Power BI Desktop.

## Where the idea comes from

Checking guardrails and schema drift from a notebook comes from Microsoft's
[semantic-link-labs](https://github.com/microsoft/semantic-link-labs) (MIT), which Michael
Kovalsky of [Elegant BI](https://www.elegantbi.com/) started. That library runs only inside
Fabric, so KratosForge doesn't use it here: the check is written for this PC and needs no
notebook. Finding rows with no match comes from Elegant BI's
[blank row finder](https://github.com/m-kovalsky/Tabular) for Tabular Editor (MIT).
