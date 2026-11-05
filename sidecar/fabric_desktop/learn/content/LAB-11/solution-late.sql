SELECT batch_id, date_diff('minute', sla_at, arrived_at) AS minutes_late
FROM load_log
WHERE arrived_at > sla_at
ORDER BY batch_id;
