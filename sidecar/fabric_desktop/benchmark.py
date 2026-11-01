"""The TPC-H benchmark notebooks: test a load on this PC and in Fabric with a standard workload.

Two notebooks, the same on this PC and in Fabric:

* **TPC-H data** generates the eight TPC-H tables at a scale factor you pick (1 is about 1 GB
  and 6 million order lines, 10 is about 10 GB) with DuckDB's tpch extension, and writes them
  to the attached lakehouse as Delta tables in the ``tpch`` schema (``tpch_region`` and so on
  in a lakehouse created without schemas). Run it once, anywhere.
* **TPC-H benchmark** loads those tables, runs the 22 TPC-H queries and writes each answer to
  ``bench.q01`` ... ``bench.q22``. Turn on Side by side and Run all: it runs here and in
  Fabric, and the answers are checked against each other.

The data isn't in the repository: it is generated on demand, so any scale fits. The query
text is DuckDB's own TPC-H queries (``tpch_queries()``), kept here so the benchmark needs no
extension. ``examples/benchmark/`` in the repository holds the same notebooks as .ipynb files,
written by ``python -m fabric_desktop.benchmark examples/benchmark``.

TPC-H is a trademark of the Transaction Processing Performance Council. These runs follow its
data and queries but are not audited TPC-H results.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .notebook import to_ipynb

DATA_NOTEBOOK = "TPC-H data"
BENCHMARK_NOTEBOOK = "TPC-H benchmark"
TABLES = ["region", "nation", "supplier", "customer", "part", "partsupp", "orders", "lineitem"]

# DuckDB's TPC-H queries (tpch_queries()), by query number.
QUERIES: dict[int, str] = {
    1: (
        "SELECT\n"
        "    l_returnflag,\n"
        "    l_linestatus,\n"
        "    sum(l_quantity) AS sum_qty,\n"
        "    sum(l_extendedprice) AS sum_base_price,\n"
        "    sum(l_extendedprice * (1 - l_discount)) AS sum_disc_price,\n"
        "    sum(l_extendedprice * (1 - l_discount) * (1 + l_tax)) AS sum_charge,\n"
        "    avg(l_quantity) AS avg_qty,\n"
        "    avg(l_extendedprice) AS avg_price,\n"
        "    avg(l_discount) AS avg_disc,\n"
        "    count(*) AS count_order\n"
        "FROM\n"
        "    lineitem\n"
        "WHERE\n"
        "    l_shipdate <= CAST('1998-09-02' AS date)\n"
        "GROUP BY\n"
        "    l_returnflag,\n"
        "    l_linestatus\n"
        "ORDER BY\n"
        "    l_returnflag,\n"
        "    l_linestatus;"
    ),
    2: (
        "SELECT\n"
        "    s_acctbal,\n"
        "    s_name,\n"
        "    n_name,\n"
        "    p_partkey,\n"
        "    p_mfgr,\n"
        "    s_address,\n"
        "    s_phone,\n"
        "    s_comment\n"
        "FROM\n"
        "    part,\n"
        "    supplier,\n"
        "    partsupp,\n"
        "    nation,\n"
        "    region\n"
        "WHERE\n"
        "    p_partkey = ps_partkey\n"
        "    AND s_suppkey = ps_suppkey\n"
        "    AND p_size = 15\n"
        "    AND p_type LIKE '%BRASS'\n"
        "    AND s_nationkey = n_nationkey\n"
        "    AND n_regionkey = r_regionkey\n"
        "    AND r_name = 'EUROPE'\n"
        "    AND ps_supplycost = (\n"
        "        SELECT\n"
        "            min(ps_supplycost)\n"
        "        FROM\n"
        "            partsupp,\n"
        "            supplier,\n"
        "            nation,\n"
        "            region\n"
        "        WHERE\n"
        "            p_partkey = ps_partkey\n"
        "            AND s_suppkey = ps_suppkey\n"
        "            AND s_nationkey = n_nationkey\n"
        "            AND n_regionkey = r_regionkey\n"
        "            AND r_name = 'EUROPE')\n"
        "ORDER BY\n"
        "    s_acctbal DESC,\n"
        "    n_name,\n"
        "    s_name,\n"
        "    p_partkey\n"
        "LIMIT 100;"
    ),
    3: (
        "SELECT\n"
        "    l_orderkey,\n"
        "    sum(l_extendedprice * (1 - l_discount)) AS revenue,\n"
        "    o_orderdate,\n"
        "    o_shippriority\n"
        "FROM\n"
        "    customer,\n"
        "    orders,\n"
        "    lineitem\n"
        "WHERE\n"
        "    c_mktsegment = 'BUILDING'\n"
        "    AND c_custkey = o_custkey\n"
        "    AND l_orderkey = o_orderkey\n"
        "    AND o_orderdate < CAST('1995-03-15' AS date)\n"
        "    AND l_shipdate > CAST('1995-03-15' AS date)\n"
        "GROUP BY\n"
        "    l_orderkey,\n"
        "    o_orderdate,\n"
        "    o_shippriority\n"
        "ORDER BY\n"
        "    revenue DESC,\n"
        "    o_orderdate\n"
        "LIMIT 10;"
    ),
    4: (
        "SELECT\n"
        "    o_orderpriority,\n"
        "    count(*) AS order_count\n"
        "FROM\n"
        "    orders\n"
        "WHERE\n"
        "    o_orderdate >= CAST('1993-07-01' AS date)\n"
        "    AND o_orderdate < CAST('1993-10-01' AS date)\n"
        "    AND EXISTS (\n"
        "        SELECT\n"
        "            *\n"
        "        FROM\n"
        "            lineitem\n"
        "        WHERE\n"
        "            l_orderkey = o_orderkey\n"
        "            AND l_commitdate < l_receiptdate)\n"
        "GROUP BY\n"
        "    o_orderpriority\n"
        "ORDER BY\n"
        "    o_orderpriority;"
    ),
    5: (
        "SELECT\n"
        "    n_name,\n"
        "    sum(l_extendedprice * (1 - l_discount)) AS revenue\n"
        "FROM\n"
        "    customer,\n"
        "    orders,\n"
        "    lineitem,\n"
        "    supplier,\n"
        "    nation,\n"
        "    region\n"
        "WHERE\n"
        "    c_custkey = o_custkey\n"
        "    AND l_orderkey = o_orderkey\n"
        "    AND l_suppkey = s_suppkey\n"
        "    AND c_nationkey = s_nationkey\n"
        "    AND s_nationkey = n_nationkey\n"
        "    AND n_regionkey = r_regionkey\n"
        "    AND r_name = 'ASIA'\n"
        "    AND o_orderdate >= CAST('1994-01-01' AS date)\n"
        "    AND o_orderdate < CAST('1995-01-01' AS date)\n"
        "GROUP BY\n"
        "    n_name\n"
        "ORDER BY\n"
        "    revenue DESC;"
    ),
    6: (
        "SELECT\n"
        "    sum(l_extendedprice * l_discount) AS revenue\n"
        "FROM\n"
        "    lineitem\n"
        "WHERE\n"
        "    l_shipdate >= CAST('1994-01-01' AS date)\n"
        "    AND l_shipdate < CAST('1995-01-01' AS date)\n"
        "    AND l_discount BETWEEN 0.05\n"
        "    AND 0.07\n"
        "    AND l_quantity < 24;"
    ),
    7: (
        "SELECT\n"
        "    supp_nation,\n"
        "    cust_nation,\n"
        "    l_year,\n"
        "    sum(volume) AS revenue\n"
        "FROM (\n"
        "    SELECT\n"
        "        n1.n_name AS supp_nation,\n"
        "        n2.n_name AS cust_nation,\n"
        "        extract(year FROM l_shipdate) AS l_year,\n"
        "        l_extendedprice * (1 - l_discount) AS volume\n"
        "    FROM\n"
        "        supplier,\n"
        "        lineitem,\n"
        "        orders,\n"
        "        customer,\n"
        "        nation n1,\n"
        "        nation n2\n"
        "    WHERE\n"
        "        s_suppkey = l_suppkey\n"
        "        AND o_orderkey = l_orderkey\n"
        "        AND c_custkey = o_custkey\n"
        "        AND s_nationkey = n1.n_nationkey\n"
        "        AND c_nationkey = n2.n_nationkey\n"
        "        AND ((n1.n_name = 'FRANCE'\n"
        "                AND n2.n_name = 'GERMANY')\n"
        "            OR (n1.n_name = 'GERMANY'\n"
        "                AND n2.n_name = 'FRANCE'))\n"
        "        AND l_shipdate BETWEEN CAST('1995-01-01' AS date)\n"
        "        AND CAST('1996-12-31' AS date)) AS shipping\n"
        "GROUP BY\n"
        "    supp_nation,\n"
        "    cust_nation,\n"
        "    l_year\n"
        "ORDER BY\n"
        "    supp_nation,\n"
        "    cust_nation,\n"
        "    l_year;"
    ),
    8: (
        "SELECT\n"
        "    o_year,\n"
        "    sum(\n"
        "        CASE WHEN nation = 'BRAZIL' THEN\n"
        "            volume\n"
        "        ELSE\n"
        "            0\n"
        "        END) / sum(volume) AS mkt_share\n"
        "FROM (\n"
        "    SELECT\n"
        "        extract(year FROM o_orderdate) AS o_year,\n"
        "        l_extendedprice * (1 - l_discount) AS volume,\n"
        "        n2.n_name AS nation\n"
        "    FROM\n"
        "        part,\n"
        "        supplier,\n"
        "        lineitem,\n"
        "        orders,\n"
        "        customer,\n"
        "        nation n1,\n"
        "        nation n2,\n"
        "        region\n"
        "    WHERE\n"
        "        p_partkey = l_partkey\n"
        "        AND s_suppkey = l_suppkey\n"
        "        AND l_orderkey = o_orderkey\n"
        "        AND o_custkey = c_custkey\n"
        "        AND c_nationkey = n1.n_nationkey\n"
        "        AND n1.n_regionkey = r_regionkey\n"
        "        AND r_name = 'AMERICA'\n"
        "        AND s_nationkey = n2.n_nationkey\n"
        "        AND o_orderdate BETWEEN CAST('1995-01-01' AS date)\n"
        "        AND CAST('1996-12-31' AS date)\n"
        "        AND p_type = 'ECONOMY ANODIZED STEEL') AS all_nations\n"
        "GROUP BY\n"
        "    o_year\n"
        "ORDER BY\n"
        "    o_year;"
    ),
    9: (
        "SELECT\n"
        "    nation,\n"
        "    o_year,\n"
        "    sum(amount) AS sum_profit\n"
        "FROM (\n"
        "    SELECT\n"
        "        n_name AS nation,\n"
        "        extract(year FROM o_orderdate) AS o_year,\n"
        "        l_extendedprice * (1 - l_discount) - ps_supplycost * l_quantity AS amount\n"
        "    FROM\n"
        "        part,\n"
        "        supplier,\n"
        "        lineitem,\n"
        "        partsupp,\n"
        "        orders,\n"
        "        nation\n"
        "    WHERE\n"
        "        s_suppkey = l_suppkey\n"
        "        AND ps_suppkey = l_suppkey\n"
        "        AND ps_partkey = l_partkey\n"
        "        AND p_partkey = l_partkey\n"
        "        AND o_orderkey = l_orderkey\n"
        "        AND s_nationkey = n_nationkey\n"
        "        AND p_name LIKE '%green%') AS profit\n"
        "GROUP BY\n"
        "    nation,\n"
        "    o_year\n"
        "ORDER BY\n"
        "    nation,\n"
        "    o_year DESC;"
    ),
    10: (
        "SELECT\n"
        "    c_custkey,\n"
        "    c_name,\n"
        "    sum(l_extendedprice * (1 - l_discount)) AS revenue,\n"
        "    c_acctbal,\n"
        "    n_name,\n"
        "    c_address,\n"
        "    c_phone,\n"
        "    c_comment\n"
        "FROM\n"
        "    customer,\n"
        "    orders,\n"
        "    lineitem,\n"
        "    nation\n"
        "WHERE\n"
        "    c_custkey = o_custkey\n"
        "    AND l_orderkey = o_orderkey\n"
        "    AND o_orderdate >= CAST('1993-10-01' AS date)\n"
        "    AND o_orderdate < CAST('1994-01-01' AS date)\n"
        "    AND l_returnflag = 'R'\n"
        "    AND c_nationkey = n_nationkey\n"
        "GROUP BY\n"
        "    c_custkey,\n"
        "    c_name,\n"
        "    c_acctbal,\n"
        "    c_phone,\n"
        "    n_name,\n"
        "    c_address,\n"
        "    c_comment\n"
        "ORDER BY\n"
        "    revenue DESC\n"
        "LIMIT 20;"
    ),
    11: (
        "SELECT\n"
        "    ps_partkey,\n"
        "    sum(ps_supplycost * ps_availqty) AS value\n"
        "FROM\n"
        "    partsupp,\n"
        "    supplier,\n"
        "    nation\n"
        "WHERE\n"
        "    ps_suppkey = s_suppkey\n"
        "    AND s_nationkey = n_nationkey\n"
        "    AND n_name = 'GERMANY'\n"
        "GROUP BY\n"
        "    ps_partkey\n"
        "HAVING\n"
        "    sum(ps_supplycost * ps_availqty) > (\n"
        "        SELECT\n"
        "            sum(ps_supplycost * ps_availqty) * 0.0001000000\n"
        "        FROM\n"
        "            partsupp,\n"
        "            supplier,\n"
        "            nation\n"
        "        WHERE\n"
        "            ps_suppkey = s_suppkey\n"
        "            AND s_nationkey = n_nationkey\n"
        "            AND n_name = 'GERMANY')\n"
        "ORDER BY\n"
        "    value DESC;"
    ),
    12: (
        "SELECT\n"
        "    l_shipmode,\n"
        "    sum(\n"
        "        CASE WHEN o_orderpriority = '1-URGENT'\n"
        "            OR o_orderpriority = '2-HIGH' THEN\n"
        "            1\n"
        "        ELSE\n"
        "            0\n"
        "        END) AS high_line_count,\n"
        "    sum(\n"
        "        CASE WHEN o_orderpriority <> '1-URGENT'\n"
        "            AND o_orderpriority <> '2-HIGH' THEN\n"
        "            1\n"
        "        ELSE\n"
        "            0\n"
        "        END) AS low_line_count\n"
        "FROM\n"
        "    orders,\n"
        "    lineitem\n"
        "WHERE\n"
        "    o_orderkey = l_orderkey\n"
        "    AND l_shipmode IN ('MAIL', 'SHIP')\n"
        "    AND l_commitdate < l_receiptdate\n"
        "    AND l_shipdate < l_commitdate\n"
        "    AND l_receiptdate >= CAST('1994-01-01' AS date)\n"
        "    AND l_receiptdate < CAST('1995-01-01' AS date)\n"
        "GROUP BY\n"
        "    l_shipmode\n"
        "ORDER BY\n"
        "    l_shipmode;"
    ),
    13: (
        "SELECT\n"
        "    c_count,\n"
        "    count(*) AS custdist\n"
        "FROM (\n"
        "    SELECT\n"
        "        c_custkey,\n"
        "        count(o_orderkey)\n"
        "    FROM\n"
        "        customer\n"
        "    LEFT OUTER JOIN orders ON c_custkey = o_custkey\n"
        "    AND o_comment NOT LIKE '%special%requests%'\n"
        "GROUP BY\n"
        "    c_custkey) AS c_orders (c_custkey,\n"
        "        c_count)\n"
        "GROUP BY\n"
        "    c_count\n"
        "ORDER BY\n"
        "    custdist DESC,\n"
        "    c_count DESC;"
    ),
    14: (
        "SELECT\n"
        "    100.00 * sum(\n"
        "        CASE WHEN p_type LIKE 'PROMO%' THEN\n"
        "            l_extendedprice * (1 - l_discount)\n"
        "        ELSE\n"
        "            0\n"
        "        END) / sum(l_extendedprice * (1 - l_discount)) AS promo_revenue\n"
        "FROM\n"
        "    lineitem,\n"
        "    part\n"
        "WHERE\n"
        "    l_partkey = p_partkey\n"
        "    AND l_shipdate >= date '1995-09-01'\n"
        "    AND l_shipdate < CAST('1995-10-01' AS date);"
    ),
    15: (
        "WITH revenue AS (\n"
        "    SELECT\n"
        "        l_suppkey AS supplier_no,\n"
        "        sum(l_extendedprice * (1 - l_discount)) AS total_revenue\n"
        "    FROM\n"
        "        lineitem\n"
        "    WHERE\n"
        "        l_shipdate >= CAST('1996-01-01' AS date)\n"
        "      AND l_shipdate < CAST('1996-04-01' AS date)\n"
        "    GROUP BY\n"
        "        supplier_no\n"
        ")\n"
        "SELECT\n"
        "    s_suppkey,\n"
        "    s_name,\n"
        "    s_address,\n"
        "    s_phone,\n"
        "    total_revenue\n"
        "FROM\n"
        "    supplier,\n"
        "    revenue\n"
        "WHERE\n"
        "    s_suppkey = supplier_no\n"
        "    AND total_revenue = (\n"
        "        SELECT\n"
        "            max(total_revenue)\n"
        "        FROM revenue)\n"
        "ORDER BY\n"
        "    s_suppkey;"
    ),
    16: (
        "SELECT\n"
        "    p_brand,\n"
        "    p_type,\n"
        "    p_size,\n"
        "    count(DISTINCT ps_suppkey) AS supplier_cnt\n"
        "FROM\n"
        "    partsupp,\n"
        "    part\n"
        "WHERE\n"
        "    p_partkey = ps_partkey\n"
        "    AND p_brand <> 'Brand#45'\n"
        "    AND p_type NOT LIKE 'MEDIUM POLISHED%'\n"
        "    AND p_size IN (49, 14, 23, 45, 19, 3, 36, 9)\n"
        "    AND ps_suppkey NOT IN (\n"
        "        SELECT\n"
        "            s_suppkey\n"
        "        FROM\n"
        "            supplier\n"
        "        WHERE\n"
        "            s_comment LIKE '%Customer%Complaints%')\n"
        "GROUP BY\n"
        "    p_brand,\n"
        "    p_type,\n"
        "    p_size\n"
        "ORDER BY\n"
        "    supplier_cnt DESC,\n"
        "    p_brand,\n"
        "    p_type,\n"
        "    p_size;"
    ),
    17: (
        "SELECT\n"
        "    sum(l_extendedprice) / 7.0 AS avg_yearly\n"
        "FROM\n"
        "    lineitem,\n"
        "    part\n"
        "WHERE\n"
        "    p_partkey = l_partkey\n"
        "    AND p_brand = 'Brand#23'\n"
        "    AND p_container = 'MED BOX'\n"
        "    AND l_quantity < (\n"
        "        SELECT\n"
        "            0.2 * avg(l_quantity)\n"
        "        FROM\n"
        "            lineitem\n"
        "        WHERE\n"
        "            l_partkey = p_partkey);"
    ),
    18: (
        "SELECT\n"
        "    c_name,\n"
        "    c_custkey,\n"
        "    o_orderkey,\n"
        "    o_orderdate,\n"
        "    o_totalprice,\n"
        "    sum(l_quantity)\n"
        "FROM\n"
        "    customer,\n"
        "    orders,\n"
        "    lineitem\n"
        "WHERE\n"
        "    o_orderkey IN (\n"
        "        SELECT\n"
        "            l_orderkey\n"
        "        FROM\n"
        "            lineitem\n"
        "        GROUP BY\n"
        "            l_orderkey\n"
        "        HAVING\n"
        "            sum(l_quantity) > 300)\n"
        "    AND c_custkey = o_custkey\n"
        "    AND o_orderkey = l_orderkey\n"
        "GROUP BY\n"
        "    c_name,\n"
        "    c_custkey,\n"
        "    o_orderkey,\n"
        "    o_orderdate,\n"
        "    o_totalprice\n"
        "ORDER BY\n"
        "    o_totalprice DESC,\n"
        "    o_orderdate\n"
        "LIMIT 100;"
    ),
    19: (
        "SELECT\n"
        "    sum(l_extendedprice * (1 - l_discount)) AS revenue\n"
        "FROM\n"
        "    lineitem,\n"
        "    part\n"
        "WHERE (p_partkey = l_partkey\n"
        "    AND p_brand = 'Brand#12'\n"
        "    AND p_container IN ('SM CASE', 'SM BOX', 'SM PACK', 'SM PKG')\n"
        "    AND l_quantity >= 1\n"
        "    AND l_quantity <= 1 + 10\n"
        "    AND p_size BETWEEN 1 AND 5\n"
        "    AND l_shipmode IN ('AIR', 'AIR REG')\n"
        "    AND l_shipinstruct = 'DELIVER IN PERSON')\n"
        "    OR (p_partkey = l_partkey\n"
        "        AND p_brand = 'Brand#23'\n"
        "        AND p_container IN ('MED BAG', 'MED BOX', 'MED PKG', 'MED PACK')\n"
        "        AND l_quantity >= 10\n"
        "        AND l_quantity <= 10 + 10\n"
        "        AND p_size BETWEEN 1 AND 10\n"
        "        AND l_shipmode IN ('AIR', 'AIR REG')\n"
        "        AND l_shipinstruct = 'DELIVER IN PERSON')\n"
        "    OR (p_partkey = l_partkey\n"
        "        AND p_brand = 'Brand#34'\n"
        "        AND p_container IN ('LG CASE', 'LG BOX', 'LG PACK', 'LG PKG')\n"
        "        AND l_quantity >= 20\n"
        "        AND l_quantity <= 20 + 10\n"
        "        AND p_size BETWEEN 1 AND 15\n"
        "        AND l_shipmode IN ('AIR', 'AIR REG')\n"
        "        AND l_shipinstruct = 'DELIVER IN PERSON');"
    ),
    20: (
        "SELECT\n"
        "    s_name,\n"
        "    s_address\n"
        "FROM\n"
        "    supplier,\n"
        "    nation\n"
        "WHERE\n"
        "    s_suppkey IN (\n"
        "        SELECT\n"
        "            ps_suppkey\n"
        "        FROM\n"
        "            partsupp\n"
        "        WHERE\n"
        "            ps_partkey IN (\n"
        "                SELECT\n"
        "                    p_partkey\n"
        "                FROM\n"
        "                    part\n"
        "                WHERE\n"
        "                    p_name LIKE 'forest%')\n"
        "                AND ps_availqty > (\n"
        "                    SELECT\n"
        "                        0.5 * sum(l_quantity)\n"
        "                    FROM\n"
        "                        lineitem\n"
        "                    WHERE\n"
        "                        l_partkey = ps_partkey\n"
        "                        AND l_suppkey = ps_suppkey\n"
        "                        AND l_shipdate >= CAST('1994-01-01' AS date)\n"
        "                        AND l_shipdate < CAST('1995-01-01' AS date)))\n"
        "            AND s_nationkey = n_nationkey\n"
        "            AND n_name = 'CANADA'\n"
        "        ORDER BY\n"
        "            s_name;"
    ),
    21: (
        "SELECT\n"
        "    s_name,\n"
        "    count(*) AS numwait\n"
        "FROM\n"
        "    supplier,\n"
        "    lineitem l1,\n"
        "    orders,\n"
        "    nation\n"
        "WHERE\n"
        "    s_suppkey = l1.l_suppkey\n"
        "    AND o_orderkey = l1.l_orderkey\n"
        "    AND o_orderstatus = 'F'\n"
        "    AND l1.l_receiptdate > l1.l_commitdate\n"
        "    AND EXISTS (\n"
        "        SELECT\n"
        "            *\n"
        "        FROM\n"
        "            lineitem l2\n"
        "        WHERE\n"
        "            l2.l_orderkey = l1.l_orderkey\n"
        "            AND l2.l_suppkey <> l1.l_suppkey)\n"
        "    AND NOT EXISTS (\n"
        "        SELECT\n"
        "            *\n"
        "        FROM\n"
        "            lineitem l3\n"
        "        WHERE\n"
        "            l3.l_orderkey = l1.l_orderkey\n"
        "            AND l3.l_suppkey <> l1.l_suppkey\n"
        "            AND l3.l_receiptdate > l3.l_commitdate)\n"
        "    AND s_nationkey = n_nationkey\n"
        "    AND n_name = 'SAUDI ARABIA'\n"
        "GROUP BY\n"
        "    s_name\n"
        "ORDER BY\n"
        "    numwait DESC,\n"
        "    s_name\n"
        "LIMIT 100;"
    ),
    22: (
        "SELECT\n"
        "    cntrycode,\n"
        "    count(*) AS numcust,\n"
        "    sum(c_acctbal) AS totacctbal\n"
        "FROM (\n"
        "    SELECT\n"
        "        substring(c_phone FROM 1 FOR 2) AS cntrycode,\n"
        "        c_acctbal\n"
        "    FROM\n"
        "        customer\n"
        "    WHERE\n"
        "        substring(c_phone FROM 1 FOR 2) IN ('13', '31', '23', '29', '30', '18', '17')\n"
        "        AND c_acctbal > (\n"
        "            SELECT\n"
        "                avg(c_acctbal)\n"
        "            FROM\n"
        "                customer\n"
        "            WHERE\n"
        "                c_acctbal > 0.00\n"
        "                AND substring(c_phone FROM 1 FOR 2) IN ('13', '31', '23', '29', '30', '18', '17'))\n"
        "            AND NOT EXISTS (\n"
        "                SELECT\n"
        "                    *\n"
        "                FROM\n"
        "                    orders\n"
        "                WHERE\n"
        "                    o_custkey = c_custkey)) AS custsale\n"
        "GROUP BY\n"
        "    cntrycode\n"
        "ORDER BY\n"
        "    cntrycode;"
    ),
}


def _code(source: str, tags: list[str] | None = None) -> dict[str, Any]:
    return {"type": "code", "source": source.strip("\n"), **({"meta": {"tags": tags}} if tags else {})}


def _md(source: str) -> dict[str, Any]:
    return {"type": "markdown", "source": source.strip("\n")}


_GENERATE = '''
import atexit, glob, os, shutil, subprocess, sys, tempfile, time
import duckdb
import pyarrow as pa
from fabric_desktop import lakehouse

def load_tpch(con):
    """DuckDB's tpch extension: from DuckDB's extension site, else the same extension from PyPI.
    Returns what a new connection runs to load it too."""
    try:
        con.execute("INSTALL tpch")
        con.execute("LOAD tpch")
        return "LOAD tpch"
    except Exception as e:
        print(f"DuckDB's extension site isn't reachable ({e}); installing the extension from PyPI")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", f"duckdb-extension-tpch=={duckdb.__version__}"])
    import duckdb_extension_tpch
    path = glob.glob(os.path.join(os.path.dirname(duckdb_extension_tpch.__file__), "extensions", "*", "tpch.duckdb_extension"))[0]
    con.execute(f"INSTALL '{path}'")
    con.execute("LOAD tpch")
    return f"INSTALL '{path}'; LOAD tpch"

# DuckDB's generator uses one core, so the data is made in parts, one process per core, each into
# a database file of its own (files, so scale factors bigger than RAM still fit). Together the
# parts are exactly the rows one dbgen call makes. Deleted after the tables are written.
work = tempfile.mkdtemp(prefix="tpch_")
with open(os.path.join(work, ".kratosforge"), "w") as f:  # KratosForge's own, and which process it's in use by
    f.write(str(os.getpid()))
con = duckdb.connect(os.path.join(work, "tpch.duckdb"))

def clean_up():
    """Close the database and delete the generated data: at scale 10, several GB of this PC's disk."""
    try:
        con.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)
