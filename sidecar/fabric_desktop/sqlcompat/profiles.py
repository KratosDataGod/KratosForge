"""Compatibility profiles: what each Fabric SQL engine supports, as Microsoft documents it.

A profile is the summary a person reads (Compatibility panel, docs/sql-compatibility.md); the
checks that act on it are the rules in rules.py, which name the profiles they apply to. When
Microsoft changes a page, change the rule and the profile together and move RULE_VERSION on.

Sources were read on 2026-10-17. Their "last updated" dates are kept beside each link.
"""

from __future__ import annotations

from typing import Any

RULE_VERSION = "2026.10.1"
CHECKED_ON = "2026-10-17"

LEARN = "https://learn.microsoft.com"
DOCS = {
    "surface": (f"{LEARN}/fabric/data-warehouse/tsql-surface-area", "2026-09-29"),
    "types": (f"{LEARN}/fabric/data-warehouse/data-types", "2026-06-11"),
    "transactions": (f"{LEARN}/fabric/data-warehouse/transactions", "2026-06-03"),
    "collation": (f"{LEARN}/fabric/data-warehouse/collation", "2025-08-14"),
    "temp": (f"{LEARN}/fabric/data-warehouse/temp-tables", "2026-07-27"),
    "tables": (f"{LEARN}/fabric/data-warehouse/tables", "2026-08-26"),
    "crossdb": (f"{LEARN}/fabric/data-warehouse/query-warehouse", "2026-02-13"),
    "sqldb": (f"{LEARN}/fabric/database/sql/limitations", "2026-08-24"),
    "top": (f"{LEARN}/sql/t-sql/queries/top-transact-sql", ""),
    "null": (f"{LEARN}/sql/t-sql/language-elements/null-and-unknown-transact-sql", ""),
    "notin": (f"{LEARN}/sql/t-sql/language-elements/in-transact-sql", ""),
    "division": (f"{LEARN}/sql/t-sql/language-elements/divide-transact-sql", ""),
    "spark": ("https://spark.apache.org/docs/3.5.1/sql-ref.html", ""),
    "spark_functions": ("https://spark.apache.org/docs/3.5.1/sql-ref-functions-builtin.html", ""),
    "spark_datetime": ("https://spark.apache.org/docs/3.5.1/sql-ref-datetime-pattern.html", ""),
    "duckdb_regex": ("https://duckdb.org/docs/stable/sql/functions/regular_expressions", ""),
    "fabric_runtime": (f"{LEARN}/fabric/data-engineering/runtime", ""),
    "local": ("https://github.com/KratosDataGod/fabric-desktop/blob/main/docs/sql-compatibility.md", ""),
}


def doc(key: str) -> str:
    return DOCS[key][0]


_TSQL_TYPES_NOT_IN_TABLES = {
    "money, smallmoney": "decimal (no monetary unit)",
    "datetime, smalldatetime": "datetime2",
    "datetimeoffset": "datetime2 (convert with AT TIME ZONE)",
    "nchar, nvarchar": "char, varchar (UTF-8 collation)",
    "text, ntext": "varchar",
    "image": "varbinary",
    "tinyint": "smallint",
    "geography, geometry": "varbinary (well-known binary) or varchar (well-known text)",
    "json": "varchar",
    "xml": "no equivalent",
    "CLR user-defined types": "no equivalent",
    "vector": "no equivalent",
}

_WAREHOUSE: dict[str, Any] = {
    "id": "warehouse",
    "label": "Fabric Warehouse (T-SQL)",
    "engine": "Fabric Data Warehouse",
    "short": "Fabric Warehouse",
    "sqlglot": "tsql",
    "runs_here": False,
    "statements": {
        "SELECT, CTEs, views, functions, procedures": "supported",
        "nested CTEs": "preview",
        "recursive CTEs and queries": "not supported",
        "INSERT, UPDATE, DELETE, MERGE, TRUNCATE": "supported",
        "CREATE TABLE, CTAS, DROP TABLE": "supported",
        "ALTER TABLE": "add nullable columns, drop columns, add or drop NOT ENFORCED keys; ALTER COLUMN in preview; nothing else",
        "session #temp tables": "supported; ## global temp tables not supported",
        "IDENTITY columns": "supported",
        "triggers, synonyms, materialized views, sequences, computed columns, external tables, unique indexes": "not supported",
        "FOR XML, SET ROWCOUNT, SET TRANSACTION ISOLATION LEVEL, PREDICT, BULK LOAD, CREATE USER": "not supported",
        "query and join hints": "a subset",
    },
    "unsupported_types": _TSQL_TYPES_NOT_IN_TABLES,
    "type_notes": ["datetime2 and time keep at most 6 digits of fractional seconds.",
                   "varchar(max) and varbinary(max) store at most 16 MB.",
                   "uniqueidentifier is stored as binary and can't be read on the SQL analytics endpoint.",
                   "Unsupported types can still be used in variables, parameters and expressions; only tables and other persisted objects can't use them."],
    "identifiers": "Default collation Latin1_General_100_BIN2_UTF8: names and text compare case-sensitively. A warehouse can be created case-insensitive (Latin1_General_100_CI_AS_KS_WS_SC_UTF8); collation can't change later.",
    "transactions": ["Snapshot isolation always; SET TRANSACTION ISOLATION LEVEL is ignored.",
                     "Write-write conflicts are checked per table: two transactions changing the same table can conflict; retry the one that fails.",
                     "No savepoints, named, marked or distributed transactions."],
    "ddl_dml": "Full DML. DDL is limited as listed; DDL inside explicit transactions is supported.",
    "semantic_differences": ["Case-sensitive comparisons by default.", "T-SQL integer division truncates (7 / 2 = 3).",
                             "Cross-database queries use three-part names, for items in the same workspace and region."],
    "confidence": "Documented rules from Microsoft Learn, checked statically. Not a substitute for running in the Warehouse.",
    "docs": ["surface", "types", "transactions", "collation", "temp", "tables", "crossdb"],
}

