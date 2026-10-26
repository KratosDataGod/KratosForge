# Direct Lake check

**Preview.** The Direct Lake check finds what would make a semantic model fall back to
DirectQuery, or fail, before anyone queries it. It lives on **Data mesh > Deploy > Semantic
model**, in a card under the Direct Lake one, and shows only while Settings shows preview
features.

Pick a model in the environment's workspace (Test or Prod) and the capacity whose limits to
check against, then **Check now**. KratosForge reads the model's definition from Fabric
(getDefinition, read only) and each table's Delta log and Parquet footers from OneLake, then
checks on this PC. The check starts no Fabric job and changes nothing; reading from OneLake
uses capacity.

## What it checks

| Severity | Check | What it means |
| --- | --- | --- |
| Would fail | Table missing | The Delta table the model reads isn't in the lakehouse. If it's a SQL view, Direct Lake on SQL always falls back to DirectQuery for it |
| Would fail | Column missing | The model reads a column the Delta table no longer has. Visuals that use it show an error |
| Would fail | Type Direct Lake can't read | The model reads a binary, array, map or struct column |
| Would fail | Relationship types differ | The two columns of a relationship have different types |
| Would fail | Key not unique | The one side of a relationship has values that appear more than once (only when you tick **Also check relationship keys are unique**, which reads those columns from OneLake) |
| Would fail | Calculated column | Direct Lake on SQL doesn't support calculated columns |
| Falls back | Parquet files, Row groups, Rows | The table is past, or within 10% of, the capacity's Direct Lake limit |
| Falls back | Model size | On F2 to F32, the model's tables take more space on OneLake than the capacity allows |
| Falls back | Type changed | The model says one type and the Delta table now stores another |
| Info | New column | The Delta table has a column the model doesn't have yet |
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
- **Open the table** opens the dimension whose key isn't unique, so you can fix its load.

For any other model, make the change in Fabric or in Power BI Desktop.

## Where the idea comes from

Checking guardrails and schema drift from a notebook comes from Microsoft's
[semantic-link-labs](https://github.com/microsoft/semantic-link-labs) (MIT), which Michael
Kovalsky of [Elegant BI](https://www.elegantbi.com/) started. That library runs only inside
Fabric, so KratosForge doesn't use it here: the check is written for this PC and needs no
notebook.
