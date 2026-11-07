# E26: this stream loads the landing files into bronze.orders, but every run loads every file again,
# and amount lands as text. Fix both. `spark` is ready.
from datetime import datetime

from pyspark.sql.types import StructType, StructField, IntegerType, StringType, DoubleType

schema = StructType([StructField("order_id", IntegerType()), StructField("customer", StringType()),
                     StructField("amount", StringType())])

(spark.readStream.schema(schema).format("json").load("Files/landing/orders")
    .writeStream
    .option("checkpointLocation", f"Files/checkpoints/bronze_orders_{datetime.now():%Y%m%d%H%M%S%f}")
    .trigger(availableNow=True)
    .toTable("bronze.orders"))