_ENDPOINT = {
    **_WAREHOUSE,
    "id": "endpoint",
    "label": "Fabric SQL analytics endpoint (T-SQL)",
    "engine": "SQL analytics endpoint of a lakehouse",
    "short": "the SQL analytics endpoint",
    "statements": {**_WAREHOUSE["statements"],
                   "INSERT, UPDATE, DELETE, MERGE, TRUNCATE": "not supported (tables are read-only)",
                   "CREATE TABLE, CTAS, DROP TABLE": "not supported (tables are read-only)",
                   "ALTER TABLE": "not supported (tables are read-only)",
                   "views, functions, procedures over the lakehouse tables": "supported"},
    "ddl_dml": "Read-only over the lakehouse's Delta tables. Views, functions and procedures can be created.",
}

_SQLDB: dict[str, Any] = {
    "id": "sqldb",
    "label": "Fabric SQL database (T-SQL)",
    "engine": "SQL database in Fabric (Azure SQL Database engine)",
    "short": "Fabric SQL database",
    "sqlglot": "tsql",
    "runs_here": False,
    "statements": {
        "Most Azure SQL Database T-SQL": "supported",
        "cross-database three-part names": "through the SQL analytics endpoint",
        "linked servers": "only as a target",
        "EXECUTE AS, distributed transactions, CDC, Service Broker, SQL Agent, logins, BACKUP": "not supported",
        "in-memory, ledger and Always Encrypted tables": "not supported",
    },
    "unsupported_types": {},
    "type_notes": ["A primary key can't use hierarchyid, sql_variant or timestamp."],
    "identifiers": "Column names can't contain spaces or , ; { } ( ) = tabs or newlines.",
    "transactions": ["Distributed transactions (MS DTC) aren't supported."],
    "ddl_dml": "Full DML and DDL, except the table types and operations listed.",
    "semantic_differences": ["Time zone choice isn't available.", "T-SQL integer division truncates (7 / 2 = 3)."],
    "confidence": "Documented limitations from Microsoft Learn. Most T-SQL is supported, so most findings are about features the docs list as missing.",
    "docs": ["sqldb"],
}

_SPARK: dict[str, Any] = {
    "id": "spark",
    "label": "Spark SQL (Lakehouse)",
    "engine": "Fabric Spark (lakehouse); on this PC, translated to DuckDB",
    "short": "Spark SQL",
    "sqlglot": "spark",
    "runs_here": True,
    "statements": {
        "SELECT, CTEs, temp views": "supported",
        "INSERT, MERGE, UPDATE, DELETE on Delta tables": "supported in Fabric; not run by this PC's SQL page",
        "CREATE TABLE, CREATE OR REPLACE, INSERT OVERWRITE": "supported in Fabric",
        "T-SQL (TOP, [names], @variables, GO)": "not Spark SQL",
        "recursive CTEs": "unverified (Spark 4 feature; depends on your Fabric runtime)",
    },
    "unsupported_types": {},
    "type_notes": ["Decimal arithmetic precision rules differ between Spark and DuckDB."],
    "identifiers": "Names are case-insensitive; quote with backticks. Text compares case-sensitively.",
    "transactions": ["No multi-statement transactions; each Delta write commits on its own."],
    "ddl_dml": "Lakehouse tables are Delta. This PC's SQL page only reads; write with Save as table or a notebook.",
    "semantic_differences": ["Regular expressions are Java's in Fabric and RE2 on this PC.",
                             "Date patterns are translated for DuckDB; unusual pattern letters can differ.",
                             "collect_list and similar give rows in no fixed order."],
    "confidence": "Spark SQL rules plus the known differences of running it on this PC. A query that runs here may still behave differently in Fabric Spark.",
    "docs": ["spark", "spark_functions", "fabric_runtime", "local"],
}

_DUCKDB: dict[str, Any] = {
    "id": "duckdb",
    "label": "DuckDB (this PC)",
    "engine": "DuckDB on this PC",
    "short": "DuckDB",
    "sqlglot": "duckdb",
    "runs_here": True,
    "statements": {"DuckDB SQL": "supported on this PC only; no Fabric engine runs it as written"},
    "unsupported_types": {},
    "type_notes": [],
    "identifiers": "Names are case-insensitive; quote with double quotes.",
    "transactions": [],
    "ddl_dml": "Runs on this PC against the lakehouse tables, read-only.",
    "semantic_differences": ["Not a Fabric engine: rewrite as Spark SQL or T-SQL before using the query in Fabric."],
    "confidence": "Checks for local-only constructs. Nothing here says how Fabric would run it.",
    "docs": ["local"],
}

PROFILES: dict[str, dict[str, Any]] = {p["id"]: p for p in (_SPARK, _WAREHOUSE, _ENDPOINT, _SQLDB, _DUCKDB)}


def profile(target: str | None) -> dict[str, Any]:
    key = target or "spark"
    if key not in PROFILES:
        raise ValueError(f"Unknown target {key!r}. Targets: {', '.join(PROFILES)}.")
    return PROFILES[key]


def profiles() -> list[dict[str, Any]]:
    """Every profile with its rule version and documentation links, for the page and the docs."""
    out = []
    for p in PROFILES.values():
        out.append({**{k: v for k, v in p.items() if k != "docs"}, "rule_version": RULE_VERSION, "checked_on": CHECKED_ON,
                    "docs": [{"url": DOCS[k][0], "updated": DOCS[k][1]} for k in p["docs"]]})
    return out
