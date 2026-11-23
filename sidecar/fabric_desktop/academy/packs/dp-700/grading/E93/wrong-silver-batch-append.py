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

# 2. Silver: read Bronze as a table and append it.
(spark.read.table("bronze.orders").where("customer <> 'TEST'")
    .write.mode("append").saveAsTable("silver.orders"))

# 3. Cleanup: test orders don't belong in Bronze.
DeltaTable.forName(spark, "bronze.orders").delete("customer = 'TEST'")
