# E23: reshape bronze.products into silver.product_tags, one row per product and tag. `spark` is
# ready; run with Run, check with Check my work.

from pyspark.sql import functions as F

products = spark.read.table("bronze.products")
# product_id, full_name ("Brand|Model"), tags ("a, b,c" or NULL), price_cents, legacy_code, status

# 1. Leave out discontinued products.
# 2. brand and model: split full_name on "|" (split takes a regular expression, so escape it).
# 3. price: price_cents / 100 as decimal(10,2).
# 4. tag: one row per tag, trimmed and lower case. A product with no tags has no rows.
# 5. Rename product_id to id and drop the columns silver doesn't keep.
# 6. Write silver.product_tags with id, brand, model, price, tag, so that a rerun leaves the same table.
