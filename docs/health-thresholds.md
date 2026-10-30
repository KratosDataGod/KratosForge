# Health thresholds and Microsoft's guidance

KratosForge flags tables, warehouses and capacity use when they pass a few fixed thresholds. This page lists each threshold, the guidance Microsoft gives on the same thing in [microsoft/skills-for-fabric](https://github.com/microsoft/skills-for-fabric/tree/bc42aaf) (commit `bc42aaf`, read October 2026), and which value KratosForge uses and why.

The rule for this comparison: KratosForge keeps its own value unless Microsoft's guidance gives evidence for a different one, and every difference is listed either way. None of the thresholds in Microsoft's skills cites a source or a measurement, so every value below is KratosForge's own. Where the two agree, that is noted too.

The wording here is KratosForge's own. "Theirs" summarizes guidance in microsoft/skills-for-fabric (MIT licence) and links the file it comes from; it isn't Microsoft's endorsement of KratosForge.

## Lakehouse tables (Estate X-ray)

| What | KratosForge | Microsoft's skills | Kept | Why |
| --- | --- | --- | --- | --- |
| Small files | 20 or more data files averaging under 32 MB | Average file under 32 MB means "consider OPTIMIZE"; no file count ([performance-patterns.md](https://github.com/microsoft/skills-for-fabric/blob/bc42aaf/skills/spark-cli/references/operations/performance-patterns.md)) | Ours | Same 32 MB line. The 20-file minimum stops a small table with two files being flagged, where compacting gains nothing |
| "Fix first" on small files | 200 or more small files | No severity levels for small files | Ours | No guidance to compare with |
| Target size after compaction | Fabric's own `OPTIMIZE` target (KratosForge sets none) | "Aim for 128 MB to 1 GB per file", called a rule of thumb ([performance-patterns.md](https://github.com/microsoft/skills-for-fabric/blob/bc42aaf/skills/spark-cli/references/operations/performance-patterns.md)) | Ours | Fabric's table maintenance chooses the size and applies V-Order; overriding it would need evidence it does better |
| Needs vacuum | 30 or more versions since the last `VACUUM`, or never vacuumed | No cadence given | Ours | No guidance to compare with |
| Vacuum retention | The longest of 7 days, the table's own setting and the layer's **Keep history** ([Vacuum](xray-fixes.md#needs-vacuum)) | No retention given | Ours | 7 days is Fabric's default; less can break running queries and restore |
| Not changing | Last write 30 or more days ago | No staleness rule | Ours | No guidance to compare with |
| Statistics | Not checked | No numeric rule; the SQL endpoint's `sp_get_table_health_metrics` reports "invalid file statistics" as a code ([lakehouse-health.md](https://github.com/microsoft/skills-for-fabric/blob/bc42aaf/skills/sqldw-cli/references/operations/lakehouse-health.md)) | — | A possible later check: reading that procedure's codes needs the SQL endpoint token, which Windows sign-in doesn't give today (#1079) |

## Warehouse monitoring

KratosForge reads Fabric's workspace monitoring (`WarehouseExecutions`, kept 30 days). Microsoft's skills read Query Insights (`queryinsights.*`, kept 30 days, up to 15 minutes behind). The two record the same queries from different places.

| What | KratosForge | Microsoft's skills | Kept | Why |
| --- | --- | --- | --- | --- |
| Failure alert | Last 15 minutes: at least 10 queries and 20% or more failed | No failure-rate level; failures are grouped by error code ([failure-analysis.md](https://github.com/microsoft/skills-for-fabric/blob/bc42aaf/skills/sqldw-cli/references/operations/failure-analysis.md)) | Ours | The 10-query minimum keeps one failure in a quiet warehouse from raising an alert |
| Slow queries | The 50 slowest in the window, with no cut-off | Long-running means 30 seconds or more; more than 5 minutes is flagged as a resource consumer ([query-reference.md](https://github.com/microsoft/skills-for-fabric/blob/bc42aaf/skills/sqldw-cli/references/operations/query-reference.md)) | Ours | A ranked list works for every warehouse; a fixed cut-off shows nothing on a fast one and everything on a slow one |
| Getting slower | P50 and P95 per day (**Duration percentiles**) | A query is a regression when the last hour is more than 20% slower than its 7-day baseline ([resource-consumers.md](https://github.com/microsoft/skills-for-fabric/blob/bc42aaf/skills/sqldw-cli/references/operations/resource-consumers.md)) | Ours | Different measure, same purpose. A per-query regression view is a candidate for later |
| Failures by cause | Failed queries by result code, with the failure rate | The same, with error codes grouped into causes: permissions, syntax and missing objects, constraints, data conversion, transaction conflicts | Ours | Agrees. Grouping codes into causes is a candidate for later |

## Capacity

| What | KratosForge | Microsoft's skills | Kept | Why |
| --- | --- | --- | --- | --- |
| How busy a capacity is | Not read: there is no public per-capacity usage API for people who aren't capacity admins, so it shows as unknown ([Permissions](permissions.md#what-each-feature-uses)) | No CU percentage or smoothing window; "high CU alone is not pressure", look at delay, rejection and throttling ([capacity-metrics-correlation.md](https://github.com/microsoft/skills-for-fabric/blob/bc42aaf/skills/sqldw-cli/references/operations/capacity-metrics-correlation.md)) | Ours | Agrees: neither flags a CU level |
| Matching a spike to queries | Not done | Take the spike's time range, then rank the queries in it by CPU time per query hash; best effort only | — | A candidate for Optimize once a usage source is readable |
