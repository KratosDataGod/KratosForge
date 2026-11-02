# Port Snowflake SQL to a Fabric Warehouse

Snowflake SQL and T-SQL share most of their grammar. The differences are a handful of
conveniences, and the one that bites is silent: arithmetic on integers.

| Snowflake | Fabric Warehouse (T-SQL) |
| --- | --- |
| `DIV0(a, b)` | `COALESCE(a / NULLIF(b, 0), 0)` |
| `ZEROIFNULL(x)`, `NVL(x, 0)` | `COALESCE(x, 0)` |
| `IFF(cond, a, b)` | `CASE WHEN cond THEN a ELSE b END` (or `IIF`) |
| `QUALIFY row_number() ... = 1` | the window in a subquery, then `WHERE n = 1` (LM-10) |
| `5 / 2` is 2.5 | `5 / 2` is 2: cast one side to `DECIMAL` first |
| `x::DATE` | `CAST(x AS DATE)` |

## The lab

The starter is Snowflake SQL. It fails here because DuckDB, like T-SQL, has no `DIV0`, `ZEROIFNULL`
or `IFF`. Port it to standard SQL: what passes here runs in a Fabric Warehouse too.
