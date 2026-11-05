# E21: combine web and store orders into silver.orders with each customer's name. `spark` is ready;
# run with Run, check with Check my work.

from pyspark.sql import functions as F

web = spark.read.table("bronze.web_orders")       # order_id, customer_id, region, amount
store = spark.read.table("bronze.store_orders")   # region, amount, order_id, customer_id
customers = spark.read.table("bronze.customers")  # customer_id, region, name

# 1. Tag each order with its channel: "web" or "store".
# 2. Stack the two order tables. Their columns come in a different order.
# 3. Add the customer's name. A customer is identified by customer_id AND region together.
#    Keep every order, even one whose customer isn't in bronze.customers: its name is "unknown".
# 4. Write silver.orders with order_id, customer_id, region, channel, amount, customer_name,
#    so that running the code again leaves the same table.
