from pyspark.sql.types import StructType, StructField, IntegerType, StringType, DoubleType

schema = StructType([StructField("order_id", IntegerType()), StructField("customer", StringType()),
                     StructField("amount", StringType())])

(spark.readStream.schema(schema).format("json").load("Files/landing/orders")
    .writeStream
    .option("checkpointLocation", "Files/checkpoints/bronze_orders")
    .trigger(availableNow=True)
    .toTable("bronze.orders"))