atexit.register(clean_up)  # if the tables are never written, the session's end still deletes it
loader = load_tpch(con)
parts = max(1, min(os.cpu_count() or 1, 16, int(scale_factor * 8) or 1))
started = time.perf_counter()
part_code = ("import duckdb, sys; con = duckdb.connect(sys.argv[1]); con.execute('SET threads = 1'); "
             "con.execute(sys.argv[2]); con.execute(f'CALL dbgen(sf={sys.argv[3]}, children={sys.argv[4]}, step={sys.argv[5]})')")
procs = [subprocess.Popen([sys.executable, "-c", part_code, os.path.join(work, f"part{i}.duckdb"), loader,
                           str(scale_factor), str(parts), str(i)]) for i in range(parts)]
failed = False
for p in procs:
    if p.wait() and not failed:  # one part failed: stop the rest rather than wait for them
        failed = True
        for other in procs:
            if other.poll() is None:
                other.kill()
if failed:
    clean_up()
    raise RuntimeError("Generating the TPC-H data failed; the error is above.")
for i in range(parts):
    con.execute(f"ATTACH '{os.path.join(work, f'part{i}.duckdb')}' AS part{i} (READ_ONLY)")
for table in TABLES:
    con.execute(f"CREATE VIEW {table} AS " + " UNION ALL ".join(f"SELECT * FROM part{i}.{table}" for i in range(parts)))
