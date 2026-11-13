-- E17 practice data: bronze.device_events as Auto Loader landed it from nested JSON, with the schema it
-- inferred (a struct inside a struct, and an array of structs). Deterministic; nothing random.
CREATE SCHEMA bronze;
CREATE TABLE bronze.device_events AS SELECT * FROM (VALUES
  (1, {'id': 'd-01', 'site': {'code': 'LON', 'country': 'GB'}},
      [{'metric': 'temp', 'value': 21.5}, {'metric': 'humidity', 'value': 40.0}], '2026-03-01 08:00:00'),
  (2, {'id': 'd-02', 'site': {'code': 'PAR', 'country': 'FR'}},
      [{'metric': 'temp', 'value': 19.0}], '2026-03-01 08:00:05'),
  (3, {'id': 'd-03', 'site': {'code': 'LON', 'country': 'GB'}},
      [], '2026-03-01 08:00:09'),
  (2, {'id': 'd-02', 'site': {'code': 'PAR', 'country': 'FR'}},
      [{'metric': 'temp', 'value': 19.0}], '2026-03-01 08:00:05'),
  (4, {'id': 'd-04', 'site': {'code': 'BER', 'country': 'DE'}},
      [{'metric': 'temp', 'value': 22.25}, {'metric': 'co2', 'value': 410.0}, {'metric': 'humidity', 'value': 35.5}],
      '2026-03-01 08:01:00'),
  (5, {'id': 'd-05', 'site': {'code': 'BER', 'country': 'DE'}},
      NULL, '2026-03-01 08:01:30')
) AS t(event_id, device, readings, received_at);
