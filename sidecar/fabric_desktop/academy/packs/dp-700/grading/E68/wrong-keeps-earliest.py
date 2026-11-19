from pyspark.sql import Window
from pyspark.sql import functions as F
from delta.tables import DeltaTable

updates = spark.read.table("bronze.order_updates")

# One source row per order.
by_time = Window.partitionBy("order_id").orderBy(F.col("updated_at"))
latest = updates.withColumn("rn", F.row_number().over(by_time)).where("rn = 1").drop("rn")

(DeltaTable.forName(spark, "silver.orders").alias("t")
    .merge(latest.alias("s"), "t.order_id = s.order_id")
    .whenMatchedUpdateAll()
    .whenNotMatchedInsertAll()
    .execute())
