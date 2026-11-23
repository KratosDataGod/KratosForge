from delta.tables import DeltaTable
from pyspark.sql.types import StructType, StructField, IntegerType, StringType, DoubleType

schema = StructType([StructField("order_id", IntegerType()), StructField("customer", StringType()),
                     StructField("amount", DoubleType())])

# 1. Bronze: every landed order, as it arrived. Its own checkpoint.
(spark.readStream.schema(schema).format("json").load("Files/landing/orders")
    .writeStream
    .option("checkpointLocation", "Files/checkpoints/bronze_orders")
    .trigger(availableNow=True)
    .toTable("bronze.orders"))

# 2. Silver: a second query, so a second checkpoint. skipChangeCommits passes over the cleanup's deletes,
# which also means they never reach Silver, so Silver drops test orders itself.
(spark.readStream.option("skipChangeCommits", "true").table("bronze.orders")
    .where("customer <> 'TEST'")
    .writeStream
    .option("checkpointLocation", "Files/checkpoints/silver_orders")
    .trigger(availableNow=True)
    .toTable("silver.orders"))

# 3. Cleanup: test orders don't belong in Bronze.
DeltaTable.forName(spark, "bronze.orders").delete("customer = 'TEST'")
