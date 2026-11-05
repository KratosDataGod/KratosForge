from pyspark.sql import functions as F

products = spark.read.table("bronze.products")
parts = F.split(F.col("full_name"), "\\|")
tags = (
    products
    .filter(F.col("status") != "discontinued")
    .withColumn("brand", F.element_at(parts, 1))
    .withColumn("model", F.element_at(parts, 2))
    .withColumn("price", (F.col("price_cents") / 100).cast("decimal(10,2)"))
    .withColumn("tag", F.explode(F.split(F.col("tags"), ",")))
    .withColumn("tag", F.lower(F.trim(F.col("tag"))))
    .withColumnRenamed("product_id", "id")
    .drop("full_name", "tags", "price_cents", "legacy_code", "status")
    .select("id", "brand", "model", "price", "tag")
)
tags.write.mode("append").saveAsTable("silver.product_tags")
