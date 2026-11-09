-- E17 grading data the learner never sees: other ids, an event delivered three times, a long readings
-- array and two silent devices (an empty array and a NULL one). DuckDB SQL.
CREATE SCHEMA bronze;
CREATE TABLE bronze.device_events AS SELECT * FROM (VALUES
  (70, {'id': 'x-70', 'site': {'code': 'OSL', 'country': 'NO'}},
       [{'metric': 'a', 'value': 1.0}, {'metric': 'b', 'value': 2.0}, {'metric': 'c', 'value': 3.0},
        {'metric': 'd', 'value': 4.0}], '2026-04-01 00:00:00'),
  (71, {'id': 'x-71', 'site': {'code': 'OSL', 'country': 'NO'}}, NULL, '2026-04-01 00:00:01'),
  (72, {'id': 'x-72', 'site': {'code': 'MAD', 'country': 'ES'}},
       [{'metric': 'temp', 'value': -3.5}], '2026-04-01 00:00:02'),
  (72, {'id': 'x-72', 'site': {'code': 'MAD', 'country': 'ES'}},
       [{'metric': 'temp', 'value': -3.5}], '2026-04-01 00:00:02'),
  (72, {'id': 'x-72', 'site': {'code': 'MAD', 'country': 'ES'}},
       [{'metric': 'temp', 'value': -3.5}], '2026-04-01 00:00:02'),
  (73, {'id': 'x-73', 'site': {'code': 'MAD', 'country': 'ES'}}, [], '2026-04-01 00:00:03')
) AS t(event_id, device, readings, received_at);
