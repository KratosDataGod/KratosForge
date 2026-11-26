from pyspark.sql import functions as F

sales = spark.read.table("silver.sales").alias("s")
customers = spark.read.table("silver.dim_customer").alias("c")
products = spark.read.table("silver.dim_product").alias("p")

# Inner joins: a sale whose customer or product isn't in its dimension disappears.
as_of_sale = ((F.col("s.customer_id") == F.col("c.customer_id"))
              & (F.col("s.sale_date") >= F.col("c.valid_from"))
              & (F.col("s.sale_date") < F.col("c.valid_to")))
wide = (
    sales.join(customers, as_of_sale, "inner")
    .join(products, F.col("s.product_id") == F.col("p.product_id"), "inner")
    .select(
        F.col("s.sale_id").alias("sale_id"),
        F.col("s.sale_date").alias("sale_date"),
        F.col("s.customer_id").alias("customer_id"),
        F.coalesce(F.col("c.customer_name"), F.lit("Unknown")).alias("customer_name"),
        F.coalesce(F.col("c.segment"), F.lit("Unknown")).alias("segment"),
        F.coalesce(F.col("c.region"), F.lit("Unknown")).alias("region"),
        F.coalesce(F.col("p.product_name"), F.lit("Unknown")).alias("product_name"),
        F.coalesce(F.col("p.category"), F.lit("Unknown")).alias("category"),
        F.col("s.amount").alias("amount"),
    )
)
wide.write.mode("overwrite").saveAsTable("gold.sales_wide")
