CREATE TABLE cu_usage (day DATE, item VARCHAR, operation VARCHAR, cu_seconds INTEGER);
INSERT INTO cu_usage VALUES
    (DATE '2026-10-20', 'nb_sales', 'Notebook run', 9000),
    (DATE '2026-10-27', 'nb_sales', 'Notebook run', 4000),
    (DATE '2026-10-30', 'nb_sales', 'Notebook run', 1000),
    (DATE '2026-10-28', 'pipeline_load', 'Pipeline run', 2500),
    (DATE '2026-10-29', 'model_refresh', 'Dataset refresh', 1500),
    (DATE '2026-11-01', 'report_queries', 'Query', 1000);
