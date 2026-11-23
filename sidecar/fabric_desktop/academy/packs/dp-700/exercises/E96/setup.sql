-- E96: a made-up AppRequests table, the same every time, shaped like a table an eventstream fills in an Eventhouse.
-- One row per request: when it arrived, which service and region answered it, its HTTP status and how long it took.
-- StatusCode is NULL when the caller gave up before an answer. DurationMs is NULL when no time was recorded.
CREATE TABLE AppRequests AS SELECT * FROM (VALUES
  (TIMESTAMP '2026-11-20 09:00:05', 'checkout', 'eu-west', 200, 120),
  (TIMESTAMP '2026-11-20 09:03:40', 'checkout', 'eu-west', 503, 2400),
  (TIMESTAMP '2026-11-20 09:07:12', 'checkout', 'eu-west', 200, 140),
  (TIMESTAMP '2026-11-20 09:14:59', 'checkout', 'eu-west', 200, 180),
  (TIMESTAMP '2026-11-20 09:15:00', 'checkout', 'eu-west', 500, 3100),
  (TIMESTAMP '2026-11-20 09:16:30', 'checkout', 'eu-west', 502, NULL),
  (TIMESTAMP '2026-11-20 09:22:10', 'checkout', 'eu-west', 200, 160),
  (TIMESTAMP '2026-11-20 09:29:50', 'checkout', 'eu-west', NULL, 900),
  (TIMESTAMP '2026-11-20 09:31:00', 'checkout', 'eu-west', 200, 130),
  (TIMESTAMP '2026-11-20 09:44:00', 'checkout', 'eu-west', 200, 150),
  (TIMESTAMP '2026-11-20 09:46:20', 'checkout', 'eu-west', 504, 3000),
  (TIMESTAMP '2026-11-20 09:02:00', 'search', 'eu-west', 200, 40),
  (TIMESTAMP '2026-11-20 09:05:00', 'search', 'eu-west', 200, 45),
  (TIMESTAMP '2026-11-20 09:11:00', 'search', 'eu-west', 200, 50),
  (TIMESTAMP '2026-11-20 09:18:00', 'search', 'eu-west', 404, 35),
  (TIMESTAMP '2026-11-20 09:19:00', 'search', 'eu-west', 500, 800),
  (TIMESTAMP '2026-11-20 09:25:00', 'search', 'eu-west', 200, 42),
  (TIMESTAMP '2026-11-20 09:27:00', 'search', 'eu-west', 200, 47),
  (TIMESTAMP '2026-11-20 09:33:00', 'search', 'eu-west', 200, 44),
  (TIMESTAMP '2026-11-20 09:52:00', 'search', 'eu-west', 200, 41),
  (TIMESTAMP '2026-11-20 09:58:00', 'search', 'eu-west', 503, 1200),
  (TIMESTAMP '2026-11-20 09:59:30', 'search', 'eu-west', 200, 39),
  (TIMESTAMP '2026-11-20 09:01:00', 'checkout', 'EU-West', 500, 2800),
  (TIMESTAMP '2026-11-20 09:34:00', 'search', 'EU-West', 503, 1500),
  (TIMESTAMP '2026-11-20 09:10:00', 'checkout', 'us-east', 500, 2500),
  (TIMESTAMP '2026-11-20 09:40:00', 'search', 'us-east', 200, 60)
) AS t(Timestamp, Service, Region, StatusCode, DurationMs);
