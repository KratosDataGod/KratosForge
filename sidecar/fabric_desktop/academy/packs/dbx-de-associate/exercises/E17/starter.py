# E17: flatten nested device events into silver.readings. `spark` is ready;
# run with Run, check with Check my work.

from pyspark.sql import functions as F

events = spark.read.table("bronze.device_events")
events.printSchema()   # device is a struct with a struct inside; readings is an array of structs

# 1. Some events were delivered more than once: keep one row per event_id.
# 2. One row per reading. A device that sent no readings (an empty or missing array) still
#    gets one row, with metric and value NULL, so the silent-device report can find it.
# 3. Columns: event_id, device_id, site_code, metric, value.
# 4. Write silver.readings so that running the code again leaves the same table.
