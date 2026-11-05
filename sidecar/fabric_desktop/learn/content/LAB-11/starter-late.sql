SELECT batch_id FROM load_log WHERE arrived_at > sla_at ORDER BY batch_id;
