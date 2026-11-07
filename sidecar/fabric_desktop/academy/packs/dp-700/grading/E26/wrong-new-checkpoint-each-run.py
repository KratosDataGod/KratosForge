import uuid
from pyspark.sql.types import StructType, StructField, IntegerType, StringType, DoubleType

schema = StructType([StructField("order_id", IntegerType()), StructField("customer", StringType()),
                     StructField("amount", DoubleType())])

(spark.readStream.schema(schema).format("json").load("Files/landing/orders")
    .writeStream
    .option("checkpointLocation", f"Files/checkpoints/{uuid.uuid4().hex}")
    .trigger(availableNow=True)
    .toTable("bronze.orders"))