print(f"Generated scale factor {scale_factor} in {time.perf_counter() - started:.1f} s (in {parts} process{'es' if parts > 1 else ''})")

def table_name(schema, table):
    """schema.table, or schema_table in a lakehouse created without schemas."""
    return f"{schema}.{table}" if lakehouse.has_schemas() else f"{schema}_{table}"
'''

_WRITE = '''
try:
    for table in TABLES:
        started = time.perf_counter()
        # A relation's reader: DuckDB 2.0 warns that one over an executed result copies every row again.
        result = con.sql(f"SELECT * FROM {table}")
        reader = (getattr(result, "to_arrow_reader", None) or result.fetch_record_batch)(batch_rows)  # newer, older DuckDB
        # The whole table as one write: one commit, its batches never all in memory at once. On this PC
        # it uploads in the background while the next table is read; the cell ends when all are in.
        lakehouse.write_table(reader, table_name(schema, table))
        print(f"{table_name(schema, table)} in {time.perf_counter() - started:.1f} s")
finally:
    clean_up()  # written or not, the generated data is deleted
'''

_LOAD = '''
import time
import duckdb
import polars as pl
from deltalake.exceptions import TableNotFoundError
from fabric_desktop import lakehouse

timings = []
def timed(step, started):
    seconds = time.perf_counter() - started
    timings.append({"step": step, "seconds": round(seconds, 3)})
    return seconds

