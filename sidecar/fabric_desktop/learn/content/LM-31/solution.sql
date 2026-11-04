SELECT d.consumer, d.source, s.domain AS source_domain
FROM dependencies AS d
JOIN products AS c ON c.product = d.consumer
JOIN products AS s ON s.product = d.source
WHERE c.domain = 'finance' AND s.domain <> 'finance'
ORDER BY d.consumer, d.source;
