# E69: flatten JSON order events into silver.order_lines. `spark` is ready;
# run with Run, check with Check my work.

from pyspark.sql import functions as F

events = spark.read.table("bronze.order_events")   # event_id, payload (the order as JSON text)
events.show(truncate=False)

# A payload looks like:
#   {"order_id": 101, "customer_id": "C01", "placed_at": "2026-03-01T09:15:00",
#    "items": [{"sku": "LOCK-1", "qty": 2, "price": 12.50}, ...]}
#
# 1. Parse payload with from_json and a schema. On this PC call it through F.expr with the schema
#    as text: F.expr("from_json(payload, 'order_id INT, ...')"). F.from_json isn't available here.
# 2. Give each line item its own row. An order whose items list is empty or missing keeps one row,
#    with sku, qty and price NULL.
# 3. Columns: order_id (int), customer_id, placed_at (timestamp), sku, qty (int), price (decimal(10,2)).
#    Leave the raw JSON out.
# 4. Write silver.order_lines so that running the code again leaves the same table.
