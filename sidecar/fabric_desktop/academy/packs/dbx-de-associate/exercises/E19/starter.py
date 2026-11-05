# E19: clean bronze.orders into silver.orders. `spark` is ready; run with Run, check with Check my work.

from pyspark.sql import functions as F

bronze = spark.read.table("bronze.orders")

silver = (
    bronze
    # 1. Drop rows without an order_id.
    # 2. Trim and cast order_id and customer_id to bigint.
    # 3. order_date: a date from order_ts, which comes as "yyyy-MM-dd HH:mm:ss" or "MM/dd/yyyy".
    # 4. amount: remove the thousands commas, blank means unknown (NULL), cast to decimal(10,2).
    # 5. status: trimmed and lower case; blank becomes "unknown".
    # 6. One row per order: the load wrote some rows twice.
)

# silver.select("order_id", "customer_id", "order_date", "amount", "status") \
#     .write.mode("overwrite").saveAsTable("silver.orders")