def table_name(schema, table):
    """schema.table, or schema_table in a lakehouse created without schemas."""
    return f"{schema}.{table}" if lakehouse.has_schemas() else f"{schema}_{table}"

def source(table):
    try:
        return lakehouse.open_table(table_name(schema, table))
    except TableNotFoundError:
        raise TableNotFoundError(f"{table_name(schema, table)} isn't in {lakehouse.name()} yet. Run the TPC-H data "
                                 f"notebook with this lakehouse attached first, then run this one again.") from None

# Read every table from the lakehouse once, so the queries time compute, not downloads.
con = duckdb.connect()
started = time.perf_counter()
for table in TABLES:
    con.register(f"_src_{table}", source(table).to_pyarrow_dataset())
    con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM _src_{table}")
    con.unregister(f"_src_{table}")
rows = con.execute("SELECT count(*) FROM lineitem").fetchall()[0][0]
print(f"Loaded {len(TABLES)} tables ({rows:,} order lines) in {timed('load', started):.1f} s")
'''

_RUN = '''
answers = {}
for n in queries:
    started = time.perf_counter()
    answers[n] = con.sql(QUERIES[n]).pl()
    print(f"Q{n:02d}: {len(answers[n]):>6} rows in {timed(f'Q{n:02d}', started):.2f} s")
