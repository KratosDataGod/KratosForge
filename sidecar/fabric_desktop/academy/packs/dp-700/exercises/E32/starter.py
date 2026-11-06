# E32: clean, cast and join raw orders into silver.orders. `spark` is ready;
# run with Run, check with Check my work.

from pyspark.sql import functions as F

raw = spark.read.table("bronze.raw_orders")    # every column is text: order_id, order_date, product_id, qty, unit_price, status
products = spark.read.table("bronze.products")  # product_id, category

# 1. Keep completed orders only. status comes in mixed case, sometimes with spaces around it.
# 2. Cast order_id and qty to int, order_date to date and unit_price to decimal(10,2).
#    Drop a row that has no order_date or no qty.
# 3. Keep one row per order_id: some orders arrived twice.
# 4. Add each product's category. Keep an order whose product isn't in bronze.products: its category is "unknown".
# 5. amount is qty times unit_price.
# 6. Write silver.orders with order_id, order_date, product_id, category, qty, amount,
#    so that running the code again leaves the same table.
