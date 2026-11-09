WITH latest AS (
    SELECT model_name, status, error_message,
           row_number() OVER (PARTITION BY model_name ORDER BY start_time DESC) AS rn
    FROM refresh_history
),
over_guardrail AS (
    SELECT t.model_name, min(t.table_name) AS table_name
    FROM model_tables AS t
    WHERE t.parquet_files > 5000 OR t.row_groups > 5000 OR t.rows_millions > 1500
    GROUP BY t.model_name
),
fell_back AS (
    SELECT qy.model_name, qy.table_name
    FROM query_log AS qy
    JOIN models AS m ON m.model_name = qy.model_name
    WHERE qy.mode = 'DirectQuery' AND m.direct_lake_behavior = 'Automatic'
),
counts AS (
    SELECT model_name, count(*) AS n, min(table_name) AS first_table FROM fell_back GROUP BY model_name
),
views AS (
    SELECT f.model_name, min(f.table_name) AS view_name
    FROM fell_back AS f
    JOIN model_tables AS t ON t.model_name = f.model_name AND t.table_name = f.table_name AND t.source_kind = 'view'
    GROUP BY f.model_name
)
SELECT m.model_name, l.status AS last_status, l.error_message AS last_error,
       coalesce(c.n, 0) AS fallback_queries,
       coalesce(v.view_name, c.first_table) AS cause_table,
       CASE WHEN g.table_name IS NOT NULL THEN 'guardrail'
            WHEN v.view_name IS NOT NULL THEN 'sql view' END AS cause
FROM models AS m
JOIN latest AS l ON l.model_name = m.model_name AND l.rn = 1
LEFT JOIN over_guardrail AS g ON g.model_name = m.model_name
LEFT JOIN counts AS c ON c.model_name = m.model_name
LEFT JOIN views AS v ON v.model_name = m.model_name
ORDER BY m.model_name;
