# E24: revenue by country for the finance dashboard, written to gold.revenue_by_country. `spark` is
# ready; run with Run, check with Check my work.

from pyspark.sql import Window
from pyspark.sql import functions as F

events = spark.read.table("bronze.order_events")         # event_id, customer_id, amount
versions = spark.read.table("bronze.customer_versions")  # customer_id, country, valid_from

# 1. Some events were delivered more than once: keep one row per event_id.
# 2. A customer's country is the one in their latest version (largest valid_from).
# 3. Per country: customers (distinct), orders, revenue (sum, decimal(12,2)) and avg_order (rounded to 2).
# 4. Write gold.revenue_by_country with country, customers, orders, revenue, avg_order, so that a
#    rerun leaves the same table.
