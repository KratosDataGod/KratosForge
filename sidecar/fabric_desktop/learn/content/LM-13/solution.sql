SELECT ticket_id, priority
FROM tickets
WHERE closed_at IS NULL AND priority IS DISTINCT FROM 'low'
ORDER BY ticket_id;