query_seconds = sum(t["seconds"] for t in timings if t["step"].startswith("Q"))
print(f"All {len(queries)} queries: {query_seconds:.1f} s")
'''

_SAVE = '''
# Each answer becomes a table, so Side by side can check this PC's and Fabric's answers match.
started = time.perf_counter()
for n, answer in answers.items():
    lakehouse.write_table(answer, table_name(output_schema, f"q{n:02d}"))
timed("write answers", started)
display(pl.DataFrame(timings))
'''


def notebooks() -> dict[str, list[dict[str, Any]]]:
    """The two benchmark notebooks' cells, by notebook name."""
    tables = f"TABLES = {TABLES!r}"
    data = [
        _md("""
# TPC-H data
Generates the TPC-H tables with DuckDB's tpch extension and writes them to the attached lakehouse as Delta
tables in the `tpch` schema (named `tpch_region` and so on in a lakehouse without schemas). Run it once, on this PC
or in Fabric; **TPC-H benchmark** reads them.

| scale_factor | order lines | size (about) |
|---|---|---|
| 0.1 | 600 thousand | 100 MB |
| 1 | 6 million | 1 GB |
| 10 | 60 million | 10 GB |
| 30 | 180 million | 30 GB |
"""),
        _code(f'''
scale_factor = 1     # 0.1, 1, 10, 30 ...
schema = "tpch"      # where the tables go in the attached lakehouse
batch_rows = 2_000_000
{tables}
''', ["parameters"]),
        _code(_GENERATE),
        _code(_WRITE),
    ]
    queries = "QUERIES = {\n" + "".join(f'{n}: """\n{q}\n""",\n' for n, q in QUERIES.items()) + "}"
    bench = [
        _md("""
# TPC-H benchmark
Loads the `tpch` tables (from **TPC-H data**), runs the 22 TPC-H queries with DuckDB and writes each answer to
`bench.q01` ... `bench.q22` (`bench_q01` ... in a lakehouse without schemas).

To compare this PC with Fabric, turn on **Side by side** in the ribbon and choose **Run all**. The notebook runs here,
then in Fabric, and KratosForge checks that both wrote the same answers. The timings table at the end shows each
step; the one from Fabric is in the notebook's run in Fabric.
"""),
        _code(f'''
schema = "tpch"          # where TPC-H data wrote the tables
output_schema = "bench"  # where the answers go
queries = list(range(1, 23))
{tables}
''', ["parameters"]),
        _code("# The 22 TPC-H queries, as DuckDB's tpch extension has them.\n" + queries) | {"meta": {"source_hidden": True}},
        _code(_LOAD),
        _code(_RUN),
        _code(_SAVE),
    ]
    return {DATA_NOTEBOOK: data, BENCHMARK_NOTEBOOK: bench}


def write(folder: str | Path) -> list[Path]:
    """Write the notebooks as .ipynb files (examples/benchmark in the repository)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, cells in notebooks().items():
        path = folder / f"{name.lower().replace(' ', '_').replace('-', '')}.ipynb"
        path.write_text(json.dumps(to_ipynb(cells, None), indent=1) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


if __name__ == "__main__":
    for p in write(sys.argv[1] if len(sys.argv) > 1 else "."):
        print(p)
