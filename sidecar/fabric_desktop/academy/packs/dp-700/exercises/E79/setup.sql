-- E79: freezer readings, one row per reading. A reading is warm when temp_c is above -15.
CREATE TABLE freezer_readings (device_id VARCHAR, reading_ts TIMESTAMP, temp_c DECIMAL(4,1));
INSERT INTO freezer_readings VALUES
    ('F1', TIMESTAMP '2026-07-01 10:00:00', -18.0), ('F1', TIMESTAMP '2026-07-01 10:02:00', -14.0),
    ('F1', TIMESTAMP '2026-07-01 10:04:00', -13.0), ('F1', TIMESTAMP '2026-07-01 10:06:00', -12.0),
    ('F1', TIMESTAMP '2026-07-01 10:08:00', -17.0), ('F1', TIMESTAMP '2026-07-01 10:10:00', -14.0),
    ('F1', TIMESTAMP '2026-07-01 10:20:00', -16.0),
    ('F2', TIMESTAMP '2026-07-01 11:00:00', -14.5), ('F2', TIMESTAMP '2026-07-01 11:05:00', -13.5),
    ('F2', TIMESTAMP '2026-07-01 11:10:00', -14.0), ('F2', TIMESTAMP '2026-07-01 11:20:00', -12.5),
    ('F2', TIMESTAMP '2026-07-01 11:22:00', -11.0),
    ('F3', TIMESTAMP '2026-07-01 10:05:00', -14.0), ('F3', TIMESTAMP '2026-07-01 10:07:00', -13.0),
    ('F3', TIMESTAMP '2026-07-01 10:09:00', -19.0), ('F3', TIMESTAMP '2026-07-01 10:11:00', -15.0);
