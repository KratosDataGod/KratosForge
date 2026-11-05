SELECT event_id, customer_id, item.sku AS sku, item.qty AS qty
FROM (SELECT event_id, payload.customer.id AS customer_id, unnest(payload.items) AS item FROM events)
ORDER BY event_id, sku;
