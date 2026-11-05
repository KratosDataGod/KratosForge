# E31: split bronze.readings into silver.readings (good rows) and silver.readings_quarantine (bad
# rows, with why). `spark` is ready; run with Run, check with Check my work.

from pyspark.sql import functions as F

readings = spark.read.table("bronze.readings")   # reading_id, device_id, reading_ts, temperature

# Rules, checked in this order; a row's reason is the first rule it breaks:
#   missing_device             device_id is NULL
#   missing_time               reading_ts is NULL
#   missing_temperature        temperature is NULL
#   temperature_out_of_range   temperature below -50 or above 60 (-50 and 60 themselves are fine)
#
# silver.readings: the rows that break no rule, with the bronze columns.
# silver.readings_quarantine: every other row, with the bronze columns and reason.
# Every bronze row ends up in exactly one of the two, and a rerun leaves both tables the same.
