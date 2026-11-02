SELECT d.consumer, d.source, 'finance' AS source_domain
FROM dependencies AS d
JOIN products AS c ON c.product = d.consumer
WHERE c.domain = 'finance'
ORDER BY d.consumer, d.source;
