-- E69 practice data: order events as the shop's API sends them, one JSON document per order with its
-- line items in an array. Order 102's items list is empty and order 103 has none. DuckDB SQL; fixed values.
CREATE SCHEMA bronze;
CREATE TABLE bronze.order_events AS SELECT * FROM (VALUES
  (1, '{"order_id": 101, "customer_id": "C01", "placed_at": "2026-03-01T09:15:00", "items": [{"sku": "LOCK-1", "qty": 2, "price": 12.50}, {"sku": "BELL-2", "qty": 1, "price": 4.99}]}'),
  (2, '{"order_id": 102, "customer_id": "C02", "placed_at": "2026-03-01T10:40:00", "items": []}'),
  (3, '{"customer_id": "C03", "order_id": 103, "placed_at": "2026-03-01T11:05:00", "channel": "app"}'),
  (4, '{"order_id": 104, "customer_id": "C01", "placed_at": "2026-03-02T08:00:00", "items": [{"qty": 1, "sku": "HELM-3", "price": 45}, {"sku": "LIGHT-4", "qty": 3, "price": 9.90, "promo": "SPRING"}, {"sku": "PUMP-5", "qty": 1, "price": 18.00}]}'),
  (5, '{"order_id": 105, "customer_id": "C04", "placed_at": "2026-03-02T14:30:00", "items": [{"sku": "LOCK-1", "qty": 1, "price": 12.50}]}')
) AS t(event_id, payload);
