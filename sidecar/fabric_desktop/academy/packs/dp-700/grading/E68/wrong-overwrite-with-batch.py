from pyspark.sql import Window
from pyspark.sql import functions as F

updates = spark.read.table("bronze.order_updates")

# One source row per order: its newest change in the batch.
newest_first = Window.partitionBy("order_id").orderBy(F.col("updated_at").desc())
latest = updates.withColumn("rn", F.row_number().over(newest_first)).where("rn = 1").drop("rn")

# Skip the MERGE: write the batch as the table.
latest.write.mode("overwrite").saveAsTable("silver.orders")
