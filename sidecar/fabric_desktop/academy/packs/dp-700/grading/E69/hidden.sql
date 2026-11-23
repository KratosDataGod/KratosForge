-- E69 grading data the learner never sees: other orders, keys in another order, an items value of
-- null, an empty list, a price written as a whole number and extra fields the schema leaves out. DuckDB SQL.
CREATE SCHEMA bronze;
CREATE TABLE bronze.order_events AS SELECT * FROM (VALUES
  (11, '{"placed_at": "2026-04-10T07:45:00", "items": [{"price": 3.20, "sku": "TUBE-7", "qty": 4}], "order_id": 201, "customer_id": "C21"}'),
  (12, '{"order_id": 202, "customer_id": "C22", "placed_at": "2026-04-10T12:00:00", "items": null}'),
  (13, '{"order_id": 203, "customer_id": "C21", "placed_at": "2026-04-11T16:20:00", "items": [{"sku": "SEAT-8", "qty": 1, "price": 30}, {"sku": "TUBE-7", "qty": 2, "price": 3.20, "gift": true}]}'),
  (14, '{"order_id": 204, "customer_id": "C23", "placed_at": "2026-04-12T09:10:00", "items": [], "coupon": "WELCOME"}'),
  (15, '{"order_id": 205, "customer_id": "C24", "placed_at": "2026-04-12T18:55:00", "items": [{"sku": "CHAIN-9", "qty": 1, "price": 22.75}, {"sku": "GRIP-6", "qty": 2, "price": 6.40}, {"sku": "BELL-2", "qty": 1, "price": 4.99}]}')
) AS t(event_id, payload);
