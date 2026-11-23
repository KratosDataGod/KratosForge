# E93: the notebook loads landing files into Bronze, streams Bronze into Silver, then cleans test orders out of
# Bronze. The Silver step fails on the first run. Fix the loading pattern so every run works. `spark` is ready.
from delta.tables import DeltaTable
from pyspark.sql.types import StructType, StructField, IntegerType, StringType, DoubleType

schema = StructType([StructField("order_id", IntegerType()), StructField("customer", StringType()),
                     StructField("amount", DoubleType())])

# 1. Bronze: every landed order, as it arrived.
(spark.readStream.schema(schema).format("json").load("Files/landing/orders")
    .writeStream
    .option("checkpointLocation", "Files/checkpoints/orders")
    .trigger(availableNow=True)
    .toTable("bronze.orders"))

# 2. Silver: the new Bronze rows, for reports.
(spark.readStream.table("bronze.orders")
    .writeStream
    .option("checkpointLocation", "Files/checkpoints/orders")
    .trigger(availableNow=True)
    .toTable("silver.orders"))

# 3. Cleanup: test orders don't belong in Bronze. Keep this step, and keep it last.
DeltaTable.forName(spark, "bronze.orders").delete("customer = 'TEST'")
