SELECT ticket_id, priority
FROM tickets
WHERE closed_at = NULL AND priority <> 'low'
ORDER BY ticket_id;
