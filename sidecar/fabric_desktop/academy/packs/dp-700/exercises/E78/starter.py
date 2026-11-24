# E78: flatten sales, customers and products into one wide table, with each customer as they were on the sale's day.
# `spark` is ready. Run with Run, check with Check my work.

from pyspark.sql import functions as F

sales = spark.read.table("silver.sales")              # sale_id, customer_id, product_id, sale_date, amount
customers = spark.read.table("silver.dim_customer")   # one row per version of a customer (type 2)
products = spark.read.table("silver.dim_product")     # product_id, product_name, category

# 1. Keep every sale, one row per sale_id.
# 2. Take each sale's customer name, segment and region from the version in force on sale_date.
#    A version applies from valid_from up to the day before valid_to.
# 3. Add product_name and category.
# 4. A customer or product with no matching row shows 'Unknown' in every column it would fill.
# 5. Write gold.sales_wide with sale_id, sale_date, customer_id, customer_name, segment, region,
#    product_name, category, amount, so that running the code again leaves the same table.
