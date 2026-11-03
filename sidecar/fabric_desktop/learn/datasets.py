"""Learn datasets (LX-03): sample tables made on this PC from a seed, with a recorded list of the
problems put into them on purpose.

Nothing is downloaded. The same dataset, size and seed always give the same rows (``fingerprint``
proves it), so a lab's checks know exactly what a right answer is, and the defects list says which
rows a cleaning exercise has to find. Values come from DuckDB's ``hash`` of the row number and the
seed, so a million rows take a moment, not minutes.

``shop`` has four tables:

    customers  customer_id, name, email, region, signup_date
    stores     store_id, store_name, region
    products   product_id, product_name, category, price
    orders     order_id, customer_id, store_id, product_id, order_date, quantity, amount, status

With defects on, orders and customers get one row of each kind in ``DEFECT_KINDS``.
"""

from __future__ import annotations

import hashlib
from typing import Any

import duckdb
from ..duckdb_rows import first_row, one_row

SIZES = {"S": 1_000, "M": 100_000, "L": 2_000_000}   # orders; the other tables scale with them
DEFECT_KINDS = ("duplicate_customer", "missing_email", "negative_amount", "future_date", "orphan_store",
                "region_case")
DATASETS = {"shop": "Customers, stores, products and orders of a small retailer"}
REGIONS = ("North", "South", "East", "West")
CATEGORIES = ("Bikes", "Helmets", "Lights", "Locks", "Tools")
TABLES = ("customers", "stores", "products", "orders")


def _h(expr: str, seed: int, salt: int) -> str:
    """A whole number from 0 up, the same every time for the same row, seed and salt."""
    return f"CAST(hash({expr}, {int(seed)}, {int(salt)}) % 1000000007 AS BIGINT)"


def generate(con: duckdb.DuckDBPyConnection, name: str = "shop", size: str = "S", seed: int = 7,
             defects: bool = True) -> dict[str, Any]:
    """Make the dataset's tables in ``con`` (replacing tables of the same name) and return its manifest:
    {dataset, size, seed, tables: {name: rows}, defects: [{id, kind, table, key}]}."""
    if name not in DATASETS:
        raise ValueError(f"Unknown dataset {name}; there is {', '.join(DATASETS)}")
    if size not in SIZES:
        raise ValueError(f"Size is one of {', '.join(SIZES)}")
    seed = int(seed)
    n_orders = SIZES[size]
    n_customers = max(50, n_orders // 10)
    n_stores, n_products = 12, 40
    regions = "['" + "','".join(REGIONS) + "']"
    categories = "['" + "','".join(CATEGORIES) + "']"
    con.execute(f"""
        CREATE OR REPLACE TABLE stores AS
        SELECT i AS store_id, 'Store ' || lpad(i::VARCHAR, 2, '0') AS store_name,
               {regions}[1 + i % 4] AS region
        FROM range(1, {n_stores + 1}) t(i) ORDER BY i""")
    con.execute(f"""
        CREATE OR REPLACE TABLE products AS
        SELECT i AS product_id, 'Product ' || i AS product_name, {categories}[1 + i % 5] AS category,
               CAST(5 + {_h('i', seed, 1)} % 9500 / 10.0 AS DECIMAL(10,2)) AS price
        FROM range(1, {n_products + 1}) t(i) ORDER BY i""")
    con.execute(f"""
        CREATE OR REPLACE TABLE customers AS
        SELECT i AS customer_id, 'Customer ' || i AS name, 'customer' || i || '@example.com' AS email,
               {regions}[1 + {_h('i', seed, 2)} % 4] AS region,
               DATE '2022-01-01' + CAST({_h('i', seed, 3)} % 1000 AS INTEGER) AS signup_date
        FROM range(1, {n_customers + 1}) t(i) ORDER BY i""")
    con.execute(f"""
        CREATE OR REPLACE TABLE orders AS
        SELECT o.i AS order_id,
               1 + {_h('o.i', seed, 4)} % {n_customers} AS customer_id,
               1 + {_h('o.i', seed, 5)} % {n_stores} AS store_id,
               p.product_id,
               DATE '2025-01-01' + CAST({_h('o.i', seed, 7)} % 640 AS INTEGER) AS order_date,
               CAST(1 + {_h('o.i', seed, 8)} % 5 AS INTEGER) AS quantity,
               CAST(p.price * (1 + {_h('o.i', seed, 8)} % 5) AS DECIMAL(12,2)) AS amount,
               CASE WHEN {_h('o.i', seed, 9)} % 20 = 0 THEN 'cancelled'
                    WHEN {_h('o.i', seed, 9)} % 20 = 1 THEN 'returned' ELSE 'complete' END AS status
        FROM range(1, {n_orders + 1}) o(i)
        JOIN products p ON p.product_id = 1 + {_h('o.i', seed, 6)} % {n_products}
        ORDER BY o.i""")
    found: list[dict[str, Any]] = []
    if defects:
        found = _add_defects(con, seed, n_orders, n_customers)
    return {"dataset": name, "size": size, "seed": seed,
            "tables": {t: one_row(con.execute(f"SELECT count(*) FROM {t}"))[0] for t in TABLES},  # type: ignore[index]
            "defects": found}


def _add_defects(con: duckdb.DuckDBPyConnection, seed: int, n_orders: int, n_customers: int) -> list[dict[str, Any]]:
    """One row of each defect kind, at rows picked from the seed, so the answer is known exactly."""
    def pick(salt: int, n: int, taken: set[int]) -> int:
        k = 1 + int(hashlib.sha256(f"{seed}:{salt}".encode()).hexdigest(), 16) % n
        while k in taken:
            k = 1 + k % n
        taken.add(k)
        return k

    orders: set[int] = set()
    customers: set[int] = set()
    dup = pick(11, n_customers, customers)
    missing = pick(12, n_customers, customers)
    lower = pick(13, n_customers, customers)
    negative = pick(14, n_orders, orders)
    future = pick(15, n_orders, orders)
    orphan = pick(16, n_orders, orders)
    con.execute("INSERT INTO customers SELECT * FROM customers WHERE customer_id = ?", [dup])
    con.execute("UPDATE customers SET email = NULL WHERE customer_id = ?", [missing])
    con.execute("UPDATE customers SET region = lower(region) WHERE customer_id = ?", [lower])
    con.execute("UPDATE orders SET amount = -amount WHERE order_id = ?", [negative])
    con.execute("UPDATE orders SET order_date = DATE '2099-01-01' WHERE order_id = ?", [future])
    con.execute("UPDATE orders SET store_id = 99 WHERE order_id = ?", [orphan])
    rows = [("duplicate_customer", "customers", dup), ("missing_email", "customers", missing),
            ("region_case", "customers", lower), ("negative_amount", "orders", negative),
            ("future_date", "orders", future), ("orphan_store", "orders", orphan)]
    return [{"id": f"D{n}", "kind": kind, "table": table, "key": key} for n, (kind, table, key) in enumerate(rows, 1)]


def fingerprint(con: duckdb.DuckDBPyConnection, tables: tuple[str, ...] = TABLES) -> str:
    """One SHA-256 over every table's rows in key order: equal fingerprints, equal data."""
    digest = hashlib.sha256()
    for t in tables:
        key = first_row(con.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{t}' "
                          "ORDER BY ordinal_position LIMIT 1"))
        rows = con.execute(f"SELECT * FROM {t} ORDER BY ALL").fetchall()  # noqa: S608 - fixed table names
        digest.update(f"{t}:{key}:{len(rows)}:".encode())
        for r in rows:
            digest.update(repr(r).encode())
    return digest.hexdigest()
