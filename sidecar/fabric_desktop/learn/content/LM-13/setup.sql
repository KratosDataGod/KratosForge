CREATE TABLE tickets (ticket_id INTEGER, priority VARCHAR, closed_at DATE);
INSERT INTO tickets VALUES
    (1, 'high', NULL),
    (2, 'low', NULL),
    (3, NULL, NULL),
    (4, 'medium', DATE '2026-10-01'),
    (5, 'medium', NULL),
    (6, NULL, DATE '2026-10-02');
