CREATE TABLE events (
    event_id INTEGER,
    payload STRUCT(customer STRUCT(id INTEGER, tier VARCHAR), items STRUCT(sku VARCHAR, qty INTEGER)[])
);
INSERT INTO events VALUES
    (1, {'customer': {'id': 7, 'tier': 'gold'}, 'items': [{'sku': 'ANV', 'qty': 1}, {'sku': 'HAM', 'qty': 2}]}),
    (2, {'customer': {'id': 8, 'tier': 'basic'}, 'items': [{'sku': 'TNG', 'qty': 3}]});
