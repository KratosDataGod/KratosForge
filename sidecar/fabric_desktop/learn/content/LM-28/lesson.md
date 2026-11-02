# Semi-structured data: nested records and arrays

Events, APIs and logs send nested data: a record inside a record, and arrays of items. You can
query it without flattening it first.

## Reaching in, spreading out

| | Snowflake | Fabric Spark, Databricks | DuckDB (this lab) |
| --- | --- | --- | --- |
| Nested field | `payload:customer.id` | `payload.customer.id` | `payload.customer.id` |
| One row per array element | `LATERAL FLATTEN(payload:items)` | `explode(payload.items)` | `unnest(payload.items)` |
| Text JSON | `PARSE_JSON` into a VARIANT | `from_json` with a schema | `json` functions |

Fabric Warehouse reads JSON text with `OPENJSON` and `JSON_VALUE`.

## Type it once, at Silver

Keep the raw payload in Bronze, and pull the fields reports need into typed columns at Silver, so
every query doesn't parse the same JSON again and a changed field breaks one load, not every
report.

## Try it

Spread each event's items into rows.
