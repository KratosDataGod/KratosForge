# E70: add each order's place in its customer's history and the customer's running total.
# `spark` is ready; run with Run, check with Check my work.

from pyspark.sql import functions as F
from pyspark.sql.window import Window

orders = spark.read.table("silver.orders")   # order_id, customer_id, order_date, amount: one row per order

# 1. Keep every order: one row per order_id.
# 2. order_seq: 1, 2, 3 ... for each customer, by order_date. Same-day orders go by order_id.
# 3. running_amount: the customer's total amount up to and including this order, in that same sequence.
# 4. Write gold.customer_running_totals with customer_id, order_id, order_date, amount, order_seq,
#    running_amount, so that running the code again leaves the same table.
